"""十题共享的整数计数概率；不含模型或网络。"""
from dataclasses import dataclass
from decimal import Decimal

from f1ap_agent.baselines.podium import decimal_text
from f1ap_agent.runtime import Json


@dataclass(frozen=True, slots=True)
class Distribution:
    probabilities: tuple[dict[str, Json], ...]
    method: str
    assumptions: str
    samples: int = 10000
    seed: int = 0
    error: str = 'Monte Carlo standard error <=0.005 (10000 samples); not calibration error'


def exclusive(counts: dict[str, int], method: str, assumptions: str) -> Distribution:
    total = sum(counts.values())
    return Distribution(tuple({'family': 'EXCLUSIVE', 'event_key': key,
        'option_keys': [] if key == 'NO_AWARD' else [key], 'rank': None,
        'probability': decimal_text(Decimal(value) / Decimal(total))}
        for key, value in sorted(counts.items())), method, assumptions)
