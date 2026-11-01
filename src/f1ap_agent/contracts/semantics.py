"""Cross-field validation only; no scheduling, settlement, calls, or persistence."""
from datetime import datetime, timezone
from decimal import Decimal

from .verifier import Rejection, canonical, digest, pointer, reject

FORBIDDEN = {
    'answer', 'chance', 'officialresult', 'officialresults', 'rawfeed', 'feed',
    'apikey', 'secret', 'password', 'authorization', 'command', 'shell', 'sql',
    'accesstoken',
}


def forbidden(value, path=''):
    if isinstance(value, dict):
        for key in sorted(value):
            child = pointer(path, key)
            if key.lower().replace('_', '').replace('-', '') in FORBIDDEN:
                reject('semantic', 'FORBIDDEN', child)
            forbidden(value[key], child)
    elif isinstance(value, list):
        for index, child_value in enumerate(value):
            forbidden(child_value, pointer(path, index))


def fail(code, path):
    reject('semantic', code, path)


def check_hash(actual, expected, path):
    if actual != expected:
        reject('hash', 'HASH', path)


def millis(value):
    parsed = datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%fZ')
    parsed = parsed.replace(tzinfo=timezone.utc)
    delta = parsed - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000


def duplicates(values):
    values = list(values)
    return len(values) != len(set(values))


def definition(definition_value, question_ids, live=False, path='/payload/definition', association_path='/payload/question_ids'):
    questions = definition_value['questions']
    question_ids_in_definition = [question['question_id'] for question in questions]
    if (
        duplicates(question_ids_in_definition)
        or sorted(question['no'] for question in questions) != list(range(1, 11))
        or question_ids != question_ids_in_definition
    ):
        fail('ASSOCIATION', association_path)
    for index, question in enumerate(questions):
        base = f'{path}/questions/{index}'
        options = question['options']
        rule = question['rule']
        if duplicates([option['option_key'] for option in options]) or duplicates(
            [(option['source_type'], option['source_id']) for option in options]
        ):
            fail('DUPLICATE', base + '/options')
        bad = rule['min_picks'] > rule['max_picks']
        bad |= rule['verification'] == 'VERIFIED' and rule['scoring']['mode'] == 'UNKNOWN'
        if question['value_kind'] == 'OPTION':
            bad |= not options or 'numeric_bounds' in question
            bad |= rule['max_picks'] > len(options)
        else:
            bad |= bool(options) or 'numeric_bounds' not in question
            if 'numeric_bounds' in question:
                bounds = question['numeric_bounds']
                bad |= Decimal(bounds['min']) > Decimal(bounds['max'])
                bad |= Decimal(bounds['step']) <= 0
        if bad:
            fail('RULE', base + '/rule')
        if live and rule['verification'] != 'VERIFIED':
            fail('RULE_BLOCKED', base + '/rule')


def identity_time(payload):
    attempt = payload['attempt_no']
    retry_of_id = payload['retry_of_id']
    if (
        attempt > payload['max_attempts']
        or (attempt == 1 and retry_of_id is not None)
        or (attempt > 1 and (retry_of_id is None or retry_of_id == payload['run_id']))
    ):
        fail('IDENTITY', '/payload/attempt_no')
    if payload['task_type'] == 'PREDICT' and duplicates(
        strategy['strategy'] for strategy in payload['profile']['strategies']
    ):
        fail('DUPLICATE', '/payload/profile/strategies')
    start = millis(payload['refresh_window']['start_at'])
    if start % 1800000:
        fail('WINDOW', '/payload/refresh_window/start_at')
    cutoff = millis(payload['data_cutoff_at'])
    expires = millis(payload['expires_at'])
    deadline = payload.get('definition', {}).get('deadline_at')
    if (
        not start <= cutoff < start + 1800000
        or cutoff > expires
        or deadline is not None and expires > millis(deadline)
        or 'incremental_from' in payload and payload['incremental_from'] > payload['data_cutoff_at']
    ):
        fail('TIME_ORDER', '/payload/expires_at')


def prediction_request(payload):
    identity_time(payload)
    definition_value = payload['definition']
    live = payload['purpose'] == 'LIVE'
    if live and (not definition_value['deadline_verified'] or definition_value['deadline_at'] is None):
        fail('ADMISSION', '/payload/definition/deadline_verified')
    definition(definition_value, payload['question_ids'], live)
    calculated = {
        'definition_sha256': digest(definition_value),
        'profile_sha256': digest(payload['profile']),
    }
    calculated['input_sha256'] = digest({
        'definition_sha256': calculated['definition_sha256'],
        'question_ids': payload['question_ids'],
        'profile_sha256': calculated['profile_sha256'],
        'data_cutoff_at': payload['data_cutoff_at'],
    })
    calculated['task_key'] = digest([
        payload['task_type'], payload['purpose'], definition_value['round_id'],
        definition_value['definition_version'], calculated['profile_sha256'],
        payload['refresh_window'],
    ])
    for key in ('definition_sha256', 'profile_sha256', 'input_sha256', 'task_key'):
        check_hash(payload[key], calculated[key], '/payload/' + key)
    return calculated


def news_request(payload):
    identity_time(payload)
    if duplicates(payload['profile']['source_allowlist']):
        fail('DUPLICATE', '/payload/profile/source_allowlist')
    task_key = digest([
        payload['task_type'], payload['purpose'], None, 0,
        digest(payload['profile']), payload['refresh_window'],
    ])
    check_hash(payload['task_key'], task_key, '/payload/task_key')
    return {'task_key': task_key}


def context_for(payload, contract, context, allow_news_refresh=False):
    if context is None:
        fail('CONTEXT_REQUIRED', '')
    try:
        forbidden(context)
        contract.validate(context, 'context.schema.json')
        request = context['request']
        if request['task_type'] == 'PREDICT':
            prediction_request(request)
        elif allow_news_refresh and request['task_type'] == 'NEWS_REFRESH':
            news_request(request)
        else:
            fail('CONTEXT', '')
    except Rejection:
        fail('CONTEXT', '')
    if payload['run_id'] != request['run_id']:
        fail('ASSOCIATION', '/payload/run_id')
    return request


def question_for(request, question_id):
    for question in request['definition']['questions']:
        if question['question_id'] == question_id:
            return question
    fail('ASSOCIATION', '/payload/question_id')


def validate_pick(pick, question, path):
    if pick['value_kind'] != question['value_kind']:
        fail('ASSOCIATION', path)
    if pick['value_kind'] == 'OPTION' and pick['option_key'] not in {
        option['option_key'] for option in question['options']
    }:
        fail('ASSOCIATION', path)


def validate_result(payload, request):
    question = question_for(request, payload['question_id'])
    check_hash(payload['definition_sha256'], request['definition_sha256'], '/payload/definition_sha256')
    check_hash(payload['input_sha256'], request['input_sha256'], '/payload/input_sha256')
    status = payload['status']
    if status == 'VALID':
        if question['rule']['verification'] != 'VERIFIED':
            fail('RULE_BLOCKED', '/payload/status')
        if payload['data_status'] != 'SUFFICIENT':
            fail('DATA_INSUFFICIENT', '/payload/data_status')
        if not payload['probabilities'] or not payload['recommendations']:
            fail('RESULT_STATE', '/payload/status')
        strategy_keys = [(item['strategy'], item['version']) for item in payload['recommendations']]
        allowed = {(item['strategy'], item['version']) for item in request['profile']['strategies']}
        if duplicates([key[0] for key in strategy_keys]) or not set(strategy_keys) <= allowed:
            fail('STRATEGY', '/payload/recommendations')
        for rec_index, recommendation in enumerate(payload['recommendations']):
            picks = recommendation['picks']
            if not question['rule']['min_picks'] <= len(picks) <= question['rule']['max_picks']:
                fail('PICK_COUNT', f'/payload/recommendations/{rec_index}/picks')
            keys = []
            for pick_index, pick in enumerate(picks):
                validate_pick(pick, question, f'/payload/recommendations/{rec_index}/picks/{pick_index}')
                identity = pick.get('option_key', pick.get('numeric_value'))
                keys.append(identity)
            if duplicates(keys):
                fail('DUPLICATE', f'/payload/recommendations/{rec_index}/picks')
            if question['value_kind'] == 'NUMERIC':
                bounds = question['numeric_bounds']
                minimum, maximum, step = map(Decimal, (bounds['min'], bounds['max'], bounds['step']))
                for pick_index, pick in enumerate(picks):
                    value = Decimal(pick['numeric_value'])
                    if value < minimum or value > maximum or (value - minimum) % step != 0:
                        fail('NUMERIC_RANGE', f'/payload/recommendations/{rec_index}/picks/{pick_index}')
        allowed_options = {option['option_key'] for option in question['options']}
        events, rank_options, exclusive_outcomes = set(), set(), set()
        families, candidate_totals = {}, {}
        for probability in payload['probabilities']:
            family, rank, options = probability['family'], probability['rank'], probability['option_keys']
            event_key = (family, probability['event_key'])
            if event_key in events or duplicates(options) or not set(options) <= allowed_options:
                fail('PROBABILITY_EVENT', '/payload/probabilities')
            events.add(event_key)
            if family == 'EXCLUSIVE':
                no_award = probability['event_key'] == 'NO_AWARD'
                if rank is not None or len(options) > 1 or (not options) != no_award:
                    fail('PROBABILITY_EVENT', '/payload/probabilities')
                outcome = tuple(options)
                if outcome in exclusive_outcomes:
                    fail('PROBABILITY_EVENT', '/payload/probabilities')
                exclusive_outcomes.add(outcome)
            elif family == 'RANK_MARGINAL':
                if rank is None or rank > question['rule']['max_picks'] or len(options) != 1:
                    fail('PROBABILITY_EVENT', '/payload/probabilities')
                rank_key = (rank, options[0])
                if rank_key in rank_options:
                    fail('PROBABILITY_EVENT', '/payload/probabilities')
                rank_options.add(rank_key)
                candidate_totals[options[0]] = candidate_totals.get(options[0], Decimal(0)) + Decimal(probability['probability'])
            elif rank is not None:
                fail('PROBABILITY_EVENT', '/payload/probabilities')
            key = (family, rank)
            families[key] = families.get(key, Decimal(0)) + Decimal(probability['probability'])
        if any(total != 1 for (family, _), total in families.items() if family in {'EXCLUSIVE', 'RANK_MARGINAL'}) or any(total > 1 for total in candidate_totals.values()):
            fail('PROBABILITY_SUM', '/payload/probabilities')
        rank_families = {rank for (family, rank) in families if family == 'RANK_MARGINAL'}
        if rank_families and rank_families != set(range(1, question['rule']['max_picks'] + 1)):
            fail('PROBABILITY_SUM', '/payload/probabilities')
    else:
        if payload['probabilities'] or payload['recommendations'] or not payload['reason']:
            fail('RESULT_STATE', '/payload/status')
    for index, evidence in enumerate(payload['evidence']):
        if evidence['first_observed_at'] > request['data_cutoff_at'] or evidence['published_at'] > request['data_cutoff_at']:
            fail('EVIDENCE_TIME', f'/payload/evidence/{index}')
    expected_result_hash = digest({key: value for key, value in payload.items() if key != 'result_sha256'})
    check_hash(payload['result_sha256'], expected_result_hash, '/payload/result_sha256')
    return {'result_sha256': expected_result_hash}


def finished(payload, request, context):
    expected_ids = payload['expected_question_ids']
    request_ids = [question['question_id'] for question in request['definition']['questions']]
    result_ids = [result['question_id'] for result in payload['results']]
    if expected_ids != request_ids or sorted(result_ids) != sorted(request_ids) or duplicates(result_ids):
        fail('FINISHED_SET', '/payload/results')
    if payload['run_status'] == 'SUCCEEDED' and any(result['status'] != 'VALID' for result in payload['results']):
        fail('RESULT_STATE', '/payload/run_status')
    if payload['run_status'] == 'PARTIAL' and not (
        any(result['status'] == 'VALID' for result in payload['results'])
        and any(result['status'] != 'VALID' for result in payload['results'])
    ):
        fail('RESULT_STATE', '/payload/run_status')
    if payload['run_status'] in {'FAILED', 'TIMED_OUT', 'CANCELLED'} and any(
        result['status'] == 'VALID' for result in payload['results']
    ):
        fail('RESULT_STATE', '/payload/run_status')
    summaries = {result['question_id']: result for result in payload['results']}
    received = set()
    for result in context['received_results']:
        if summaries.get(result['question_id']) != result:
            fail('RESULT_CONFLICT', '/payload/results')
        received.add(result['question_id'])
    return {'results_complete': set(request_ids) <= received}


def usage(payload, context):
    pricing = payload['pricing']
    if pricing['status'] == 'UNKNOWN':
        if any(pricing[key] is not None for key in ('unit_price', 'original_cost', 'charged_cny')):
            fail('COST_STATE', '/payload/pricing')
    else:
        if any(pricing[key] is None for key in ('unit_price', 'original_cost', 'charged_cny', 'currency', 'price_version')):
            fail('COST_STATE', '/payload/pricing')
    history = {}
    for item in context['usage_history']:
        history[item['call_key']] = max(history.get(item['call_key'], 0), item['usage_revision'])
    action = 'APPLY' if payload['usage_revision'] > history.get(payload['call_key'], 0) else 'IGNORE_STALE'
    return {'usage_action': action}


def event(value, contract, context):
    forbidden(value)
    contract.validate(value)
    event_type = value['event_type']
    payload = value['payload']
    schema_name = event_type.removesuffix('.v1').replace('.', '-') + '.schema.json'
    contract.validate(payload, schema_name, '/payload')
    if len(canonical(payload)) > contract.limits['payload_canonical_bytes']:
        fail('SIZE', '/payload')
    computed = {}
    if event_type == 'prediction.requested.v1':
        computed.update(prediction_request(payload))
    elif event_type == 'news.refresh.requested.v1':
        computed.update(news_request(payload))
    elif event_type == 'agent.run.progress.v1':
        request = context_for(payload, contract, context, allow_news_refresh=True)
        lease = millis(payload['lease_until'])
        deadline = request.get('definition', {}).get('deadline_at')
        if (
            lease < millis(value['occurred_at'])
            or lease > millis(request['expires_at'])
            or deadline is not None and lease > millis(deadline)
        ):
            fail('LEASE', '/payload/lease_until')
    elif event_type == 'prediction.question.completed.v1':
        request = context_for(payload, contract, context)
        computed.update(validate_result(payload, request))
    elif event_type == 'agent.run.finished.v1':
        request = context_for(payload, contract, context)
        computed.update(finished(payload, request, context))
    elif event_type == 'model.usage.updated.v1':
        context_for(payload, contract, context, allow_news_refresh=True)
        computed.update(usage(payload, context))
    elif event_type == 'news.index.updated.v1':
        if duplicates(document['document_key'] for document in payload['documents']):
            fail('DUPLICATE', '/payload/documents')
    elif event_type == 'publication.changed.v1':
        expected = f"/api/v1/rounds/{payload['round_id']}/publications/{payload['publication_no']}"
        if payload['snapshot_ref'] != expected:
            fail('ASSOCIATION', '/payload/snapshot_ref')
    computed['payload_sha256'] = digest(payload)
    check_hash(value['payload_sha256'], computed['payload_sha256'], '/payload_sha256')
    return computed
