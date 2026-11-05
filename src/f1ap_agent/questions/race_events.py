"""完整历史正赛的事件频数先验；不读取目标正赛答案。"""
from dataclasses import replace
from decimal import Decimal
from typing import Literal, assert_never

from f1ap_agent.baselines.podium import decimal_text
from f1ap_agent.data.openf1.boundary import timestamp
from f1ap_agent.features.cleaning import Insufficient
from f1ap_agent.questions import Distribution, exclusive
from f1ap_agent.runtime import Json, RuntimeFault, json_array, json_integer, json_object, json_string


def calculate(binding: dict[str, Json], keys: tuple[str, ...], cutoff: str,
              target_session: int) -> Distribution:
    """每个完整且截止前可用的历史正赛贡献一次观察。"""
    history = json_array(binding['history'])
    if not history:
        raise Insufficient('RACE_EVENT_HISTORY_MISSING')
    counts = dict.fromkeys(keys, 0)
    seen: set[int] = set()
    method: Literal['RED_FLAG_COUNT_V1', 'ALL_FINISH_FIRST_LAP_V1']
    match json_string(binding['method']):
        case 'RED_FLAG_COUNT_V1':
            method = 'RED_FLAG_COUNT_V1'
        case 'ALL_FINISH_FIRST_LAP_V1':
            method = 'ALL_FINISH_FIRST_LAP_V1'
        case _:
            raise RuntimeFault('RACE_EVENT_METHOD_UNKNOWN')
    for raw in history:
        row = json_object(raw)
        session = json_integer(row['session_key'])
        if (session == target_session or session in seen or row['complete'] is not True
                or timestamp(json_string(row['available_at'])) > timestamp(cutoff)
                or not json_string(row['source_ref'])):
            raise Insufficient('RACE_EVENT_HISTORY_INVALID')
        seen.add(session)
        match method:
            case 'RED_FLAG_COUNT_V1':
                value = json_integer(row['red_flags'])
                if value < 0:
                    raise Insufficient('RACE_EVENT_COUNT_INVALID')
                categories = json_object(binding['categories'])
                selected = [json_string(key) for label, key in categories.items()
                            if (label.startswith('>') and value > int(label[1:]))
                            or (label.endswith('+') and value >= int(label[:-1]))
                            or (label.isdigit() and value == int(label))]
                if len(selected) != 1:
                    raise Insufficient('RACE_EVENT_CATEGORY_INVALID')
                chosen = selected[0]
            case 'ALL_FINISH_FIRST_LAP_V1':
                value = row['all_first_lap']
                if type(value) is not bool:
                    raise Insufficient('FIRST_LAP_HISTORY_INVALID')
                chosen = json_string(binding['yes'] if value else binding['no'])
            case unreachable:
                assert_never(unreachable)
        if chosen not in counts:
            raise Insufficient('RACE_EVENT_OPTION_INVALID')
        counts[chosen] += 1
    result = exclusive(counts, method, 'HISTORICAL_RACE_FREQUENCY_V1; no target outcomes; not calibrated')
    probabilities = list(result.probabilities)
    remainder = Decimal(1) - sum((Decimal(json_string(p['probability'])) for p in probabilities[:-1]), Decimal(0))
    probabilities[-1] = {**probabilities[-1], 'probability': decimal_text(remainder)}
    return replace(result, probabilities=tuple(probabilities), samples=len(history),
                   error='Historical empirical frequency; small-sample and transfer error unquantified')
