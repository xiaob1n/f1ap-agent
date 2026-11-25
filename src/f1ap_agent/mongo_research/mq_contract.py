"""独立研究消息边界，不扩展冻结预测契约。"""
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import NAMESPACE_URL, uuid5

from f1ap_agent.contracts.artifact import Contract, check_format
from f1ap_agent.contracts.verifier import digest, parse
from f1ap_agent.contracts.answers import VERSION, definition as answer_definition
from f1ap_agent.runtime import (
    Json, RuntimeFault, json_array, json_events, json_integer, json_object, json_string,
)

REQUESTED: Final = 'prediction.answers.requested.v2'
COMPLETED: Final = 'prediction.answers.completed.v2'


def stamp(value: Json) -> datetime:
    text = json_string(value)
    check_format('f1ap-utc-ms', text, '/time')
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


@dataclass(frozen=True, slots=True)
class ResearchTask:
    request: dict[str, Json]
    payload: dict[str, Json]
    definition: dict[str, Json]
    run_id: str
    request_id: str
    expires_at: datetime

    @classmethod
    def parse(cls, raw: bytes, contract: Contract) -> 'ResearchTask':
        event = json_object(parse(raw))
        if set(event) != {'schema_version', 'event_id', 'event_type', 'occurred_at',
                          'trace_id', 'payload_sha256', 'payload'}:
            raise RuntimeFault('UNSCORED_ENVELOPE_FIELDS')
        if event['schema_version'] != VERSION or event['event_type'] != REQUESTED:
            raise RuntimeFault('UNSCORED_EVENT_TYPE')
        for key in ('event_id', 'trace_id'):
            check_format('f1ap-uuid', json_string(event[key]), '/' + key)
        occurred = stamp(event['occurred_at'])
        payload = json_object(event['payload'])
        if set(payload) - {'target_event'} != {'request_id', 'run_id', 'round_id', 'definition',
                            'definition_sha256', 'reference_session_key',
                            'data_cutoff_at', 'expires_at', 'config'}:
            raise RuntimeFault('UNSCORED_PAYLOAD_FIELDS')
        target = payload.get('target_event')
        if target is not None:
            target = json_object(target)
            if (set(target) != {'season_year', 'event_name', 'round_id'}
                    or not 1950 <= json_integer(target['season_year']) <= 2100
                    or not json_string(target['event_name']).strip()
                    or target['round_id'] != payload['round_id']):
                raise RuntimeFault('UNSCORED_TARGET_EVENT')
        request_id = json_string(payload['request_id'])
        check_format('f1ap-uuid', request_id, '/request_id')
        for key in ('run_id', 'round_id'):
            check_format('f1ap-id', json_string(payload[key]), '/' + key)
        definition = answer_definition(payload['definition'])
        questions = json_events(definition['questions'])
        if (definition['round_id'] != payload['round_id'] or len(questions) != 10
                or len({json_string(q['source_question_id']) for q in questions}) != 10
                or any(q['value_kind'] != 'OPTION' for q in questions)):
            raise RuntimeFault('UNSCORED_DEFINITION_IDENTITY')
        if digest(definition) != payload['definition_sha256'] or digest(payload) != event['payload_sha256']:
            raise RuntimeFault('UNSCORED_HASH')
        if json_integer(payload['reference_session_key']) <= 0 or payload['config'] != {'model': 'deepseek-flash'}:
            raise RuntimeFault('UNSCORED_CONFIG')
        cutoff, expires = stamp(payload['data_cutoff_at']), stamp(payload['expires_at'])
        if cutoff > occurred or expires <= occurred or event['trace_id'] != request_id:
            raise RuntimeFault('UNSCORED_TIME_OR_TRACE')
        return cls(event, payload, definition, json_string(payload['run_id']), request_id, expires)

    def prediction(self, value: Json) -> dict[str, Json]:
        """校验生成文件和恢复事件；不足数据允许没有可引用证据。"""
        prediction = json_object(value)
        if set(prediction) != {'schema_version', 'request_id', 'definition_sha256',
                              'reference_session_key', 'data_cutoff_at', 'generated_at',
                              'model', 'results', 'limitations'}:
            raise RuntimeFault('UNSCORED_PREDICTION_FIELDS')
        expected = {'schema_version': VERSION, 'request_id': self.request_id,
                    'model': 'deepseek-flash', **{key: self.payload[key] for key in
                    ('definition_sha256', 'reference_session_key', 'data_cutoff_at')}}
        if any(prediction[key] != value for key, value in expected.items()):
            raise RuntimeFault('UNSCORED_PREDICTION_IDENTITY')
        if stamp(prediction['generated_at']) < stamp(self.request['occurred_at']):
            raise RuntimeFault('UNSCORED_GENERATED_TIME')
        limitations = json_array(prediction['limitations'])
        if len(limitations) > 20:
            raise RuntimeFault('UNSCORED_LIMITATIONS')
        for limitation in limitations:
            if not json_string(limitation).strip() or len(json_string(limitation)) > 500:
                raise RuntimeFault('UNSCORED_LIMITATIONS')
        results = json_events(prediction['results'])
        if len(results) != 10:
            raise RuntimeFault('UNSCORED_RESULT_COUNT')
        for question, item in zip(json_events(self.definition['questions']), results, strict=True):
            if (set(item) != {'source_question_id', 'status', 'picks', 'explanation', 'evidence_ids'}
                    or item['source_question_id'] != question['source_question_id']):
                raise RuntimeFault('UNSCORED_RESULT_IDENTITY')
            explanation = json_string(item['explanation'])
            evidence = [json_integer(value) for value in json_array(item['evidence_ids'])]
            picks = [json_string(value) for value in json_array(item['picks'])]
            if (not explanation.strip() or len(explanation) > 2000 or len(evidence) > 32
                    or len(set(evidence)) != len(evidence)
                    or any(value <= 0 for value in evidence)):
                raise RuntimeFault('UNSCORED_RESULT_EVIDENCE')
            if item['status'] == 'PREDICTED':
                rule = json_object(question['rule'])
                allowed = {json_string(option['option_key']) for option in json_events(question['options'])}
                if (not evidence or len(set(picks)) != len(picks) or not set(picks) <= allowed
                        or not json_integer(rule['min_picks']) <= len(picks) <= json_integer(rule['max_picks'])):
                    raise RuntimeFault('UNSCORED_RESULT_PICKS')
            elif item['status'] != 'DATA_INSUFFICIENT' or picks:
                raise RuntimeFault('UNSCORED_RESULT_STATUS')
        return prediction

    def failure(self, reason: str, now: str) -> dict[str, Json]:
        return {'schema_version': VERSION, 'request_id': self.request_id,
                **{key: self.payload[key] for key in
                   ('definition_sha256', 'reference_session_key', 'data_cutoff_at')},
                'generated_at': now, 'model': 'deepseek-flash',
                'results': [{'source_question_id': q['source_question_id'],
                    'status': 'DATA_INSUFFICIENT', 'picks': [], 'explanation': reason,
                    'evidence_ids': []} for q in json_events(self.definition['questions'])],
                'limitations': [reason]}

    def completed(self, prediction: Json) -> dict[str, Json]:
        checked = self.prediction(prediction)
        payload: dict[str, Json] = {'request_id': self.request_id, 'run_id': self.run_id,
            'round_id': self.payload['round_id'],
            'request_payload_sha256': self.request['payload_sha256'], 'prediction': checked}
        return {'schema_version': VERSION, 'event_id': str(uuid5(NAMESPACE_URL,
            f'f1ap:{COMPLETED}:{self.run_id}:{self.request_id}')), 'event_type': COMPLETED,
            'occurred_at': checked['generated_at'], 'trace_id': self.request['trace_id'],
            'payload_sha256': digest(payload), 'payload': payload}
