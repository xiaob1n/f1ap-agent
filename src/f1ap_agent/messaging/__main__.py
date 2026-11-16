"""RabbitMQ TEST_FIXTURE consumer；只在 --run 时连接隔离 vhost。"""
import argparse
import asyncio
import os
import sys
import hashlib
import re
import math
from collections.abc import Mapping
from typing import Final
from pathlib import Path

import aio_pika

from f1ap_agent.messaging import LocalSender
from f1ap_agent.messaging.consumer import ConnectionConfig, DrainTimeout, connect_and_consume
from f1ap_agent.messaging.health import Health, check_health
from f1ap_agent.runtime import RuntimeFault, Store
from f1ap_agent.worker.models import load_contract
from f1ap_agent.models.mock import MockPlan, MockProvider
from f1ap_agent.models.deepseek import DeepSeek
from f1ap_agent.contracts.artifact import check_format
from f1ap_agent.contracts.verifier import Rejection

ENV_KEYS: Final = ('F1AP_RABBITMQ_HOST', 'F1AP_RABBITMQ_PORT', 'F1AP_RABBITMQ_VHOST',
            'F1AP_RABBITMQ_USERNAME', 'F1AP_RABBITMQ_PASSWORD')


class Options(argparse.Namespace):
    """命令行解析结果的确定类型。"""
    run: bool = False
    runtime_dir: Path | None = None
    output: Path | None = None
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
    daemon: bool = False
    ensure_runtime: bool = False
    healthcheck: bool = False
    require_ready: bool = False
    health_file: Path | None = None
    wait_topology_seconds: float = 60.0
    shutdown_timeout: float = 30.0
    llm_config: Path | None = None
    check_config: bool = False


def connection_config(environment: Mapping[str, str] | None = None) -> ConnectionConfig:
    values = os.environ if environment is None else environment
    missing = [key for key in ENV_KEYS if not values.get(key)]
    if missing:
        raise RuntimeFault('MISSING_ENV:' + ','.join(missing))
    port = int(values['F1AP_RABBITMQ_PORT'])
    vhost = values['F1AP_RABBITMQ_VHOST']
    if not 1 <= port <= 65535 or vhost in ('', '/'):
        raise RuntimeFault('INVALID_PORT_OR_DEFAULT_VHOST')
    return {'host': values['F1AP_RABBITMQ_HOST'], 'port': port, 'virtualhost': vhost,
            'login': values['F1AP_RABBITMQ_USERNAME'], 'password': values['F1AP_RABBITMQ_PASSWORD']}


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
    _ = parser.add_argument('--runtime-dir', type=Path)
    _ = parser.add_argument('--check-config', action='store_true', help='只读取并校验 Nacos，不连接业务服务')
    _ = parser.add_argument('--output', type=Path)
    _ = parser.add_argument('--daemon', action='store_true')
    _ = parser.add_argument('--ensure-runtime', action='store_true')
    _ = parser.add_argument('--healthcheck', action='store_true')
    _ = parser.add_argument('--require-ready', action='store_true')
    _ = parser.add_argument('--health-file', type=Path)
    _ = parser.add_argument('--wait-topology-seconds', type=float, default=60.0)
    _ = parser.add_argument('--shutdown-timeout', type=float, default=30.0)
    _ = parser.add_argument('--max-messages', type=int, default=0)
    _ = parser.add_argument('--delivery-timeout', type=float, default=30.0)
    _ = parser.add_argument('--confirm-timeout', type=float, default=10.0)
    _ = parser.add_argument('--init-runtime', action='store_true')
    _ = parser.add_argument('--mapping', type=Path)
    _ = parser.add_argument('--llm-config', type=Path, help='Agent 本地 DeepSeek TOML 配置')
    _ = parser.add_argument('--existing-topology', action='store_true')
    _ = parser.add_argument('--mock-plan', type=Path)
    _ = parser.add_argument('--news-catalog', type=Path)
    _ = parser.add_argument('--mock-revisions', type=Path, help='已登记 mock 修订脚本；与 --mock-scenario 同用')
    _ = parser.add_argument('--mock-scenario', help='修订脚本中的唯一场景 ID')
    _ = parser.add_argument('--reconcile-usage', metavar='RUN_ID:CALL_KEY:REVISION',
        help='仅执行一次已登记用量补价；要求 --run、--mock-plan、--mock-revisions、--mock-scenario 及计划绑定的 --news-catalog')
    args = parser.parse_args(namespace=Options())
    health: Health | None = None
    try:
        from f1ap_agent.nacos import load
        startup = None
        if not args.healthcheck:
            if (not args.run and not args.check_config
                    and any(key.startswith('F1AP_NACOS_') for key in os.environ)):
                raise RuntimeFault('NACOS_RUN_OR_CHECK_REQUIRED')
            startup = load()
            if startup is not None:
                if args.llm_config is not None:
                    raise RuntimeFault('NACOS_LOCAL_CONFIG_CONFLICT')
                args.runtime_dir = args.runtime_dir or startup.runtime_dir
                args.mapping = args.mapping or startup.mapping
                for name, value in startup.mq.items():
                    if not any(arg == '--' + name.replace('_', '-') or
                            arg.startswith('--' + name.replace('_', '-') + '=') for arg in sys.argv[1:]):
                        setattr(args, name, value)
            if args.check_config:
                if startup is None:
                    raise RuntimeFault('NACOS_BOOTSTRAP_REQUIRED')
                print('NACOS_CONFIG_VALID; business services not contacted')
                return 0
        if args.runtime_dir is None:
            raise RuntimeFault('RUNTIME_DIR_REQUIRED')
        if (args.max_messages < 0 or any(not math.isfinite(value) or value <= 0 for value in
                (args.delivery_timeout, args.confirm_timeout, args.wait_topology_seconds, args.shutdown_timeout))):
            raise RuntimeFault('INVALID_LIMITS')
        health_path = args.health_file or args.runtime_dir / '.agent-health.json'
        if args.healthcheck:
            if args.run or args.daemon or args.init_runtime or args.ensure_runtime:
                raise RuntimeFault('HEALTHCHECK_ARGUMENTS')
            return check_health(health_path, args.require_ready)
        if args.output is None or args.require_ready or (args.daemon and not args.run):
            raise RuntimeFault('OUTPUT_OR_RUN_REQUIRED')
        if args.init_runtime and args.ensure_runtime:
            raise RuntimeFault('RUNTIME_ARGUMENTS')
        args.mock_plan = args.mock_plan or (Path(os.environ['F1AP_E07_MOCK_PLAN_FILE'])
            if os.environ.get('F1AP_E07_MOCK_PLAN_FILE') else None)
        args.news_catalog = args.news_catalog or (Path(os.environ['F1AP_E07_NEWS_CATALOG_FILE'])
            if os.environ.get('F1AP_E07_NEWS_CATALOG_FILE') else None)
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
        if (args.mock_plan is None) != (args.news_catalog is None):
            raise RuntimeFault('NEWS_FILES_REQUIRED')
        config = connection_config(startup.secrets if startup is not None else None)
        contract = load_contract()
        llm_provider = startup.model if startup is not None else (
            DeepSeek.load(args.llm_config) if args.llm_config is not None else None)
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
        if startup is not None:
            startup.install()
        from f1ap_agent.worker.engine import FakeExecutor, Worker
        store = Store(runtime)
        health = Health(health_path)
        # 首次初始化必须先于状态文件，否则健康文件会把空卷变为非空卷。
        if args.init_runtime:
            store.initialize()
        if args.ensure_runtime:
            _ = store.ensure_initialized()
        _ = store.registry()
        health.update('STARTING')
        worker = Worker(store, contract, FakeExecutor(runtime / 'fake-executor.json'), LocalSender(runtime), args.mapping,
                        mock_plan=plan, provider=MockProvider(plan, runtime / 'mock-effects.json') if plan else None,
                        mock_plan_path=args.mock_plan, news_catalog_path=args.news_catalog,
                        llm_provider=llm_provider,
                        research_config=startup.research if startup is not None else None)
        # 排空超时不能使用 asyncio.run 的无限 executor join；由进程入口强制退出。
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(connect_and_consume(worker, config, 'f1ap.agent.tasks.v1', output,
                args.max_messages, args.delivery_timeout, args.confirm_timeout, args.existing_topology,
                target, daemon=args.daemon, health=health, wait_topology_seconds=args.wait_topology_seconds,
                shutdown_timeout=args.shutdown_timeout))
            loop.run_until_complete(loop.shutdown_default_executor())
            health.update('DRAINING' if result == 0 else 'FAILED')
            return result
        finally:
            loop.close()
    except DrainTimeout:
        # console_scripts 和 python -m 共用该边界，超时不再等待线程池退出。
        os._exit(1)
    except (RuntimeFault, Rejection, ValueError) as error:
        if health is not None:
            health.update('FAILED')
        code = error.code if isinstance(error, RuntimeFault) else 'INVALID_CONFIGURATION'
        print(code if re.fullmatch(r'[A-Z0-9_:,]+', code) else 'INVALID_CONFIGURATION', file=sys.stderr)
        return 2
    except (aio_pika.exceptions.AMQPException, OSError, TimeoutError):
        if health is not None:
            health.update('FAILED')
        print('BROKER_OR_STORAGE_FAILURE', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
