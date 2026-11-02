"""RabbitMQ TEST_FIXTURE consumer；只在 --run 时连接隔离 vhost。"""
import argparse
import asyncio
import os
import sys
from typing import Final
from pathlib import Path

import aio_pika

from f1ap_agent.messaging import LocalSender
from f1ap_agent.messaging.consumer import ConnectionConfig, connect_and_consume
from f1ap_agent.runtime import RuntimeFault, Store
from f1ap_agent.worker.engine import FakeExecutor, Worker
from f1ap_agent.worker.models import load_contract

ENV_KEYS: Final = ('F1AP_RABBITMQ_HOST', 'F1AP_RABBITMQ_PORT', 'F1AP_RABBITMQ_VHOST',
            'F1AP_RABBITMQ_USERNAME', 'F1AP_RABBITMQ_PASSWORD')


class Options(argparse.Namespace):
    """命令行解析结果的确定类型。"""
    run: bool = False
    runtime_dir: Path = Path()
    output: Path = Path()
    max_messages: int = 0
    delivery_timeout: float = 30.0
    confirm_timeout: float = 10.0
    init_runtime: bool = False
    mapping: Path | None = None
    existing_topology: bool = False


def connection_config() -> ConnectionConfig:
    missing = [key for key in ENV_KEYS if not os.environ.get(key)]
    if missing:
        raise RuntimeFault('MISSING_ENV:' + ','.join(missing))
    port = int(os.environ['F1AP_RABBITMQ_PORT'])
    vhost = os.environ['F1AP_RABBITMQ_VHOST']
    if not 1 <= port <= 65535 or vhost in ('', '/'):
        raise RuntimeFault('INVALID_PORT_OR_DEFAULT_VHOST')
    return {'host': os.environ['F1AP_RABBITMQ_HOST'], 'port': port, 'virtualhost': vhost,
            'login': os.environ['F1AP_RABBITMQ_USERNAME'], 'password': os.environ['F1AP_RABBITMQ_PASSWORD']}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, epilog='环境变量：' + ', '.join(ENV_KEYS))
    _ = parser.add_argument('--run', action='store_true')
    _ = parser.add_argument('--runtime-dir', type=Path, required=True)
    _ = parser.add_argument('--output', type=Path, required=True)
    _ = parser.add_argument('--max-messages', type=int, default=0)
    _ = parser.add_argument('--delivery-timeout', type=float, default=30.0)
    _ = parser.add_argument('--confirm-timeout', type=float, default=10.0)
    _ = parser.add_argument('--init-runtime', action='store_true')
    _ = parser.add_argument('--mapping', type=Path)
    _ = parser.add_argument('--existing-topology', action='store_true')
    args = parser.parse_args(namespace=Options())
    try:
        if args.max_messages < 0 or args.delivery_timeout <= 0 or args.confirm_timeout <= 0:
            raise RuntimeFault('INVALID_LIMITS')
        config = connection_config()
        contract = load_contract()
        runtime, output = args.runtime_dir.resolve(), args.output.resolve()
        protected = (contract.root, contract.lock_path)
        for candidate in (runtime, output):
            if any(candidate == path or candidate.is_relative_to(path) or path.is_relative_to(candidate)
                   for path in protected):
                raise RuntimeFault('OUTPUT_OVERLAP')
        if runtime == output or runtime.is_relative_to(output) or output.is_relative_to(runtime):
            raise RuntimeFault('OUTPUT_RUNTIME_OVERLAP')
        if not args.run:
            print('CONFIG_VALID; network disabled without --run')
            return 0
        store = Store(runtime)
        if args.init_runtime:
            store.initialize()
        _ = store.registry()
        worker = Worker(store, contract, FakeExecutor(runtime / 'fake-executor.json'), LocalSender(runtime), args.mapping)
        return asyncio.run(connect_and_consume(worker, config, 'f1ap.agent.tasks.v1', output,
                            args.max_messages, args.delivery_timeout, args.confirm_timeout,
                            args.existing_topology))
    except (RuntimeFault, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except (aio_pika.exceptions.AMQPException, OSError, TimeoutError):
        print('BROKER_OR_STORAGE_FAILURE', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
