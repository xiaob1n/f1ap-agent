"""同字节受控目录的运行专属新闻元数据渲染。"""
import hashlib
import uuid
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from f1ap_agent.contracts.artifact import Contract, check_format
from f1ap_agent.contracts.verifier import Rejection, canonical, digest, evaluate, parse
from f1ap_agent.models.mock import MockPlan, fields
from f1ap_agent.runtime import Json, RuntimeFault, json_array, json_events, json_object, json_string
from f1ap_agent.worker.models import NewsTask


class NewsDependency(RuntimeFault):
    """受控输入不可用或漂移，保留 delivery 供恢复。"""


class NewsInvalid(RuntimeFault):
    """确定非法的受控目录；消费者拒绝且不重投。"""


def normalized_uri(value: str) -> str:
    """只规范 scheme/host/default port/fragment，保留 path/query 原始编码。"""
    try:
        uri = urlsplit(value)
        if (uri.scheme.lower() not in ('http', 'https') or not uri.hostname
                or uri.username is not None or uri.password is not None
                or any(char.isspace() or ord(char) < 32 for char in value)):
            raise NewsInvalid('NEWS_SOURCE_URI')
        host = uri.hostname.lower()
        if ':' in host:
            host = '[' + host + ']'
        port = uri.port
        if port is not None and port != (443 if uri.scheme.lower() == 'https' else 80):
            host += ':' + str(port)
        return urlunsplit((uri.scheme.lower(), host, uri.path or '/', uri.query, ''))
    except ValueError as error:
        raise NewsInvalid('NEWS_SOURCE_URI') from error


def document_identity(document: dict[str, Json], contract: Contract) -> str:
    """元数据精确字段集和类型先于文件读取及调用意图。"""
    wire_names = 'document_key content_version content_sha256 source_uri published_at first_observed_at title'.split()
    metadata = 'source_name language_code storage_ref permission_ref availability_verified index_version index_ordinal'.split()
    if set(document) != set(wire_names + metadata):
        raise NewsInvalid('NEWS_DOCUMENT_FIELDS')
    for name in ('source_name', 'language_code', 'storage_ref', 'permission_ref', 'index_version'):
        if not isinstance(document[name], str) or not json_string(document[name]).strip():
            raise NewsInvalid('NEWS_DOCUMENT_TEXT:' + name)
    ordinal = document['index_ordinal']
    if type(ordinal) is not int or not 1 <= ordinal <= 2147483647:
        raise NewsInvalid('NEWS_INDEX_ORDINAL')
    available = document['availability_verified']
    if type(available) is not int or available not in (0, 1):
        raise NewsInvalid('NEWS_AVAILABILITY_INTEGER')
    contract.validate({'run_id': '1', 'index_version': document['index_version'],
        'documents': [{key: document[key] for key in wire_names}], 'fixture_class': 'TEST_FIXTURE'},
        'news-index-updated.schema.json')
    normalized = normalized_uri(json_string(document['source_uri']))
    return digest([normalized, document['content_sha256']])


def load(task: NewsTask, plan_path: Path | None, catalog_path: Path | None,
         contract: Contract, registered_plan: MockPlan | None = None) -> tuple[MockPlan, list[dict[str, Json]]]:
    """请求配置引用绑定原始计划摘要，计划再绑定原始目录摘要。"""
    if plan_path is None or catalog_path is None:
        raise NewsDependency('NEWS_FILES_REQUIRED')
    profile = json_object(task.payload['profile'])
    ref = json_string(profile['embedding_config_ref'])
    base, separator, expected = ref.partition('@')
    if task.fixture_class != 'TEST_FIXTURE' or not base.startswith('E07_MOCK_') or not separator:
        raise NewsDependency('NEWS_PROFILE_BINDING')
    try:
        raw_plan, raw_catalog = plan_path.read_bytes(), catalog_path.read_bytes()
        if hashlib.sha256(raw_plan).hexdigest() != expected:
            raise NewsDependency('NEWS_PLAN_HASH')
        source = json_object(parse(raw_plan))
        if hashlib.sha256(raw_catalog).hexdigest() != source['news_catalog_sha256']:
            raise NewsDependency('NEWS_CATALOG_HASH')
        expected_profile = dict(json_object(source['profile']))
        expected_profile['embedding_config_ref'] = ref
        if profile != expected_profile or json_object(source['profile'])['embedding_config_ref'] != base:
            raise NewsDependency('NEWS_PROFILE_BINDING')
        plan = MockPlan.load(plan_path, expected, contract)
        if registered_plan is not None:
            if registered_plan.sha256 != plan.sha256:
                raise NewsDependency('NEWS_PLAN_HASH')
            plan = replace(plan, revisions=registered_plan.revisions,
                           script_sha256=registered_plan.script_sha256)
        selected = tuple(call for call in plan.calls if call.call_type == 'EMBEDDING'
                         and call.model_config_ref == ref)
        if len(selected) != 1:
            raise NewsDependency('NEWS_EMBEDDING_IDENTITY')
        catalog = fields(parse(raw_catalog), 'schema_version fixture_class documents batches')
        if catalog['schema_version'] != '1.0' or catalog['fixture_class'] != 'TEST_FIXTURE':
            raise NewsInvalid('NEWS_CATALOG_VERSION')
        documents = json_events(catalog['documents'])
        if not 1 <= len(documents) <= 100:
            raise NewsInvalid('NEWS_DOCUMENT_COUNT')
        identities: set[str] = set()
        document_keys: set[str] = set()
        for document in documents:
            identity = document_identity(document, contract)
            key = json_string(document['document_key'])
            if key in document_keys or identity in identities:
                raise NewsInvalid('NEWS_DOCUMENT_DUPLICATE')
            document_keys.add(key)
            identities.add(identity)
            if document['source_name'] not in json_array(profile['source_allowlist']):
                raise NewsInvalid('NEWS_SOURCE')
            relative = json_string(document['storage_ref'])
            if (Path(relative).is_absolute() or '\\' in relative
                    or any(part in ('', '.', '..') for part in relative.split('/'))):
                raise NewsInvalid('NEWS_CONTENT_PATH')
            path = (catalog_path.parent / relative).resolve()
            if not path.is_relative_to(catalog_path.parent.resolve()):
                raise NewsInvalid('NEWS_CONTENT_PATH')
            if hashlib.sha256(path.read_bytes()).hexdigest() != document['content_sha256']:
                raise NewsDependency('NEWS_CONTENT_HASH')
        events: list[dict[str, Json]] = []
        covered: list[str] = []
        for batch in json_events(catalog['batches']):
            if set(batch) != set('batch_id event_id index_ordinal document_keys payload_sha256 payload'.split()):
                raise NewsInvalid('NEWS_BATCH_FIELDS')
            if not isinstance(batch['batch_id'], str) or not json_string(batch['batch_id']).strip():
                raise NewsInvalid('NEWS_BATCH_IDENTITY')
            if type(batch['index_ordinal']) is not int or not 1 <= batch['index_ordinal'] <= 2147483647:
                raise NewsInvalid('NEWS_INDEX_ORDINAL')
            payload = dict(json_object(batch['payload']))
            contract.validate(payload, 'news-index-updated.schema.json')
            check_format('f1ap-uuid', json_string(batch['event_id']), '/event_id')
            check_format('f1ap-sha256', json_string(batch['payload_sha256']), '/payload_sha256')
            if digest(payload) != batch['payload_sha256']:
                raise NewsInvalid('NEWS_TEMPLATE_HASH')
            keys = [json_string(doc['document_key']) for doc in json_events(payload['documents'])]
            if len(set(keys)) != len(keys) or set(keys) & set(covered):
                raise NewsInvalid('NEWS_BATCH_DOCUMENT_DUPLICATE')
            if keys != batch['document_keys']:
                raise NewsInvalid('NEWS_BATCH_IDENTITY')
            for wire in json_events(payload['documents']):
                matches = [doc for doc in documents if doc['document_key'] == wire['document_key']]
                if len(matches) != 1 or wire != {key: matches[0][key] for key in wire}:
                    raise NewsInvalid('NEWS_DOCUMENT_IDENTITY')
                if matches[0]['index_ordinal'] != batch['index_ordinal'] or matches[0]['index_version'] != payload['index_version']:
                    raise NewsInvalid('NEWS_INDEX_IDENTITY')
            covered.extend(keys)
            payload['run_id'] = str(task.run_id)
            event: dict[str, Json] = {'schema_version': '1.0', 'event_id': str(uuid.uuid5(
                uuid.NAMESPACE_URL, f"f1ap-e07:{task.run_id}:{batch['batch_id']}")),
                'event_type': 'news.index.updated.v1', 'occurred_at': task.stamp(),
                'trace_id': task.request['trace_id'], 'payload_sha256': digest(payload), 'payload': payload}
            if not evaluate(canonical(event), contract=contract,
                            context={'request': task.payload})['accepted']:
                raise NewsInvalid('NEWS_OUTPUT_CONTRACT')
            events.append(event)
        if sorted(covered) != sorted(json_string(doc['document_key']) for doc in documents) or len({json_string(e['event_id']) for e in events}) != len(events):
            raise NewsInvalid('NEWS_BATCH_SET')
        return replace(plan, calls=selected), events
    except Rejection as error:
        raise NewsInvalid('NEWS_CATALOG_CONTRACT:' + str(error)) from error
    except OSError as error:
        raise NewsDependency('NEWS_FILES_UNAVAILABLE') from error


def validate(task: NewsTask, log: dict[str, Json], events: list[dict[str, Json]]) -> None:
    """恢复时核对持久化集合，不允许伪造已发送或预测终态。"""
    if (json_object(log['request'])['payload'] != task.payload or log['phase'] not in ('REGISTERED', 'FINISHED')
            or 'results' in log or 'finished' in log):
        raise RuntimeFault('NEWS_LOG_IDENTITY')
    actual = json_events(log['events'])
    news = [e for e in actual if e['event_type'] == 'news.index.updated.v1']
    if any(e['event_type'] not in ('news.index.updated.v1', 'model.usage.updated.v1') for e in actual):
        raise RuntimeFault('NEWS_LOG_EVENT_TYPE')
    # occurred_at 属于首次处理事实；运行专属 payload 与事件身份必须不变。
    def identities(values: list[dict[str, Json]]) -> list[tuple[Json, Json, Json]]:
        return [(e['event_id'], e['payload_sha256'], e['payload']) for e in values]
    if news and identities(news) != identities(events):
        raise RuntimeFault('NEWS_LOG_BATCH_SET')
    if log['phase'] == 'FINISHED' and identities(news) != identities(events):
        raise RuntimeFault('NEWS_LOG_BATCH_SET')
    ids = [json_string(e['event_id']) for e in actual]
    accepted = [json_string(item) for item in json_array(log.get('accepted', []))]
    if len(set(ids)) != len(ids) or len(set(accepted)) != len(accepted) or not set(accepted) <= set(ids):
        raise RuntimeFault('NEWS_LOG_ACCEPTED')
