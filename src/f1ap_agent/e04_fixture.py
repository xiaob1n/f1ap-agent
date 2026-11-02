"""明确合成的 E04 离线夹具及真实 Worker 事件导出。"""
import argparse
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

from f1ap_agent.contracts.verifier import digest
from f1ap_agent.data.openf1.boundary import decode
from f1ap_agent.data.openf1.client import Response
from f1ap_agent.data.openf1.config import FIELDS
from f1ap_agent.data.openf1.prepare import encoded, freeze, write_once
from f1ap_agent.messaging import LocalSender
from f1ap_agent.runtime import Json, RuntimeFault, Store, atomic_json, json_events, json_object
from f1ap_agent.worker.engine import Worker
from f1ap_agent.worker.models import Task, load_contract


@dataclass(frozen=True, slots=True)
class FixturePaths:
    root: Path
    request: Path
    mapping: Path
    manifest: Path
    output: Path


def synthetic_responses() -> tuple[Response, ...]:
    """ID 仅为本地合成值，绝不作为真实 OpenF1 场次查询。"""
    common: dict[str, Json] = {'meeting_key': 910001, 'session_key': 910003}
    rows: dict[str, list[dict[str, Json]]] = {
        'sessions': [{**common, 'session_type': 'Practice', 'date_start': '2025-12-31T12:00:00.000Z',
                      'date_end': '2025-12-31T13:00:00.000Z'}],
        'drivers': [{**common, 'driver_number': number} for number in (901, 902, 903, 904)],
        'laps': [], 'stints': [], 'pit': [],
        'weather': [{**common, 'date': '2025-12-31T12:00:00.000Z', 'rainfall': 0}],
        'race_control': [{**common, 'date': '2025-12-31T12:00:00.000Z', 'flag': 'GREEN',
                          'category': 'Flag', 'message': 'SYNTHETIC TRACK CLEAR'}],
    }
    for index, number in enumerate((901, 902, 903, 904)):
        rows['stints'].append({**common, 'driver_number': number, 'stint_number': 1,
            'lap_start': 1, 'lap_end': 6, 'compound': 'SOFT', 'tyre_age_at_start': 1})
        for lap_no in range(1, 7):
            rows['laps'].append({**common, 'driver_number': number, 'lap_number': lap_no,
                'date_start': f'2025-12-31T12:{lap_no * 2:02d}:00.000Z',
                'lap_duration': str(80 + index + (lap_no % 3) * 2), 'is_pit_out_lap': False})
    return tuple(Response(endpoint, encoded(data), '2026-01-01T00:09:00.000Z', tuple(data))
                 for endpoint, data in rows.items())


def build_fixture(root: Path, verified: bool = False) -> FixturePaths:
    if root.exists() or any(part.is_symlink() for part in (root, *root.parents)):
        raise RuntimeFault('FIXTURE_ROOT_EXISTS_OR_SYMLINK')
    root.mkdir(parents=True)
    contract = load_contract()
    request = json_object(decode(contract.read_bytes('fixtures/valid/prediction-valid.json')))
    payload = json_object(request['payload'])
    definition = json_object(payload['definition'])
    definition.update(deadline_verified=False, deadline_at=None, deadline_basis='RESEARCH_NOT_OFFICIAL')
    questions = json_events(definition['questions'])
    for question in questions:
        rule = json_object(question['rule'])
        if question['no'] != 1 or not verified:
            rule.update(verification='BLOCKED', evidence_ref='SYNTHETIC_RULE_BLOCKED')
            scoring = json_object(rule['scoring'])
            scoring.update(mode='UNKNOWN', exact_multiplier=None)
    options = json_events(questions[0]['options'])
    options.append({'label': 'SYNTHETIC fourth driver', 'option_key': 'q1:choice:3',
                    'points': '4', 'source_id': '8013', 'source_type': 'CHOICE'})
    questions[0]['options'] = options
    definition['questions'] = questions
    payload.update(definition=definition, purpose='RESEARCH', fixture_class='CONTROLLED_INPUT', call_limit=0,
                   expires_at='2026-01-01T00:25:00.000Z', definition_sha256=digest(definition))
    manifest_sha = freeze(synthetic_responses(), root, '2026-01-01T00:09:30.000Z')
    record: dict[str, Json] = {'round_id': definition['round_id'], 'definition_sha256': payload['definition_sha256'],
        'mapping_version': 'E04_OFFLINE_SYNTHETIC_V1', 'source_ref': 'LOCAL_TEST_SYNTHETIC_NOT_REAL_SERVICE',
        'valid_until': '2026-01-01T01:00:00.000Z',
        'candidates': {f'q1:choice:{index}': number for index, number in enumerate((901, 902, 903, 904))},
        'target_session': {'meeting_key': 910001, 'session_key': 910002, 'session_type': 'Race'},
        'feature_session': {'meeting_key': 910001, 'session_key': 910003, 'session_type': 'Practice'},
        'event_start_at': '2025-12-31T12:00:00.000Z', 'event_cutoff_at': '2025-12-31T12:20:00.000Z',
        'endpoints': {endpoint: list(fields) for endpoint, fields in FIELDS.items()},
        'method': {'seed': 0, 'samples': 10000, 'laps_per_sample': 5, 'compound': 'SOFT', 'tyre_age_min': 1, 'tyre_age_max': 20},
        'manifest': {'path': 'manifest.json', 'sha256': manifest_sha}}
    mapping_path = root / 'mapping.json'
    mapping_raw = encoded({'schema_version': 'OPENF1_MAPPING_V1', 'mappings': [record]})
    write_once(mapping_path, mapping_raw)
    profile: dict[str, Json] = {'algorithm_version': 'PODIUM_PACE_BOOTSTRAP_V1@' + hashlib.sha256(mapping_raw).hexdigest(),
        'tools': ['OPENF1_READ'], 'strategies': [{'strategy': 'EXPECTED_POINTS', 'version': 'EXPECTED_POINTS_V1'}],
        'model_config_ref': 'NO_MODEL', 'price_version': 'NOT_APPLICABLE'}
    payload.update(profile=profile, profile_sha256=digest(profile))
    payload['input_sha256'] = digest({key: payload[key] for key in (
        'definition_sha256', 'question_ids', 'profile_sha256', 'data_cutoff_at')})
    payload['task_key'] = digest([payload['task_type'], payload['purpose'], definition['round_id'],
        definition['definition_version'], payload['profile_sha256'], payload['refresh_window']])
    request.update(payload=payload, payload_sha256=digest(payload))
    request_path = root / 'request.json'
    write_once(request_path, encoded(request))
    return FixturePaths(root, request_path, mapping_path, root / 'manifest.json', root / 'output')


class NoExecutor:
    def execute(self, task: Task) -> None:
        raise RuntimeFault('RESEARCH_EXECUTOR_MUST_NOT_RUN:' + str(task.run_id))


def run_fixture(root: Path, verified: bool = False) -> FixturePaths:
    paths = build_fixture(root, verified)
    store = Store(root / 'runtime')
    store.initialize()
    worker = Worker(store, load_contract(), NoExecutor(), LocalSender(store.root), paths.mapping)
    task, accepted = worker.run(paths.request.read_bytes())
    if not accepted:
        raise RuntimeFault('FIXTURE_PENDING')
    log = store.registry_log(str(task.run_id))
    summaries = [{key: result[key] for key in ('question_id', 'status', 'result_sha256')}
                 for result in json_events(log['results'])]
    atomic_json(paths.output / 'request.json', log['request'])
    atomic_json(paths.output / 'events.json', log['events'])
    atomic_json(paths.output / 'context.json', {'request': task.payload,
        'received_results': summaries, 'usage_history': []})
    return paths


class Options(argparse.Namespace):
    output: Path = Path()
    verified: bool = False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument('--output', type=Path, required=True)
    _ = parser.add_argument('--verified', action='store_true')
    args = parser.parse_args(namespace=Options())
    try:
        paths = run_fixture(args.output, args.verified)
        print(f'LOCAL_TEST output={paths.output} external_services=NOT_RUN')
        return 0
    except (RuntimeFault, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
