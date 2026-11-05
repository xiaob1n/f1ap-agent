"""PODIUM_PACE_BOOTSTRAP_V1 的纯本地计算。"""
import itertools
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from f1ap_agent.runtime import RuntimeFault


@dataclass(frozen=True, slots=True)
class Marginal:
    option_key: str
    counts: tuple[int, int, int]

    def probabilities(self) -> tuple[str, str, str]:
        first, second, third = self.counts
        return (decimal_text(Decimal(first) / Decimal(10000)),
                decimal_text(Decimal(second) / Decimal(10000)),
                decimal_text(Decimal(third) / Decimal(10000)))


def decimal_text(value: Decimal) -> str:
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


@dataclass(frozen=True, slots=True)
class Bootstrap:
    marginals: tuple[Marginal, ...]
    samples: int = 10000
    seed: int = 0
    laps_per_sample: int = 5
    rankings: tuple[tuple[str, ...], ...] = ()


def bootstrap(laps: Mapping[str, Sequence[int]]) -> Bootstrap:
    """固定 seed、有放回抽样并返回前三名边际计数。"""
    options = tuple(sorted(laps))
    if len(options) < 3 or any(len(laps[key]) < 5 for key in options):
        raise RuntimeFault('DATA_INSUFFICIENT')
    if any(any(type(value) is not int or value <= 0 for value in laps[key]) for key in options):
        raise RuntimeFault('OPENF1_LAP_VALUE')
    rng = random.Random(0)
    pools = {key: sorted(laps[key]) for key in options}
    counts = {key: [0, 0, 0] for key in options}
    rankings: list[tuple[str, ...]] = []
    for _ in range(10000):
        ranking = sorted(
            (sorted(pools[key][rng.randrange(len(pools[key]))] for _ in range(5))[2], key)
            for key in options
        )
        for rank, (_, option_key) in enumerate(ranking[:3]):
            counts[option_key][rank] += 1
        rankings.append(tuple(key for _, key in ranking))
    return Bootstrap(tuple(Marginal(key, (counts[key][0], counts[key][1], counts[key][2]))
                           for key in options), rankings=tuple(rankings))


def optimal_podium(result: Bootstrap, points: Mapping[str, Decimal], exact_multiplier: Decimal) -> tuple[tuple[str, str, str], Decimal]:
    """用独立排列枚举计算 EXPECTED_POINTS 的最优无重复领奖台。"""
    by_key = {item.option_key: item for item in result.marginals}
    if set(points) != set(by_key) or exact_multiplier < 0:
        raise RuntimeFault('PODIUM_SCORE_INPUT')
    best: tuple[str, str, str] | None = None
    best_score: Decimal | None = None
    for picks in itertools.permutations(sorted(by_key), 3):
        score = Decimal(0)
        for rank, option_key in enumerate(picks):
            marginal = Decimal(by_key[option_key].counts[rank]) / Decimal(result.samples)
            podium = Decimal(sum(by_key[option_key].counts)) / Decimal(result.samples)
            score += points[option_key] * (podium + (exact_multiplier - 1) * marginal)
        if best_score is None or score > best_score or (
            score == best_score and best is not None and picks < best
        ):
            best, best_score = picks, score
    if best is None or best_score is None:
        raise RuntimeFault('PODIUM_SCORE_INPUT')
    return best, best_score
