"""按官网首页的当前赛事选择规则同步题目，API 保持业务库唯一写入方。"""
import argparse
import json
import os
import signal
import threading
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import FrameType
from typing import Final
from urllib.parse import urlsplit

import httpx

from f1ap_agent.contracts.verifier import Rejection
from f1ap_agent.contracts.answers import VERSION, definition as answer_definition
from f1ap_agent.data.openf1.boundary import decode
from f1ap_agent.runtime import Json, RuntimeFault, json_array, json_integer, json_object, json_string

SOURCE: Final = 'https://f1predict.formula1.com'
INTERVAL_SECONDS: Final = 1800


def current_fixture(raw: bytes) -> dict[str, Json]:
    """与首页选择函数相同：排除额外赛季题，按比赛状态优先级选首条。"""
    root = json_object(decode(raw))
    fixtures = [json_object(value) for value in json_array(json_object(root['Data'])['Value'])]
    for status in ('2', '3', '5', '1', '0'):
        for fixture in fixtures:
            if fixture.get('IsBonusRound', 0) == 0 and str(fixture.get('MatchStatus')) == status:
                return fixture
    raise RuntimeFault('CURRENT_ROUND_UNAVAILABLE')


def project(fixture: dict[str, Json], raw: bytes, now: datetime) -> dict[str, Json]:
    """只投影题目、候选及可见选择形状，答案和Chance不进入导入请求。"""
    gameday = json_integer(fixture['GamedayId'])
    tour = json_integer(fixture['TourId'])
    season_text = json_string(fixture['Season'])
    if not season_text.isdigit():
        raise RuntimeFault('SOURCE_SEASON_INVALID')
    season = int(season_text)
    source_url = f'{SOURCE}/feeds/questions/questions_{gameday}_en.json'
    root = json_object(decode(raw))
    rows = json_array(json_object(json_object(root['Data'])['Value'])['Questions'])
    questions: list[Json] = []
    for value in rows:
        question = json_object(value)
        identifier = json_integer(question['Id'])
        template = json_integer(question['OptionTemplateId'])
        text = json_string(question['Text'])
        subtext = json_string(question.get('SubText', ''))
        picks = json_integer(json_object(question['Config'])['ChoiceLimit'])
        options: list[Json] = []
        for candidate in json_array(question['Options']):
            option = json_object(candidate)
            source_id = str(json_integer(option['Id']))
            match json_integer(option['Type']):
                case 1:
                    source_type = 'DRIVER'
                case 2:
                    source_type = 'CONSTRUCTOR'
                case 3:
                    source_type = 'CHOICE'
                case _:
                    raise RuntimeFault('SOURCE_OPTION_TYPE')
            options.append({'option_key': f'{template}:{source_id}', 'source_id': source_id,
                            'source_type': source_type, 'label': json_string(option['Value'])})
        questions.append({'question_id': str(identifier), 'source_question_id': str(identifier),
            'no': json_integer(question['No']), 'text': '\n'.join(part for part in (text, subtext) if part),
            'value_kind': 'OPTION', 'options': options,
            'rule': {'min_picks': picks, 'max_picks': picks, 'ordered': template == 1}})
    questions.sort(key=lambda value: json_integer(json_object(value)['no']))
    definition: dict[str, Json] = {'schema_version': VERSION, 'round_id': str(gameday), 'definition_version': 1,
        'name': f"{season} {json_string(fixture['MeetingName'])}", 'deadline_at': None,
        'deadline_verified': False,
        'deadline_basis': 'UNVERIFIED: page countdown is not an independently verified absolute cutoff',
        'questions': questions}
    answer_definition(definition)
    return {'fixture_class': 'CONTROLLED_INPUT',
            'source': {'source_id': source_url, 'observed_at': now.isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
                       'permission_ref': 'USER_AUTHORIZED_PERIODIC_CURRENT_ROUND'},
            'round': {'source_system': 'CONTROLLED_RESEARCH', 'season_year': season,
                      'source_tour_id': str(tour), 'source_gameday_id': str(gameday), 'status': 'DRAFT'},
            'definition': definition}


def sync_once(client: httpx.Client, api_url: str, token: str, now: datetime) -> dict[str, Json]:
    """完整验证后才调用同步入口，409表示API行锁内保护轮次而非重试写入。"""
    schedule = client.get(f'{SOURCE}/feeds/schedule/raceday_en.json')
    _ = schedule.raise_for_status()
    fixture = current_fixture(schedule.content)
    gameday = json_integer(fixture['GamedayId'])
    feed = client.get(f'{SOURCE}/feeds/questions/questions_{gameday}_en.json')
    _ = feed.raise_for_status()
    payload = project(fixture, feed.content, now)
    result = client.post(api_url + '/internal/v1/round-syncs', headers={'Authorization': 'Bearer ' + token},
                         json=payload)
    if result.status_code == 409:
        return {'status': 'SKIPPED_PROTECTED_ROUND', 'source_gameday_id': str(gameday)}
    _ = result.raise_for_status()
    saved = json_object(decode(result.content))
    return {'status': 'UNCHANGED' if saved['idempotent'] is True else 'IMPORTED',
            'source_gameday_id': str(gameday), 'gameplay_url': f'{SOURCE}/en/gameplay/{gameday}',
            'definition_url': f"{api_url}/internal/v2/rounds/{json_string(saved['round_id'])}/predictions/definition",
            'response': saved}


class Options(argparse.Namespace):
    config: Path = Path()
    once: bool = False


def main() -> int:
    parser = argparse.ArgumentParser(description='启动即同步，随后每30分钟同步官网当前分站；不启动预测Worker')
    _ = parser.add_argument('--config', default=Path('config/question-sync.toml'), type=Path)
    _ = parser.add_argument('--once', action='store_true')
    args = parser.parse_args(namespace=Options())
    with args.config.open('rb') as stream:
        config = json_object(decode(json.dumps(tomllib.load(stream)).encode()))
    api_url = json_string(config['api_base_url'])
    address = urlsplit(api_url)
    if address.scheme != 'http' or address.hostname not in ('127.0.0.1', 'localhost') or address.username or address.password or address.query or address.path:
        raise RuntimeFault('SYNC_API_URL_REJECTED')
    timeout = json_integer(config['timeout_seconds'])
    if not 1 <= timeout <= 120:
        raise RuntimeFault('SYNC_TIMEOUT_RANGE')
    token = os.environ.get('F1AP_INTERNAL_WRITE_TOKEN')
    if not token:
        raise RuntimeFault('SYNC_TOKEN_REQUIRED')
    stopped = threading.Event()
    def stop(_number: int, _frame: FrameType | None) -> None:
        stopped.set()
    _ = signal.signal(signal.SIGINT, stop)
    _ = signal.signal(signal.SIGTERM, stop)
    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        while not stopped.is_set():
            result: dict[str, Json]
            try:
                result = sync_once(client, api_url, token, datetime.now(timezone.utc))
            except (RuntimeFault, Rejection, httpx.HTTPError, KeyError) as error:
                code = error.code if isinstance(error, RuntimeFault) else type(error).__name__
                result = {'status': 'FAILED', 'error': code}
            next_at = datetime.now(timezone.utc) + timedelta(seconds=INTERVAL_SECONDS)
            result['next_run_at'] = next_at.isoformat(timespec='seconds')
            result['interval_seconds'] = INTERVAL_SECONDS
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if args.once:
                return 1 if result['status'] == 'FAILED' else 0
            _ = stopped.wait(INTERVAL_SECONDS)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
