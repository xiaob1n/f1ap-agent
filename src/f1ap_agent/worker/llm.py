"""将冻结输入交给独立模型，并用现有契约核验模型生成的预测。"""
from pathlib import Path
from decimal import Decimal, InvalidOperation

from f1ap_agent.contracts.artifact import Contract
from f1ap_agent.contracts.verifier import Rejection, canonical, digest, evaluate, parse
from f1ap_agent.data.openf1.snapshot import load_snapshot
from f1ap_agent.runtime import Json, RuntimeFault, json_events, json_object, json_string
from f1ap_agent.worker.models import Task
from f1ap_agent.worker.research import bind


def prompt(task: Task, mapping_path: Path | None) -> tuple[str, list[dict[str, Json]]]:
    config = bind(task, mapping_path)
    snapshot = load_snapshot(config, json_string(task.payload['data_cutoff_at']))
    evidence: list[dict[str, Json]] = [{
        'document_key': 'OPENF1_MANIFEST:' + snapshot.manifest_sha256,
        'content_sha256': snapshot.manifest_sha256, 'published_at': snapshot.generated_at,
        'first_observed_at': snapshot.first_observed_at, 'retrieval_model_ref': 'NONE',
        'collection_version': 'OPENF1_SNAPSHOT_V1',
        'excerpt': 'Frozen OpenF1 input; LLM estimates are uncalibrated; fuel and traffic unknown',
    }]
    data: dict[str, Json] = {
        'definition': task.payload['definition'], 'profile': task.payload['profile'],
        'data_cutoff_at': task.payload['data_cutoff_at'],
        'candidate_driver_numbers': dict(config.candidates),
        'rows': {key: list(rows) for key, rows in snapshot.rows.items()},
    }
    instruction = (
        '根据以下冻结比赛数据逐题预测。只处理 rule.verification=VERIFIED 的题目。'
        '输出 JSON 对象 {"predictions":[...]}，每题必须包含 question_id、probabilities、recommendations。'
        'probabilities 每项包含 event_key、family、option_keys、probability（十进制字符串）、rank（整数或 null）。'
        '单选使用 EXCLUSIVE，覆盖所有候选且概率和严格为1；有序名次使用 RANK_MARGINAL，'
        'RANK_MARGINAL 每条只描述一个名次下的一个候选，option_keys 必须恰好含一个键。'
        '例如四名候选预测前三名，必须输出3×4=12条概率，不是3条含全部候选的概率。'
        '结构示例：{"event_key":"RANK_1:候选A","family":"RANK_MARGINAL",'
        '"option_keys":["候选A"],"probability":"0.25","rank":1}。'
        '每个名次的概率和为1，每个候选跨名次概率和不超过1；其他使用 JOINT，rank=null。'
        'recommendations 每项包含 strategy="EXPECTED_POINTS"、version="EXPECTED_POINTS_V1"、'
        'objective="EXPECTED_POINTS"、expected_score（非负十进制字符串）以及 picks。'
        '选项 picks 为 {"value_kind":"OPTION","option_key":"实际候选键"}；数值 picks 为'
        ' {"value_kind":"NUMERIC","numeric_value":"十进制字符串"}，遵守题目范围和步长。'
        '选项不能重复，数量遵守 min_picks/max_picks；概率和分数是模型估计。'
        '数据不足时该题仅返回 {"question_id":"实际ID","insufficient":true}，不得编造事实。\n'
    )
    return instruction + canonical(data).decode('utf-8'), evidence


def results(task: Task, contract: Contract, response: dict[str, Json],
            evidence: list[dict[str, Json]]) -> list[dict[str, Json]]:
    predictions: dict[str, dict[str, Json]] = {}
    reason = json_string(response['reason']) or 'LLM_OUTPUT_INVALID'
    content = response['content']
    if content is not None and not response['reason']:
        try:
            document = json_object(parse(json_string(content).encode('utf-8')))
            if set(document) != {'predictions'}:
                raise RuntimeFault('LLM_OUTPUT_FIELDS')
            for item in json_events(document['predictions']):
                key = json_string(item['question_id'])
                if key in predictions or key not in task.question_ids:
                    raise RuntimeFault('LLM_QUESTION_ID')
                predictions[key] = item
        except (Rejection, RuntimeFault, KeyError):
            predictions = {}
    outputs: list[dict[str, Json]] = []
    for question in json_events(json_object(task.payload['definition'])['questions']):
        key = json_string(question['question_id'])
        blocked = json_object(question['rule'])['verification'] != 'VERIFIED'
        result: dict[str, Json] = {
            'run_id': str(task.run_id), 'question_id': key,
            'definition_sha256': task.payload['definition_sha256'],
            'input_sha256': task.payload['input_sha256'], 'status': 'FAILED',
            'data_status': 'INSUFFICIENT', 'reason': 'RULE_BLOCKED' if blocked else reason,
            'probabilities': [], 'recommendations': [], 'evidence': list(evidence),
            'fixture_class': task.fixture_class,
        }
        if not blocked and key in predictions:
            item = predictions[key]
            if set(item) == {'question_id', 'insufficient'} and item['insufficient'] is True:
                result.update(status='DATA_INSUFFICIENT', reason='LLM_DATA_INSUFFICIENT')
            elif set(item) == {'question_id', 'probabilities', 'recommendations'}:
                try:
                    probabilities = [dict(value) for value in json_events(item['probabilities'])]
                    recommendations = [dict(value) for value in json_events(item['recommendations'])]
                    decimals = [(value, 'probability') for value in probabilities]
                    decimals.extend((value, 'expected_score') for value in recommendations)
                    for recommendation in recommendations:
                        picks = [dict(value) for value in json_events(recommendation['picks'])]
                        recommendation['picks'] = picks
                        decimals.extend((value, 'numeric_value') for value in picks if 'numeric_value' in value)
                    for value, field in decimals:
                        text = json_string(value[field])
                        number = Decimal(text)
                        if not number.is_finite() or len(text) > 64 or abs(number.adjusted()) > 32:
                            raise RuntimeFault('LLM_DECIMAL_INVALID')
                        # 仅移除表示冗余，不舍入、不改变模型给出的概率或数值。
                        normalized = format(number, 'f')
                        value[field] = normalized.rstrip('0').rstrip('.') if '.' in normalized else normalized
                        if number == 0:
                            value[field] = '0'
                    result.update(status='VALID', data_status='SUFFICIENT', reason='',
                                  probabilities=probabilities, recommendations=recommendations)
                except (RuntimeFault, InvalidOperation, KeyError):
                    result['reason'] = 'LLM_OUTPUT_INVALID'
        result['result_sha256'] = digest(result)
        event: dict[str, Json] = {
            'schema_version': '1.0', 'event_id': task.request['event_id'],
            'event_type': 'prediction.question.completed.v1', 'occurred_at': task.stamp(),
            'trace_id': task.request['trace_id'], 'payload_sha256': digest(result), 'payload': result,
        }
        if not evaluate(canonical(event), contract=contract,
                        context={'request': task.payload, 'received_results': [], 'usage_history': []})['accepted']:
            result.update(status='FAILED', data_status='INSUFFICIENT', reason='LLM_OUTPUT_INVALID',
                          probabilities=[], recommendations=[])
            _ = result.pop('result_sha256')
            result['result_sha256'] = digest(result)
        outputs.append(result)
    return outputs
