"""E05 三种完全离线、确定性 TEST_FIXTURE 输入及 Worker 输出。"""
import argparse
import hashlib
from pathlib import Path

from f1ap_agent.contracts.verifier import digest
from f1ap_agent.data.openf1.boundary import decode
from f1ap_agent.data.openf1.client import Response
from f1ap_agent.data.openf1.config import E05_FIELDS
from f1ap_agent.data.openf1.prepare import encoded, freeze, write_once
from f1ap_agent.e04_fixture import FixturePaths, NoExecutor, synthetic_responses
from f1ap_agent.messaging import LocalSender
from f1ap_agent.runtime import Json, RuntimeFault, Store, atomic_json, json_events, json_object, json_string, read_json
from f1ap_agent.worker.engine import Worker
from f1ap_agent.worker.models import load_contract


def build_fixture(root: Path, scenario: str = 'all-valid') -> FixturePaths:
    if root.exists() or any(p.is_symlink() for p in (root, *root.parents)):
        raise RuntimeFault('FIXTURE_ROOT_EXISTS_OR_SYMLINK')
    root.mkdir(parents=True)
    contract = load_contract()
    request = json_object(decode(contract.read_bytes('fixtures/valid/prediction-valid.json')))
    payload = json_object(request['payload'])
    rules = Path(__file__).resolve().parents[3] / 'f1ap-api/tools/e05/fixtures/rules'
    definition = json_object(read_json(rules / ('definition-unknown.json' if scenario == 'rule-blocked' else 'definition-verified.json')))
    questions = json_events(definition['questions'])
    by_id = {json_string(o['source_id']): json_string(o['option_key']) for o in json_events(questions[0]['options'])}
    candidates = {key: 900 + i for i, key in enumerate(by_id.values(), 1)}
    # 合成队伍归属显式记录，不代表真实赛季阵容。
    team_ids = ('29', '28', '28', '25', '25', '27', '27', '2636', '23', '2636', '23', '29', '2640', '2640', '26', '26', '210', '210', '24', '24', '2641', '2641')
    team_options = json_events(questions[5]['options'])
    team_key = {json_string(o['source_id']): json_string(o['option_key']) for o in team_options}
    driver_teams = {key: team_key[team] for key, team in zip(candidates, team_ids, strict=True)}
    bindings: dict[str, Json] = {}
    for q in questions:
        no = json_string(str(q['no']))
        bindings[no] = {'drivers': {json_string(o['option_key']): by_id[json_string(o['source_id'])]
                         for o in json_events(q['options']) if o['source_type'] == 'DRIVER'}}
    classified: dict[str, Json] = {key: True for key in candidates}
    classified[by_id['117']] = False
    bindings['4'] = {'members': [by_id['18'], by_id['11059']], 'stages': {'1': 'q4:choice:3', '2': 'q4:choice:2', '3': 'q4:choice:1'}}
    bindings['6'] = {'teams': {team: [driver for driver, t in driver_teams.items() if t == team] for team in team_key.values()}}
    json_object(bindings['7'])['classified'] = classified
    bindings['8'] = {'counts': {str(i): f'q8:choice:{i + 1}' for i in range(5)},
                     'classified': {by_id[i]: classified[by_id[i]] for i in ('11031', '118', '121', '13')}}
    bindings['9'] = {'driver': by_id['117'], 'classified': classified,
                     'categories': dict(zip(('PODIUM', 'TOP10', 'REST', 'NOT_CLASSIFIED'),
                                            (f'q9:choice:{i}' for i in range(1, 5)), strict=True))}
    bindings['10'] = {'yes': 'q10:choice:1', 'no': 'q10:choice:2', 'conditions': [
        {'driver': by_id['11161'], 'min': 1, 'max': 1}, {'driver': by_id['124'], 'min': 9, 'max': 22},
        {'driver': by_id['110'], 'min': 5, 'max': 22}]}
    if scenario == 'partial':
        json_object(questions[9]['rule']).update(verification='BLOCKED')
    definition['questions'] = questions
    common: dict[str, Json] = {'meeting_key': 910001, 'session_key': 910003}
    rows = {r.endpoint: list(r.rows) for r in synthetic_responses()}
    rows.update(drivers=[], laps=[], stints=[], pit=[], session_result=[])
    for index, (key, number) in enumerate(candidates.items()):
        rows['drivers'].append({**common, 'driver_number': number, 'team_name': driver_teams[key]})
        rows['stints'].append({**common, 'driver_number': number, 'stint_number': 1, 'lap_start': 1,
                               'lap_end': 6, 'compound': 'SOFT', 'tyre_age_at_start': 1})
        for lap in range(1, 7):
            rows['laps'].append({**common, 'driver_number': number, 'lap_number': lap,
                'date_start': f'2025-12-31T12:{lap * 2:02d}:00.000Z',
                'lap_duration': str(80 + index % 4 + lap % 3 * 2), 'is_pit_out_lap': False})
        rows['pit'].append({**common, 'driver_number': number, 'lap_number': 20,
            'date': '2025-12-31T12:18:00.000Z', 'stop_duration': str(2 + index % 3), 'lane_duration': '25'})
        rows['session_result'].append({**common, 'session_key': 910004, 'driver_number': number,
            'position': index + 1, 'dnf': False, 'dns': False, 'dsq': False, 'number_of_laps': 20,
            'duration': ['82', str(81 + index % 3), str(80 + index % 4)]})
    if scenario == 'partial':
        rows['pit'] = []
    responses = tuple(Response(ep, encoded(data), '2026-01-01T00:09:00.000Z', tuple(data)) for ep, data in rows.items())
    manifest_sha = freeze(responses, root, '2026-01-01T00:09:30.000Z', E05_FIELDS)
    payload.update(definition=definition, definition_sha256=digest(definition), purpose='RESEARCH',
        fixture_class='CONTROLLED_INPUT', call_limit=0, question_ids=[q['question_id'] for q in questions],
        expires_at='2026-01-01T00:25:00.000Z')
    record: dict[str, Json] = {'round_id': definition['round_id'], 'definition_sha256': payload['definition_sha256'],
        'mapping_version': 'E05_TEST_FIXTURE_V1', 'source_ref': 'TEST_FIXTURE_NOT_REAL_SERVICE',
        'valid_until': '2026-01-01T01:00:00.000Z', 'candidates': candidates,
        'target_session': {'meeting_key': 910001, 'session_key': 910002, 'session_type': 'Race'},
        'feature_session': {**common, 'session_type': 'Practice'},
        'qualifying_session': {'meeting_key': 910001, 'session_key': 910004, 'session_type': 'Qualifying'},
        'event_start_at': '2025-12-31T12:00:00.000Z', 'event_cutoff_at': '2025-12-31T12:20:00.000Z',
        'endpoints': {ep: list(fields) for ep, fields in E05_FIELDS.items()}, 'question_bindings': bindings,
        'method': {'seed': 0, 'samples': 10000, 'laps_per_sample': 5, 'compound': 'SOFT', 'tyre_age_min': 1, 'tyre_age_max': 20},
        'manifest': {'path': 'manifest.json', 'sha256': manifest_sha}}
    mapping_raw = encoded({'schema_version': 'OPENF1_MAPPING_V1', 'mappings': [record]})
    mapping_path = root / 'mapping.json'
    write_once(mapping_path, mapping_raw)
    profile: dict[str, Json] = {'algorithm_version': 'TEN_QUESTION_GRAPH_V1@' + hashlib.sha256(mapping_raw).hexdigest(),
        'tools': ['OPENF1_READ'], 'strategies': [{'strategy': 'EXPECTED_POINTS', 'version': 'EXPECTED_POINTS_V1'},
        {'strategy': 'HIT_RATE', 'version': 'HIT_RATE_V1'}], 'model_config_ref': 'NO_MODEL', 'price_version': 'NOT_APPLICABLE'}
    payload.update(profile=profile, profile_sha256=digest(profile))
    payload['input_sha256'] = digest({k: payload[k] for k in ('definition_sha256', 'question_ids', 'profile_sha256', 'data_cutoff_at')})
    payload['task_key'] = digest([payload['task_type'], payload['purpose'], definition['round_id'],
        definition['definition_version'], payload['profile_sha256'], payload['refresh_window']])
    request.update(payload=payload, payload_sha256=digest(payload))
    write_once(root / 'request.json', encoded(request))
    return FixturePaths(root, root / 'request.json', mapping_path, root / 'manifest.json', root)


def run_fixture(root: Path, scenario: str = 'all-valid') -> FixturePaths:
    paths = build_fixture(root, scenario)
    store = Store(root / 'runtime')
    store.initialize()
    worker = Worker(store, load_contract(), NoExecutor(), LocalSender(store.root), paths.mapping)
    task, accepted = worker.run(paths.request.read_bytes())
    if not accepted:
        raise RuntimeFault('FIXTURE_PENDING')
    log = store.registry_log(str(task.run_id))
    atomic_json(root / 'events.json', log['events'])
    atomic_json(root / 'context.json', {'request': task.payload, 'received_results': [
        {key: r[key] for key in ('question_id', 'status', 'result_sha256')} for r in json_events(log['results'])], 'usage_history': []})
    return paths


class Options(argparse.Namespace):
    output: Path = Path()
    scenario: str = 'all-valid'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument('--output', type=Path, required=True)
    _ = parser.add_argument('--scenario', choices=('all-valid', 'partial', 'rule-blocked'), required=True)
    args = parser.parse_args(namespace=Options())
    paths = run_fixture(args.output, args.scenario)
    print(f'LOCAL_TEST scenario={args.scenario} output={paths.root} external_services=NOT_RUN')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
