"""官方 MongoDB MCP stdio 研究命令，凭据仅从环境读取。"""
import argparse
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TextIO

import anyio
import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from f1ap_agent.runtime import Json, RuntimeFault, atomic_json, json_object

from .config import Config, Request
from .graph import research


async def run(request: Request, config: Config, errors: TextIO) -> None:
    uri = os.environ.get('MDB_MCP_CONNECTION_STRING', '')
    key = os.environ.get('DEEPSEEK_API_KEY', '')
    if not uri or not key:
        raise RuntimeFault('CREDENTIAL_ENV_MISSING')
    # 不继承其他 MDB 配置，避免主机设置覆盖只读模式或开启磁盘日志。
    env = {name: os.environ[name] for name in
           ('PATH', 'HOME', 'TMPDIR', 'NODE_COMPILE_CACHE', 'BUN_RUNTIME_TRANSPILER_CACHE_PATH')
           if name in os.environ}
    env['MDB_MCP_CONNECTION_STRING'] = uri
    # 提前安装固定版本，避免将包下载计入协议初始化超时。
    server = StdioServerParameters(command='mongodb-mcp-server', args=[
        '--readOnly', '--transport', 'stdio',
        '--telemetry', 'disabled', '--loggers', '',
        '--maxDocumentsPerQuery', str(config.row_limit),
        '--maxBytesPerQuery', str(config.byte_limit), '--timeoutMs', str(config.timeout_seconds * 1000)], env=env)
    async with (stdio_client(server, errlog=errors) as (reader, writer),
                ClientSession(reader, writer,
                    read_timeout_seconds=timedelta(seconds=config.timeout_seconds)) as session):
        with anyio.fail_after(config.timeout_seconds):
            _ = await session.initialize()
            tools = await session.list_tools()
        find = next((tool for tool in tools.tools if tool.name == 'find'), None)
        schema: Json = find.inputSchema if find is not None else {}
        properties = json_object(json_object(schema).get('properties', {}))
        if not {'database', 'collection', 'filter', 'projection', 'limit',
                'responseBytesLimit'} <= set(properties):
            raise RuntimeFault('MCP_FIND_SCHEMA')
        async with httpx.AsyncClient(timeout=config.timeout_seconds,
                follow_redirects=False, trust_env=False) as client:
            await research(request, config, session, client, key)


class Arguments(argparse.Namespace):
    question: str = ''
    session_key: int = 0
    cutoff: str = ''
    config: Path = Path()
    output: Path = Path()
    authorize_model: bool = False
    definition: Path | None = None


def main() -> int:
    parser = argparse.ArgumentParser(description='当前 F1 数据研究；不是冻结十题预测输出')
    _ = parser.add_argument('--question', required=True)
    _ = parser.add_argument('--session-key', type=int, required=True)
    _ = parser.add_argument('--cutoff', required=True)
    _ = parser.add_argument('--config', type=Path, required=True)
    _ = parser.add_argument('--output', type=Path, required=True)
    _ = parser.add_argument('--definition', type=Path, help='十题定义或导入文件；所选会话明确仅作历史参考')
    _ = parser.add_argument('--authorize-model', action='store_true',
                        help='确认本次运行允许真实模型调用，最多配置的轮数；不是历史授权')
    args = parser.parse_args(namespace=Arguments())
    request = Request(args.question, args.session_key, args.cutoff, args.output, args.definition)
    created = False
    try:
        request.validate()
        config = Config.load(args.config)
        if not args.authorize_model:
            raise RuntimeFault('MODEL_AUTHORIZATION_REQUIRED')
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
        code = error.code if isinstance(error, RuntimeFault) else type(error).__name__
        if created:
            atomic_json(request.output / 'status.json', {'outcome': 'FAILED', 'reason': code})
        print(code, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
