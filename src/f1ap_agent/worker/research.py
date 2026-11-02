"""冻结研究输入的纯计算及 wire 规则门适配。"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

from f1ap_agent.baselines.podium import Bootstrap, bootstrap, decimal_text, optimal_podium
from f1ap_agent.contracts.verifier import digest
from f1ap_agent.data.openf1.config import MappingConfig, Selection, load_mapping
from f1ap_agent.data.openf1.snapshot import Snapshot, load_snapshot
from f1ap_agent.features.cleaning import Features, Insufficient, clean
from f1ap_agent.runtime import Json, RuntimeFault, json_events, json_integer, json_object, json_string
from f1ap_agent.worker.models import Task


@dataclass(frozen=True, slots=True)
class Research:
    identity: dict[str, Json]
    evidence: tuple[dict[str, Json], ...]
    features: Features | None
    baseline: Bootstrap | None
    error: str | None
    insufficient: bool


def bind(task: Task, mapping_path: Path | None) -> MappingConfig:
    if mapping_path is None:
        raise RuntimeFault('RESEARCH_MAPPING_REQUIRED')
    definition = json_object(task.payload['definition'])
    config = load_mapping(mapping_path, Selection(json_string(definition['round_id']),
        json_string(task.payload['definition_sha256']), task.mapping_sha256))
    question = next(item for item in json_events(definition['questions']) if item['no'] == 1)
    if {key for key, _ in config.candidates} != {json_string(item['option_key']) for item in json_events(question['options'])}:
        raise RuntimeFault('OPENF1_CANDIDATE_DEFINITION')
    return config


def compute(task: Task, config: MappingConfig) -> Research:
    identity: dict[str, Json] = {'mapping_sha256': task.mapping_sha256,
        'manifest_sha256': config.manifest_sha256, 'data_cutoff_at': task.payload['data_cutoff_at']}
    evidence: tuple[dict[str, Json], ...] = ()
    try:
        snapshot: Snapshot = load_snapshot(config, json_string(task.payload['data_cutoff_at']))
        evidence = ({'document_key': 'OPENF1_MANIFEST:' + snapshot.manifest_sha256,
            'content_sha256': snapshot.manifest_sha256, 'published_at': snapshot.generated_at,
            'first_observed_at': snapshot.first_observed_at, 'retrieval_model_ref': 'NONE',
            'collection_version': 'OPENF1_SNAPSHOT_V1',
            'excerpt': 'Practice pace proxy, not calibrated race probabilities; availability_unverified; fuel_unknown; traffic_track_evolution_unobserved'},)
        features = clean(config, snapshot)
        baseline = bootstrap(dict(features.laps))
        return Research(identity, evidence, features, baseline, None, False)
    except Insufficient as error:
        return Research(identity, evidence, None, None, error.code, True)
    except RuntimeFault as error:
        return Research(identity, evidence, None, None, error.code, False)


def results(task: Task, research: Research) -> list[dict[str, Json]]:
    outputs: list[dict[str, Json]] = []
    for question in json_events(json_object(task.payload['definition'])['questions']):
        rule = json_object(question['rule'])
        q1 = json_integer(question['no']) == 1
        data_sufficient = q1 and research.baseline is not None
        reason = 'RULE_BLOCKED' if q1 else 'UNSUPPORTED_E04'
        status = 'FAILED'
        if q1 and rule['verification'] == 'VERIFIED' and research.error is not None:
            reason = research.error
            status = 'DATA_INSUFFICIENT' if research.insufficient else 'FAILED'
        payload: dict[str, Json] = {'run_id': str(task.run_id), 'question_id': question['question_id'],
            'definition_sha256': task.payload['definition_sha256'], 'input_sha256': task.payload['input_sha256'],
            'status': status, 'data_status': 'SUFFICIENT' if data_sufficient else 'INSUFFICIENT',
            'reason': reason, 'probabilities': [], 'recommendations': [],
            'evidence': list(research.evidence), 'fixture_class': 'CONTROLLED_INPUT'}
        if q1 and rule['verification'] == 'VERIFIED' and research.baseline is not None:
            scoring = json_object(rule['scoring'])
            supported = (question['value_kind'] == 'OPTION' and rule['min_picks'] == 3
                and rule['max_picks'] == 3 and rule['ordered'] is True and scoring['mode'] == 'PODIUM'
                and scoring['exact_multiplier'] is not None and scoring['void_policy'] == 'SOURCE_CONFIRMED')
            if supported:
                baseline = research.baseline
                points = {json_string(option['option_key']): Decimal(json_string(option['points']))
                          for option in json_events(question['options'])}
                picks, score = optimal_podium(baseline, points, Decimal(json_string(scoring['exact_multiplier'])))
                probabilities: list[Json] = []
                for marginal in baseline.marginals:
                    for rank, probability in enumerate(marginal.probabilities(), 1):
                        probabilities.append({'family': 'RANK_MARGINAL',
                            'event_key': f'RANK_{rank}:{marginal.option_key}', 'option_keys': [marginal.option_key],
                            'rank': rank, 'probability': probability})
                payload.update(status='VALID', reason='', probabilities=probabilities,
                    recommendations=[{'strategy': 'EXPECTED_POINTS', 'version': 'EXPECTED_POINTS_V1',
                        'objective': 'EXPECTED_POINTS', 'expected_score': decimal_text(score.quantize(Decimal('0.000001'), rounding=ROUND_HALF_EVEN)),
                        'picks': [{'value_kind': 'OPTION', 'option_key': key} for key in picks]}])
            else:
                payload['reason'] = 'UNSUPPORTED_E04_RULE'
        payload['result_sha256'] = digest(payload)
        outputs.append(payload)
    return outputs
