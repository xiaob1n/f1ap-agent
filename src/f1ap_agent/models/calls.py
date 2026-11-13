"""生成和 Embedding 共享的预写调用日志。"""
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
import uuid

from f1ap_agent.contracts.artifact import Contract, check_format
from f1ap_agent.contracts.verifier import canonical, digest, evaluate
from f1ap_agent.models.mock import CallInput, CallResult, MockPlan, Pricing, Provider, call_key, result_from
from f1ap_agent.runtime import Json, RuntimeFault, Store, json_events, json_integer, json_object, json_string
from f1ap_agent.worker.models import Task as PredictTask, NewsTask

type Task = PredictTask | NewsTask


def instant(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace('Z', '+00:00'))


def stamp(now: datetime) -> str:
    return now.isoformat(timespec='milliseconds').replace('+00:00', 'Z')


@dataclass(frozen=True, slots=True)
class Calls:
    store: Store
    contract: Contract
    plan: MockPlan
    provider: Provider
    clock: Callable[[], datetime]
    schedule: Callable[[float], None]
    checkpoint: Callable[[str], None]

    def initialize(self, task: Task, log: dict[str, Json]) -> None:
        now = self.clock()
        lease = min(task.expires_at, instant(self.plan.expires_at),
                    instant(self.plan.initial_lease_until), now + timedelta(seconds=30))
        deadline = task.deadline
        if deadline is not None:
            lease = min(lease, deadline)
        log.update(schema_version='1.1', mock_plan_sha256=self.plan.sha256,
                   mock_script_sha256=self.plan.script_sha256, lease_until=stamp(lease), calls=[])
        self.store.save(str(task.run_id), log)

    def validate(self, task: Task, log: dict[str, Json]) -> None:
        if (log.get('mock_plan_sha256') != self.plan.sha256
                or log.get('mock_script_sha256') != self.plan.script_sha256):
            raise RuntimeFault('LOG_PLAN_HASH')
        attempts = json_events(log['calls'])
        if log.get('schema_version') != '1.1' or log.get('call_status') is not None:
            raise RuntimeFault('LOG_CALL_VERSION')
        check_format('f1ap-utc-ms', json_string(log['lease_until']), '/lease_until')
        if len(attempts) > min(task.call_limit, self.plan.call_limit):
            raise RuntimeFault('LOG_ATTEMPT_LIMIT')
        keys: set[str] = set()
        for attempt in attempts:
            logical = json_string(attempt['logical_key'])
            planned = self.plan.call(logical)
            number = json_integer(attempt['attempt_no'])
            key = call_key(str(task.run_id), logical, number)
            if (key in keys or attempt['call_key'] != key or attempt['request_sha256'] != planned.request_sha256
                    or attempt['model_config_ref'] != planned.model_config_ref
                    or attempt['call_type'] != planned.call_type or not 1 <= number <= len(planned.attempts)):
                raise RuntimeFault('LOG_CALL_IDENTITY')
            keys.add(key)
            check_format('f1ap-utc-ms', json_string(attempt['call_started_at']), '/call_started_at')
            if attempt['call_started_at'] != planned.attempt_started_at[number - 1]:
                raise RuntimeFault('LOG_CALL_IDENTITY')
            prior = [item for item in attempts if item['logical_key'] == logical and item['attempt_no'] == number - 1]
            if number > 1 and (len(prior) != 1 or json_object(prior[0]['result'])['retryable'] is not True):
                raise RuntimeFault('LOG_RETRY_CLASSIFICATION')
            state = attempt['outcome']
            if state not in ('INTENT', 'SUCCEEDED', 'FAILED', 'UNKNOWN'):
                raise RuntimeFault('LOG_CALL_OUTCOME')
            if state == 'INTENT':
                if attempt['result'] is not None or json_events(attempt['revisions']):
                    raise RuntimeFault('LOG_INTENT_RESULT')
            else:
                result = result_from(json_object(attempt['result']))
                if result.outcome != state:
                    raise RuntimeFault('LOG_RESULT_STATE')
                if state != 'UNKNOWN' and result != planned.attempts[number - 1]:
                    raise RuntimeFault('LOG_RESULT_SCRIPT')
            revisions = json_events(attempt['revisions'])
            numbers: set[int] = set()
            for event in revisions:
                payload = json_object(event['payload'])
                revision = json_integer(payload['usage_revision'])
                if revision in numbers or event != self.event(task, attempt, revision, payload):
                    raise RuntimeFault('LOG_REVISION_IDENTITY')
                if revision == 1:
                    result_value = json_object(attempt['result'])
                    if any(payload[key] != result_value[source] for key, source in (
                            ('call_status', 'outcome'), ('input_tokens', 'input_tokens'),
                            ('output_tokens', 'output_tokens'), ('pricing', 'pricing'))):
                        raise RuntimeFault('LOG_REVISION_RESULT')
                else:
                    expected = self.plan.revision(str(task.run_id), attempt, revision)
                    if payload != expected:
                        raise RuntimeFault('LOG_REVISION_SCRIPT')
                numbers.add(revision)
                if event not in json_events(log['events']):
                    raise RuntimeFault('LOG_REVISION_MISSING')
                context = {'request': task.payload, 'received_results': [], 'usage_history': []}
                if not evaluate(canonical(event), contract=self.contract, context=context)['accepted']:
                    raise RuntimeFault('LOG_USAGE_CONTRACT')
        recorded = [event for attempt in attempts for event in json_events(attempt['revisions'])]
        if [event for event in json_events(log['events']) if event['event_type'] == 'model.usage.updated.v1'] != recorded:
            # 后续修订可追加在 finished 后，比较集合身份而不要求调用分组排序。
            emitted = [canonical(event) for event in json_events(log['events']) if event['event_type'] == 'model.usage.updated.v1']
            if sorted(emitted) != sorted(canonical(event) for event in recorded):
                raise RuntimeFault('LOG_USAGE_SET')

    def allowed(self, task: Task, log: dict[str, Json], delay: float = 0) -> bool:
        now = self.clock() + timedelta(seconds=delay)
        return (not task.expired(now) and now < instant(self.plan.expires_at)
                and now < instant(json_string(log['lease_until']))
                and len(json_events(log['calls'])) < min(task.call_limit, self.plan.call_limit))

    def run(self, task: Task, log: dict[str, Json]) -> None:
        attempts = json_events(log['calls'])
        for planned in self.plan.calls:
            history = [item for item in attempts if item['logical_key'] == planned.logical_key]
            for item in history:
                if item['outcome'] == 'INTENT':
                    unknown = CallResult('UNKNOWN', 'CRASH_UNCERTAIN', False, None, 0, 0,
                                         Pricing('UNKNOWN', None, None, None, None, None))
                    item.update(outcome='UNKNOWN', result=asdict(unknown))
                    self.store.save(str(task.run_id), log)
                if not json_events(item['revisions']):
                    self.append(task, log, item, 1)
            while len(history) < min(len(planned.attempts), self.plan.max_provider_attempts):
                if history:
                    result = json_object(history[-1]['result'])
                    if result['outcome'] != 'FAILED' or result['retryable'] is not True:
                        break
                    ready = instant(json_string(history[-1]['retry_at']))
                    delay = max(0.0, (ready - self.clock()).total_seconds())
                    if not self.allowed(task, log, delay):
                        break
                    self.schedule(delay)
                if not self.allowed(task, log):
                    break
                number = len(history) + 1
                # 计划时间固定调用身份；时钟只决定准入与可用超时。
                started = planned.attempt_started_at[number - 1]
                admitted = self.clock()
                call = CallInput(str(task.run_id), planned.logical_key, number,
                    call_key(str(task.run_id), planned.logical_key, number), planned.call_type,
                    planned.model_config_ref, planned.request, planned.request_sha256, started,
                    min(self.plan.provider_timeout_ms, max(1, int((instant(json_string(log['lease_until'])) - admitted).total_seconds() * 1000))))
                item: dict[str, Json] = {'logical_key': planned.logical_key, 'attempt_no': number,
                    'call_key': call.call_key, 'call_type': call.call_type,
                    'model_config_ref': call.model_config_ref, 'request_sha256': call.request_sha256,
                    'call_started_at': started, 'outcome': 'INTENT', 'result': None, 'revisions': []}
                self.checkpoint('before_intent')
                attempts.append(item)
                log['calls'] = attempts
                self.store.save(str(task.run_id), log)
                self.checkpoint('after_intent')
                current = self.clock()
                if (task.expired(current) or current >= instant(self.plan.expires_at)
                        or current >= instant(json_string(log['lease_until']))):
                    # 本进程尚未执行 effect，撤销意图；崩溃恢复仍按 UNKNOWN 处理。
                    attempts.remove(item)
                    log['calls'] = attempts
                    self.store.save(str(task.run_id), log)
                    break
                call = replace(call, timeout_ms=min(call.timeout_ms, max(1, int(
                    (instant(json_string(log['lease_until'])) - current).total_seconds() * 1000))))
                result = self.provider.execute(call)
                self.checkpoint('after_effect')
                item.update(outcome=result.outcome, result=asdict(result))
                if result.retryable:
                    item['retry_at'] = stamp(self.clock() + timedelta(milliseconds=self.plan.retry_delay_ms))
                self.store.save(str(task.run_id), log)
                self.checkpoint('after_result')
                self.append(task, log, item, 1)
                history.append(item)

    def event(self, task: Task, attempt: dict[str, Json], revision: int,
              payload: dict[str, Json]) -> dict[str, Json]:
        identity = (str(task.run_id), attempt['call_key'], attempt['call_started_at'], attempt['model_config_ref'])
        actual = tuple(payload[k] for k in ('run_id', 'call_key', 'call_started_at', 'model_config_ref'))
        if identity != actual or payload['usage_revision'] != revision:
            raise RuntimeFault('LOG_REVISION_IDENTITY')
        return {'schema_version': '1.0', 'event_id': str(uuid.uuid5(uuid.NAMESPACE_URL,
                    f"f1ap-e07:{task.run_id}:{attempt['call_key']}:usage:{revision}")),
                'event_type': 'model.usage.updated.v1', 'occurred_at': attempt['call_started_at'],
                'trace_id': task.request['trace_id'], 'payload_sha256': digest(payload), 'payload': payload}

    def append(self, task: Task, log: dict[str, Json], attempt: dict[str, Json], revision: int) -> None:
        revisions = json_events(attempt['revisions'])
        if any(json_object(event['payload'])['usage_revision'] == revision for event in revisions):
            return
        if revision == 1:
            result = json_object(attempt['result'])
            payload: dict[str, Json] = {'run_id': str(task.run_id), 'call_key': attempt['call_key'],
                'usage_revision': 1, 'call_started_at': attempt['call_started_at'],
                'call_status': result['outcome'], 'model_config_ref': attempt['model_config_ref'],
                'input_tokens': result['input_tokens'], 'output_tokens': result['output_tokens'],
                'pricing': result['pricing'], 'fixture_class': 'TEST_FIXTURE'}
        else:
            payload = self.plan.revision(str(task.run_id), attempt, revision)
        event = self.event(task, attempt, revision, payload)
        context = {'request': task.payload, 'received_results': [], 'usage_history': []}
        if not evaluate(canonical(event), contract=self.contract, context=context)['accepted']:
            raise RuntimeFault('LOG_USAGE_CONTRACT')
        revisions.append(event)
        attempt['revisions'] = revisions
        events = json_events(log['events'])
        events.append(event)
        log['events'] = events
        self.store.save(str(task.run_id), log)
