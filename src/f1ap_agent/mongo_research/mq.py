"""研究调用至多一次，完成事件至少一次发送；由消费端持有 runtime 写锁。"""
import os
from collections.abc import Callable
from contextvars import Context
from pathlib import Path
from typing import TYPE_CHECKING

import anyio

from f1ap_agent.contracts.verifier import canonical, digest, parse
from f1ap_agent.runtime import Json, RuntimeFault, atomic_json, json_integer, json_object, json_string, read_json
from .config import Config, Request
from .mq_contract import ResearchTask, stamp

if TYPE_CHECKING:
    from f1ap_agent.worker.engine import Worker


async def invoke(request: Request, config: Config, remaining: float) -> None:
    from .__main__ import run
    # 超时覆盖 MCP 初始化、全部模型轮次及资源清理，不按消息重新拉配置。
    with anyio.fail_after(remaining), open(os.devnull, 'w') as errors:
        await run(request, config, errors)


def save(path: Path, records: dict[str, Json]) -> None:
    atomic_json(path, {'sha256': digest(records), 'records': records})


def process(worker: 'Worker', task: ResearchTask, clock: Callable[[], str]) -> bool:
    if stamp(task.request['occurred_at']) > stamp(clock()):
        raise RuntimeFault('UNSCORED_FUTURE_REQUEST')
    path = worker.store.root / 'unscored' / 'records.json'
    records: dict[str, Json] = {}
    if path.exists():
        document = json_object(parse(path.read_bytes()))
        if set(document) != {'sha256', 'records'} or digest(document['records']) != document['sha256']:
            raise RuntimeFault('UNSCORED_RECORD_CORRUPT')
        records = json_object(document['records'])
    for run_id, raw_record in records.items():
        record = json_object(raw_record)
        if set(record) != {'request', 'phase', 'event', 'confirmed'} or type(record['confirmed']) is not bool:
            raise RuntimeFault('UNSCORED_RECORD_CORRUPT')
        previous = ResearchTask.parse(canonical(record['request']), worker.contract)
        if previous.run_id != run_id or record['phase'] not in ('REGISTERED', 'INTENT', 'COMPLETED'):
            raise RuntimeFault('UNSCORED_RECORD_CORRUPT')
        if record['phase'] == 'COMPLETED':
            event = json_object(record['event'])
            if event != previous.completed(json_object(event.get('payload', {})).get('prediction')):
                raise RuntimeFault('UNSCORED_RECORD_CORRUPT')
        elif record['event'] is not None or record['confirmed']:
            raise RuntimeFault('UNSCORED_RECORD_CORRUPT')
        if (previous.run_id == task.run_id or previous.request_id == task.request_id
                or previous.request['event_id'] == task.request['event_id']):
            if previous.request != task.request:
                raise RuntimeFault('UNSCORED_IDENTITY_CONFLICT')
    directory = worker.store.root / 'unscored' / 'runs' / task.run_id
    if task.run_id not in records:
        if directory.exists():
            raise RuntimeFault('UNSCORED_UNREGISTERED_RUN')
        if worker.research_config is None:
            raise RuntimeFault('UNSCORED_CONFIG_REQUIRED')
        records[task.run_id] = {'request': task.request, 'phase': 'REGISTERED',
                                'event': None, 'confirmed': False}
        save(path, records)
        worker.checkpoint('unscored_registered')
    record = json_object(records[task.run_id])
    if record['phase'] != 'COMPLETED':
        reason = 'RESEARCH_INTERRUPTED'
        prediction: Json = None
        if record['phase'] == 'REGISTERED':
            if worker.research_config is None:
                raise RuntimeFault('UNSCORED_CONFIG_REQUIRED')
            remaining = (task.expires_at - stamp(clock())).total_seconds()
            if remaining <= 0:
                reason = 'RESEARCH_EXPIRED'
            else:
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                definition_path = directory / 'definition.json'
                atomic_json(definition_path, task.definition)
                request = Request('依据提供的十题定义和历史参考会话给出无积分研究预测。',
                    json_integer(task.payload['reference_session_key']), json_string(task.payload['data_cutoff_at']),
                    directory, definition_path)
                record['phase'] = 'INTENT'
                save(path, records)
                worker.checkpoint('unscored_after_intent')
                try:
                    remaining = (task.expires_at - stamp(clock())).total_seconds()
                    # asyncio.to_thread 复制了异步库标记；新事件循环使用独立上下文。
                    Context().run(anyio.run, invoke, request, worker.research_config, max(0, remaining))
                    worker.checkpoint('unscored_after_effect')
                    generated = json_object(read_json(directory / 'predictions.json'))
                    prediction = task.prediction({**generated, 'request_id': task.request_id})
                    atomic_json(directory / 'predictions.json', prediction)
                except (RuntimeFault, OSError, ValueError, TypeError, KeyError, ExceptionGroup) as error:
                    # 只保存稳定类别，第三方错误正文可能含连接凭据。
                    reason = 'RESEARCH_FAILED_' + type(error).__name__
        if prediction is None:
            prediction = task.failure(reason, clock())
        record.update(phase='COMPLETED', event=task.completed(prediction))
        save(path, records)
        worker.checkpoint('unscored_after_completed')
    if record['confirmed']:
        return True
    event = json_object(record['event'])
    if not worker.sender.send(event):
        return False
    worker.checkpoint('unscored_after_confirm')
    record['confirmed'] = True
    save(path, records)
    return True
