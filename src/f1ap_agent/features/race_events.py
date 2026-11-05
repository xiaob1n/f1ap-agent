"""原始 OpenF1 历史正赛的观察证据；完整性不代表上游真实性认证。"""
from collections.abc import Mapping, Sequence
from datetime import datetime

from f1ap_agent.data.openf1.boundary import timestamp
from f1ap_agent.features.cleaning import Insufficient, event_time, micros
from f1ap_agent.runtime import Json, json_integer, json_string


def extract_history(session: Mapping[str, Json], drivers: Sequence[Mapping[str, Json]],
                    laps: Sequence[Mapping[str, Json]], race_control: Sequence[Mapping[str, Json]],
                    results: Sequence[Mapping[str, Json]], available_at: str,
                    source_ref: str, target_session: int) -> dict[str, Json]:
    """固定历史车手全集，逐车核实首圈，按赛道旗状态转换统计红旗。"""
    session_key = json_integer(session.get('session_key'))
    meeting_key = json_integer(session.get('meeting_key'))
    if (session_key == target_session or session_key <= 0 or meeting_key <= 0
            or session.get('session_type') != 'Race'):
        raise Insufficient('RACE_EVENT_SESSION_SCOPE')
    available = timestamp(available_at)
    if not source_ref.strip():
        raise Insufficient('RACE_EVENT_SOURCE_MISSING')
    for feed in (drivers, laps, race_control, results):
        for row in feed:
            if (json_integer(row.get('session_key')) != session_key
                    or json_integer(row.get('meeting_key')) != meeting_key):
                raise Insufficient('RACE_EVENT_SESSION_SCOPE')

    candidates = [json_integer(row.get('driver_number')) for row in drivers]
    if not candidates or len(candidates) != len(set(candidates)) or min(candidates) <= 0:
        raise Insufficient('RACE_EVENT_CANDIDATES')
    by_result: dict[int, Mapping[str, Json]] = {}
    for row in results:
        number = json_integer(row.get('driver_number'))
        if number not in candidates or number in by_result:
            raise Insufficient('RACE_EVENT_CANDIDATES')
        by_result[number] = row
    if set(by_result) != set(candidates):
        raise Insufficient('RACE_EVENT_CANDIDATES')
    by_lap: dict[tuple[int, int], Mapping[str, Json]] = {}
    for row in laps:
        number = json_integer(row.get('driver_number'))
        lap = json_integer(row.get('lap_number'))
        if number not in candidates or lap <= 0 or (number, lap) in by_lap:
            raise Insufficient('RACE_EVENT_LAPS')
        by_lap[number, lap] = row

    # 验证 UTC 后按原始精度排序，避免同毫秒内不同状态被截断重排。
    for row in race_control:
        _ = event_time(row.get('date'))
    ordered = sorted(race_control, key=lambda row: datetime.fromisoformat(
        json_string(row['date']).replace('Z', '+00:00')))
    started = False
    finished = False
    red = False
    red_flags = 0
    for row in ordered:
        if finished:
            continue
        if row.get('message') == 'SESSION STARTED':
            started = True
            red = False
        if not started:
            continue
        if row.get('message') == 'RED FLAG - RACE SUSPENDED':
            if not red:
                red_flags += 1
            red = True
        if row.get('category') != 'Flag' or row.get('scope') != 'Track':
            continue
        flag = row.get('flag')
        if flag == 'RED':
            if not red:
                red_flags += 1
            red = True
        if flag in ('GREEN', 'CLEAR'):
            red = False
        if flag == 'CHEQUERED' and started:
            finished = True
    if not started or not finished or any(event_time(row['date']) > available for row in ordered):
        raise Insufficient('RACE_EVENT_FEED_INCOMPLETE')

    all_first_lap = True
    for number in candidates:
        result = by_result[number]
        total = result.get('number_of_laps')
        if total is not None:
            total = json_integer(total)
            if total < 0:
                raise Insufficient('RACE_EVENT_RESULT_LAPS')
        first = by_lap.get((number, 1))
        duration = first.get('lap_duration') if first is not None else None
        positive_first = duration is not None and micros(duration) > 0
        lap_completion = positive_first and (number, 2) in by_lap
        zero = total == 0 and (result.get('dns') is True or result.get('dnf') is True)
        completed = total is not None and total >= 1
        if (zero and (positive_first or (number, 2) in by_lap)
                or result.get('dns') is True and (completed or lap_completion)):
            raise Insufficient('RACE_EVENT_FIRST_LAP_CONFLICT')
        if zero:
            all_first_lap = False
        elif not completed and not lap_completion:
            raise Insufficient('RACE_EVENT_FIRST_LAP_AMBIGUOUS')

    return {'session_key': session_key, 'source_ref': source_ref, 'available_at': available,
            'complete': True, 'red_flags': red_flags, 'all_first_lap': all_first_lap,
            'source': 'OPENF1_RAW_OBSERVATION_V1; observational completeness only; upstream unauthenticated'}
