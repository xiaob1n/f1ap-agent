"""RabbitMQ TEST_FIXTURE consumer；只在 --run 时连接隔离 vhost。"""
import argparse
import asyncio
import os
import sys
import hashlib
import re
from typing import Final
from pathlib import Path

import aio_pika

from f1ap_agent.messaging import LocalSender
from f1ap_agent.messaging.consumer import ConnectionConfig, connect_and_consume
from f1ap_agent.runtime import RuntimeFault, Store
from f1ap_agent.worker.engine import FakeExecutor, Worker
from f1ap_agent.worker.models import load_contract
from f1ap_agent.models.mock import MockPlan, MockProvider
from f1ap_agent.contracts.artifact import check_format
from f1ap_agent.contracts.verifier import Rejection

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
    mock_plan: Path | None = None
    news_catalog: Path | None = None
    mock_revisions: Path | None = None
    mock_scenario: str | None = None
    reconcile_usage: str | None = None


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


def reconciliation(value: str) -> tuple[str, str, int]:
    """仅接受规范十进制 run/revision 与冻结小写 UUID 三元组。"""
    parts = value.split(':')
    if len(parts) != 3:
        raise RuntimeFault('RECONCILE_USAGE_FORMAT')
    run_id, key, revision = parts
    try:
        check_format('f1ap-id', run_id, '/run_id')
        check_format('f1ap-uuid', key, '/call_key')
    except Rejection as error:
        raise RuntimeFault('RECONCILE_USAGE_FORMAT') from error
    if not re.fullmatch(r'[1-9][0-9]{0,9}', revision) or int(revision) > 2147483647:
        raise RuntimeFault('RECONCILE_USAGE_FORMAT')
    return run_id, key, int(revision)


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
    _ = parser.add_argument('--mock-plan', type=Path)
    _ = parser.add_argument('--news-catalog', type=Path)
    _ = parser.add_argument('--mock-revisions', type=Path, help='已登记 mock 修订脚本；与 --mock-scenario 同用')
    _ = parser.add_argument('--mock-scenario', help='修订脚本中的唯一场景 ID')
    _ = parser.add_argument('--reconcile-usage', metavar='RUN_ID:CALL_KEY:REVISION',
        help='仅执行一次已登记用量补价；要求 --run、--mock-plan、--mock-revisions、--mock-scenario 及计划绑定的 --news-catalog')
    args = parser.parse_args(namespace=Options())
    try:
        target = reconciliation(args.reconcile_usage) if args.reconcile_usage is not None else None
        if target is not None:
            if not args.run:
                raise RuntimeFault('RECONCILE_RUN_REQUIRED')
            if args.mock_plan is None:
                raise RuntimeFault('RECONCILE_MOCK_PLAN_REQUIRED')
            if args.mock_revisions is None or args.mock_scenario is None:
                raise RuntimeFault('RECONCILE_REVISIONS_REQUIRED')
        if (args.mock_revisions is None) != (args.mock_scenario is None):
            raise RuntimeFault('MOCK_REVISIONS_ARGUMENTS')
        if args.mock_revisions is not None and args.mock_plan is None:
            raise RuntimeFault('RECONCILE_MOCK_PLAN_REQUIRED')
        if args.max_messages < 0 or args.delivery_timeout <= 0 or args.confirm_timeout <= 0:
            raise RuntimeFault('INVALID_LIMITS')
        config = connection_config()
        contract = load_contract()
        if (args.mock_plan is None) != (args.news_catalog is None):
            raise RuntimeFault('NEWS_FILES_REQUIRED')
        plan = None
        if args.mock_plan is not None and args.news_catalog is not None:
            raw = args.mock_plan.read_bytes()
            plan = MockPlan.load(args.mock_plan, hashlib.sha256(raw).hexdigest(), contract)
            from f1ap_agent.contracts.verifier import parse
            from f1ap_agent.runtime import json_object
            if hashlib.sha256(args.news_catalog.read_bytes()).hexdigest() != json_object(parse(raw))['news_catalog_sha256']:
                raise RuntimeFault('NEWS_CATALOG_HASH')
            if args.mock_revisions is not None and args.mock_scenario is not None:
                plan = plan.with_revisions(args.mock_revisions, args.mock_scenario, contract)
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
        worker = Worker(store, contract, FakeExecutor(runtime / 'fake-executor.json'), LocalSender(runtime), args.mapping,
                        mock_plan=plan, provider=MockProvider(plan, runtime / 'mock-effects.json') if plan else None,
                        mock_plan_path=args.mock_plan, news_catalog_path=args.news_catalog)
        return asyncio.run(connect_and_consume(worker, config, 'f1ap.agent.tasks.v1', output,
                            args.max_messages, args.delivery_timeout, args.confirm_timeout,
                            args.existing_topology, target))
    except (RuntimeFault, Rejection, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except (aio_pika.exceptions.AMQPException, OSError, TimeoutError):
        print('BROKER_OR_STORAGE_FAILURE', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
