"""冻结清单校验，不建立任何网络连接。"""
from dataclasses import dataclass
from typing import Final

from f1ap_agent.data.openf1.boundary import checked_file, exact, safe_path, sha, timestamp
from .config import MappingConfig
from f1ap_agent.runtime import Json, RuntimeFault, json_array, json_integer, json_object, json_string

UNITS: Final = {'lap_duration': 'seconds', 'tyre_age_at_start': 'laps', 'rainfall': 'boolean_0_1'}
E05_UNITS: Final = {**UNITS, 'stop_duration': 'seconds', 'lane_duration': 'seconds',
    'duration': 'seconds_by_qualifying_segment', 'position': 'rank', 'number_of_laps': 'laps',
    'dnf': 'boolean', 'dns': 'boolean', 'dsq': 'boolean'}


@dataclass(frozen=True, slots=True)
class Snapshot:
    rows: dict[str, tuple[dict[str, Json], ...]]
    generated_at: str
    first_observed_at: str
    manifest_sha256: str


def load_snapshot(config: MappingConfig, cutoff: str) -> Snapshot:
    manifest = exact(checked_file(config.manifest_path, config.manifest_sha256),
        {'schema_version', 'generated_at', 'responses', 'units', 'limitations'})
    units = E05_UNITS if config.bindings else UNITS
    if manifest['schema_version'] != 'OPENF1_SNAPSHOT_V1' or manifest['units'] != units:
        raise RuntimeFault('OPENF1_MANIFEST_VERSION_OR_UNITS')
    generated = timestamp(manifest['generated_at'])
    if generated > cutoff or cutoff > config.valid_until or config.event_cutoff_at > cutoff:
        raise RuntimeFault('OPENF1_OBSERVATION_AFTER_CUTOFF')
    if manifest['limitations'] != ['availability_unverified', 'fuel_unknown', 'traffic_track_evolution_unobserved']:
        raise RuntimeFault('OPENF1_LIMITATIONS')
    rows: dict[str, tuple[dict[str, Json], ...]] = {}
    observations: list[str] = []
    endpoints = dict(config.endpoints)
    for entry in json_array(manifest['responses']):
        record = exact(entry, {'endpoint', 'path', 'sha256', 'row_count', 'first_observed_at', 'fields', 'observed_fields'})
        endpoint = json_string(record['endpoint'])
        if endpoint not in endpoints or endpoint in rows or record['fields'] != list(endpoints[endpoint]):
            raise RuntimeFault('OPENF1_MANIFEST_ENDPOINT')
        observed = timestamp(record['first_observed_at'])
        if observed > generated:
            raise RuntimeFault('OPENF1_OBSERVATION_ORDER')
        observations.append(observed)
        data = checked_file(safe_path(config.manifest_path.parent, json_string(record['path'])), sha(record['sha256']))
        parsed = tuple(json_object(row) for row in json_array(data))
        if len(parsed) != json_integer(record['row_count']):
            raise RuntimeFault('OPENF1_ROW_COUNT')
        if record['observed_fields'] != sorted({key for row in parsed for key in row}):
            raise RuntimeFault('OPENF1_OBSERVED_FIELDS')
        # 原始字节及 observed_fields 完整留证，计算只消费显式登记的字段。
        rows[endpoint] = tuple({key: value for key, value in row.items()
                                if key in endpoints[endpoint]} for row in parsed) if config.bindings else parsed
    if set(rows) != set(endpoints):
        raise RuntimeFault('OPENF1_MANIFEST_ENDPOINTS')
    return Snapshot(rows, generated, min(observations), config.manifest_sha256)
