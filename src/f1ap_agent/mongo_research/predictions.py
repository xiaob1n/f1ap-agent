"""无积分研究预测：只校验题目身份、合法选项与作答数量顺序。"""
import uuid
from datetime import UTC, datetime
from pathlib import Path

from f1ap_agent.contracts.verifier import digest, parse
from f1ap_agent.contracts.answers import VERSION, definition as answer_definition
from f1ap_agent.runtime import (
    Json,
    RuntimeFault,
    atomic_json,
    json_array,
    json_events,
    json_integer,
    json_object,
    json_string,
)


def load_definition(path: Path) -> dict[str, Json]:
    document = json_object(parse(path.read_bytes()))
    return answer_definition(document.get('definition', document))


def save_predictions(definition: dict[str, Json], answer: dict[str, Json], output: Path,
                     session_key: int, cutoff: str) -> None:
    supplied = json_events(answer['predictions'])
    questions = json_events(definition['questions'])
    expected = {json_string(q['source_question_id']) for q in questions}
    by_id = {json_string(item['source_question_id']): item for item in supplied}
    if len(by_id) != len(supplied) or set(by_id) != expected:
        raise RuntimeFault('PREDICTION_QUESTION_IDS')
    limitations = [json_string(value) for value in json_array(answer['limitations'])]
    if not 1 <= len(limitations) <= 20 or any(not value.strip() or len(value) > 500 for value in limitations):
        raise RuntimeFault('PREDICTION_LIMITATIONS')
    results: list[Json] = []
    for question in questions:
        item = by_id[json_string(question['source_question_id'])]
        if set(item) != {'source_question_id', 'status', 'picks', 'explanation', 'evidence_ids'}:
            raise RuntimeFault('PREDICTION_FIELDS')
        picks = [json_string(pick) for pick in json_array(item['picks'])]
        explanation = json_string(item['explanation'])
        ids = [json_integer(value) for value in json_array(item['evidence_ids'])]
        if (not explanation.strip() or len(explanation) > 2000 or not ids or len(ids) > 32
                or any(value <= 0 for value in ids) or len(set(ids)) != len(ids)
                or not set(ids) <= set(json_array(answer['evidence_ids']))):
            raise RuntimeFault('PREDICTION_EVIDENCE')
        if question['value_kind'] != 'OPTION':
            raise RuntimeFault('PREDICTION_KIND_UNSUPPORTED')
        if item['status'] != 'PREDICTED':
            raise RuntimeFault('PREDICTION_STATUS')
        rule = json_object(question['rule'])
        allowed = {json_string(option['option_key']) for option in json_events(question['options'])}
        if (not json_integer(rule['min_picks']) <= len(picks) <= json_integer(rule['max_picks'])
                or len(set(picks)) != len(picks) or not set(picks) <= allowed):
            raise RuntimeFault('PREDICTION_PICKS')
        results.append({**item, 'picks': picks})
    atomic_json(output / 'predictions.json', {
        'schema_version': VERSION, 'request_id': str(uuid.uuid4()),
        'definition_sha256': digest(definition), 'reference_session_key': session_key,
        'data_cutoff_at': datetime.fromisoformat(cutoff).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
        'generated_at': datetime.now(UTC).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
        'model': 'deepseek-flash', 'results': results, 'limitations': limitations})
