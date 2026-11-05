"""Q2 最终 Q3 最快，Q3 两车排序，Q4 两车最远阶段。"""
from dataclasses import dataclass

from f1ap_agent.questions import Distribution, exclusive
from f1ap_agent.runtime import Json, json_array, json_object, json_string


@dataclass(frozen=True, slots=True)
class Qualifying:
    times: tuple[tuple[str, tuple[int, ...]], ...]


def calculate(no: int, shared: Qualifying, binding: dict[str, Json], keys: tuple[str, ...]) -> Distribution:
    times = dict(shared.times)
    stages = {driver: max((i + 1 for i, t in enumerate(values) if t), default=0) for driver, values in times.items()}
    drivers = {key: json_string(value) for key, value in json_object(binding.get('drivers', {})).items()}
    if no == 2:
        eligible = [key for key in keys if times[drivers[key]][2] > 0]
        chosen = min(eligible, key=lambda key: (times[drivers[key]][2], key)) if eligible else 'NO_AWARD'
    elif no == 3:
        eligible = [key for key in keys if stages[drivers[key]] > 0]
        chosen = min(eligible, key=lambda key: (-stages[drivers[key]], times[drivers[key]][stages[drivers[key]] - 1], key)) if eligible else 'NO_AWARD'
    else:
        team = [json_string(v) for v in json_array(binding['members'])]
        stage = max(stages[driver] for driver in team)
        chosen = json_string(json_object(binding['stages'])[str(stage)]) if stage else 'NO_AWARD'
    return exclusive({key: 10000 if key == chosen else 0 for key in (*keys, *((chosen,) if chosen == 'NO_AWARD' else ()))},
                     'QUALIFYING_EMPIRICAL_POINT_V1', 'HISTORICAL_SEGMENTS_POINT_PRIOR_V1; no grid position; ties by option key')
