"""版本化受控场次映射；不推断真实赛事或车手 ID。"""
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from f1ap_agent.data.openf1.boundary import checked_file, exact, safe_path, sha, timestamp
from f1ap_agent.runtime import Json, RuntimeFault, json_array, json_integer, json_object, json_string

FIELDS: Final[dict[str, tuple[str, ...]]] = {
    'sessions': ('meeting_key', 'session_key', 'session_type', 'date_start', 'date_end'),
    'drivers': ('meeting_key', 'session_key', 'driver_number'),
    'laps': ('meeting_key', 'session_key', 'driver_number', 'lap_number', 'date_start',
             'lap_duration', 'is_pit_out_lap'),
    'stints': ('meeting_key', 'session_key', 'driver_number', 'stint_number', 'lap_start',
               'lap_end', 'compound', 'tyre_age_at_start'),
    'weather': ('meeting_key', 'session_key', 'date', 'rainfall'),
    'race_control': ('meeting_key', 'session_key', 'date', 'flag', 'category', 'message'),
    'pit': ('meeting_key', 'session_key', 'driver_number', 'lap_number', 'date'),
}
MAPPING_KEYS: Final = {'round_id', 'definition_sha256', 'mapping_version', 'source_ref',
    'valid_until', 'candidates', 'target_session', 'feature_session', 'event_start_at',
    'event_cutoff_at', 'endpoints', 'method', 'manifest'}
E05_FIELDS: Final = {**FIELDS,
    'drivers': (*FIELDS['drivers'], 'team_name'),
    'pit': (*FIELDS['pit'], 'stop_duration', 'lane_duration'),
    'session_result': ('meeting_key', 'session_key', 'driver_number', 'position',
                       'dnf', 'dns', 'dsq', 'duration', 'number_of_laps')}


@dataclass(frozen=True, slots=True)
class Selection:
    round_id: str
    definition_sha256: str
    mapping_sha256: str


@dataclass(frozen=True, slots=True)
class Session:
    meeting_key: int
    session_key: int
    session_type: str


@dataclass(frozen=True, slots=True)
class Method:
    compound: str
    tyre_age_min: int
    tyre_age_max: int


@dataclass(frozen=True, slots=True)
class MappingConfig:
    selection: Selection
    mapping_version: str
    source_ref: str
    valid_until: str
    candidates: tuple[tuple[str, int], ...]
    target: Session
    feature: Session
    event_start_at: str
    event_cutoff_at: str
    endpoints: tuple[tuple[str, tuple[str, ...]], ...]
    method: Method
    manifest_path: Path
    manifest_sha256: str
    bindings: dict[str, Json]
    qualifying: Session | None
    pit_session: Session | None = None


def session(value: Json, expected: str) -> Session:
    item = exact(value, {'meeting_key', 'session_key', 'session_type'})
    meeting, key = json_integer(item['meeting_key']), json_integer(item['session_key'])
    if meeting <= 0 or key <= 0 or item['session_type'] != expected:
        raise RuntimeFault('OPENF1_SESSION')
    return Session(meeting, key, expected)


def parse_record(value: Json, path: Path, selection: Selection) -> MappingConfig:
    source = json_object(value)
    e05 = 'question_bindings' in source
    extra = {'question_bindings', 'qualifying_session'} if e05 else set()
    if e05 and 'pit_session' in source:
        extra.add('pit_session')
    item = exact(value, MAPPING_KEYS | extra)
    if item['round_id'] != selection.round_id or sha(item['definition_sha256']) != selection.definition_sha256:
        raise RuntimeFault('OPENF1_MAPPING_IDENTITY')
    candidates = tuple(sorted((key, json_integer(number))
                       for key, number in json_object(item['candidates']).items()))
    if not 3 <= len(candidates) <= 22 or any(not key or number <= 0 for key, number in candidates):
        raise RuntimeFault('OPENF1_CANDIDATES')
    if len({number for _, number in candidates}) != len(candidates):
        raise RuntimeFault('OPENF1_DUPLICATE_DRIVER')
    target, feature = session(item['target_session'], 'Race'), session(item['feature_session'], 'Practice')
    if target.session_key == feature.session_key:
        raise RuntimeFault('OPENF1_TARGET_LEAKAGE')
    if 'pit_session' in item and session(item['pit_session'], 'Race').session_key == target.session_key:
        raise RuntimeFault('OPENF1_TARGET_LEAKAGE')
    start, cutoff, valid = (timestamp(item[key]) for key in ('event_start_at', 'event_cutoff_at', 'valid_until'))
    if not start < cutoff <= valid:
        raise RuntimeFault('OPENF1_TIME_ORDER')
    allowed = E05_FIELDS if e05 else FIELDS
    endpoints = exact(item['endpoints'], set(allowed))
    parsed_endpoints: list[tuple[str, tuple[str, ...]]] = []
    for endpoint, required in allowed.items():
        fields = tuple(json_string(field) for field in json_array(endpoints[endpoint]))
        permitted = [set(required)]
        if endpoint == 'race_control':
            permitted.append(set(required) | {'scope', 'sector', 'driver_number'})
        if len(set(fields)) != len(fields) or set(fields) not in permitted:
            raise RuntimeFault('OPENF1_ENDPOINT_FIELDS:' + endpoint)
        parsed_endpoints.append((endpoint, fields))
    method = exact(item['method'], {'seed', 'samples', 'laps_per_sample', 'compound', 'tyre_age_min', 'tyre_age_max'})
    if any(json_integer(method[key]) != value for key, value in (('seed', 0), ('samples', 10000), ('laps_per_sample', 5))):
        raise RuntimeFault('OPENF1_METHOD_VERSION')
    compound = json_string(method['compound'])
    low, high = json_integer(method['tyre_age_min']), json_integer(method['tyre_age_max'])
    if compound not in ('SOFT', 'MEDIUM', 'HARD') or not 0 <= low <= high <= 100:
        raise RuntimeFault('OPENF1_COMPARABILITY')
    manifest = exact(item['manifest'], {'path', 'sha256'})
    version, source = json_string(item['mapping_version']), json_string(item['source_ref'])
    if not version or not source:
        raise RuntimeFault('OPENF1_PROVENANCE')
    return MappingConfig(selection, version, source, valid, candidates, target, feature, start, cutoff,
        tuple(parsed_endpoints), Method(compound, low, high),
        safe_path(path.parent, json_string(manifest['path'])), sha(manifest['sha256']),
        json_object(item['question_bindings']) if e05 else {},
        session(item['qualifying_session'], 'Qualifying') if e05 else None,
        session(item['pit_session'], 'Race') if 'pit_session' in item else None)


def load_mapping(path: Path, selection: Selection) -> MappingConfig:
    envelope = exact(checked_file(path, selection.mapping_sha256), {'schema_version', 'mappings'})
    if envelope['schema_version'] != 'OPENF1_MAPPING_V1':
        raise RuntimeFault('OPENF1_MAPPING_VERSION')
    records = [json_object(item) for item in json_array(envelope['mappings'])]
    try:
        rounds = [json_string(record['round_id']) for record in records]
        if len(rounds) != len(set(rounds)):
            raise RuntimeFault('OPENF1_DUPLICATE_ROUND')
        matches = [record for record in records if record['round_id'] == selection.round_id
                   and record['definition_sha256'] == selection.definition_sha256]
        if len(matches) != 1:
            raise RuntimeFault('OPENF1_MAPPING_MISSING')
        return parse_record(matches[0], path, selection)
    except KeyError as error:
        raise RuntimeFault('OPENF1_FIELDS') from error
