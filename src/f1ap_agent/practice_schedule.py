"""通过官网明确 session 身份定位同站练习赛；只读 MCP，不调用模型。"""
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import anyio
from mcp import ClientSession

from f1ap_agent.mongo_research.config import Config
from f1ap_agent.mongo_research.mcp_session import connect
from f1ap_agent.runtime import Json, RuntimeFault, json_array, json_integer, json_object, json_string


def instant(value: Json) -> datetime:
    text = json_string(json_object(value)['$date']) if isinstance(value, dict) else json_string(value)
    result = datetime.fromisoformat(text)
    if result.tzinfo is None:
        raise RuntimeFault('SESSION_TIMEZONE_REQUIRED')
    return result.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class PracticeSchedule:
    first_practice_at: datetime
    reference_session_key: int | None


async def inspect(session: ClientSession, fixture: dict[str, Json], now: datetime,
                  historical_session: int, config: Config) -> PracticeSchedule:
    async def find(collection: str, filters: dict[str, Json], projection: dict[str, Json], limit: int) -> list[dict[str, Json]]:
        with anyio.fail_after(config.timeout_seconds):
            response = await session.call_tool('find', {'connectionId': 'preconfigured',
                'database': 'openf1-livetiming', 'collection': collection, 'filter': filters,
                'projection': projection, 'limit': limit, 'responseBytesLimit': config.byte_limit})
        if response.isError:
            raise RuntimeFault('SCHEDULE_MCP_FAILED')
        return [json_object(row) for row in json_array(json_object(response.structuredContent)['documents'])]

    key = int(json_string(fixture['FOMMEETINGSESSIONKEY']))
    projection: dict[str, Json] = {field: 1 for field in
        ('_id', 'session_key', 'meeting_key', 'session_type', 'session_name', 'date_start', 'year')}
    source = await find('sessions', {'_id': key}, projection, 2)
    if (len(source) != 1 or source[0]['session_key'] != key
            or instant(source[0]['date_start']) != instant(fixture['SessionStartDateISO8601'])
            or str(source[0]['year']) != fixture['Season']
            or source[0]['session_type'] != fixture['SessionType']):
        raise RuntimeFault('SCHEDULE_SESSION_MISMATCH')
    meeting = json_integer(source[0]['meeting_key'])
    practices = await find('sessions', {'meeting_key': meeting, 'session_type': 'Practice'}, projection, 10)
    if not practices or len(practices) == 10:
        raise RuntimeFault('PRACTICE_SCHEDULE_UNAVAILABLE')
    if any(row['meeting_key'] != meeting or row['session_type'] != 'Practice' for row in practices):
        raise RuntimeFault('PRACTICE_SESSION_MISMATCH')
    practices.sort(key=lambda row: instant(row['date_start']))
    first = instant(practices[0]['date_start'])
    if first >= instant(fixture['SessionStartDateISO8601']):
        raise RuntimeFault('PRACTICE_TIME_ORDER')
    candidates = [historical_session] if now < first else [json_integer(row['session_key'])
        for row in reversed(practices) if instant(row['date_start']) <= now]
    cutoff = now.isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    for candidate in candidates:
        laps = await find('laps', {'session_key': candidate, 'lap_duration': {'$gt': 0},
            'date_start': {'$type': 'date', '$lte': {'$date': cutoff}}, 'is_pit_out_lap': False},
            {'_id': 0, 'session_key': 1, 'lap_duration': 1}, 1)
        if laps:
            return PracticeSchedule(first, candidate)
    return PracticeSchedule(first, None)


def load_practice(fixture: dict[str, Json], now: datetime, historical_session: int,
                  config: Config) -> PracticeSchedule:
    async def run() -> PracticeSchedule:
        with open(os.devnull, 'w') as errors:
            async with connect(config, errors) as session:
                return await inspect(session, fixture, now, historical_session, config)
    return anyio.run(run)
