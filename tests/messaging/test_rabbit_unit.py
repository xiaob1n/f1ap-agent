"""真实 Worker 文件状态与可控 confirm 的确定性测试；不连接 Broker。"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

import aio_pika
from aio_pika.abc import AbstractMessage
from aiormq.abc import AbstractChannel, ConfirmationFrameType, DeliveredMessage
from pamqp.commands import Basic
from pamqp.header import ContentHeader

from f1ap_agent.contracts.verifier import canonical, digest
from f1ap_agent.messaging import RabbitPublisher
from f1ap_agent.messaging.consumer import RabbitConsumer, RetryableDelivery, RoutingPublisher
from f1ap_agent.runtime import Store, json_object
from f1ap_agent.worker.engine import FakeExecutor, Worker
from f1ap_agent.worker.models import load_contract


class Delivery:
    def __init__(self) -> None:
        self.acked = False
        self.requeued: bool | None = None

    async def ack(self) -> None:
        self.acked = True

    async def reject(self, requeue: bool = False) -> None:
        self.requeued = requeue


class Exchange:
    def __init__(self, result: ConfirmationFrameType | None = None,
                 error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.messages: list[AbstractMessage] = []
        self.routes: list[str] = []

    async def publish(self, message: AbstractMessage, routing_key: str, *,
                      mandatory: bool = True, immediate: bool = False,
                      timeout: float | None = None) -> ConfirmationFrameType | None:
        if not mandatory or immediate or timeout is None:
            raise AssertionError('mandatory confirm timeout required')
        self.messages.append(message)
        self.routes.append(routing_key)
        if self.error is not None:
            raise self.error
        return self.result


class RabbitUnitTest(unittest.IsolatedAsyncioTestCase):
    def __init__(self, methodName='runTest') -> None:
        super().__init__(methodName)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name).resolve() / 'runtime')
        self.store.initialize()
        self.contract = load_contract()
        self.request = json.loads(self.contract.read_bytes('fixtures/valid/prediction-valid.json'))
        self.counter = self.store.root / 'fake-executor.json'
        self.exchange = Exchange(Basic.Ack(delivery_tag=1))

    def worker(self) -> Worker:
        return Worker(self.store, self.contract, FakeExecutor(self.counter),
                      RoutingPublisher(self.exchange, asyncio.get_running_loop(), 1.0))

    def raw(self) -> bytes:
        self.request['payload_sha256'] = digest(self.request['payload'])
        return canonical(self.request)

    def consumer(self) -> RabbitConsumer:
        return RabbitConsumer(self.worker(), lambda: '2026-01-01T00:10:00.000Z')

    async def test_ack_after_all_persisted_events_confirmed_and_duplicate_executes_once(self) -> None:
        raw = self.raw()
        for _ in range(3):
            delivery = Delivery()
            self.assertTrue(await self.consumer().handle(raw, delivery))
            self.assertTrue(delivery.acked)
        self.assertEqual(json.loads(self.counter.read_bytes())['calls'], 1)
        self.assertEqual(len(self.exchange.messages), 12)
        self.assertEqual(self.exchange.routes[-1], 'agent.run.finished.v1')
        self.assertTrue(all(message.delivery_mode == 2 for message in self.exchange.messages))
        self.assertEqual(list((self.store.root / 'pending').glob('*.json')), [])

    async def test_no_ack_pending_identity_survives_failure_and_recovery(self) -> None:
        raw = self.raw()
        self.exchange.error = TimeoutError()
        delivery = Delivery()
        with self.assertRaises(RetryableDelivery):
            await self.consumer().handle(raw, delivery)
        before = {path.name: path.read_bytes() for path in (self.store.root / 'pending').glob('*.json')}
        self.assertEqual(len(before), 12)
        self.assertFalse(delivery.acked)
        self.assertIsNone(delivery.requeued)
        self.exchange.error = None
        self.assertTrue(await self.consumer().handle(raw, Delivery()))
        self.assertEqual(json.loads(self.counter.read_bytes())['calls'], 1)
        sent = {str(json.loads(message.body)['event_id']) + '.json': json.loads(message.body)
                for message in self.exchange.messages}
        self.assertEqual({name: json.loads(value) for name, value in before.items()}, sent)

    async def test_source_event_conflict_rejected_before_new_side_effect(self) -> None:
        await self.consumer().handle(self.raw(), Delivery())
        self.request['payload']['run_id'] = '101'
        delivery = Delivery()
        self.assertFalse(await self.consumer().handle(self.raw(), delivery))
        self.assertFalse(delivery.acked)
        self.assertFalse(delivery.requeued)
        self.assertEqual(json.loads(self.counter.read_bytes())['calls'], 1)

    async def test_same_run_new_source_id_only_resumes(self) -> None:
        await self.consumer().handle(self.raw(), Delivery())
        self.request['event_id'] = '00000000-0000-0000-0000-000000000001'
        self.assertTrue(await self.consumer().handle(self.raw(), Delivery()))
        self.assertEqual(json.loads(self.counter.read_bytes())['calls'], 1)
        self.assertEqual(len(json.loads((self.store.root / 'sources.json').read_bytes())), 2)

    async def test_invalid_unknown_and_bad_hash_are_not_requeued(self) -> None:
        raw = self.raw()
        broken = json.loads(raw)
        broken['payload_sha256'] = '0' * 64
        for body in (b'invalid', b'{"event_type":"unknown"}', canonical(broken)):
            delivery = Delivery()
            self.assertFalse(await self.consumer().handle(body, delivery))
            self.assertFalse(delivery.requeued)
        self.assertFalse(self.counter.exists())
        self.assertFalse((self.store.root / 'sources.json').exists())

    async def test_default_clock_expires_old_fixture_without_execution(self) -> None:
        consumer = RabbitConsumer(self.worker())
        await consumer.handle(self.raw(), Delivery())
        self.assertFalse(self.counter.exists())
        log = self.store.registry_log(self.request['payload']['run_id'])
        self.assertEqual(json_object(log['finished'])['run_status'], 'TIMED_OUT')

    async def test_publisher_accepts_only_ack(self) -> None:
        for confirmation in (Basic.Nack(delivery_tag=1), Basic.Reject(delivery_tag=1), None):
            self.exchange.result = confirmation
            sender = RabbitPublisher(self.exchange, asyncio.get_running_loop(), 'test', 1)
            self.assertFalse(await sender.publish(self.request))

    async def test_publisher_return_nack_timeout_close_are_unsent(self) -> None:
        returned = DeliveredMessage(
            delivery=Basic.Return(reply_code=312, reply_text='NO_ROUTE', exchange='f1ap.v1', routing_key='test'),
            header=ContentHeader(), body=b'', channel=Mock(spec=AbstractChannel),
        )
        errors = (aio_pika.exceptions.PublishError(returned, returned.delivery),
                  aio_pika.exceptions.DeliveryError(None, Basic.Nack(delivery_tag=1)),
                  TimeoutError(), aio_pika.exceptions.ChannelInvalidStateError('closed'))
        for error in errors:
            self.exchange.error = error
            sender = RabbitPublisher(self.exchange, asyncio.get_running_loop(), 'test', 1)
            self.assertFalse(await sender.publish(self.request))


class CliTest(unittest.TestCase):
    def test_help_and_negative_config_do_not_connect(self) -> None:
        command = [sys.executable, '-m', 'f1ap_agent.messaging']
        help_result = subprocess.run(command + ['--help'], capture_output=True)
        env = {key: value for key, value in os.environ.items() if not key.startswith('F1AP_RABBITMQ_')}
        missing = subprocess.run(command + ['--run', '--runtime-dir', '/unused', '--output', '/unused-output'],
                                 env=env, capture_output=True)
        self.assertEqual(help_result.returncode, 0)
        self.assertEqual(missing.returncode, 2)
        self.assertIn(b'MISSING_ENV', missing.stderr)
