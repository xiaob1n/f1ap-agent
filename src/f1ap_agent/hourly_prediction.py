"""按北京时间的工作日与周末窗口预测官网当前分站。"""
import argparse
import json
import os
import signal
import threading
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import httpx

from f1ap_agent.data.openf1.boundary import decode
from f1ap_agent.question_sync import sync_once
from f1ap_agent.runtime import Json, RuntimeFault, json_integer, json_object, json_string


BEIJING = timezone(timedelta(hours=8))


def prediction_window(now: datetime) -> tuple[datetime | None, datetime]:
    """返回当前可执行窗口及下次计划时刻；周日不补跑。"""
    local = now.astimezone(BEIJING)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if local.weekday() == 6:
        return None, (midnight + timedelta(days=1)).astimezone(timezone.utc)
    interval = 12 if local.weekday() < 4 else 2
    start = midnight + timedelta(hours=local.hour // interval * interval)
    end = start + timedelta(hours=interval)
    next_at = end
    if end.weekday() == 6:
        next_at = end + timedelta(days=1)
    return start.astimezone(timezone.utc), next_at.astimezone(timezone.utc)


def predict_once(client: httpx.Client, api_url: str, token: str,
                 reference_session_key: int, now: datetime) -> dict[str, Json]:
    start, next_at = prediction_window(now)
    if start is None or now.astimezone(timezone.utc) >= start + timedelta(minutes=1):
        return {'status': 'WAITING_SCHEDULE', 'next_run_at': next_at.isoformat()}
    synced = sync_once(client, api_url, token, now)
    if synced['status'] == 'SKIPPED_PROTECTED_ROUND':
        return synced
    round_id = json_string(json_object(synced['response'])['round_id'])
    # 保留原小时身份命名，使相同整点重启与旧版任务仍能去重。
    request_id = str(uuid5(NAMESPACE_URL, f'f1ap:hourly:{round_id}:{start.isoformat()}'))
    endpoint = f'{api_url}/internal/v2/rounds/{round_id}/predictions'
    headers = {'Authorization': 'Bearer ' + token}
    existing = client.get(f'{endpoint}/{request_id}', headers=headers)
    if existing.status_code == 200:
        return {'status': 'ALREADY_SCHEDULED', 'request_id': request_id,
                'response': json_object(decode(existing.content))}
    if existing.status_code != 404:
        existing.raise_for_status()
    definition = client.get(endpoint + '/definition', headers=headers)
    definition.raise_for_status()
    body = json_object(decode(definition.content))
    submitted = client.post(endpoint, headers=headers, json={
        'schema_version': 'ANSWERS_V2', 'request_id': request_id,
        'definition_sha256': json_string(body['definition_sha256']),
        'reference_session_key': reference_session_key,
        'data_cutoff_at': start.isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
        # 周六最后一轮到周日零点到期，不将执行窗口延长到周一。
        'expires_at': min(next_at, start + timedelta(hours=12 if start.astimezone(BEIJING).weekday() < 4 else 2)).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
        'model': 'deepseek-flash'})
    submitted.raise_for_status()
    return {'status': 'SCHEDULED', 'request_id': request_id,
            'response': json_object(decode(submitted.content))}


def main() -> int:
    parser = argparse.ArgumentParser(description='北京时间周一至周四每12小时、周五至周六每2小时，周日不预测')
    parser.add_argument('--config', type=Path, default=Path('config/hourly-prediction.toml'))
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    with args.config.open('rb') as stream:
        config = tomllib.load(stream)
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
    stopped = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        while not stopped.is_set():
            try:
                result = predict_once(client, api_url, token, reference, datetime.now(timezone.utc))
            except (RuntimeFault, httpx.HTTPError, ValueError, KeyError) as error:
                result = {'status': 'FAILED', 'error': error.code if isinstance(error, RuntimeFault) else type(error).__name__}
            now = datetime.now(timezone.utc)
            _, next_at = prediction_window(now)
            result['next_run_at'] = next_at.isoformat()
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if args.once:
                return 1 if result['status'] == 'FAILED' else 0
            stopped.wait((next_at - now).total_seconds())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
