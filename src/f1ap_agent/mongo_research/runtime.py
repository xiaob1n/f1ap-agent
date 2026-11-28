"""V2 研究处理所需的最小运行时，不加载历史预测执行器。"""
from collections.abc import Callable
from dataclasses import dataclass

from f1ap_agent.contracts.artifact import Contract
from f1ap_agent.messaging import Sender
from f1ap_agent.runtime import Store
from .config import Config


def checkpoint(name: str) -> None:
    """默认不注入崩溃检查点。"""


@dataclass(frozen=True, slots=True)
class ResearchRuntime:
    store: Store
    contract: Contract
    sender: Sender
    research_config: Config | None = None
    checkpoint: Callable[[str], None] = checkpoint
