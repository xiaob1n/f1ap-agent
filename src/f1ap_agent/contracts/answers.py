"""无积分第二版定义边界，不修改冻结的1.0.2制品。"""
from typing import Final

from f1ap_agent.contracts.artifact import check_format
from f1ap_agent.runtime import Json, RuntimeFault, json_events, json_integer, json_object, json_string

VERSION: Final = 'ANSWERS_V2'


def definition(value: Json) -> dict[str, Json]:
    """拒绝积分字段，并校验十题身份、候选和作答形状。"""
    document = json_object(value)
    if set(document) != {'schema_version', 'round_id', 'definition_version', 'name',
                         'deadline_at', 'deadline_verified', 'deadline_basis', 'questions'}:
        raise RuntimeFault('ANSWER_DEFINITION_FIELDS')
    if document['schema_version'] != VERSION:
        raise RuntimeFault('ANSWER_DEFINITION_VERSION')
    check_format('f1ap-id', json_string(document['round_id']), '/round_id')
    if (json_integer(document['definition_version']) < 1 or not json_string(document['name']).strip()
            or not json_string(document['deadline_basis']).strip()
            or type(document['deadline_verified']) is not bool):
        raise RuntimeFault('ANSWER_DEFINITION_METADATA')
    if document['deadline_at'] is not None:
        check_format('f1ap-utc-ms', json_string(document['deadline_at']), '/deadline_at')
    elif document['deadline_verified']:
        raise RuntimeFault('ANSWER_DEADLINE_MISSING')
    questions = json_events(document['questions'])
    if len(questions) != 10:
        raise RuntimeFault('ANSWER_QUESTION_COUNT')
    ids: set[str] = set()
    sources: set[str] = set()
    for number, question in enumerate(questions, 1):
        if set(question) != {'question_id', 'source_question_id', 'no', 'text', 'value_kind', 'options', 'rule'}:
            raise RuntimeFault('ANSWER_QUESTION_FIELDS')
        identity = json_string(question['question_id'])
        source = json_string(question['source_question_id'])
        check_format('f1ap-id', identity, '/question_id')
        if (identity in ids or not source.strip() or source in sources or question['value_kind'] != 'OPTION'
                or json_integer(question['no']) != number or not json_string(question['text']).strip()):
            raise RuntimeFault('ANSWER_QUESTION_IDENTITY')
        ids.add(identity)
        sources.add(source)
        rule = json_object(question['rule'])
        if set(rule) != {'min_picks', 'max_picks', 'ordered'}:
            raise RuntimeFault('ANSWER_RULE_FIELDS')
        minimum, maximum = json_integer(rule['min_picks']), json_integer(rule['max_picks'])
        options = json_events(question['options'])
        if not 1 <= minimum <= maximum <= len(options) <= 200 or type(rule['ordered']) is not bool:
            raise RuntimeFault('ANSWER_RULE_BOUNDS')
        keys: set[str] = set()
        for option in options:
            if set(option) != {'option_key', 'source_id', 'source_type', 'label'}:
                raise RuntimeFault('ANSWER_OPTION_FIELDS')
            key = json_string(option['option_key'])
            if (not key.strip() or key in keys or not json_string(option['source_id']).strip()
                    or option['source_type'] not in ('DRIVER', 'CONSTRUCTOR', 'CHOICE')
                    or not json_string(option['label']).strip()):
                raise RuntimeFault('ANSWER_OPTION_VALUE')
            keys.add(key)
    return document
