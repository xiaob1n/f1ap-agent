"""Q1/Q7/Q9/Q10 在同一批正赛排列上计数。"""
from f1ap_agent.baselines.podium import Bootstrap
from f1ap_agent.questions import Distribution, exclusive
from f1ap_agent.runtime import Json, RuntimeFault, json_array, json_integer, json_object, json_string
from f1ap_agent.features.cleaning import Insufficient


def calculate(no: int, baseline: Bootstrap, binding: dict[str, Json], keys: tuple[str, ...]) -> Distribution:
    if no == 1:
        return Distribution(tuple({'family': 'RANK_MARGINAL', 'event_key': f'RANK_{rank}:{m.option_key}',
            'option_keys': [m.option_key], 'rank': rank, 'probability': probability}
            for m in baseline.marginals for rank, probability in enumerate(m.probabilities(), 1)),
            'RACE_PACE_BOOTSTRAP_V1', 'PRACTICE_PROXY_V1; lexicographic ties; classification prior supplied in mapping')
    drivers = {key: json_string(value) for key, value in json_object(binding.get('drivers', {})).items()}
    counts = {key: 0 for key in keys}
    classified = json_object(binding.get('classified', {}))
    if no in (7, 9):
        required = set(drivers.values()) if no == 7 else {json_string(binding['driver'])}
        if any(key not in classified or type(classified[key]) is not bool for key in required):
            raise Insufficient('CLASSIFICATION_PRIOR_MISSING')
    for ranking in baseline.rankings:
        if no == 7:
            eligible = [key for key in keys if classified.get(drivers[key]) is True]
            chosen = min(eligible, key=lambda key: ranking.index(drivers[key])) if eligible else 'NO_AWARD'
        elif no == 9:
            driver = json_string(binding['driver'])
            rank = ranking.index(driver) + 1
            category = 'NOT_CLASSIFIED' if classified.get(driver) is False else 'PODIUM' if rank <= 3 else 'TOP10' if rank <= 10 else 'REST'
            chosen = json_string(json_object(binding['categories'])[category])
        elif no == 10:
            conditions = [json_object(v) for v in json_array(binding['conditions'])]
            hit = all(json_integer(c['min']) <= ranking.index(json_string(c['driver'])) + 1 <= json_integer(c['max']) for c in conditions)
            chosen = json_string(binding['yes'] if hit else binding['no'])
        else:
            raise RuntimeFault('UNKNOWN_QUESTION')
        counts[chosen] = counts.get(chosen, 0) + 1
    return exclusive(counts, 'RACE_JOINT_BOOTSTRAP_V1', 'CLASSIFICATION_FIXED_V1: explicit controlled prior, not DNF inference; Q10 same-sample conjunction')
