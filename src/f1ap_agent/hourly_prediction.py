"""新题开始预测，FP1前12小时、开始后2小时，官方场次开始时停止。"""
import argparse
import json
import os
import signal
import threading
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import FrameType
from uuid import NAMESPACE_URL, uuid5

import httpx

from f1ap_agent.data.openf1.boundary import decode
from f1ap_agent.question_sync import sync_once
from f1ap_agent.mongo_research.config import Config
from f1ap_agent.mongo_research.output import failure_code
from f1ap_agent.practice_schedule import instant, load_practice
from f1ap_agent.runtime import Json, RuntimeFault, Store, atomic_json, json_integer, json_object, json_string, read_json


def prediction_window(now: datetime, first_seen: datetime, practice: datetime,
                      deadline: datetime) -> tuple[datetime | None, datetime]:
    """只执行当前窗口，不补跑停机窗口；晚于FP1才出题仍立即执行一次。"""
    if now >= deadline:
        return None, deadline
    before = now < practice
    anchor = first_seen if before else practice
    interval = timedelta(hours=12 if before else 2)
    start = anchor + ((now - anchor) // interval) * interval
    end = min(start + interval, practice if before else deadline, deadline)
    return max(start, first_seen), end


def predict_once(client: httpx.Client, api_url: str, token: str,
                 reference_session_key: int, now: datetime, state_path: Path,
                 research: Config) -> dict[str, Json]:
    headers = {'Authorization': 'Bearer ' + token}
    state: dict[str, Json] = json_object(read_json(state_path)) if state_path.exists() else {}
    next_poll = now + timedelta(minutes=30)
    # 先处理已经知道的截止；官网暂时不可用或已切站也不能让旧轮继续预测。
    if state and now >= instant(state['deadline_at']) and not state['locked']:
        locked = client.post(f"{api_url}/internal/v2/rounds/{json_string(state['round_id'])}/predictions/lock", headers=headers)
        _ = locked.raise_for_status()
        state['locked'] = True
        atomic_json(state_path, state)
    synced = sync_once(client, api_url, token, now)
    if synced['status'] == 'SKIPPED_PROTECTED_ROUND':
        return {**synced, 'next_run_at': next_poll.isoformat()}
    round_id = json_string(json_object(synced['response'])['round_id'])
    endpoint = f'{api_url}/internal/v2/rounds/{round_id}/predictions'
    definition = client.get(endpoint + '/definition', headers=headers)
    _ = definition.raise_for_status()
    body = json_object(decode(definition.content))
    details = json_object(body['definition'])
    if details['deadline_verified'] is not True or details['deadline_at'] is None:
        return {'status': 'WAITING_DEADLINE', 'next_run_at': next_poll.isoformat()}
    deadline = instant(details['deadline_at'])
    definition_hash = json_string(body['definition_sha256'])
    if state.get('round_id') != round_id or state.get('definition_sha256') != definition_hash:
        state = {'round_id': round_id, 'definition_sha256': definition_hash,
            'first_seen_at': now.isoformat(), 'deadline_at': deadline.isoformat(), 'locked': False, 'requests': {}}
        atomic_json(state_path, state)
    if now >= deadline:
        locked = client.post(endpoint + '/lock', headers=headers)
        _ = locked.raise_for_status()
        state['locked'] = True
        atomic_json(state_path, state)
        return {'status': 'LOCKED', 'round_id': round_id, 'next_run_at': next_poll.isoformat()}
    practice = load_practice(json_object(synced['fixture']), now, reference_session_key, research)
    start, end = prediction_window(now, instant(state['first_seen_at']), practice.first_practice_at, deadline)
    next_at = min(next_poll, end)
    if start is None:
        raise RuntimeFault('PREDICTION_WINDOW_CLOSED')
    request_id = str(uuid5(NAMESPACE_URL, f'f1ap:race:{round_id}:{definition_hash}:{start.isoformat()}'))
    existing = client.get(f'{endpoint}/{request_id}', headers=headers)
    if existing.status_code == 200:
        return {'status': 'ALREADY_SCHEDULED', 'request_id': request_id, 'next_run_at': next_at.isoformat()}
    if existing.status_code != 404:
        _ = existing.raise_for_status()
    requests = json_object(state['requests'])
    if request_id in requests:
        return {'status': 'SUBMISSION_NOT_RETRIED', 'request_id': request_id, 'next_run_at': next_at.isoformat()}
    if practice.reference_session_key is None:
        return {'status': 'WAITING_PRACTICE_DATA', 'next_run_at': next_at.isoformat()}
    request: dict[str, Json] = {
        'schema_version': 'ANSWERS_V2', 'request_id': request_id,
        'definition_sha256': definition_hash,
        'reference_session_key': practice.reference_session_key,
        'data_cutoff_at': now.isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
        'expires_at': min(deadline, now + timedelta(minutes=15)).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
        'model': 'deepseek-flash'}
    requests[request_id] = request
    state['requests'] = requests
    atomic_json(state_path, state)
    submitted = client.post(endpoint, headers=headers, json=request)
    _ = submitted.raise_for_status()
    return {'status': 'SCHEDULED', 'request_id': request_id,
            'next_run_at': next_at.isoformat(),
            'response': json_object(decode(submitted.content))}


class Options(argparse.Namespace):
    config: Path = Path('config/hourly-prediction.toml')
    state_dir: Path = Path('runtime/scheduler')
    once: bool = False


def main() -> int:
    parser = argparse.ArgumentParser(description='每30分钟查新题；新题立即预测，FP1前12小时、之后2小时，截止锁定')
    _ = parser.add_argument('--config', type=Path, default=Path('config/hourly-prediction.toml'))
    _ = parser.add_argument('--once', action='store_true')
    _ = parser.add_argument('--state-dir', type=Path, default=Path('runtime/scheduler'))
    args = parser.parse_args(namespace=Options())
    with args.config.open('rb') as stream:
        config = json_object(tomllib.load(stream))
    api_url = json_string(config['api_base_url'])
    if api_url not in ('http://127.0.0.1:8080', 'http://localhost:8080', 'http://api:8080'):
        raise RuntimeFault('PREDICTION_API_URL_REJECTED')
    reference = json_integer(config['reference_session_key'])
    timeout = json_integer(config['timeout_seconds'])
    if reference <= 0 or not 1 <= timeout <= 120:
        raise RuntimeFault('PREDICTION_CONFIG_INVALID')
    token = os.environ.get('F1AP_INTERNAL_WRITE_TOKEN')
    token_file = os.environ.get('F1AP_INTERNAL_WRITE_TOKEN_FILE')
    if token_file:
        if token:
            raise RuntimeFault('PREDICTION_TOKEN_CONFLICT')
        token = Path(token_file).read_text().strip()
    if not token:
        raise RuntimeFault('PREDICTION_TOKEN_REQUIRED')
    from f1ap_agent.nacos import load
    startup = load()
    research = Config(timeout_seconds=timeout)
    if startup is not None:
        # 调度器只取Mongo只读连接，不安装模型或MQ凭据。
        os.environ['MDB_MCP_CONNECTION_STRING'] = startup.secrets['MDB_MCP_CONNECTION_STRING']
        research = startup.research
    args.state_dir.mkdir(parents=True, exist_ok=True)
    stopped = threading.Event()
    def stop(_number: int, _frame: FrameType | None) -> None:
        stopped.set()
    _ = signal.signal(signal.SIGINT, stop)
    _ = signal.signal(signal.SIGTERM, stop)
    with Store(args.state_dir).exclusive(), httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        while not stopped.is_set():
            try:
                result = predict_once(client, api_url, token, reference, datetime.now(timezone.utc), args.state_dir / 'schedule.json', research)
            except (RuntimeFault, httpx.HTTPError, ValueError, KeyError, OSError, ExceptionGroup) as error:
                result = {'status': 'FAILED', 'error': failure_code(error)}
            now = datetime.now(timezone.utc)
            next_at = instant(result['next_run_at']) if 'next_run_at' in result else now + timedelta(minutes=30)
            result['next_run_at'] = next_at.isoformat()
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if args.once:
                return 1 if result['status'] == 'FAILED' else 0
            _ = stopped.wait(max(0, (next_at - now).total_seconds()))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
