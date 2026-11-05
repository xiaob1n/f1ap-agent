"""单 host、固定 ID、固定七个 GET 请求的准备客户端。"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlsplit

import httpx

from f1ap_agent.data.openf1.boundary import MAX_BYTES, decode
from f1ap_agent.data.openf1.config import MappingConfig
from f1ap_agent.runtime import Json, RuntimeFault, json_array, json_object


NO_RESULTS = b'{"detail":"No results found."}'


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


@dataclass(frozen=True, slots=True)
class Response:
    endpoint: str
    raw: bytes
    first_observed_at: str
    rows: tuple[dict[str, Json], ...]


class OpenF1Client:
    """只有准备命令使用；transport 和时钟支持确定性本地测试。"""

    def __init__(self, base_url: str, *, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], str] = utc_now) -> None:
        address = urlsplit(base_url)
        if (address.scheme not in ('http', 'https') or not address.hostname or address.username
            or address.password or address.query or address.fragment or address.path.rstrip('/') != '/v1'):
            raise RuntimeFault('OPENF1_BASE_URL')
        self.base_url: str = base_url.rstrip('/')
        self.transport: httpx.BaseTransport | None = transport
        self.clock: Callable[[], str] = clock

    def collect(self, config: MappingConfig) -> tuple[Response, ...]:
        responses: list[Response] = []
        try:
            with httpx.Client(transport=self.transport, timeout=httpx.Timeout(10, connect=5),
                              limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
                              follow_redirects=False, trust_env=False) as client:
                for endpoint, _ in config.endpoints:
                    scope = config.qualifying if endpoint == 'session_result' and config.qualifying else config.feature
                    if endpoint == 'pit' and config.pit_session:
                        scope = config.pit_session
                    params = {'meeting_key': str(scope.meeting_key),
                              'session_key': str(scope.session_key)}
                    time_field = 'date_start' if endpoint == 'laps' else 'date'
                    if endpoint in ('laps', 'weather', 'race_control', 'pit'):
                        # OpenF1 比较符放在参数名末尾，等号由 URL 编码器添加。
                        if not (endpoint == 'pit' and config.pit_session):
                            params[time_field + '>'] = config.event_start_at
                        params[time_field + '<'] = config.event_cutoff_at
                    with client.stream('GET', self.base_url + '/' + endpoint, params=params) as response:
                        # 公共 OpenF1 对空结果返回精确的 404 No results found；只有这一种规范化为空数组
                        if response.status_code == 404 and response.read() == NO_RESULTS:
                            responses.append(Response(endpoint, b'[]', self.clock(), ()))
                            continue
                        if response.status_code != 200:
                            raise RuntimeFault('OPENF1_HTTP_STATUS:' + str(response.status_code))
                        observed = self.clock()
                        content = bytearray()
                        for chunk in response.iter_bytes():
                            content.extend(chunk)
                            if len(content) > MAX_BYTES:
                                raise RuntimeFault('OPENF1_RESPONSE_SIZE')
                        raw = bytes(content)
                        rows = tuple(json_object(row) for row in json_array(decode(raw)))
                        responses.append(Response(endpoint, raw, observed, rows))
        except httpx.HTTPError as error:
            raise RuntimeFault('OPENF1_HTTP_FAILURE') from error
        return tuple(responses)
