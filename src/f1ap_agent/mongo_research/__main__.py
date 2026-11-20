"""官方 MongoDB MCP stdio 研究命令，凭据仅从环境读取。"""
import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

import anyio
import httpx

from f1ap_agent.runtime import RuntimeFault, atomic_json

from .config import Config, Request
from .output import failure_code
from .mcp_session import connect


async def run(request: Request, config: Config, errors: TextIO) -> None:
    from .graph import research
    key = os.environ.get('DEEPSEEK_API_KEY', '')
    if not key:
        raise RuntimeFault('CREDENTIAL_ENV_MISSING')
    async with connect(config, errors) as session:
        async with httpx.AsyncClient(timeout=config.timeout_seconds,
                follow_redirects=False, trust_env=False) as client:
            await research(request, config, session, client, key)


class Arguments(argparse.Namespace):
    question: str | None = None
    session_key: int | None = None
    cutoff: str | None = None
    config: Path | None = None
    output: Path | None = None
    authorize_model: bool = False
    definition: Path | None = None
    check_config: bool = False


def main() -> int:
    parser = argparse.ArgumentParser(description='当前 F1 数据研究；不是冻结十题预测输出')
    _ = parser.add_argument('--question')
    _ = parser.add_argument('--session-key', type=int)
    _ = parser.add_argument('--cutoff')
    _ = parser.add_argument('--config', type=Path)
    _ = parser.add_argument('--output', type=Path)
    _ = parser.add_argument('--check-config', action='store_true')
    _ = parser.add_argument('--definition', type=Path, help='十题定义或导入文件；所选会话明确仅作历史参考')
    _ = parser.add_argument('--authorize-model', action='store_true',
                        help='确认本次运行允许真实模型调用，最多配置的轮数；不是历史授权')
    args = parser.parse_args(namespace=Arguments())
    created = False
    try:
        from f1ap_agent.nacos import load
        startup = load()
        if startup is not None and args.config is not None:
            raise RuntimeFault('NACOS_LOCAL_CONFIG_CONFLICT')
        if args.check_config:
            if startup is None:
                raise RuntimeFault('NACOS_BOOTSTRAP_REQUIRED')
            print('NACOS_CONFIG_VALID; business services not contacted')
            return 0
        if args.question is None or args.session_key is None or args.cutoff is None or args.output is None:
            raise RuntimeFault('TASK_ARGUMENTS_REQUIRED')
        request = Request(args.question, args.session_key, args.cutoff, args.output, args.definition)
        request.validate()
        if startup is not None:
            config = startup.research
        else:
            if args.config is None:
                raise RuntimeFault('CONFIG_REQUIRED')
            config = Config.load(args.config)
        if not args.authorize_model:
            raise RuntimeFault('MODEL_AUTHORIZATION_REQUIRED')
        if startup is not None:
            startup.install()
        request.output.mkdir(mode=0o700, parents=True, exist_ok=False)
        created = True
        atomic_json(request.output / 'request.json', {'question': request.question,
            'session_key': request.session_key, 'cutoff': request.cutoff,
            'observed_at': datetime.now(UTC).isoformat(), 'kind': 'CURRENT_RESEARCH'})
        with open(os.devnull, 'w') as errors:
            anyio.run(run, request, config, errors)
        atomic_json(request.output / 'status.json', {'outcome': 'SUCCEEDED'})
        print(str(request.output / 'answer.json'))
        return 0
    except (RuntimeFault, OSError, ValueError, KeyError, TypeError, ExceptionGroup) as error:
        # CLI 边界只保存错误类别，不输出第三方异常正文中的凭据。
        code = failure_code(error)
        if created and args.output is not None:
            atomic_json(args.output / 'status.json', {'outcome': 'FAILED', 'reason': code})
        print(code, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
