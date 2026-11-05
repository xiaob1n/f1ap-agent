"""Deterministic local fake execution and durable recovery state machine."""
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from f1ap_agent.contracts.artifact import Contract
from f1ap_agent.contracts.verifier import canonical, digest, evaluate
from f1ap_agent.worker.research import bind, compute, results as research_results
from f1ap_agent.messaging import Sender
from f1ap_agent.runtime import (
    Json, RuntimeFault, Store, atomic_json, json_events, json_object, json_array, json_integer, read_json, json_string,
)
from f1ap_agent.worker.models import Task
from f1ap_agent.contracts.artifact import check_format


def validate_log(task: Task, log: dict[str, Json], contract: Contract) -> None:
    """Verify persisted state shape, event hashes, and frozen event contracts."""
    required = {'run_id', 'phase', 'events', 'request', 'call_status'}
    optional = {'call_key', 'call_started_at', 'results', 'finished', 'accepted', 'research'}
    if not required <= set(log) or set(log) - required - optional:
        raise RuntimeFault('LOG_SHAPE')
    if digest(json_object(log['request'])['payload']) != digest(task.payload):
        raise RuntimeFault('LOG_REQUEST_CONFLICT')
    Task.parse(canonical(log['request']), contract)
    if log['call_status'] not in (None, 'CALL_STARTED', 'SUCCEEDED', 'UNKNOWN'):
        raise RuntimeFault('LOG_CALL_STATE')
    if log['phase'] not in ('REGISTERED', 'FINISHED'):
        raise RuntimeFault('LOG_PHASE')
    if log['call_status'] is not None:
        check_format('f1ap-uuid', json_string(log['call_key']), '/call_key')
        check_format('f1ap-utc-ms', json_string(log['call_started_at']), '/call_started_at')
    events = json_events(log['events'])
    if log['phase'] == 'REGISTERED':
        if events or 'accepted' in log or 'results' in log or 'finished' in log:
            raise RuntimeFault('LOG_INCOMPLETE_STATE')
        return
    results = json_events(log['results'])
    summaries = [{key: result[key] for key in ('question_id', 'result_sha256', 'status')}
                 for result in results]
    context = {'request': task.payload, 'received_results': summaries, 'usage_history': []}
    revision = 0 if log['call_status'] is None else 1
    if len(events) != 11 + revision or len(results) != 10:
        raise RuntimeFault('LOG_EVENT_SET')
    ids = [event['event_id'] for event in events]
    if len(set(json_string(item) for item in ids)) != len(ids):
        raise RuntimeFault('LOG_DUPLICATE_EVENTS')
    for event in events:
        verdict = evaluate(canonical(event), contract=contract, context=context)
        if not verdict['accepted']:
            raise RuntimeFault('LOG_EVENT_CONTRACT')
    if [event['payload'] for event in events[revision:-1]] != results:
        raise RuntimeFault('LOG_RESULTS_CONFLICT')
    if events[-1]['event_type'] != 'agent.run.finished.v1' or events[-1]['payload'] != log['finished']:
        raise RuntimeFault('LOG_FINISHED_CONFLICT')
    if json_object(log['finished'])['last_usage_revision'] != revision:
        raise RuntimeFault('LOG_USAGE_CONFLICT')
    accepted_ids = [json_string(item) for item in json_array(log.get('accepted', []))]
    event_ids = [json_string(item) for item in ids]
    if len(set(accepted_ids)) != len(accepted_ids) or not set(accepted_ids) <= set(event_ids):
        raise RuntimeFault('LOG_ACCEPTED_CONFLICT')


class Executor(Protocol):
    def execute(self, task: Task) -> None: ...


class SimulatedInterruption(Exception):
    """Test-only crash point after the call intent is durable."""


@dataclass(frozen=True, slots=True)
class FakeExecutor:
    calls_path: Path
    interrupt: bool = False

    def execute(self, task: Task) -> None:
        """Persist a counted fake effect; never contact a service."""
        record = json_object(read_json(self.calls_path)) if self.calls_path.exists() else {'calls': 0}
        count = record['calls']
        if type(count) is not int:
            raise RuntimeFault('FAKE_COUNTER_CORRUPT')
        atomic_json(self.calls_path, {'calls': count + 1})
        if self.interrupt:
            raise SimulatedInterruption


def stable_id(run_id: str, name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'f1ap-b1:{run_id}:{name}'))


def envelope(task: Task, name: str, event_type: str,
             payload: dict[str, Json]) -> dict[str, Json]:
    return {'schema_version': '1.0', 'event_id': stable_id(str(task.run_id), name),
            'event_type': event_type, 'occurred_at': task.stamp(),
            'trace_id': task.request['trace_id'], 'payload_sha256': digest(payload), 'payload': payload}


def fixed_results(task: Task, reason: str) -> list[dict[str, Json]]:
    """An honest fixed fake output: no claimed inference or business-rule support."""
    results: list[dict[str, Json]] = []
    for question_id in task.question_ids:
        payload: dict[str, Json] = {
            'run_id': str(task.run_id), 'question_id': question_id,
            'definition_sha256': task.payload['definition_sha256'],
            'input_sha256': task.payload['input_sha256'], 'status': 'FAILED',
            'data_status': 'INSUFFICIENT', 'reason': reason, 'probabilities': [],
            'recommendations': [], 'evidence': [], 'fixture_class': task.fixture_class,
        }
        if reason in ('FAKE_FIXED_OUTPUT', 'E06_SYNTHETIC_V1'):
            question = next(q for q in json_events(json_object(task.payload['definition'])['questions'])
                            if q['question_id'] == question_id)
            rule = json_object(question['rule'])
            if rule['verification'] == 'VERIFIED':
                options = json_events(question['options'])
                count = json_integer(rule['min_picks'])
                picks: list[dict[str, Json]] = []
                if question['value_kind'] == 'OPTION':
                    picks = [{'value_kind': 'OPTION', 'option_key': option['option_key']}
                             for option in options[:max(1, count)]]
                else:
                    picks = [{'value_kind': 'NUMERIC', 'numeric_value':
                              json_object(question['numeric_bounds'])['min']}]
                strategy = json_events(json_object(task.payload['profile'])['strategies'])[0]
                payload.update(status='VALID', data_status='SUFFICIENT',
                    probabilities=[{'event_key': 'B1_SYNTHETIC', 'family': 'JOINT',
                                    'option_keys': [], 'probability': '1', 'rank': None}],
                    recommendations=[{'strategy': strategy['strategy'], 'version': strategy['version'],
                        'objective': 'EXPECTED_POINTS', 'expected_score': '0', 'picks': picks}])
                if reason == 'E06_SYNTHETIC_V1':
                    alternatives = ([{'value_kind': 'OPTION', 'option_key': option['option_key']}
                                     for option in reversed(options[-max(1, count):])]
                                    if question['value_kind'] == 'OPTION' else
                                    [{'value_kind': 'NUMERIC', 'numeric_value':
                                      json_object(question['numeric_bounds'])['max']}])
                    payload['recommendations'] = [
                        {'strategy': item['strategy'], 'version': item['version'],
                         'objective': item['strategy'], 'expected_score': '0',
                         'picks': picks if item['strategy'] == 'EXPECTED_POINTS' else alternatives}
                        for item in json_events(json_object(task.payload['profile'])['strategies'])]
        payload['result_sha256'] = digest(payload)
        results.append(payload)
    return results


def usage(task: Task, call_key: str, status: str, started_at: str) -> dict[str, Json]:
    return {'run_id': str(task.run_id), 'call_key': call_key, 'usage_revision': 1,
            'call_started_at': started_at, 'call_status': status, 'model_config_ref': 'MOCK_ONLY',
            'input_tokens': 10 if status == 'SUCCEEDED' else 0,
            'output_tokens': 20 if status == 'SUCCEEDED' else 0,
            'pricing': {'status': 'UNKNOWN', 'unit_price': None, 'original_cost': None,
                        'charged_cny': None, 'currency': None, 'price_version': None},
            'fixture_class': 'TEST_FIXTURE'}


@dataclass(frozen=True, slots=True)
class Worker:
    store: Store
    contract: Contract
    executor: Executor
    sender: Sender
    mapping_path: Path | None = None

    def run(self, raw: bytes, now: str | None = None) -> tuple[Task, bool]:
        """Consume one fake delivery. Registered history never becomes a fresh run."""
        with self.store.exclusive():
            return self._run(raw, now)

    def _run(self, raw: bytes, now: str | None) -> tuple[Task, bool]:
        task = Task.parse(raw, self.contract, now)
        registry = self.store.registry()
        run_id = str(task.run_id)
        fingerprint = digest(task.payload)
        if run_id in registry:
            if registry[run_id] != fingerprint:
                raise RuntimeFault('RUN_IDENTITY_CONFLICT')
            log = self.store.registry_log(run_id)
            validate_log(task, log, self.contract)
            if log.get('phase') == 'FINISHED':
                return task, self._send(log)
            if log.get('call_status') == 'CALL_STARTED':
                return task, self._finish(task, log, 'CALL_UNKNOWN', 'UNKNOWN')
            if log.get('call_status') == 'SUCCEEDED':
                return task, self._finish(task, log, 'FAKE_FIXED_OUTPUT', 'SUCCEEDED')
            if log.get('call_status') == 'UNKNOWN':
                return task, self._finish(task, log, 'CALL_UNKNOWN', 'UNKNOWN')
            if log.get('phase') != 'REGISTERED':
                raise RuntimeFault('LOG_STATE_INVALID')
        else:
            if self.store.log_path(run_id).exists():
                raise RuntimeFault('UNREGISTERED_LOG_EXISTS')
            registry[run_id] = fingerprint
            atomic_json(self.store.root / 'runs.json', registry)
            log: dict[str, Json] = {'run_id': run_id, 'phase': 'REGISTERED', 'events': [],
                                    'request': task.request, 'call_status': None}
            self.store.save(run_id, log)
        if task.expired():
            return task, self._finish(task, log, 'EXPIRED', None)
        if task.is_research:
            return task, self._finish(task, log, 'RESEARCH', None)
        profile = json_object(task.payload['profile'])
        if task.fixture_class == 'TEST_FIXTURE' and profile['algorithm_version'] == 'E06_SYNTHETIC_V1':
            expected: dict[str, Json] = {
                'algorithm_version': 'E06_SYNTHETIC_V1',
                'strategies': [{'strategy': name, 'version': name + '_V1'}
                               for name in ('EXPECTED_POINTS', 'HIT_RATE')],
                'model_config_ref': 'MOCK_ONLY', 'price_version': 'NOT_APPLICABLE', 'tools': []}
            if profile != expected or task.call_limit != 0:
                raise RuntimeFault('E06_SYNTHETIC_PROFILE')
            return task, self._finish(task, log, 'E06_SYNTHETIC_V1', None)
        if task.call_limit == 0:
            return task, self._finish(task, log, 'CALL_LIMIT_ZERO', None)
        call_key = stable_id(run_id, 'call:1')
        log.update(call_status='CALL_STARTED', call_key=call_key, call_started_at=task.stamp())
        self.store.save(run_id, log)
        atomic_json(self.store.root / 'calls' / f'{call_key}.json',
                    {'run_id': run_id, 'call_key': call_key, 'status': 'CALL_STARTED',
                     'started_at': task.stamp()})
        self.executor.execute(task)
        log['call_status'] = 'SUCCEEDED'
        self.store.save(run_id, log)
        return task, self._finish(task, log, 'FAKE_FIXED_OUTPUT', 'SUCCEEDED')

    def _finish(self, task: Task, log: dict[str, Json], reason: str,
                call_status: str | None) -> bool:
        results = self._research_results(task, log) if reason == 'RESEARCH' else fixed_results(task, reason)
        events: list[dict[str, Json]] = []
        revision = 0
        if call_status is not None:
            revision = 1
            call_key = str(log['call_key'])
            log['call_status'] = call_status
            atomic_json(self.store.root / 'calls' / f'{call_key}.json',
                        {'run_id': str(task.run_id), 'call_key': call_key, 'status': call_status,
                         'started_at': log['call_started_at']})
            events.append(envelope(task, 'usage:1', 'model.usage.updated.v1',
                                   usage(task, call_key, call_status, str(log['call_started_at']))))
        summaries: list[dict[str, Json]] = []
        for result in results:
            events.append(envelope(task, f"result:{result['question_id']}",
                                   'prediction.question.completed.v1', result))
            summaries.append({key: result[key] for key in ('question_id', 'status', 'result_sha256')})
        final: dict[str, Json] = {'run_id': str(task.run_id),
            'expected_question_ids': list(task.question_ids), 'results': summaries,
            'run_status': ('TIMED_OUT' if reason == 'EXPIRED' else
                'SUCCEEDED' if all(result['status'] == 'VALID' for result in results) else
                'PARTIAL' if any(result['status'] == 'VALID' for result in results) else 'FAILED'),
            'last_usage_revision': revision, 'fixture_class': task.fixture_class}
        events.append(envelope(task, 'finished', 'agent.run.finished.v1', final))
        context = {'request': task.payload, 'received_results': summaries, 'usage_history': []}
        for event in events:
            verdict = evaluate(canonical(event), contract=self.contract, context=context)
            if not verdict['accepted']:
                raise RuntimeFault('OUTPUT_CONTRACT:' + str(verdict['error']))
        log.update(events=events, results=results, finished=final, phase='FINISHED')
        self.store.save(str(task.run_id), log)
        return self._send(log)

    def _research_results(self, task: Task, log: dict[str, Json]) -> list[dict[str, Json]]:
        """冻结计算身份后生成研究结果，复用原有事件落盘与恢复。"""
        config = bind(task, self.mapping_path)
        identity: dict[str, Json] = {'mapping_sha256': task.mapping_sha256,
            'manifest_sha256': config.manifest_sha256, 'data_cutoff_at': task.payload['data_cutoff_at']}
        if 'research' in log and log['research'] != identity:
            raise RuntimeFault('RESEARCH_IDENTITY_CONFLICT')
        log['research'] = identity
        self.store.save(str(task.run_id), log)
        if json_string(json_object(task.payload['profile'])['algorithm_version']).startswith('TEN_QUESTION_GRAPH_V1@'):
            from f1ap_agent.graph import run
            return run(task, config)
        research = compute(task, config)
        results = research_results(task, research)
        if research.features is not None and research.baseline is not None:
            atomic_json(self.store.root / 'runs' / str(task.run_id) / 'research.json', {
                'identity': identity, 'features': {key: list(values) for key, values in research.features.laps},
                'counts': {item.option_key: list(item.counts) for item in research.baseline.marginals},
                'method': 'PODIUM_PACE_BOOTSTRAP_V1', 'seed': 0, 'samples': 10000, 'laps_per_sample': 5})
        return results

    def _send(self, log: dict[str, Json]) -> bool:
        """Persist every pending event before the first local acceptance attempt."""
        events = json_events(log['events'])
        accepted = log.get('accepted', [])
        if not isinstance(accepted, list):
            raise RuntimeFault('ACCEPTANCE_LOG_CORRUPT')
        for event in events:
            if event['event_id'] not in accepted:
                path = self.store.root / 'pending' / f"{event['event_id']}.json"
                if path.exists() and read_json(path) != event:
                    raise RuntimeFault('PENDING_EVENT_CORRUPT')
                atomic_json(path, event)
        for event in events:
            if event['event_id'] not in accepted and self.sender.send(event):
                accepted.append(event['event_id'])
                log['accepted'] = accepted
                self.store.save(str(log['run_id']), log)
                (self.store.root / 'pending' / f"{event['event_id']}.json").unlink()
        return len(accepted) == len(events)
