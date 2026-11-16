"""Exa MCP 搜索入口：只输出待人工确认的来源，不读取网页或写业务库。"""
import argparse
import json
import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from f1ap_agent.runtime import Json, RuntimeFault, json_array, json_object, json_string


@dataclass(frozen=True, slots=True)
class SearchConfig:
    endpoint: str
    timeout_seconds: int

    @classmethod
    def load(cls, path: Path) -> 'SearchConfig':
        with path.open('rb') as stream:
            config = tomllib.load(stream)
        if set(config) != {'endpoint', 'timeout_seconds'}:
            raise RuntimeFault('WEBSEARCH_CONFIG_FIELDS')
        endpoint = os.environ.get('F1AP_WEBSEARCH_MCP_URL') or config['endpoint']
        if not isinstance(endpoint, str):
            raise RuntimeFault('WEBSEARCH_ENDPOINT_TYPE')
        address = urlsplit(endpoint)
        if address.scheme != 'https' or address.hostname != 'mcp.exa.ai' or address.username or address.password:
            raise RuntimeFault('WEBSEARCH_ENDPOINT_REJECTED')
        timeout = config['timeout_seconds']
        if type(timeout) is not int or not 1 <= timeout <= 120:
            raise RuntimeFault('WEBSEARCH_TIMEOUT_RANGE')
        return cls(endpoint, timeout)


def rpc(client: httpx.Client, endpoint: str, method: str, identifier: int,
        params: Json) -> dict[str, Json]:
    response = client.post(endpoint, json={'jsonrpc': '2.0', 'id': identifier,
                                          'method': method, 'params': params})
    response.raise_for_status()
    session = response.headers.get('mcp-session-id')
    if session:
        client.headers['Mcp-Session-Id'] = session
    if response.headers.get('content-type', '').startswith('text/event-stream'):
        messages = [line[5:].strip() for line in response.text.splitlines() if line.startswith('data:')]
        # MCP 不是冻结业务消息；小数元数据不参与协议判断，保留为文本而非套用整数契约。
        replies = [json_object(json.loads(message, parse_float=str)) for message in messages if message != '[DONE]']
        matches = [reply for reply in replies if reply.get('id') == identifier]
        if len(matches) != 1:
            raise RuntimeFault('MCP_RESPONSE_ID')
        result = matches[0]
    else:
        result = json_object(json.loads(response.content, parse_float=str))
    if result.get('id') != identifier or result.get('jsonrpc') != '2.0' or 'error' in result:
        raise RuntimeFault('MCP_RPC_REJECTED')
    return json_object(result['result'])


def search(config: SearchConfig, query: str, objective: str, count: int = 5,
           transport: httpx.BaseTransport | None = None) -> dict[str, Json]:
    if not query.strip() or not objective.strip() or not 1 <= count <= 10:
        raise RuntimeFault('WEBSEARCH_INPUT')
    with httpx.Client(timeout=config.timeout_seconds, follow_redirects=False, trust_env=False,
                      transport=transport, headers={'Accept': 'application/json, text/event-stream',
                                                    'Content-Type': 'application/json'}) as client:
        initialized = rpc(client, config.endpoint, 'initialize', 1, {
            'protocolVersion': '2024-11-05', 'capabilities': {},
            'clientInfo': {'name': 'f1ap-agent', 'version': '0.1.0'}})
        protocol = json_string(initialized['protocolVersion'])
        if protocol != '2024-11-05':
            raise RuntimeFault('MCP_PROTOCOL_UNSUPPORTED')
        client.headers['MCP-Protocol-Version'] = protocol
        client.post(config.endpoint, json={'jsonrpc': '2.0', 'method': 'notifications/initialized'}).raise_for_status()
        tools = json_array(rpc(client, config.endpoint, 'tools/list', 2, {})['tools'])
        tool = next((json_object(item) for item in tools
                     if json_object(item).get('name') == 'web_search_exa'), None)
        if tool is None:
            raise RuntimeFault('WEBSEARCH_TOOL_UNAVAILABLE')
        arguments: dict[str, Json] = {'query': query, 'numResults': count}
        properties = json_object(json_object(tool['inputSchema'])['properties'])
        if 'objective' in properties:
            arguments['objective'] = objective
        else:
            arguments['query'] = query + '\n' + objective
        result = rpc(client, config.endpoint, 'tools/call', 3,
                     {'name': 'web_search_exa', 'arguments': arguments})
        if result.get('isError') is True:
            raise RuntimeFault('WEBSEARCH_TOOL_FAILED')
        texts = [json_string(item['text']) for value in json_array(result['content'])
                 if (item := json_object(value)).get('type') == 'text']
        if not texts:
            raise RuntimeFault('WEBSEARCH_CONTENT_MISSING')
        return {'status': 'AWAITING_HUMAN_CONFIRMATION', 'provider': 'exa', 'query': query,
                'content': texts, 'imported': False, 'pages_fetched': False}


def main() -> int:
    parser = argparse.ArgumentParser(description='通过 Exa MCP 搜索来源候选；不读取题源、不入库')
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--query', required=True)
    parser.add_argument('--objective', default='优先官方 F1 Predict 当前分站页面及题目来源；排除预测文章，不推断缺失题目。')
    parser.add_argument('--num-results', type=int, default=5)
    args = parser.parse_args()
    try:
        result = search(SearchConfig.load(args.config), args.query, args.objective, args.num_results)
    except (RuntimeFault, json.JSONDecodeError, httpx.HTTPError, OSError, tomllib.TOMLDecodeError, KeyError) as error:
        # 不输出可能包含密钥的 MCP URL 或底层异常正文。
        code = error.code if isinstance(error, RuntimeFault) else type(error).__name__
        print(json.dumps({'status': 'FAILED', 'error': code, 'imported': False}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
