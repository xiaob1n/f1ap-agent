"""Typed B1 boundary over the existing frozen contract validator."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import NewType

from f1ap_agent.contracts.artifact import Contract, check_format
from f1ap_agent.contracts.verifier import canonical, evaluate, parse
from f1ap_agent.runtime import Json, RuntimeFault, json_object, json_string, json_integer, json_array

RunId = NewType('RunId', str)


def load_contract() -> Contract:
    root = Path(__file__).resolve().parents[3]
    contract = Contract(root.parent / 'f1ap-api/contracts/v1', root / 'contracts.lock.json')
    if contract.release != '1.0.2':
        raise RuntimeFault('RELEASE_NOT_1_0_2')
    return contract


@dataclass(frozen=True, slots=True)
class Task:
    request: dict[str, Json]
    run_id: RunId
    task_key: str
    attempt_no: int
    call_limit: int
    now: datetime
    expires_at: datetime
    question_ids: tuple[str, ...]
    points: tuple[Decimal, ...]

    @property
    def payload(self) -> dict[str, Json]:
        from f1ap_agent.runtime import json_object
        return json_object(self.request['payload'])

    @property
    def is_research(self) -> bool:
        """研究任务不允许调用模型或外部服务。"""
        return self.payload['purpose'] == 'RESEARCH' and self.payload['fixture_class'] == 'CONTROLLED_INPUT'

    @property
    def fixture_class(self) -> str:
        """返回契约中的受控 fixture 身份。"""
        return json_string(self.payload['fixture_class'])

    @property
    def mapping_sha256(self) -> str:
        """从算法版本读取 mapping 原始字节摘要。"""
        algorithm = json_object(self.payload['profile'])['algorithm_version']
        prefix = 'PODIUM_PACE_BOOTSTRAP_V1@'
        value = json_string(algorithm)
        if not value.startswith(prefix) or len(value) != len(prefix) + 64:
            raise RuntimeFault('RESEARCH_MAPPING_VERSION')
        check_format('f1ap-sha256', value.removeprefix(prefix), '/payload/profile/algorithm_version')
        return value.removeprefix(prefix)

    @classmethod
    def parse(cls, raw: bytes, contract: Contract, now: str | None = None) -> 'Task':
        """Validate exactly once with E01, then convert domain fields without coercion."""
        verdict = evaluate(raw, contract=contract)
        if not verdict['accepted']:
            raise RuntimeFault('INVALID_PAYLOAD:' + str(verdict['error']))
        request = json_object(parse(raw))
        payload = json_object(request['payload'])
        if json_string(request['event_type']) != 'prediction.requested.v1':
            raise RuntimeFault('B1_REQUIRES_PREDICTION_REQUEST')
        fixture_class = json_string(payload['fixture_class'])
        purpose = json_string(payload['purpose'])
        if fixture_class == 'CONTROLLED_INPUT' and purpose != 'RESEARCH':
            raise RuntimeFault('B1_REQUIRES_RESEARCH_CONTROLLED_INPUT')
        if fixture_class not in ('TEST_FIXTURE', 'CONTROLLED_INPUT'):
            raise RuntimeFault('B1_UNKNOWN_FIXTURE_CLASS')
        if fixture_class == 'CONTROLLED_INPUT':
            profile = json_object(payload['profile'])
            algorithm = json_string(profile['algorithm_version'])
            prefix = 'PODIUM_PACE_BOOTSTRAP_V1@'
            if (profile['tools'] != ['OPENF1_READ'] or profile['model_config_ref'] != 'NO_MODEL'
                or profile['price_version'] != 'NOT_APPLICABLE'
                or profile['strategies'] != [{'strategy': 'EXPECTED_POINTS', 'version': 'EXPECTED_POINTS_V1'}]
                or not algorithm.startswith(prefix)):
                raise RuntimeFault('RESEARCH_PROFILE')
            check_format('f1ap-sha256', algorithm.removeprefix(prefix), '/payload/profile/algorithm_version')
        stamp = now or json_string(payload['data_cutoff_at'])
        check_format('f1ap-utc-ms', stamp, '/now')
        return cls(request, RunId(json_string(payload['run_id'])), json_string(payload['task_key']),
                   json_integer(payload['attempt_no']), json_integer(payload['call_limit']),
                   datetime.fromisoformat(stamp.replace('Z', '+00:00')),
                   datetime.fromisoformat(json_string(payload['expires_at']).replace('Z', '+00:00')),
                   tuple(json_string(item) for item in json_array(payload['question_ids'])),
                   tuple(Decimal(json_string(json_object(option)['points']))
                   for question in json_array(json_object(payload['definition'])['questions'])
                   for option in json_array(json_object(question)['options'])))

    def raw(self) -> bytes:
        return canonical(self.request)

    def stamp(self) -> str:
        return self.now.isoformat(timespec='milliseconds').replace('+00:00', 'Z')

    def expired(self) -> bool:
        definition = json_object(self.payload['definition'])
        deadline = definition['deadline_at']
        live_expired = (self.payload['purpose'] == 'LIVE' and deadline is not None
                        and self.stamp() >= json_string(deadline))
        return self.now >= self.expires_at or live_expired
