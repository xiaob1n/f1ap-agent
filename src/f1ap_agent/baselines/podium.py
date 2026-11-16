"""PODIUM_PACE_BOOTSTRAP_V1 的纯本地计算。"""
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
