"""模型最终输出只接受纯 JSON 或单个完整代码围栏。"""
import re

from f1ap_agent.contracts.verifier import Rejection, parse
from f1ap_agent.runtime import Json, RuntimeFault, json_object


def failure_code(error: Exception) -> str:
    """展开任务组的单一异常，只保留内部故障码或类型，避免泄漏第三方正文。"""
    while isinstance(error, ExceptionGroup) and len(error.exceptions) == 1:
        error = error.exceptions[0]
    return error.code if isinstance(error, RuntimeFault) else type(error).__name__


def parse_output(text: str) -> dict[str, Json]:
    text = text.strip()
    fenced = re.fullmatch(r'```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```', text)
    if fenced is not None:
        text = fenced.group(1)
    try:
        return json_object(parse(text.encode('utf-8')))
    except Rejection:
        raise RuntimeFault('MODEL_JSON_INVALID') from None
