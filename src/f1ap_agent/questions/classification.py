"""Q8 明确的合成分类先验，五个离散 OPTION。"""
from f1ap_agent.questions import Distribution, exclusive
from f1ap_agent.runtime import Json, RuntimeFault, json_object, json_string


def calculate(binding: dict[str, Json]) -> Distribution:
    categories = json_object(binding['counts'])
    classified = json_object(binding['classified'])
    if set(categories) != {'0', '1', '2', '3', '4'} or len(classified) != 4 or any(type(v) is not bool for v in classified.values()):
        raise RuntimeFault('CLASSIFICATION_PRIOR_REQUIRED')
    count = sum(value is True for value in classified.values())
    return exclusive({json_string(key): 10000 if int(label) == count else 0 for label, key in categories.items()},
                     'CLASSIFICATION_POINT_V1', 'CLASSIFICATION_FIXED_V1: supplied explicit prior, not DNF/DNS/DSQ or lap-ratio inference')
