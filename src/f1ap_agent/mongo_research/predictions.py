"""独立十题研究输出，复用冻结结果 Schema 与语义校验。"""
from pathlib import Path

from f1ap_agent.contracts.semantics import validate_result
from f1ap_agent.contracts.verifier import Rejection, canonical, digest, evaluate, parse
from f1ap_agent.runtime import (
    Json,
    RuntimeFault,
    atomic_json,
    json_events,
    json_object,
    json_string,
)
from f1ap_agent.worker.models import load_contract


def load_definition(path: Path) -> dict[str, Json]:
    document = json_object(parse(path.read_bytes()))
    definition = json_object(document.get('definition', document))
    verdict = evaluate(canonical({'definition': definition}), kind='definition_projection', contract=load_contract())
    if not verdict['accepted']:
        raise RuntimeFault('RESEARCH_DEFINITION_INVALID')
    return definition


def save_predictions(definition: dict[str, Json], answer: dict[str, Json], output: Path,
                     session_key: int, cutoff: str) -> None:
    contract = load_contract()
    supplied = json_events(answer['predictions'])
    questions = json_events(definition['questions'])
    expected = {json_string(q['question_id']) for q in questions}
    by_id = {json_string(item['question_id']): item for item in supplied}
    if len(by_id) != len(supplied) or set(by_id) - expected:
        raise RuntimeFault('PREDICTION_QUESTION_IDS')
    context: dict[str, Json] = {'definition': definition, 'definition_sha256': digest(definition),
        'input_sha256': digest({'definition': definition, 'reference_session_key': session_key, 'cutoff': cutoff}),
        'data_cutoff_at': cutoff,
        'profile': {'strategies': [{'strategy': 'EXPECTED_POINTS', 'version': 'EXPECTED_POINTS_V1'}]}}
    results: list[Json] = []
    for question in questions:
        qid = json_string(question['question_id'])
        blocked = json_object(question['rule'])['verification'] != 'VERIFIED'
        result: dict[str, Json] = {'run_id': '1', 'question_id': qid,
            'definition_sha256': context['definition_sha256'], 'input_sha256': context['input_sha256'],
            'status': 'FAILED', 'data_status': 'INSUFFICIENT',
            'reason': 'RULE_BLOCKED' if blocked else 'LLM_OUTPUT_INVALID',
            'probabilities': [], 'recommendations': [], 'evidence': [], 'fixture_class': 'CONTROLLED_INPUT'}
        if not blocked and qid in by_id:
            item = by_id[qid]
            if set(item) == {'question_id', 'insufficient'} and item['insufficient'] is True:
                result.update(status='DATA_INSUFFICIENT', reason='LLM_DATA_INSUFFICIENT')
            elif set(item) == {'question_id', 'probabilities', 'recommendations'}:
                result.update(status='VALID', data_status='SUFFICIENT', reason='',
                              probabilities=item['probabilities'], recommendations=item['recommendations'])
        result['result_sha256'] = digest(result)
        try:
            contract.validate(result, 'prediction-question-completed.schema.json')
            validate_result(result, context)
        except Rejection:
            result.update(status='FAILED', data_status='INSUFFICIENT', reason='LLM_OUTPUT_INVALID',
                          probabilities=[], recommendations=[])
            _ = result.pop('result_sha256')
            result['result_sha256'] = digest(result)
            contract.validate(result, 'prediction-question-completed.schema.json')
            validate_result(result, context)
        results.append(result)
    atomic_json(output / 'predictions.json', {'kind': 'CURRENT_RESEARCH',
        'wire_dispatched': False, 'result_schema_validated': True, 'run_id_scope': 'LOCAL_OUTPUT_DIRECTORY',
        'reference_session_key': session_key, 'reference_role': 'HISTORICAL_REFERENCE',
        'historical_availability_guaranteed': False, 'query_evidence_location': 'query-*.json',
        'definition': definition, 'results': results})
