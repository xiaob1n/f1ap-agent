"""单进程、prefetch=1 的 RabbitMQ Worker 适配。"""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol, TypedDict

import aio_pika

from f1ap_agent.contracts.verifier import Rejection
from f1ap_agent.messaging import Exchange, RabbitPublisher, source_identity
from f1ap_agent.runtime import Json, RuntimeFault, atomic_json, json_object, read_json
from f1ap_agent.worker.engine import Worker
from f1ap_agent.worker.models import Task


class ConnectionConfig(TypedDict):
    """aio-pika connection parameters after environment parsing."""

    host: str
    port: int
    virtualhost: str
    login: str
    password: str


def utc_now() -> str:
    """生产消费始终使用实际 UTC 时间。"""
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


@dataclass(frozen=True, slots=True)
class RoutingPublisher:
    exchange: Exchange
    loop: asyncio.AbstractEventLoop
    timeout: float

    def send(self, event: dict[str, Json]) -> bool:
        return RabbitPublisher(self.exchange, self.loop, str(event['event_type']), self.timeout).send(event)


class Delivery(Protocol):
    async def ack(self) -> None: ...
    async def reject(self, requeue: bool = False) -> None: ...


class RetryableDelivery(RuntimeError):
    """回包未确认；保留源 delivery，由关闭连接触发重投。"""


@dataclass(frozen=True, slots=True)
class RabbitConsumer:
    worker: Worker
    clock: Callable[[], str] = utc_now

    def process(self, body: bytes) -> tuple[Task, bool]:
        """验证和源身份落盘与 Worker 状态共用同一个写锁。"""
        with self.worker.store.exclusive():
            now = self.clock()
            task = Task.parse(body, self.worker.contract, now)
            event_id, payload_sha256, run_id = source_identity(task.request)
            path = self.worker.store.root / 'sources.json'
            sources = json_object(read_json(path)) if path.exists() else {}
            identity: dict[str, Json] = {'payload_sha256': payload_sha256, 'run_id': run_id}
            if event_id in sources and sources[event_id] != identity:
                raise RuntimeFault('SOURCE_IDENTITY_CONFLICT')
            sources[event_id] = identity
            atomic_json(path, sources)
            return self.worker._run(body, now)

    async def handle(self, body: bytes, delivery: Delivery) -> bool:
        """仅成功落盘并确认全部回包后 ACK；非法消息明确拒绝。"""
        try:
            _, accepted = await asyncio.to_thread(self.process, body)
        except (RuntimeFault, Rejection, ValueError, UnicodeError):
            await delivery.reject(requeue=False)
            return False
        if not accepted:
            raise RetryableDelivery('PUBLISH_NOT_CONFIRMED')
        await delivery.ack()
        return True


async def consume(worker: Worker, queue: aio_pika.abc.AbstractQueue, output: Path,
                  max_messages: int, delivery_timeout: float,
                  clock: Callable[[], str] = utc_now) -> int:
    """每次只处理一个 delivery；失败不热循环，返回非零供运行器恢复。"""
    consumer = RabbitConsumer(worker, clock)
    count = 0
    async with queue.iterator(timeout=delivery_timeout) as iterator:
        while not max_messages or count < max_messages:
            message = await anext(iterator)
            try:
                accepted = await consumer.handle(message.body, message)
            except RetryableDelivery:
                return 2
            if accepted:
                task = Task.parse(message.body, worker.contract, clock())
                log = worker.store.registry_log(str(task.run_id))
                atomic_json(output / 'request.json', log['request'])
                atomic_json(output / 'events.json', log['events'])
            count += 1
    return 0


async def connect_and_consume(worker: Worker, connection_args: ConnectionConfig,
                              queue_name: str, output_dir: Path, max_messages: int = 0,
                              delivery_timeout: float = 30.0,
                              confirm_timeout: float = 10.0) -> int:
    """建立单一连接，显式开启 mandatory return 异常和 publisher confirms。"""
    connection = await aio_pika.connect(
        host=str(connection_args['host']),
        port=int(connection_args['port']),
        virtualhost=str(connection_args['virtualhost']),
        login=str(connection_args['login']),
        password=str(connection_args['password']),
        timeout=delivery_timeout,
    )
    async with connection:
        channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
        _ = await channel.set_qos(prefetch_count=1)
        queue = await channel.declare_queue(queue_name, durable=True, auto_delete=False)
        exchange = await channel.declare_exchange('f1ap.v1', aio_pika.ExchangeType.DIRECT, durable=True)
        _ = await queue.bind(exchange, routing_key='prediction.requested.v1')
        worker = Worker(worker.store, worker.contract, worker.executor,
                        RoutingPublisher(exchange, asyncio.get_running_loop(), confirm_timeout))
        return await consume(worker, queue, output_dir, max_messages, delivery_timeout)
