"""仅启动时读取 Nacos；完整校验后才注入固定业务凭据。"""
import argparse
import json
import os
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

import httpx

from f1ap_agent.models.deepseek import DeepSeek
from f1ap_agent.mongo_research.config import Config
from f1ap_agent.runtime import Json, RuntimeFault, json_integer, json_object, json_string

SECRET_KEYS: Final[set[str]] = {'F1AP_RABBITMQ_HOST', 'F1AP_RABBITMQ_PORT',
    'F1AP_RABBITMQ_VHOST', 'F1AP_RABBITMQ_USERNAME', 'F1AP_RABBITMQ_PASSWORD',
    'DEEPSEEK_API_KEY', 'MDB_MCP_CONNECTION_STRING'}
MQ_KEYS: Final[set[str]] = {'delivery_timeout', 'confirm_timeout',
    'wait_topology_seconds', 'shutdown_timeout'}


@dataclass(frozen=True, slots=True)
class Startup:
    model: DeepSeek
    research: Config
    mq: Mapping[str, int]
    runtime_dir: Path
    mapping: Path | None
    secrets: Mapping[str, str] = field(repr=False)

    def install(self) -> None:
        """只写入已完整校验的固定键，不复制任意远端环境变量。"""
        os.environ.update(self.secrets)


def parse_config(content: str) -> Startup:
    try:
        raw: Json = tomllib.loads(content)
    except (tomllib.TOMLDecodeError, ValueError):
        raise RuntimeFault('NACOS_PARSE_FAILED') from None
    try:
        config = json_object(raw)
        if set(config) != {'model', 'research', 'mq', 'paths', 'secrets'}:
            raise RuntimeFault('FIELDS')
        model = DeepSeek.from_config(json_object(config['model']))
        if model.api_key_env != 'DEEPSEEK_API_KEY':
            raise RuntimeFault('KEY_ENV')
        research = Config.from_config(json_object(config['research']))
        mq = {key: json_integer(value) for key, value in json_object(config['mq']).items()}
        if set(mq) != MQ_KEYS or any(value <= 0 for value in mq.values()):
            raise RuntimeFault('MQ_LIMITS')
        paths = json_object(config['paths'])
        if set(paths) != {'runtime_dir', 'mapping'}:
            raise RuntimeFault('PATH_FIELDS')
        runtime_dir = json_string(paths['runtime_dir'])
        mapping = json_string(paths['mapping'])
        if not runtime_dir.strip() or '\x00' in runtime_dir + mapping:
            raise RuntimeFault('PATH_VALUE')
        secrets = {key: json_string(value) for key, value in json_object(config['secrets']).items()}
        if set(secrets) != SECRET_KEYS or any(not value.strip() or '\x00' in value for value in secrets.values()):
            raise RuntimeFault('SECRET_FIELDS')
        if not 1 <= int(secrets['F1AP_RABBITMQ_PORT']) <= 65535 or secrets['F1AP_RABBITMQ_VHOST'] == '/':
            raise RuntimeFault('MQ_CONNECTION')
        uri = urlsplit(secrets['MDB_MCP_CONNECTION_STRING'])
        if uri.scheme not in ('mongodb', 'mongodb+srv') or not uri.hostname:
            raise RuntimeFault('MONGO_URI')
        return Startup(model, research, mq, Path(runtime_dir), Path(mapping) if mapping else None, secrets)
    except (RuntimeFault, ValueError, TypeError, KeyError):
        raise RuntimeFault('NACOS_CONFIG_INVALID') from None


def load() -> Startup | None:
    """每个入口只调用一次；任一引导变量存在即选择 Nacos，不回退。"""
    if not any(key.startswith('F1AP_NACOS_') for key in os.environ):
        return None
    addr = os.environ.get('F1AP_NACOS_SERVER_ADDR', '')
    group = os.environ.get('F1AP_NACOS_GROUP', '')
    data_id = os.environ.get('F1AP_NACOS_DATA_ID', '')
    username = os.environ.get('F1AP_NACOS_USERNAME', '')
    password = os.environ.get('F1AP_NACOS_PASSWORD', '')
    if (not all((addr, group, data_id, username, password))
            or 'F1AP_NACOS_NAMESPACE' not in os.environ):
        raise RuntimeFault('NACOS_BOOTSTRAP_REQUIRED')
    try:
        base = addr if '://' in addr else 'http://' + addr
        url = urlsplit(base)
        if (url.scheme not in ('http', 'https') or not url.hostname or url.username
                or url.password or url.query or url.fragment or url.path not in ('', '/')):
            raise ValueError
        _ = url.port
    except ValueError:
        raise RuntimeFault('NACOS_BOOTSTRAP_INVALID') from None
    try:
        with httpx.Client(timeout=10.0, trust_env=False, follow_redirects=False) as client:
            login = client.post(base.rstrip('/') + '/nacos/v1/auth/login',
                data={'username': username, 'password': password})
            if login.status_code != 200:
                raise RuntimeFault('NACOS_AUTH_FAILED')
            try:
                auth = json_object(json.loads(login.content))
                token = json_string(auth['accessToken'])
                if not token.strip():
                    raise ValueError
            except (ValueError, RuntimeFault, KeyError, TypeError):
                raise RuntimeFault('NACOS_AUTH_FAILED') from None
            response = client.get(base.rstrip('/') + '/nacos/v1/cs/configs', params={
                'dataId': data_id, 'group': group, 'tenant': os.environ['F1AP_NACOS_NAMESPACE'],
                'accessToken': token})
    except httpx.HTTPError:
        raise RuntimeFault('NACOS_TRANSPORT_FAILED') from None
    if response.status_code in (401, 403):
        raise RuntimeFault('NACOS_AUTH_FAILED')
    if response.status_code == 404:
        raise RuntimeFault('NACOS_CONFIG_NOT_FOUND')
    if response.status_code != 200:
        raise RuntimeFault('NACOS_FETCH_FAILED')
    if not response.text.strip():
        raise RuntimeFault('NACOS_CONFIG_EMPTY')
    return parse_config(response.text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.parse_args()
    try:
        if load() is None:
            raise RuntimeFault('NACOS_BOOTSTRAP_REQUIRED')
        print('NACOS_CONFIG_VALID; startup-only; business services not contacted')
        return 0
    except RuntimeFault as error:
        print(error.code, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
