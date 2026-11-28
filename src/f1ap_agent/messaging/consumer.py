"""单进程、prefetch=1 的 RabbitMQ Worker 适配。"""
import asyncio
import contextlib
import signal
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol, TypedDict

import aio_pika

from f1ap_agent.contracts.verifier import Rejection
from f1ap_agent.mongo_research.mq_contract import ResearchTask
from f1ap_agent.mongo_research.mq import process as process_research
from f1ap_agent.messaging import Exchange, RabbitPublisher
from f1ap_agent.runtime import Json, RuntimeFault, atomic_json, json_object, read_json
from f1ap_agent.mongo_research.runtime import ResearchRuntime
from f1ap_agent.messaging.health import Health


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


class DrainTimeout(RuntimeError):
    """排空期限已到；进程边界必须终止，不能再等待不可取消的线程。"""


@dataclass(frozen=True, slots=True)
class RabbitConsumer:
    worker: ResearchRuntime
    clock: Callable[[], str] = utc_now

    def process(self, body: bytes) -> tuple[ResearchTask, bool]:
        """在唯一写锁内验证 V2 边界并处理持久化研究状态。"""
        with self.worker.store.exclusive():
            research = ResearchTask.parse(body, self.worker.contract)
            return research, process_research(self.worker, research, self.clock)

    async def handle(self, body: bytes, delivery: Delivery, output: Path | None = None) -> bool:
        """仅成功落盘并确认全部回包后 ACK；非法消息明确拒绝。"""
        try:
            task, accepted = await asyncio.to_thread(self.process, body)
        except (RuntimeFault, Rejection, ValueError, UnicodeError):
            await delivery.reject(requeue=False)
            return False
        if not accepted:
            raise RetryableDelivery('PUBLISH_NOT_CONFIRMED')
        if output is not None:
            atomic_json(output / 'request.json', task.request)
            document = json_object(read_json(self.worker.store.root / 'unscored' / 'records.json'))
            record = json_object(json_object(document['records'])[task.run_id])
            atomic_json(output / 'events.json', [record['event']])
        await delivery.ack()
        return True


async def consume(worker: ResearchRuntime, queue: aio_pika.abc.AbstractQueue, output: Path,
                  max_messages: int, delivery_timeout: float,
                  clock: Callable[[], str] = utc_now, *, daemon: bool = False,
                  health: Health | None = None, stop: asyncio.Event | None = None,
                  shutdown_timeout: float = 30.0) -> int:
    """每次只处理一个 delivery；失败不热循环，返回非零供运行器恢复。"""
    consumer = RabbitConsumer(worker, clock)
    count = 0
    stopping = stop if stop is not None else asyncio.Event()
    stopped = asyncio.create_task(stopping.wait())
    try:
        while not stopping.is_set() and (not max_messages or count < max_messages):
            try:
                async with queue.iterator(timeout=delivery_timeout) as iterator:
                    try:
                        if health is not None and not stopping.is_set():
                            health.update('HEALTHY')
                        while not stopping.is_set() and (not max_messages or count < max_messages):
                            pull = asyncio.create_task(anext(iterator))
                            try:
                                done, _ = await asyncio.wait((pull, stopped), return_when=asyncio.FIRST_COMPLETED)
                                if stopped in done:
                                    return 0
                                message = pull.result()
                            finally:
                                if not pull.done():
                                    pull.cancel()
                                with contextlib.suppress(asyncio.CancelledError):
                                    await pull
                            handling = asyncio.create_task(consumer.handle(message.body, message, output))
                            try:
                                done, _ = await asyncio.wait((handling, stopped), return_when=asyncio.FIRST_COMPLETED)
                                if stopped in done:
                                    done, _ = await asyncio.wait((handling,), timeout=shutdown_timeout)
                                    if not done:
                                        raise DrainTimeout('SHUTDOWN_TIMEOUT')
                                await handling
                            except RetryableDelivery as error:
                                if daemon and not stopping.is_set():
                                    raise ConnectionError('DELIVERY_NOT_CONFIRMED') from error
                                return 2
                            finally:
                                if not handling.done():
                                    handling.cancel()
                                with contextlib.suppress(asyncio.CancelledError):
                                    await handling
                            count += 1
                    finally:
                        if health is not None and not stopping.is_set():
                            health.update('STARTING')
            except TimeoutError:
                if not daemon:
                    raise
            except StopAsyncIteration as error:
                if not stopping.is_set():
                    raise ConnectionError('CONSUMER_CLOSED') from error
        return 0
    finally:
        stopped.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stopped


async def connect_and_consume(worker: ResearchRuntime, connection_args: ConnectionConfig,
                              queue_name: str, output_dir: Path, max_messages: int = 0,
                              delivery_timeout: float = 30.0,
                              confirm_timeout: float = 10.0,
                              existing_topology: bool = False,
                              *,
                              daemon: bool = False, health: Health | None = None,
                              wait_topology_seconds: float = 60.0,
                              shutdown_timeout: float = 30.0) -> int:
    """建立单一连接，显式开启 mandatory return 异常和 publisher confirms。"""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def drain() -> None:
        stop.set()
        if health is not None:
            health.update('DRAINING')

    async def heartbeat() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), 5.0)
            except TimeoutError:
                if health is not None:
                    health.update(health.state)

    old_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    for sig in old_handlers:
        loop.add_signal_handler(sig, drain)
    beat = asyncio.create_task(heartbeat())
    delay = 0.1
    try:
        while not stop.is_set():
            try:
                try:
                    return await _session(worker, connection_args, queue_name, output_dir, max_messages,
                        delivery_timeout, confirm_timeout, existing_topology,
                        daemon, health, stop, wait_topology_seconds, shutdown_timeout)
                except asyncio.CancelledError as error:
                    # aiormq 心跳关闭会传播内部取消；调用方取消仍必须原样退出。
                    task = asyncio.current_task()
                    if task is None or task.cancelling():
                        raise
                    raise ConnectionError('BROKER_SESSION_CANCELLED') from error
            except (aio_pika.exceptions.AMQPException, aio_pika.exceptions.ChannelInvalidStateError,
                    ConnectionError, TimeoutError):
                if health is not None and not stop.is_set():
                    health.update('DEGRADED')
                if not daemon:
                    raise
                try:
                    await asyncio.wait_for(stop.wait(), delay)
                except TimeoutError:
                    delay = min(delay * 2, 5.0)
        return 0
    finally:
        beat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await beat
        for sig, handler in old_handlers.items():
            loop.remove_signal_handler(sig)
            signal.signal(sig, handler)


async def _session(worker: ResearchRuntime, connection_args: ConnectionConfig, queue_name: str,
                   output_dir: Path, max_messages: int, delivery_timeout: float,
                   confirm_timeout: float, existing_topology: bool,
                   daemon: bool,
                   health: Health | None, stop: asyncio.Event, wait_topology_seconds: float,
                   shutdown_timeout: float) -> int:
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
        # existing_topology 仅保留命令行兼容；任何模式都不创建或重绑拓扑。
        deadline = asyncio.get_running_loop().time() + wait_topology_seconds
        delay = 0.1
        while not stop.is_set():
            try:
                queue = await channel.get_queue(queue_name, ensure=True)
                exchange = await channel.get_exchange('f1ap.v1', ensure=True)
                break
            except aio_pika.exceptions.ChannelNotFoundEntity as error:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise RuntimeFault('TOPOLOGY_MISSING_ON_TEST_VHOST') from error
                try:
                    await asyncio.wait_for(stop.wait(), min(delay, remaining))
                except TimeoutError:
                    delay = min(delay * 2, 2.0)
                # passive 404 会关闭通道，下一次核对必须使用新通道。
                channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
                await channel.set_qos(prefetch_count=1)
        else:
            return 0
        worker = replace(worker, sender=RoutingPublisher(exchange, asyncio.get_running_loop(), confirm_timeout))
        return await consume(worker, queue, output_dir, max_messages, delivery_timeout,
                             daemon=daemon, health=health, stop=stop, shutdown_timeout=shutdown_timeout)
