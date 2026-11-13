"""只从已登记脚本读取结果的本地提供方。"""
import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from f1ap_agent.contracts.artifact import Contract, check_format
from f1ap_agent.contracts.verifier import canonical, digest, evaluate, parse
from f1ap_agent.runtime import (
    Json, RuntimeFault, atomic_json, json_array, json_events, json_integer,
    json_object, json_string, read_json,
)


@dataclass(frozen=True, slots=True)
class MockRequest:
    fixture_class: str
    operation: str
    input: str


@dataclass(frozen=True, slots=True)
class Pricing:
    status: str
    unit_price: str | None
    original_cost: str | None
    charged_cny: str | None
    currency: str | None
    price_version: str | None


@dataclass(frozen=True, slots=True)
class CallResult:
    outcome: Literal['SUCCEEDED', 'FAILED', 'UNKNOWN']
    error_code: str | None
    retryable: bool
    provider_request_id: str | None
    input_tokens: int
    output_tokens: int
    pricing: Pricing


@dataclass(frozen=True, slots=True)
class CallInput:
    run_id: str
    logical_key: str
    attempt_no: int
    call_key: str
    call_type: str
    model_config_ref: str
    request: MockRequest
    request_sha256: str
    call_started_at: str
    timeout_ms: int


class Provider(Protocol):
    def execute(self, call: CallInput) -> CallResult: ...


def call_key(run_id: str, logical_key: str, attempt_no: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'f1ap-e07:{run_id}:{logical_key}:{attempt_no}'))


def fields(value: Json, names: str) -> dict[str, Json]:
    result = json_object(value)
    if set(result) != set(names.split()):
        raise RuntimeFault('MOCK_PLAN_FIELDS')
    return result


def nullable(value: Json) -> str | None:
    return None if value is None else json_string(value)


def result_from(value: dict[str, Json]) -> CallResult:
    outcome = value['outcome']
    if outcome not in ('SUCCEEDED', 'FAILED', 'UNKNOWN') or type(value['retryable']) is not bool:
        raise RuntimeFault('MOCK_OUTCOME')
    if value['retryable'] and (outcome != 'FAILED' or not value['error_code']):
        raise RuntimeFault('MOCK_RETRY_CLASSIFICATION')
    pricing = fields(value['pricing'], 'status unit_price original_cost charged_cny currency price_version')
    if pricing['status'] not in ('KNOWN', 'UNKNOWN'):
        raise RuntimeFault('MOCK_PRICING')
    for name in ('unit_price', 'original_cost', 'charged_cny'):
        check_format('f1ap-nonnegative', nullable(pricing[name]), '/' + name)
    known = pricing['status'] == 'KNOWN'
    amounts = [pricing[key] for key in ('unit_price', 'original_cost', 'charged_cny')]
    if (known and (None in amounts or pricing['currency'] is None or pricing['price_version'] is None)
            or not known and any(item is not None for item in amounts)):
        raise RuntimeFault('MOCK_PRICING')
    tokens = [json_integer(value[key]) for key in ('input_tokens', 'output_tokens')]
    if any(not 0 <= item <= 2147483647 for item in tokens):
        raise RuntimeFault('MOCK_TOKENS')
    # 分支将外部字符串收窄为明确的终态类型。
    status: Literal['SUCCEEDED', 'FAILED', 'UNKNOWN']
    match outcome:
        case 'SUCCEEDED': status = 'SUCCEEDED'
        case 'FAILED': status = 'FAILED'
        case _: status = 'UNKNOWN'
    return CallResult(status, nullable(value['error_code']), value['retryable'] is True,
                      nullable(value['provider_request_id']), tokens[0], tokens[1],
                      Pricing(json_string(pricing['status']), *(nullable(pricing[k]) for k in
                              ('unit_price', 'original_cost', 'charged_cny', 'currency', 'price_version'))))


@dataclass(frozen=True, slots=True)
class PlannedCall:
    logical_key: str
    call_type: str
    model_config_ref: str
    request: MockRequest
    request_sha256: str
    attempts: tuple[CallResult, ...]
    attempt_started_at: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UsageRevision:
    identity: str
    revision: int
    request_sha256: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class MockPlan:
    sha256: str
    run_id: str  # 仅保留夹具标签；实际调用身份来自 CallInput.run_id。
    call_limit: int
    expires_at: str
    initial_lease_until: str
    provider_timeout_ms: int
    retry_delay_ms: int
    max_provider_attempts: int
    calls: tuple[PlannedCall, ...]
    revisions: tuple[UsageRevision, ...] = ()
    script_sha256: str | None = None
    news_catalog_sha256: str | None = None

    @classmethod
    def load(cls, path: Path, expected_sha256: str, contract: Contract) -> 'MockPlan':
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise RuntimeFault('MOCK_PLAN_HASH')
        plan = fields(parse(raw), 'schema_version fixture_class run_id profile call_limit '
            'data_cutoff_at expires_at initial_lease_until provider_timeout_ms retry_delay_ms '
            'max_provider_attempts news_catalog_sha256 calls')
        if plan['schema_version'] != '1.0' or plan['fixture_class'] != 'TEST_FIXTURE':
            raise RuntimeFault('MOCK_PLAN_VERSION')
        run_id = json_string(plan['run_id'])
        check_format('f1ap-id', run_id, '/run_id')
        profile = fields(plan['profile'], 'source_allowlist embedding_config_ref collection_version')
        sources = [json_string(item) for item in json_array(profile['source_allowlist'])]
        if not sources or len(set(sources)) != len(sources):
            raise RuntimeFault('MOCK_PROFILE')
        for key in ('embedding_config_ref', 'collection_version'):
            if not json_string(profile[key]):
                raise RuntimeFault('MOCK_PROFILE')
        for name in ('data_cutoff_at', 'expires_at', 'initial_lease_until'):
            check_format('f1ap-utc-ms', json_string(plan[name]), '/' + name)
        check_format('f1ap-sha256', json_string(plan['news_catalog_sha256']), '/news_catalog_sha256')
        limits = [json_integer(plan[k]) for k in ('call_limit', 'provider_timeout_ms', 'retry_delay_ms', 'max_provider_attempts')]
        if not 0 <= limits[0] <= 2147483647 or not 1 <= limits[1] <= 10000 or limits[2] != 1000 or not 1 <= limits[3] <= 2:
            raise RuntimeFault('MOCK_PLAN_LIMITS')
        calls: list[PlannedCall] = []
        for value in json_events(plan['calls']):
            call = fields(value, 'fixture_class logical_key call_type model_config_ref provider_code model_name request request_sha256 attempts')
            if call['fixture_class'] != 'TEST_FIXTURE' or call['provider_code'] != 'MOCK' or call['call_type'] not in ('GENERATION', 'EMBEDDING', 'NEWS_API'):
                raise RuntimeFault('MOCK_PROVIDER_ONLY')
            request = fields(call['request'], 'fixture_class operation input')
            if request['fixture_class'] != 'TEST_FIXTURE' or request['operation'] != call['call_type'] or digest(request) != call['request_sha256']:
                raise RuntimeFault('MOCK_REQUEST_HASH')
            attempts: list[CallResult] = []
            started_at: list[str] = []
            for index, value in enumerate(json_events(call['attempts']), 1):
                attempt = fields(value, 'attempt_no call_started_at outcome error_code retryable provider_request_id input_tokens output_tokens pricing rate_basis')
                if json_integer(attempt['attempt_no']) != index:
                    raise RuntimeFault('MOCK_ATTEMPT_SEQUENCE')
                check_format('f1ap-utc-ms', json_string(attempt['call_started_at']), '/call_started_at')
                started_at.append(json_string(attempt['call_started_at']))
                basis = fields(attempt['rate_basis'], 'quantity unit exchange_rate price_basis_ref')
                for name in ('quantity', 'exchange_rate'):
                    check_format('f1ap-nonnegative', json_string(basis[name]), '/' + name)
                if not json_string(basis['unit']):
                    raise RuntimeFault('MOCK_RATE_BASIS')
                _ = nullable(basis['price_basis_ref'])
                attempts.append(result_from(attempt))
            if not 1 <= len(attempts) <= limits[3]:
                raise RuntimeFault('MOCK_ATTEMPT_LIMIT')
            ref = json_string(call['model_config_ref']) + '@' + expected_sha256
            if len(ref) > 256 or not ref.startswith('E07_MOCK_'):
                raise RuntimeFault('MOCK_CONFIG_REF')
            calls.append(PlannedCall(json_string(call['logical_key']), json_string(call['call_type']), ref,
                MockRequest(*(json_string(request[k]) for k in ('fixture_class', 'operation', 'input'))),
                json_string(call['request_sha256']), tuple(attempts), tuple(started_at)))
        if not calls or any(not call.logical_key for call in calls) or len({call.logical_key for call in calls}) != len(calls):
            raise RuntimeFault('MOCK_LOGICAL_KEY_DUPLICATE')
        return cls(expected_sha256, run_id, limits[0], json_string(plan['expires_at']),
                   json_string(plan['initial_lease_until']), limits[1], limits[2], limits[3], tuple(calls),
                   news_catalog_sha256=json_string(plan['news_catalog_sha256']))

    def call(self, logical_key: str) -> PlannedCall:
        for call in self.calls:
            if call.logical_key == logical_key:
                return call
        raise RuntimeFault('MOCK_KEY_NOT_REGISTERED')

    def with_revisions(self, path: Path, scenario: str, contract: Contract) -> 'MockPlan':
        from dataclasses import replace
        raw = path.read_bytes()
        script = json_object(parse(raw))
        registration = json_object(script['registration'])
        if registration['mock_plan_sha256'] != self.sha256:
            raise RuntimeFault('MOCK_SCRIPT_PLAN_HASH')
        if registration['news_catalog_sha256'] != self.news_catalog_sha256:
            raise RuntimeFault('MOCK_SCRIPT_CATALOG_HASH')
        matches = [case for case in json_events(script['usage_cases']) if case['id'] == scenario]
        if len(matches) != 1:
            raise RuntimeFault('MOCK_SCENARIO')
        events = json_events(matches[0]['events'])
        revisions: dict[tuple[str, int], UsageRevision] = {}
        for event in events:
            payload = json_object(event['payload'])
            context = dict(json_object(script['context']))
            context['request'] = dict(json_object(context['request']), run_id=payload['run_id'])
            if (event['event_type'] != 'model.usage.updated.v1'
                    or not evaluate(canonical(event), contract=contract, context=context)['accepted']):
                raise RuntimeFault('MOCK_REVISION_CONTRACT')
            registered = [(call, i) for call in self.calls for i in range(1, len(call.attempts) + 1)
                          if call_key(json_string(payload['run_id']), call.logical_key, i) == payload['call_key']]
            if len(registered) > 1:
                raise RuntimeFault('MOCK_REVISION_AMBIGUOUS')
            if not registered:
                raise RuntimeFault('MOCK_KEY_NOT_REGISTERED')
            call, number = registered[0]
            if (payload['model_config_ref'] != call.model_config_ref
                    or payload['call_started_at'] != call.attempt_started_at[number - 1]):
                raise RuntimeFault('MOCK_REVISION_IDENTITY')
            identity = digest([self.sha256, call.logical_key, number])
            revision = json_integer(payload['usage_revision'])
            pair = (identity, revision)
            data = canonical({key: value for key, value in payload.items() if key not in ('run_id', 'call_key')})
            entry = UsageRevision(identity, revision, call.request_sha256, data)
            if pair in revisions and revisions[pair] != entry:
                raise RuntimeFault('MOCK_REVISION_CONFLICT')
            revisions[pair] = entry
        return replace(self, revisions=tuple(revisions.values()), script_sha256=hashlib.sha256(raw + scenario.encode()).hexdigest())

    def revision(self, run_id: str, attempt: dict[str, Json], revision: int) -> dict[str, Json]:
        """按计划及逻辑尝试查表，再渲染实际运行身份，绝不改写登记时间。"""
        logical = json_string(attempt['logical_key'])
        number = json_integer(attempt['attempt_no'])
        identity = digest([self.sha256, logical, number])
        matches = [entry for entry in self.revisions if entry.identity == identity and entry.revision == revision]
        if len(matches) > 1:
            raise RuntimeFault('MOCK_REVISION_AMBIGUOUS')
        if not matches:
            raise RuntimeFault('MOCK_REVISION_NOT_REGISTERED')
        entry = matches[0]
        payload = json_object(parse(entry.payload))
        planned = self.call(logical)
        if (entry.request_sha256 != planned.request_sha256 or entry.request_sha256 != attempt['request_sha256']
                or payload['model_config_ref'] != planned.model_config_ref
                or payload['call_started_at'] != planned.attempt_started_at[number - 1]):
            raise RuntimeFault('MOCK_REVISION_IDENTITY')
        return dict(payload, run_id=run_id, call_key=call_key(run_id, logical, number))


@dataclass(frozen=True, slots=True)
class MockProvider:
    plan: MockPlan
    effects_path: Path

    def execute(self, call: CallInput) -> CallResult:
        planned = self.plan.call(call.logical_key)
        if (call.call_key != call_key(call.run_id, call.logical_key, call.attempt_no)
                or call.request != planned.request or call.request_sha256 != planned.request_sha256
                or call.model_config_ref != planned.model_config_ref or call.call_type != planned.call_type
                or not 1 <= call.attempt_no <= len(planned.attempts) or not 1 <= call.timeout_ms <= self.plan.provider_timeout_ms):
            raise RuntimeFault('MOCK_CALL_IDENTITY')
        effects = json_object(read_json(self.effects_path)) if self.effects_path.exists() else {}
        effects[call.call_key] = json_integer(effects.get(call.call_key, 0)) + 1
        atomic_json(self.effects_path, effects)
        return planned.attempts[call.attempt_no - 1]
