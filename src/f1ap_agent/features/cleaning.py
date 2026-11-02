"""同一练习场次、干地、固定配方胎龄的整数微秒圈时。"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from f1ap_agent.data.openf1.config import FIELDS, MappingConfig
from f1ap_agent.data.openf1.snapshot import Snapshot
from f1ap_agent.runtime import Json, RuntimeFault, json_integer, json_string


class Insufficient(RuntimeFault):
    """数据解析成功，但不足以支持所有候选的可比样本。"""


@dataclass(frozen=True, slots=True)
class Features:
    laps: tuple[tuple[str, tuple[int, ...]], ...]
    excluded: int


def micros(value: Json) -> int:
    try:
        number = Decimal(str(value)) * 1000000
        if not number.is_finite() or number <= 0 or number != number.to_integral_value():
            raise Insufficient('OPENF1_LAP_UNIT_OR_VALUE')
        return int(number)
    except InvalidOperation as error:
        raise Insufficient('OPENF1_LAP_UNIT_OR_VALUE') from error


def event_time(value: Json) -> str:
    """上游时间接受 UTC ISO 精度，内部统一到毫秒用于边界比较。"""
    text = json_string(value)
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError as error:
        raise Insufficient('OPENF1_EVENT_TIME') from error
    if parsed.utcoffset() != timedelta(0):
        raise Insufficient('OPENF1_EVENT_TIME')
    return parsed.isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def clean(config: MappingConfig, snapshot: Snapshot) -> Features:
    rows = snapshot.rows
    for endpoint, required in FIELDS.items():
        for row in rows[endpoint]:
            if not set(required) <= set(row):
                raise Insufficient('OPENF1_MISSING_FIELDS:' + endpoint)
            if row['meeting_key'] != config.feature.meeting_key or row['session_key'] != config.feature.session_key:
                raise Insufficient('OPENF1_SESSION_SCOPE')
    sessions = rows['sessions']
    if len(sessions) != 1 or sessions[0]['session_type'] != 'Practice':
        raise Insufficient('OPENF1_PRACTICE_REQUIRED')
    if not (event_time(sessions[0]['date_start']) <= config.event_start_at
            < config.event_cutoff_at <= event_time(sessions[0]['date_end'])):
        raise Insufficient('OPENF1_SESSION_RANGE')
    drivers = [json_integer(row['driver_number']) for row in rows['drivers']]
    if len(drivers) != len(set(drivers)) or not {number for _, number in config.candidates} <= set(drivers):
        raise Insufficient('OPENF1_CANDIDATE_MISSING')
    weather = sorted(rows['weather'], key=lambda row: event_time(row['date']))
    flags = sorted(rows['race_control'], key=lambda row: event_time(row['date']))
    if not weather or not flags:
        raise Insufficient('OPENF1_CLEANING_METADATA_MISSING')
    for endpoint in ('weather', 'race_control', 'pit'):
        if any(not config.event_start_at <= event_time(row['date']) <= config.event_cutoff_at
               for row in rows[endpoint]):
            raise Insufficient('OPENF1_EVENT_RANGE')
        identities = [(event_time(row['date']), row.get('driver_number')) for row in rows[endpoint]]
        if len(identities) != len(set(identities)):
            raise Insufficient('OPENF1_DUPLICATE_EVENT')
    stint_keys: set[tuple[int, int]] = set()
    for stint in rows['stints']:
        driver, sequence = json_integer(stint['driver_number']), json_integer(stint['stint_number'])
        if (driver, sequence) in stint_keys:
            raise Insufficient('OPENF1_DUPLICATE_EVENT')
        stint_keys.add((driver, sequence))
        if (sequence < 1 or json_integer(stint['lap_start']) < 1
            or json_integer(stint['lap_end']) < json_integer(stint['lap_start'])
            or json_integer(stint['tyre_age_at_start']) < 0):
            raise Insufficient('OPENF1_STINT_RANGE')
    if any(type(row['rainfall']) is not int or row['rainfall'] not in (0, 1) for row in weather):
        raise Insufficient('OPENF1_WEATHER_UNIT')
    pits = {(json_integer(row['driver_number']), json_integer(row['lap_number'])) for row in rows['pit']}
    if len(pits) != len(rows['pit']):
        raise Insufficient('OPENF1_DUPLICATE_EVENT')
    pools: dict[int, list[int]] = {number: [] for _, number in config.candidates}
    seen: set[tuple[int, int]] = set()
    excluded = 0
    for lap in rows['laps']:
        number, lap_no = json_integer(lap['driver_number']), json_integer(lap['lap_number'])
        if lap_no < 1:
            raise Insufficient('OPENF1_LAP_NUMBER')
        key = (number, lap_no)
        if key in seen:
            raise Insufficient('OPENF1_DUPLICATE_EVENT')
        seen.add(key)
        start = event_time(lap['date_start'])
        if not config.event_start_at <= start <= config.event_cutoff_at:
            raise Insufficient('OPENF1_EVENT_RANGE')
        if lap['lap_duration'] is None:
            excluded += 1
            continue
        duration = micros(lap['lap_duration'])
        end = (datetime.fromisoformat(start.replace('Z', '+00:00')) + timedelta(microseconds=duration)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        if end > config.event_cutoff_at:
            raise Insufficient('OPENF1_EVENT_RANGE')
        if type(lap['is_pit_out_lap']) is not bool:
            raise Insufficient('OPENF1_PIT_FLAG')
        if number not in pools or lap['is_pit_out_lap'] or key in pits or (number, lap_no - 1) in pits:
            excluded += 1
            continue
        prior_weather = [row for row in weather if event_time(row['date']) <= start]
        prior_flags = [row for row in flags if event_time(row['date']) <= start]
        if not prior_weather or not prior_flags:
            raise Insufficient('OPENF1_CLEANING_COVERAGE')
        relevant_weather = [prior_weather[-1], *(row for row in weather if start < event_time(row['date']) <= end)]
        relevant_flags = [prior_flags[-1], *(row for row in flags if start < event_time(row['date']) <= end)]
        if any(row['rainfall'] != 0 for row in relevant_weather) or any(row['flag'] != 'GREEN' for row in relevant_flags):
            excluded += 1
            continue
        stints = [row for row in rows['stints'] if row['driver_number'] == number
                  and json_integer(row['lap_start']) <= lap_no <= json_integer(row['lap_end'])]
        if len(stints) != 1:
            raise Insufficient('OPENF1_STINT_MISSING_OR_OVERLAP')
        stint = stints[0]
        age = json_integer(stint['tyre_age_at_start']) + lap_no - json_integer(stint['lap_start'])
        if stint['compound'] != config.method.compound or not config.method.tyre_age_min <= age <= config.method.tyre_age_max:
            excluded += 1
            continue
        pools[number].append(duration)
    if any(len(pool) < 5 for pool in pools.values()):
        raise Insufficient('OPENF1_COMPARABLE_LAPS_MISSING')
    return Features(tuple((key, tuple(sorted(pools[number]))) for key, number in config.candidates), excluded)
