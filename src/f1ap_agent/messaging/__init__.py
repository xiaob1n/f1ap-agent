"""本地发送器与 RabbitMQ confirm 适配器。"""
import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import aio_pika
from aio_pika.abc import AbstractMessage
from aiormq.abc import ConfirmationFrameType
from pamqp.commands import Basic

from f1ap_agent.contracts.verifier import canonical
from f1ap_agent.runtime import Json, atomic_json


class Sender(Protocol):
    def send(self, event: dict[str, Json]) -> bool: ...


@dataclass(frozen=True, slots=True)
class LocalSender:
    root: Path
    accept: bool = True

    def send(self, event: dict[str, Json]) -> bool:
        """Accept by synchronously persisting a local receipt, or retain pending."""
        if not self.accept:
            return False
        atomic_json(self.root / 'sent' / f"{event['event_id']}.json", event)
        return True


class Exchange(Protocol):
    async def publish(self, message: AbstractMessage, routing_key: str, *,
                      mandatory: bool = True, immediate: bool = False,
                      timeout: float | None = None) -> ConfirmationFrameType | None: ...


@dataclass(frozen=True, slots=True)
class RabbitPublisher:
    """在同步 Worker 与 asyncio publisher 之间提供单写者桥接。"""

    exchange: Exchange
    loop: asyncio.AbstractEventLoop
    routing_key: str
    timeout: float

    async def publish(self, event: dict[str, Json]) -> bool:
        """只有 confirm ack 且没有 mandatory return 才返回成功。"""
        message = aio_pika.Message(
            body=canonical(event),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=str(event['event_id']),
            content_type='application/json',
            headers={'payload_sha256': str(event['payload_sha256'])},
        )
        try:
            confirmation = await self.exchange.publish(
                message,
                routing_key=self.routing_key,
                mandatory=True,
                timeout=self.timeout,
            )
        except (aio_pika.exceptions.AMQPException, aio_pika.exceptions.ChannelInvalidStateError,
                OSError, TimeoutError):
            return False
        return isinstance(confirmation, Basic.Ack)

    def send(self, event: dict[str, Json]) -> bool:
        """由 Worker 线程等待单个 publisher confirm，不丢 pending。"""
        if self.loop.is_closed():
            return False
        future = asyncio.run_coroutine_threadsafe(self.publish(event), self.loop)
        try:
            return future.result()
        except (aio_pika.exceptions.AMQPException, OSError, RuntimeError):
            _ = future.cancel()
            return False


def source_identity(event: dict[str, Json]) -> tuple[str, str, str]:
    """提取入站源身份，供消费端先做幂等冲突判断。"""
    payload = event.get('payload')
    if not isinstance(payload, dict):
        raise ValueError('SOURCE_PAYLOAD')
    event_id = event.get('event_id')
    payload_sha256 = event.get('payload_sha256')
    run_id = payload.get('run_id')
    if not isinstance(event_id, str) or not isinstance(payload_sha256, str) or not isinstance(run_id, str):
        raise ValueError('SOURCE_IDENTITY')
    if not event_id or not payload_sha256 or not run_id:
        raise ValueError('SOURCE_IDENTITY')
    return event_id, payload_sha256, run_id
