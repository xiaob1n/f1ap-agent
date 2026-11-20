"""发布包内的研究 skill；模型按名称选择，文件不可用时继续基础研究。"""
from pathlib import Path
from typing import Final

from f1ap_agent.runtime import Json, RuntimeFault, json_object, json_string

ROOT: Final = Path(__file__).parent
CATALOG: Final = {
    'lap-analysis': '比较车手圈速或预测名次时使用：规划车手覆盖、区分有效圈与重复样本、评估可比性和证据不足。',
}
TOOL: Final = {'type': 'function', 'function': {
    'name': 'load_skill',
    'description': '按需读取可用研究 skill 的操作要求；不是比赛数据，不提供 query_id。文件不可用时沿用基础提示。',
    'parameters': {'type': 'object', 'additionalProperties': False,
        'properties': {'name': {'type': 'string', 'enum': list(CATALOG)}}, 'required': ['name']}}}


def load(raw: Json) -> dict[str, Json]:
    value = json_object(raw)
    if set(value) != {'name'}:
        raise RuntimeFault('SKILL_ARGUMENTS')
    name = json_string(value['name'])
    if name not in CATALOG:
        return {'name': name, 'outcome': 'SKIPPED', 'reason': 'SKILL_NOT_FOUND'}
    try:
        content = (ROOT / name / 'SKILL.md').read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return {'name': name, 'outcome': 'SKIPPED', 'reason': 'SKILL_UNAVAILABLE'}
    if not content.strip():
        return {'name': name, 'outcome': 'SKIPPED', 'reason': 'SKILL_EMPTY'}
    return {'name': name, 'outcome': 'SUCCEEDED', 'content': content}
