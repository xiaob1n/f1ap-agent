"""研究与赛程核验共用官方只读 MCP 连接，不继承其他 Mongo 配置。"""
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import TextIO

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from f1ap_agent.runtime import Json, RuntimeFault, json_object
from .config import Config


@asynccontextmanager
async def connect(config: Config, errors: TextIO) -> AsyncGenerator[ClientSession]:
    uri = os.environ.get('MDB_MCP_CONNECTION_STRING', '')
    if not uri:
        raise RuntimeFault('CREDENTIAL_ENV_MISSING')
    environment = {name: os.environ[name] for name in
        ('PATH', 'HOME', 'TMPDIR', 'NODE_COMPILE_CACHE', 'BUN_RUNTIME_TRANSPILER_CACHE_PATH') if name in os.environ}
    environment['MDB_MCP_CONNECTION_STRING'] = uri
    server = StdioServerParameters(command='mongodb-mcp-server', args=[
        '--readOnly', '--transport', 'stdio', '--telemetry', 'disabled', '--loggers', '',
        '--maxDocumentsPerQuery', str(config.row_limit), '--maxBytesPerQuery', str(config.byte_limit),
        '--timeoutMs', str(config.timeout_seconds * 1000)], env=environment)
    async with (stdio_client(server, errlog=errors) as (reader, writer),
                ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=config.timeout_seconds)) as session):
        with anyio.fail_after(config.timeout_seconds):
            _ = await session.initialize()
            tools = await session.list_tools()
        find = next((tool for tool in tools.tools if tool.name == 'find'), None)
        schema: Json = find.inputSchema if find is not None else {}
        properties = json_object(json_object(schema).get('properties', {}))
        if not {'database', 'collection', 'filter', 'projection', 'limit', 'responseBytesLimit'} <= set(properties):
            raise RuntimeFault('MCP_FIND_SCHEMA')
        yield session
