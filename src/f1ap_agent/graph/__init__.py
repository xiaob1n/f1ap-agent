"""固定九节点 LangGraph；冻结读取一次，单题计算错误独立收口。"""
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from f1ap_agent.baselines.podium import Bootstrap, bootstrap
from f1ap_agent.contracts.verifier import canonical, digest, evaluate
from f1ap_agent.data.openf1.boundary import checked_file, exact, safe_path, sha
from f1ap_agent.data.openf1.config import MappingConfig
from f1ap_agent.data.openf1.snapshot import Snapshot, load_snapshot
from f1ap_agent.features.cleaning import Insufficient
from f1ap_agent.features.ten import TenFeatures, extract
from f1ap_agent.questions import Distribution
from f1ap_agent.questions import classification, fastest_lap, pit_stop, qualifying, race_events, race_outcome
from f1ap_agent.runtime import Json, RuntimeFault, json_array, json_events, json_integer, json_object, json_string
from f1ap_agent.strategies import recommend
from f1ap_agent.worker.models import Task, load_contract


class State(TypedDict):
    task: Task
    config: MappingConfig
    snapshot: Snapshot
    features: TenFeatures
    race: Bootstrap
    qualifying: qualifying.Qualifying
    questions: list[dict[str, Json]]
    distributions: dict[str, Distribution]
    results: list[dict[str, Json]]
    trace: list[str]
    error: str


class Update(TypedDict, total=False):
    snapshot: Snapshot
    features: TenFeatures
    race: Bootstrap
    qualifying: qualifying.Qualifying
    questions: list[dict[str, Json]]
    distributions: dict[str, Distribution]
    results: list[dict[str, Json]]
    trace: list[str]
    error: str


def validate(state: State) -> Update:
    task, config = state['task'], state['config']
    if (not task.is_research or config.selection.mapping_sha256 != task.mapping_sha256
        or config.selection.definition_sha256 != task.payload['definition_sha256']):
        raise RuntimeFault('E05_INPUT_IDENTITY')
    return {'trace': ['validate']}


def classify(state: State) -> Update:
    questions = json_events(json_object(state['task'].payload['definition'])['questions'])
    return {'questions': questions, 'trace': [*state['trace'], 'classify']}


def quality(state: State) -> Update:
    try:
        snapshot = load_snapshot(state['config'], json_string(state['task'].payload['data_cutoff_at']))
        return {'snapshot': snapshot, 'trace': [*state['trace'], 'time_quality']}
    except RuntimeFault as error:
        return {'error': error.code, 'trace': [*state['trace'], 'time_quality']}


def features(state: State) -> Update:
    update: Update = {'trace': [*state['trace'], 'features']}
    if not state['error']:
        shared = extract(state['config'], state['snapshot'])
        update['features'] = shared
        if shared.pace.values:
            update['race'] = bootstrap(dict(shared.pace.values))
        update['qualifying'] = qualifying.Qualifying(shared.qualifying.values)
    return update


def news(state: State) -> Update:
    return {'trace': [*state['trace'], 'news_disabled']}


def question_distribution(state: State, question: dict[str, Json]) -> Distribution:
    no = json_integer(question['no'])
    binding = json_object(state['config'].bindings[str(no)])
    keys = tuple(json_string(o['option_key']) for o in json_events(question['options']))
    if 'method' in binding:
        method = json_string(binding['method'])
        if method == 'PIT_AWARD_POINT_V1':
            reference = exact(binding['source'], {'path', 'sha256'})
            path = safe_path(state['config'].manifest_path.parent, json_string(reference['path']))
            source = json_object(checked_file(path, sha(reference['sha256'])))
            return pit_stop.award_prior(source, binding, keys,
                                        json_string(state['task'].payload['data_cutoff_at']),
                                        state['config'].target.session_key)
        if method in ('RED_FLAG_COUNT_V1', 'ALL_FINISH_FIRST_LAP_V1'):
            if 'history_sources' in binding:
                from f1ap_agent.features.race_events import extract_history
                history: list[Json] = []
                for raw in json_array(binding['history_sources']):
                    source = exact(raw, {'available_at', 'source_ref', 'files'})
                    files = exact(source['files'], {'sessions', 'drivers', 'laps', 'race_control', 'session_result'})
                    data: dict[str, list[dict[str, Json]]] = {}
                    for endpoint, reference in files.items():
                        ref = exact(reference, {'path', 'sha256'})
                        path = safe_path(state['config'].manifest_path.parent, json_string(ref['path']))
                        data[endpoint] = json_events(checked_file(path, sha(ref['sha256'])))
                    if len(data['sessions']) != 1:
                        raise Insufficient('RACE_EVENT_SESSION_MISSING')
                    if {json_integer(row['driver_number']) for row in data['drivers']} != {
                            number for _, number in state['config'].candidates}:
                        raise Insufficient('RACE_EVENT_CANDIDATE_MISMATCH')
                    history.append(extract_history(data['sessions'][0], data['drivers'], data['laps'],
                        data['race_control'], data['session_result'], json_string(source['available_at']),
                        json_string(source['source_ref']), state['config'].target.session_key))
                binding = {**binding, 'history': history}
            return race_events.calculate(binding, keys, json_string(state['task'].payload['data_cutoff_at']),
                                         state['config'].target.session_key)
        routes = {'PODIUM': 1, 'POLE': 2, 'QUALIFYING_COMPARISON': 3, 'TEAM_QUALIFYING_STAGE': 4,
                  'FASTEST_LAP': 5, 'FASTEST_PIT': 6, 'RACE_COMPARISON': 7,
                  'CLASSIFIED_COUNT': 8, 'FINISH_RANGE': 9, 'CHAMPIONSHIP_CONDITIONS': 10}
        if method not in routes:
            raise RuntimeFault('QUESTION_METHOD_UNKNOWN')
        no = routes[method]
    shared = state['features']
    if no in (1, 7, 9, 10):
        if not shared.pace.values:
            raise Insufficient(shared.pace.missing_reason)
        return race_outcome.calculate(no, state['race'], binding, keys)
    if no in (2, 3, 4):
        if not shared.qualifying.values:
            raise Insufficient(shared.qualifying.missing_reason)
        return qualifying.calculate(no, state['qualifying'], binding, keys)
    if no == 5:
        if not shared.pace.values:
            raise Insufficient(shared.pace.missing_reason)
        return fastest_lap.calculate(shared.pace, {key: json_string(v) for key, v in json_object(binding['drivers']).items()})
    if no == 6:
        if not shared.pit.values:
            raise Insufficient(shared.pit.missing_reason)
        return pit_stop.calculate(shared.pit, {key: tuple(json_string(v) for v in json_array(values))
                                            for key, values in json_object(binding['teams']).items()})
    if no == 8:
        if not binding.get('classified'):
            raise Insufficient('CLASSIFICATION_PRIOR_MISSING')
        return classification.calculate(binding)
    raise RuntimeFault('UNKNOWN_QUESTION')


def probability(state: State) -> Update:
    outputs: list[dict[str, Json]] = []
    distributions: dict[str, Distribution] = {}
    task = state['task']
    for question in state['questions']:
        qid = json_string(question['question_id'])
        payload: dict[str, Json] = {'run_id': str(task.run_id), 'question_id': qid,
            'definition_sha256': task.payload['definition_sha256'], 'input_sha256': task.payload['input_sha256'],
            'status': 'FAILED', 'data_status': 'INSUFFICIENT', 'reason': 'RULE_BLOCKED',
            'probabilities': [], 'recommendations': [], 'evidence': [], 'fixture_class': 'CONTROLLED_INPUT'}
        if json_object(question['rule'])['verification'] == 'VERIFIED':
            try:
                if state['error']:
                    raise RuntimeFault(state['error'])
                rule = json_object(question['rule'])
                scoring = json_object(rule['scoring'])
                podium = question['no'] == 1
                if (scoring['mode'] != ('PODIUM' if podium else 'FIXED')
                    or scoring['void_policy'] != 'SOURCE_CONFIRMED' or scoring['no_award'] == 'UNKNOWN'
                    or rule['min_picks'] != (3 if podium else 1) or rule['max_picks'] != (3 if podium else 1)):
                    raise RuntimeFault('UNSUPPORTED_E05_RULE')
                distribution = question_distribution(state, question)
                if scoring['no_award'] != 'POSSIBLE' and any(p['event_key'] == 'NO_AWARD' for p in distribution.probabilities):
                    raise Insufficient('NO_AWARD_RULE_UNSUPPORTED')
                distributions[qid] = distribution
                payload.update(status='VALID', data_status='SUFFICIENT', reason='', probabilities=list(distribution.probabilities))
            except Insufficient as error:
                payload.update(status='DATA_INSUFFICIENT', reason=error.code)
            except (RuntimeFault, KeyError, ValueError, IndexError, TypeError, ArithmeticError) as error:
                payload.update(reason='QUESTION_ERROR:' + str(error))
        outputs.append(payload)
    return {'distributions': distributions, 'results': outputs, 'trace': [*state['trace'], 'probability']}


def strategy(state: State) -> Update:
    for question, result in zip(state['questions'], state['results'], strict=True):
        qid = json_string(question['question_id'])
        if qid in state['distributions']:
            try:
                result['recommendations'] = recommend(question, state['distributions'][qid])
            except (RuntimeFault, KeyError, ValueError, IndexError, TypeError, ArithmeticError) as error:
                result.update(status='FAILED', data_status='INSUFFICIENT', reason='STRATEGY_ERROR:' + str(error),
                              probabilities=[], recommendations=[])
                del state['distributions'][qid]
    return {'results': state['results'], 'trace': [*state['trace'], 'strategy']}


def explain(state: State) -> Update:
    for result in state['results']:
        qid = json_string(result['question_id'])
        if qid in state['distributions']:
            d = state['distributions'][qid]
            snapshot = state['snapshot']
            result['evidence'] = [{'document_key': 'OPENF1_MANIFEST:' + snapshot.manifest_sha256,
                'content_sha256': snapshot.manifest_sha256, 'published_at': snapshot.generated_at,
                'first_observed_at': snapshot.first_observed_at, 'retrieval_model_ref': 'NONE',
                'collection_version': 'TEN_QUESTION_GRAPH_V1',
                'excerpt': f'{d.method}; {d.assumptions}; seed={d.seed}; samples={d.samples}; laps_per_sample=5; {d.error}'}]
    return {'results': state['results'], 'trace': [*state['trace'], 'explain']}


def output_validate(state: State) -> Update:
    contract = load_contract()
    for result in state['results']:
        result['result_sha256'] = digest(result)
        task = state['task']
        event: dict[str, Json] = {'schema_version': '1.0', 'event_id': task.request['event_id'],
            'event_type': 'prediction.question.completed.v1', 'occurred_at': task.stamp(),
            'trace_id': task.request['trace_id'], 'payload': result, 'payload_sha256': digest(result)}
        verdict = evaluate(canonical(event), contract=contract, context={'request': state['task'].payload, 'received_results': [], 'usage_history': []})
        if not verdict['accepted']:
            raise RuntimeFault('E05_OUTPUT_CONTRACT:' + str(verdict['error']))
    return {'results': state['results'], 'trace': [*state['trace'], 'output_validate']}


def run(task: Task, config: MappingConfig) -> list[dict[str, Json]]:
    graph = StateGraph(State)
    nodes = [('validate', validate), ('classify', classify), ('time_quality', quality), ('features', features),
             ('news_disabled', news), ('probability', probability), ('strategy', strategy),
             ('explain', explain), ('output_validate', output_validate)]
    previous = START
    for name, node in nodes:
        graph.add_node(name, node)
        graph.add_edge(previous, name)
        previous = name
    graph.add_edge(previous, END)
    from f1ap_agent.features.ten import Feature
    empty = Feature((), 'microseconds', 0, 'NOT_LOADED', '')
    initial: State = {'task': task, 'config': config, 'snapshot': Snapshot({}, '', '', ''),
        'features': TenFeatures(empty, empty, empty, ()), 'race': Bootstrap(()),
        'qualifying': qualifying.Qualifying(()), 'questions': [], 'distributions': {},
        'results': [], 'trace': [], 'error': ''}
    output = graph.compile().invoke(initial, {'recursion_limit': 12})
    return json_events(output['results'])
