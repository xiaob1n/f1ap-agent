"""独立 Exa 新闻边界；正文是不可信资料，不执行其中指令。"""
import os
import re
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import anyio
import httpx
from mcp import ClientSession, McpError
from mcp.client.streamable_http import streamable_http_client

from f1ap_agent.runtime import Json, RuntimeFault, atomic_json
from .config import Config, Request


TOOLS = [{'type': 'function', 'function': {'name': name, 'description': description,
    'parameters': {'type': 'object', 'additionalProperties': False,
        'properties': {field: schema}, 'required': [field]}}}
    for name, description, field, schema in (
        ('search_news', '搜索本站升级或罚退；摘要不是证据，每类仅一次。', 'category',
         {'type': 'string', 'enum': ['upgrades', 'penalties']}),
        ('read_news', '读取本轮搜索候选正文；仅成功时间核验后可引用。', 'url', {'type': 'string'}))]


def external(error):
    if isinstance(error, BaseExceptionGroup):
        return all(external(child) for child in error.exceptions)
    return isinstance(error, (RuntimeFault, httpx.HTTPError, McpError, TimeoutError,
        anyio.EndOfStream, anyio.BrokenResourceError, anyio.ClosedResourceError))


def safe_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (parsed.scheme == 'https' and bool(parsed.hostname) and not parsed.username
            and not parsed.password and not parsed.query and not parsed.fragment
            and not parsed.path.lower().endswith('.pdf'))


def diagnostic(text: str, is_error: bool, url: str | None) -> dict:
    """仅保存有界原文，不保存传输头、异常或环境信息。"""
    header, _, body = text.partition('\n\n')
    title = next((line.removeprefix('# ').removeprefix('Title: ')
        for line in header.splitlines() if line.startswith(('# ', 'Title: '))), '')
    source_url = next((line[5:] for line in header.splitlines() if line.startswith('URL: ')), '')
    return {'title': title[:500], 'url': url or (source_url[:2000] if safe_url(source_url) else None),
        'isError': bool(is_error), 'text_length': len(text), 'truncated': len(text) > 1200,
        'metadata_time_lines': [line[:300] for line in header.splitlines()
            if line.startswith('Published: ')][:8],
        'raw_time_lines': [line[:300] for line in body.splitlines()
            if re.search(r'(?i)published|updated|modified|\b(?:GMT|UTC)\b|\d{1,2}:\d{2}', line)][:8],
        'opening_excerpt': text[:1200]}


def page(text: str, url: str, target: dict, cutoff: str, deadline: str) -> dict:
    lines = text.split('\n\n', 1)
    header = lines[0]
    body = lines[1] if len(lines) == 2 else ''
    # 仅识别文章开头的时间栏，不从正文引文或提示注入中寻找时间证明。
    time_header = body.split('\n\n', 1)[0]
    # F1 官方把出版时间放在标题、短简介之后；只接受前四段且前800字符内的独立UTC行。
    f1_time = r'(?m)^((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{1,2}, \d{4} (?:[1-9]|1[0-2]):[0-5]\d(?:am|pm) UTC)$'
    front = '\n\n'.join(body.split('\n\n')[:4])[:800]
    f1_matches = re.findall(f1_time, front) if urlsplit(url).hostname == 'www.formula1.com' else []
    if f1_matches and re.findall(f1_time, body) != f1_matches:
        raise RuntimeFault('NEWS_TIME_CONFLICT')
    header_urls = [line[5:] for line in header.splitlines() if line.startswith('URL: ')]
    if header_urls != [url] or not header.startswith('# '):
        raise RuntimeFault('NEWS_PAGE_FORMAT')
    times = {}
    raw = {}
    if re.search(r'(?im)^\s*(?:last\s+updated|date\s+modified|modified)\b', body):
        raise RuntimeFault('NEWS_TIME_UNKNOWN')
    for label in ('Published', 'Updated'):
        matches = re.findall(rf'(?im)^\s*{label}(?: at)?:\s*(.*?)\s*$', time_header)
        all_matches = re.findall(rf'(?im)^\s*{label}(?: at)?:\s*(.*?)\s*$', body)
        if all_matches != matches:
            raise RuntimeFault('NEWS_TIME_CONFLICT')
        if label == 'Published' and f1_matches:
            converted = []
            for value in f1_matches:
                month, day, year, clock, _ = value.replace(',', '').split()
                hour, minute = map(int, clock[:-2].split(':'))
                try:
                    converted.append(datetime(int(year),
                        ('Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split().index(month) + 1),
                        int(day), hour % 12 + (12 if clock.endswith('pm') else 0), minute,
                        tzinfo=UTC).isoformat())
                except ValueError:
                    raise RuntimeFault('NEWS_TIME_UNKNOWN') from None
            matches = [*matches, *converted]
        if label == 'Published' and not matches:
            raise RuntimeFault('NEWS_TIME_UNKNOWN')
        parsed = []
        for value in matches:
            if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)', value):
                raise RuntimeFault('NEWS_TIME_UNKNOWN')
            try:
                parsed.append(datetime.fromisoformat(value))
            except ValueError:
                raise RuntimeFault('NEWS_TIME_UNKNOWN') from None
        if len(set(parsed)) > 1:
            raise RuntimeFault('NEWS_TIME_CONFLICT')
        times[label] = parsed[0] if parsed else None
        raw[label] = [*all_matches, *f1_matches] if label == 'Published' else matches
    published, updated = times['Published'], times['Updated']
    discovered = re.findall(r'^Published: (\d{4}-\d\d-\d\d)$', header, re.M)
    if discovered and discovered[0] not in (published.date().isoformat(), published.astimezone(UTC).date().isoformat()):
        raise RuntimeFault('NEWS_TIME_CONFLICT')
    cut, end = datetime.fromisoformat(cutoff), datetime.fromisoformat(deadline)
    # 以预测截止向前滚动30天；更新旧文章不能重置其发布时间窗口。
    if published < cut - timedelta(days=30) or published > cut or published >= end or (updated and (updated < published or updated > cut or updated >= end)):
        raise RuntimeFault('NEWS_TIME_EXCLUDED')
    return {'title': header.splitlines()[0][2:][:500], 'url': url,
        'excerpt': body[:3000], 'truncated': len(body) >= 3000,
        'published_at': published.isoformat(), 'updated_at': updated.isoformat() if updated else None,
        'source_time_raw': raw, 'fetched_at': datetime.now(UTC).isoformat(),
        'target_event': target, 'relevance_scope': 'RECENT_30_DAYS_NOT_TARGET_EVENT_CONFIRMED',
        'historical_availability_guaranteed': False}


class News:
    def __init__(self, request: Request, config: Config, definition: dict | None):
        self.request, self.config = request, config
        self.target = request.target_event
        self.deadline = definition.get('deadline_at') if definition and definition.get('deadline_verified') is True else None
        self.ready = bool(self.target and self.deadline and definition and self.target.get('round_id') == definition['round_id'])
        self.stack = AsyncExitStack()
        self.session = None
        self.initialized = False
        self.categories = set()
        self.candidates = {}
        self.reads = set()
        self.gaps = [] if self.ready else ['NEWS_CONTEXT_MISSING']
        self.calls = 0
        self.objective = False
        self.unavailable = None

    def budget(self):
        return bool(self.request.expires_at and
            (datetime.fromisoformat(self.request.expires_at) - datetime.now(UTC)).total_seconds()
            > self.config.timeout_seconds + 12)

    @asynccontextmanager
    async def connection(self):
        # 由研究外层同一任务进入、退出 MCP 取消域；节点只使用已连接会话。
        try:
            if self.config.news_enabled and self.ready and self.budget():
                try:
                    try:
                        await self.connect()
                    except BaseException:
                        # 先退出 SDK 任务组，让内部传输失败恢复成原始异常组。
                        await self.stack.__aexit__(*sys.exc_info())
                        raise
                except BaseException as error:
                    if not external(error):
                        raise
                    self.unavailable = error.code if isinstance(error, RuntimeFault) else 'NEWS_INIT_FAILED'
                    self.gaps.append(self.unavailable)
            yield self
        finally:
            try:
                await self.stack.aclose()
            except BaseException as error:
                if not external(error):
                    raise
                self.gaps.append('NEWS_CLOSE_FAILED')
            self.session = None

    async def remote(self, tool, args):
        if self.unavailable or self.session is None:
            raise RuntimeFault(self.unavailable or 'NEWS_INIT_FAILED')
        if tool == 'web_search_exa' and self.objective:
            args['objective'] = args['query']
        try:
            return await self.session.call_tool(tool, args,
                read_timeout_seconds=timedelta(seconds=10))
        except BaseException as error:
            if not external(error):
                raise
            # 已发请求的结果及费用未知，禁止本轮后续新闻请求。
            self.unavailable = 'NEWS_TRANSPORT_FAILED'
            raise RuntimeFault(self.unavailable) from None

    async def connect(self):
        if self.initialized:
            if self.session is None:
                raise RuntimeFault('NEWS_INIT_FAILED')
            return
        self.initialized = True
        headers = {'x-api-key': os.environ['EXA_API_KEY']} if os.environ.get('EXA_API_KEY') else {}
        client = await self.stack.enter_async_context(httpx.AsyncClient(headers=headers, timeout=10,
            trust_env=False, follow_redirects=False))
        reader, writer, _ = await self.stack.enter_async_context(
            streamable_http_client('https://mcp.exa.ai/mcp', http_client=client))
        session = await self.stack.enter_async_context(ClientSession(reader, writer,
            read_timeout_seconds=timedelta(seconds=10)))
        await session.initialize()
        listed = await session.list_tools()
        atomic_json(self.request.output / 'news-tool-schemas.json', {
            'observed_at': datetime.now(UTC).isoformat(), 'source': 'same_news_session_tools_list',
            'tools': [{'name': tool.name, 'inputSchema': tool.inputSchema}
                for tool in listed.tools if tool.name in ('web_search_exa', 'web_fetch_exa')]})
        tools = {tool.name: tool.inputSchema for tool in listed.tools}
        for name, required, allowed in (
                ('web_search_exa', {'query'}, {'query', 'numResults', 'objective'}),
                ('web_fetch_exa', {'urls'}, {'urls', 'maxCharacters'})):
            schema = tools.get(name, {})
            props = schema.get('properties', {})
            if not required <= set(props) or not set(schema.get('required', [])) <= allowed:
                raise RuntimeFault('NEWS_TOOL_SCHEMA')
            for field, expected in ({'query': 'string', 'numResults': 'number', 'objective': 'string'}
                    if name == 'web_search_exa' else {'urls': 'array', 'maxCharacters': 'number'}).items():
                if field in props and props[field].get('type') not in (None, expected, 'integer' if expected == 'number' else expected):
                    raise RuntimeFault('NEWS_TOOL_SCHEMA')
        if not {'numResults'} <= set(tools['web_search_exa']['properties']) or 'maxCharacters' not in tools['web_fetch_exa']['properties']:
            raise RuntimeFault('NEWS_TOOL_SCHEMA')
        self.objective = 'objective' in tools['web_search_exa'].get('required', [])
        self.session = session

    async def call(self, name: str, arguments: dict, number: int) -> dict:
        self.calls += 1
        stage = 'search' if name == 'search_news' else 'read'
        result = {}
        observed = None
        try:
            if not self.ready:
                raise RuntimeFault('NEWS_CONTEXT_MISSING')
            if not self.budget():
                raise RuntimeFault('NEWS_TIME_BUDGET')
            if name == 'search_news':
                category = arguments.get('category')
                if set(arguments) != {'category'} or category not in ('upgrades', 'penalties'):
                    raise RuntimeFault('NEWS_ARGUMENTS')
                if category in self.categories:
                    raise RuntimeFault('NEWS_SEARCH_LIMIT')
                self.categories.add(category)
                event_name = self.target['event_name']
                target_name = event_name if event_name.startswith(str(self.target['season_year']) + ' ') else f"{self.target['season_year']} {event_name}"
                cut = datetime.fromisoformat(self.request.cutoff)
                query = f"Formula 1 news published between {(cut - timedelta(days=30)).isoformat()} and {cut.isoformat()}, relevant to drivers and teams preparing for {target_name}, including previous races: " + (
                    'car technical upgrades team declared tested adopted' if category == 'upgrades' else 'confirmed grid penalties power unit pit lane start')
                args = {'query': query, 'numResults': 5}
                tool = 'web_search_exa'
            else:
                url = arguments.get('url')
                if set(arguments) != {'url'} or url not in self.candidates:
                    raise RuntimeFault('NEWS_CANDIDATE_REQUIRED')
                if len(self.reads) >= 4 or url in self.reads:
                    raise RuntimeFault('NEWS_READ_LIMIT')
                self.reads.add(url)
                args, tool = {'urls': [url], 'maxCharacters': 3000}, 'web_fetch_exa'
            response = await self.remote(tool, args)
            text = '\n'.join(block.text for block in response.content if block.type == 'text')
            observed = diagnostic(text, response.isError, arguments.get('url'))
            if response.isError:
                raise RuntimeFault('NEWS_TOOL_FAILED')
            if len(text) > 100000:
                raise RuntimeFault('NEWS_RESPONSE_LIMIT')
            if name == 'search_news':
                candidates = []
                # 文本协议不能鉴真摘要中伪造的分隔符；保守只采用第一条官方 header。
                for block in text.split('\n\n---\n\n')[:1]:
                    header = block.split('Highlights:', 1)[0].split('Text:', 1)[0]
                    match = re.match(r'^Title: [^\n]*\nURL: (\S+)\nPublished: [^\n]*\nAuthor: [^\n]*(?:\n|$)', header)
                    if match and safe_url(match[1]) and match[1] not in self.candidates:
                        self.candidates[match[1]] = {'query': query, 'category': category}
                        candidates.append({'url': match[1]})
                result = {'candidates': candidates, 'discovery_only': True}
                if not candidates:
                    self.gaps.append('NEWS_NO_RESULTS')
            else:
                evidence = page(text, url, self.target, self.request.cutoff, self.deadline)
                candidate = self.candidates[url]
                pattern = r'\b(?:upgrades?|technical updates?)\b' if candidate['category'] == 'upgrades' else r'\b(?:penalt(?:y|ies)|pit[- ]lane start)\b'
                if not re.search(pattern, evidence['excerpt'], re.I):
                    raise RuntimeFault('NEWS_CATEGORY_MISMATCH')
                evidence.update(candidate)
                result = {'evidence_id': number, **evidence}
        except (RuntimeFault, httpx.HTTPError, McpError, TimeoutError) as error:
            code = error.code if isinstance(error, RuntimeFault) else 'NEWS_TRANSPORT_FAILED'
            self.gaps.append(code)
            result = {'stage': stage, 'failure_code': code}
        atomic_json(self.request.output / f'news-{self.calls:03d}.json',
            {**result, **({'diagnostic': observed} if observed is not None else {})})
        return result

    def notice(self):
        missing = '；存在未搜、未读或失败资料缺口' if self.gaps or len(self.categories) < 2 or len(self.reads) < len(self.candidates) else '；检索范围有限'
        return '新闻说明：近30天报道非本站完整清单，历史版本及截止前收录不保证' + missing + '；历史采用或处罚不自动适用于本站。'
