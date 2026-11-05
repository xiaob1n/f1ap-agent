"""有效练习圈最小值的经验点先验，不附加前十门槛。"""
from f1ap_agent.features.ten import Feature
from f1ap_agent.questions import Distribution, exclusive


def calculate(feature: Feature, drivers: dict[str, str]) -> Distribution:
    pools = dict(feature.values)
    winner = min(drivers, key=lambda key: (min(pools[drivers[key]]), key))
    return exclusive({key: 10000 if key == winner else 0 for key in drivers},
                     'FASTEST_LAP_POINT_V1', 'PRACTICE_MINIMUM_POINT_PRIOR_V1; ties by option key')
