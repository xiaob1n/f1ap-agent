"""从同一概率生成两种目标，不重复统计计算。"""
from decimal import Decimal, ROUND_HALF_EVEN
from itertools import permutations

from f1ap_agent.baselines.podium import decimal_text
from f1ap_agent.questions import Distribution
from f1ap_agent.runtime import Json, json_array, json_events, json_integer, json_object, json_string


def recommend(question: dict[str, Json], distribution: Distribution) -> list[dict[str, Json]]:
    points = {json_string(o['option_key']): Decimal(json_string(o['points'])) for o in json_events(question['options'])}
    probabilities = distribution.probabilities
    podium = json_integer(question['no']) == 1
    ranks = {(json_string(json_array(p['option_keys'])[0]), json_integer(p['rank'])): Decimal(json_string(p['probability']))
             for p in probabilities if p['family'] == 'RANK_MARGINAL'}
    hits = {key: sum((value for (driver, _), value in ranks.items() if driver == key), Decimal(0))
            if podium else next(Decimal(json_string(p['probability'])) for p in probabilities if p['option_keys'] == [key])
            for key in points}
    multiplier = Decimal(json_string(json_object(json_object(question['rule'])['scoring'])['exact_multiplier'])) if podium else Decimal(1)

    def score(picks: tuple[str, ...]) -> Decimal:
        return sum((points[key] * (hits[key] + (multiplier - 1) * ranks[key, rank])
                    if podium else points[key] * hits[key]
                    for rank, key in enumerate(picks, 1)), Decimal(0))

    choices = tuple(permutations(sorted(points), 3)) if podium else tuple((key,) for key in sorted(points))
    expected = min(choices, key=lambda picks: (-score(picks), picks))
    hit_rate = tuple(sorted(points, key=lambda key: (-hits[key], key))[:3 if podium else 1])
    return [{'strategy': name, 'version': name + '_V1', 'objective': name,
             'expected_score': decimal_text(score(picks).quantize(Decimal('0.000001'), rounding=ROUND_HALF_EVEN)),
             'picks': [{'value_kind': 'OPTION', 'option_key': key} for key in picks]}
            for name, picks in (('EXPECTED_POINTS', expected), ('HIT_RATE', hit_rate))]
