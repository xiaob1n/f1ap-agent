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

    @classmethod
    def parse(cls, raw: bytes, contract: Contract, now: str | None = None) -> 'Task':
        """Validate exactly once with E01, then convert domain fields without coercion."""
        verdict = evaluate(raw, contract=contract)
        if not verdict['accepted']:
            raise RuntimeFault('INVALID_PAYLOAD:' + str(verdict['error']))
        request = json_object(parse(raw))
        payload = json_object(request['payload'])
        if json_string(request['event_type']) != 'prediction.requested.v1' or json_string(payload['fixture_class']) != 'TEST_FIXTURE':
            raise RuntimeFault('B1_REQUIRES_PREDICTION_TEST_FIXTURE')
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
