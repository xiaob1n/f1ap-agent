"""Deterministic local fake execution and durable recovery state machine."""
import uuid
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from f1ap_agent.contracts.artifact import Contract
from f1ap_agent.contracts.verifier import canonical, digest, evaluate
from f1ap_agent.worker.research import bind, compute, results as research_results
from f1ap_agent.messaging import Sender
from f1ap_agent.runtime import (
    Json, RuntimeFault, Store, atomic_json, json_events, json_object, json_array, json_integer, read_json, json_string,
)
from f1ap_agent.worker.models import Task, NewsTask
import f1ap_agent.worker.news as news
from f1ap_agent.contracts.artifact import check_format
from f1ap_agent.models.calls import Calls
from f1ap_agent.models.mock import MockPlan, MockProvider, Provider
from f1ap_agent.models.deepseek import DeepSeek
from f1ap_agent.worker import llm


class LlmState(TypedDict):
    prompt: str
    evidence: list[dict[str, Json]]
    reason: str
    results: list[dict[str, Json]]


def validate_log(task: Task, log: dict[str, Json], contract: Contract) -> None:
    """Verify persisted state shape, event hashes, and frozen event contracts."""
    required = {'run_id', 'phase', 'events', 'request', 'call_status'}
    optional = {'call_key', 'call_started_at', 'results', 'finished', 'accepted', 'research',
                'schema_version', 'mock_plan_sha256', 'mock_script_sha256', 'lease_until', 'calls', 'llm'}
    if not required <= set(log) or set(log) - required - optional:
        raise RuntimeFault('LOG_SHAPE')
    if digest(json_object(log['request'])['payload']) != digest(task.payload):
        raise RuntimeFault('LOG_REQUEST_CONFLICT')
    Task.parse(canonical(log['request']), contract)
    if log['call_status'] not in (None, 'CALL_STARTED', 'SUCCEEDED', 'FAILED', 'UNKNOWN'):
        raise RuntimeFault('LOG_CALL_STATE')
    if log['phase'] not in ('REGISTERED', 'FINISHED'):
        raise RuntimeFault('LOG_PHASE')
    if log['call_status'] is not None:
        check_format('f1ap-uuid', json_string(log['call_key']), '/call_key')
        check_format('f1ap-utc-ms', json_string(log['call_started_at']), '/call_started_at')
    events = json_events(log['events'])
    if 'schema_version' in log and log['schema_version'] != '1.1':
        raise RuntimeFault('LOG_VERSION')
    if log['phase'] == 'REGISTERED':
        if ((events and log.get('schema_version') != '1.1')
                or ('accepted' in log and log.get('schema_version') != '1.1')
                or 'results' in log or 'finished' in log):
            raise RuntimeFault('LOG_INCOMPLETE_STATE')
        return
    results = json_events(log['results'])
    summaries = [{key: result[key] for key in ('question_id', 'result_sha256', 'status')}
                 for result in results]
    context = {'request': task.payload, 'received_results': summaries, 'usage_history': []}
    predictions = [event for event in events if event['event_type'] == 'prediction.question.completed.v1']
    finishes = [event for event in events if event['event_type'] == 'agent.run.finished.v1']
    usages = [event for event in events if event['event_type'] == 'model.usage.updated.v1']
    if (len(results) != len(task.question_ids) or len(finishes) != 1
            or len(events) != len(predictions) + len(finishes) + len(usages)):
        raise RuntimeFault('LOG_EVENT_SET')
    ids = [event['event_id'] for event in events]
    if len(set(json_string(item) for item in ids)) != len(ids):
        raise RuntimeFault('LOG_DUPLICATE_EVENTS')
    for event in events:
        verdict = evaluate(canonical(event), contract=contract, context=context)
        if not verdict['accepted']:
            raise RuntimeFault('LOG_EVENT_CONTRACT')
    if [event['payload'] for event in predictions] != results:
        raise RuntimeFault('LOG_RESULTS_CONFLICT')
    if finishes[0]['payload'] != log['finished']:
        raise RuntimeFault('LOG_FINISHED_CONFLICT')
    prior_usages = [event for event in events[:events.index(finishes[0])]
                    if event['event_type'] == 'model.usage.updated.v1']
    revision = max((json_integer(json_object(event['payload'])['usage_revision']) for event in prior_usages), default=0)
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
    mock_plan: MockPlan | None = None
    provider: Provider | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    scheduler: Callable[[float], None] = time.sleep
    checkpoint: Callable[[str], None] = lambda name: None
    mock_plan_path: Path | None = None
    news_catalog_path: Path | None = None
    llm_provider: DeepSeek | None = None

    def calls(self) -> Calls:
        if self.mock_plan is None or self.provider is None:
            raise RuntimeFault('MOCK_PROVIDER_REQUIRED')
        return Calls(self.store, self.contract, self.mock_plan, self.provider,
                     self.clock, self.scheduler, self.checkpoint)

    def run(self, raw: bytes, now: str | None = None) -> tuple[Task | NewsTask, bool]:
        """Consume one fake delivery. Registered history never becomes a fresh run."""
        with self.store.exclusive():
            return self._run(raw, now)

    def _run(self, raw: bytes, now: str | None) -> tuple[Task | NewsTask, bool]:
        task = Task.parse(raw, self.contract, now)
        match task:
            case NewsTask():
                return task, self._news(task)
            case Task():
                return self._predict(task)

    def _predict(self, task: Task) -> tuple[Task, bool]:
        profile = json_object(task.payload['profile'])
        controlled = json_string(profile['model_config_ref']).startswith('E07_MOCK_')
        if controlled:
            calls = self.calls()
            if (task.fixture_class != 'TEST_FIXTURE'
                    or profile['model_config_ref'] not in {call.model_config_ref for call in calls.plan.calls}
                    or task.is_research or profile['algorithm_version'] == 'E06_SYNTHETIC_V1'):
                raise RuntimeFault('MOCK_PLAN_BINDING')
        registry = self.store.registry()
        run_id = str(task.run_id)
        fingerprint = digest(task.payload)
        if run_id in registry:
            if registry[run_id] != fingerprint:
                raise RuntimeFault('RUN_IDENTITY_CONFLICT')
            log = self.store.registry_log(run_id)
            validate_log(task, log, self.contract)
            if log.get('schema_version') == '1.1':
                self.calls().validate(task, log)
                if not controlled:
                    raise RuntimeFault('LOG_PLAN_BINDING')
            if log.get('phase') == 'FINISHED':
                return task, self._send(log)
            if task.is_llm:
                return task, self._llm(task, log)
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
        if task.is_llm:
            return task, self._llm(task, log)
        if controlled:
            calls = self.calls()
            if 'schema_version' not in log:
                calls.initialize(task, log)
            calls.run(task, log)
            reason = 'EXPIRED' if task.expired(self.clock()) else 'E07_MOCK_OUTPUT'
            return task, self._finish(task, log, reason, None)
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

    def _news(self, task: NewsTask) -> bool:
        plan, batches = news.load(task, self.mock_plan_path, self.news_catalog_path, self.contract, self.mock_plan)
        calls = Calls(self.store, self.contract, plan,
                      self.provider or MockProvider(plan, self.store.root / 'mock-effects.json'),
                      self.clock, self.scheduler, self.checkpoint)
        run_id = str(task.run_id)
        registry = self.store.registry()
        fingerprint = digest(task.payload)
        if run_id in registry:
            if registry[run_id] != fingerprint:
                raise RuntimeFault('RUN_IDENTITY_CONFLICT')
            log = self.store.registry_log(run_id)
            news.validate(task, log, batches)
            if 'schema_version' not in log:
                if log['events'] or log['call_status'] is not None:
                    raise RuntimeFault('NEWS_LOG_INITIALIZATION')
                calls.initialize(task, log)
            calls.validate(task, log)
            for event in json_events(log['events']):
                if not evaluate(canonical(event), contract=self.contract,
                                context={'request': task.payload, 'received_results': [], 'usage_history': []})['accepted']:
                    raise RuntimeFault('NEWS_LOG_CONTRACT')
            if log['phase'] == 'FINISHED':
                return self._send(log)
        else:
            if self.store.log_path(run_id).exists():
                raise RuntimeFault('UNREGISTERED_LOG_EXISTS')
            if task.expired(self.clock()):
                raise news.NewsDependency('NEWS_EXPIRED')
            registry[run_id] = fingerprint
            atomic_json(self.store.root / 'runs.json', registry)
            log: dict[str, Json] = {'run_id': run_id, 'phase': 'REGISTERED', 'events': [],
                                   'request': task.request, 'call_status': None}
            self.store.save(run_id, log)
            calls.initialize(task, log)
        calls.run(task, log)
        attempts = json_events(log['calls'])
        if not attempts or attempts[-1]['outcome'] != 'SUCCEEDED':
            self._send(log)
            raise news.NewsDependency('NEWS_EMBEDDING_INCOMPLETE')
        log['events'] = json_events(log['events']) + batches
        log['phase'] = 'FINISHED'
        self.store.save(run_id, log)
        return self._send(log)

    def _llm(self, task: Task, log: dict[str, Json]) -> bool:
        """图编排输入、模型与校验；调用恢复仍以 Worker 持久记录为准。"""
        def prepare(state: LlmState) -> LlmState:
            if 'llm' in log:
                return state
            if task.expired(self.clock()):
                return {**state, 'reason': 'EXPIRED'}
            if self.llm_provider is None:
                raise RuntimeFault('LLM_CONFIG_REQUIRED')
            # 在登记调用前确认凭据，配置错误不能消耗一次调用机会。
            import os
            if not os.environ.get(self.llm_provider.api_key_env):
                raise RuntimeFault('LLM_API_KEY_MISSING')
            text, evidence = llm.prompt(task, self.mapping_path)
            if not any(json_object(q['rule'])['verification'] == 'VERIFIED'
                       for q in json_events(json_object(task.payload['definition'])['questions'])):
                return {**state, 'reason': 'RULE_BLOCKED'}
            return {**state, 'prompt': text, 'evidence': evidence}

        def generate(state: LlmState) -> LlmState:
            if 'llm' in log:
                saved = json_object(log['llm'])
                if saved['response'] is None:
                    saved['response'] = {'status': 'UNKNOWN', 'reason': 'LLM_CALL_UNKNOWN',
                                         'input_tokens': 0, 'output_tokens': 0, 'content': None}
                    log['call_status'] = 'UNKNOWN'
                    self.store.save(str(task.run_id), log)
                return state
            remaining = (task.expires_at - self.clock()).total_seconds()
            if remaining <= 0:
                return {**state, 'reason': 'EXPIRED'}
            if self.llm_provider is None:
                raise RuntimeFault('LLM_CONFIG_REQUIRED')
            stamp = self.clock().isoformat(timespec='milliseconds').replace('+00:00', 'Z')
            log.update(call_status='CALL_STARTED', call_key=str(uuid.uuid5(uuid.NAMESPACE_URL,
                       f'f1ap-e07:{task.run_id}:llm:1')),
                       call_started_at=stamp, llm={'prompt_sha256': digest(state['prompt']), 'evidence': state['evidence'],
                                                  'response': None})
            self.store.save(str(task.run_id), log)
            self.checkpoint('llm_after_intent')
            response = self.llm_provider.generate(state['prompt'], remaining)
            saved = json_object(log['llm'])
            saved['response'] = response
            log['call_status'] = response['status']
            self.store.save(str(task.run_id), log)
            self.checkpoint('llm_after_response')
            return state

        def validate(state: LlmState) -> LlmState:
            reason = state['reason']
            if reason == 'LLM' and task.expired(self.clock()):
                reason = 'EXPIRED'
            if reason != 'LLM':
                return {**state, 'reason': reason, 'results': fixed_results(task, reason)}
            saved = json_object(log['llm'])
            return {**state, 'results': llm.results(task, self.contract,
                json_object(saved['response']), json_events(saved['evidence']))}

        graph = StateGraph(LlmState)
        graph.add_node('prepare', prepare)
        graph.add_node('generate', generate)
        graph.add_node('validate', validate)
        graph.add_edge(START, 'prepare')
        graph.add_conditional_edges('prepare', lambda state: 'generate' if state['reason'] == 'LLM' else 'validate',
                                    {'generate': 'generate', 'validate': 'validate'})
        graph.add_edge('generate', 'validate')
        graph.add_edge('validate', END)
        initial: LlmState = {'prompt': '', 'evidence': [], 'reason': 'LLM', 'results': []}
        output = graph.compile().invoke(initial)
        status = json_string(log['call_status']) if log['call_status'] is not None else None
        return self._finish(task, log, json_string(output['reason']), status, json_events(output['results']))

    def _finish(self, task: Task, log: dict[str, Json], reason: str,
                call_status: str | None, results: list[dict[str, Json]] | None = None) -> bool:
        if results is None:
            results = self._research_results(task, log) if reason == 'RESEARCH' else fixed_results(task, reason)
        events = json_events(log['events']) if log.get('schema_version') == '1.1' else []
        revision = max((json_integer(json_object(event['payload'])['usage_revision'])
                        for event in events if event['event_type'] == 'model.usage.updated.v1'), default=0)
        if call_status is not None:
            revision = 1
            call_key = str(log['call_key'])
            log['call_status'] = call_status
            atomic_json(self.store.root / 'calls' / f'{call_key}.json',
                        {'run_id': str(task.run_id), 'call_key': call_key, 'status': call_status,
                         'started_at': log['call_started_at']})
            payload = usage(task, call_key, call_status, str(log['call_started_at']))
            if task.is_llm:
                response = json_object(json_object(log['llm'])['response'])
                payload.update(model_config_ref='DEEPSEEK_FLASH_V1', fixture_class=task.fixture_class,
                               input_tokens=response['input_tokens'], output_tokens=response['output_tokens'])
            events.append(envelope(task, 'usage:1', 'model.usage.updated.v1', payload))
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

    def reconcile_usage(self, run_id: str, call_key: str, revision: int) -> bool:
        """补价仅追加已登记快照，不重做调用，也不修改 finished。"""
        with self.store.exclusive():
            registry = self.store.registry()
            if run_id not in registry:
                raise RuntimeFault('RUN_NOT_REGISTERED')
            log = self.store.registry_log(run_id)
            task = Task.parse(canonical(log['request']), self.contract)
            if str(task.run_id) != run_id or registry[run_id] != digest(task.payload):
                raise RuntimeFault('RUN_IDENTITY_CONFLICT')
            match task:
                case NewsTask():
                    plan, batches = news.load(task, self.mock_plan_path, self.news_catalog_path, self.contract, self.mock_plan)
                    news.validate(task, log, batches)
                    calls = Calls(self.store, self.contract, plan,
                        self.provider or MockProvider(plan, self.store.root / 'mock-effects.json'),
                        self.clock, self.scheduler, self.checkpoint)
                case Task():
                    validate_log(task, log, self.contract)
                    calls = self.calls()
            calls.validate(task, log)
            matches = [item for item in json_events(log['calls']) if item['call_key'] == call_key]
            if (len(matches) != 1 or matches[0]['outcome'] == 'INTENT'
                    or type(revision) is not int or not 1 <= revision <= 2147483647):
                raise RuntimeFault('MOCK_REVISION_CALL')
            payload = calls.plan.revision(run_id, matches[0], revision)
            prior = [event for event in json_events(matches[0]['revisions'])
                     if json_object(event['payload'])['usage_revision'] == revision]
            if prior and prior[0]['payload'] != payload:
                raise RuntimeFault('MOCK_REVISION_CONFLICT')
            calls.append(task, log, matches[0], revision)
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
        self.checkpoint('after_pending')
        for event in events:
            if event['event_id'] not in accepted and self.sender.send(event):
                self.checkpoint('after_confirm')
                accepted.append(event['event_id'])
                log['accepted'] = accepted
                self.store.save(str(log['run_id']), log)
                (self.store.root / 'pending' / f"{event['event_id']}.json").unlink()
            elif event['event_id'] in accepted:
                (self.store.root / 'pending' / f"{event['event_id']}.json").unlink(missing_ok=True)
        return len(accepted) == len(events)
