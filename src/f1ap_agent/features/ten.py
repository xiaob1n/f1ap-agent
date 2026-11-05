"""只读取已验证的冻结清单；缺失特征按题隔离。"""
from dataclasses import dataclass

from f1ap_agent.data.openf1.config import E05_FIELDS, MappingConfig
from f1ap_agent.data.openf1.snapshot import Snapshot
from f1ap_agent.features.cleaning import Features, Insufficient, clean, event_time, micros
from f1ap_agent.runtime import json_integer, json_string


@dataclass(frozen=True, slots=True)
class Feature:
    values: tuple[tuple[str, tuple[int, ...]], ...]
    unit: str
    sample_count: int
    missing_reason: str
    available_at: str


@dataclass(frozen=True, slots=True)
class TenFeatures:
    pace: Feature
    qualifying: Feature
    pit: Feature
    teams: tuple[tuple[str, str], ...]
    classification: Feature | None = None


def extract(config: MappingConfig, snapshot: Snapshot) -> TenFeatures:
    def feature(kind: str) -> Feature:
        values: dict[str, tuple[int, ...]] = {}
        try:
            if kind == 'pace':
                pace: Features = clean(config, snapshot)
                values = dict(pace.laps)
            else:
                endpoint = 'session_result' if kind == 'qualifying' else 'pit'
                scope = config.qualifying if kind == 'qualifying' else (config.pit_session or config.feature)
                if scope is None:
                    raise Insufficient('QUALIFYING_SESSION_MISSING')
                by_number = {number: key for key, number in config.candidates}
                for row in snapshot.rows.get(endpoint, ()):
                    if not set(E05_FIELDS[endpoint]) <= set(row):
                        raise Insufficient('MISSING_FIELDS:' + endpoint)
                    if row['meeting_key'] != scope.meeting_key or row['session_key'] != scope.session_key:
                        raise Insufficient('SESSION_SCOPE:' + endpoint)
                    key = by_number.get(json_integer(row['driver_number']))
                    if key is None:
                        continue
                    if kind == 'qualifying':
                        duration = row['duration']
                        if not isinstance(duration, list) or len(duration) != 3 or key in values:
                            raise Insufficient('QUALIFYING_SEGMENTS_REQUIRED')
                        values[key] = tuple(0 if value is None else micros(value) for value in duration)
                    else:
                        observed_event = event_time(row['date'])
                        if observed_event > config.event_cutoff_at or (config.pit_session is None and observed_event < config.event_start_at):
                            raise Insufficient('PIT_AFTER_CUTOFF')
                        values[key] = (*values.get(key, ()), micros(row['stop_duration']))
                if set(values) != set(by_number.values()):
                    raise Insufficient('CANDIDATE_SAMPLES_MISSING:' + endpoint)
            return Feature(tuple(sorted(values.items())), 'microseconds',
                           sum(len(v) for v in values.values()), '', snapshot.generated_at)
        except (Insufficient, KeyError) as error:
            return Feature((), 'microseconds', 0, str(error), snapshot.generated_at)

    teams = tuple((key, json_string(row['team_name'])) for key, number in config.candidates
                  for row in snapshot.rows['drivers']
                  if row['driver_number'] == number and 'team_name' in row)
    states: list[tuple[str, tuple[int, ...]]] = []
    by_number = {number: key for key, number in config.candidates}
    for row in snapshot.rows.get('session_result', ()):
        if (row.get('driver_number') in by_number and all(type(row.get(k)) is bool for k in ('dnf', 'dns', 'dsq'))
            and type(row.get('position')) is int and type(row.get('number_of_laps')) is int):
            states.append((by_number[json_integer(row['driver_number'])],
                (json_integer(row['position']), int(row['dnf'] is True), int(row['dns'] is True),
                 int(row['dsq'] is True), json_integer(row['number_of_laps']))))
    status = Feature(tuple(sorted(states)), 'rank_dnf_dns_dsq_laps', len(states),
                     '' if len(states) == len(config.candidates) else 'CLASSIFICATION_METADATA_MISSING', snapshot.generated_at)
    return TenFeatures(feature('pace'), feature('qualifying'), feature('pit'), teams, status)
