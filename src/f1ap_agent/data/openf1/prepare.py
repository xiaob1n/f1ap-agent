"""固定场次采集后冻结观测，再输出最终 mapping。"""
import hashlib
import json
import os
from pathlib import Path

from f1ap_agent.data.openf1.boundary import decode, exact, read_bytes, timestamp
from f1ap_agent.data.openf1.client import OpenF1Client, Response
from f1ap_agent.data.openf1.config import FIELDS, MAPPING_KEYS, Selection, parse_record
from f1ap_agent.data.openf1.snapshot import UNITS
from f1ap_agent.runtime import Json, RuntimeFault, json_string


def write_once(path: Path, raw: bytes) -> None:
    """独占创建冻结产物，不覆盖既有证据。"""
    with path.open('xb') as stream:
        _ = stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def encoded(value: Json) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def freeze(responses: tuple[Response, ...], output_dir: Path, generated_at: str) -> str:
    """保存原始响应及字段/单位/观测摘要，返回清单原始字节哈希。"""
    _ = timestamp(generated_at)
    entries: list[Json] = []
    if {response.endpoint for response in responses} != set(FIELDS) or len(responses) != len(FIELDS):
        raise RuntimeFault('OPENF1_RESPONSE_SET')
    for response in responses:
        if timestamp(response.first_observed_at) > generated_at:
            raise RuntimeFault('OPENF1_OBSERVATION_ORDER')
        filename = response.endpoint + '.json'
        write_once(output_dir / filename, response.raw)
        entries.append({'endpoint': response.endpoint, 'path': filename,
            'sha256': hashlib.sha256(response.raw).hexdigest(), 'row_count': len(response.rows),
            'first_observed_at': response.first_observed_at, 'fields': list(FIELDS[response.endpoint]),
            'observed_fields': sorted({key for row in response.rows for key in row})})
    manifest: dict[str, Json] = {'schema_version': 'OPENF1_SNAPSHOT_V1', 'generated_at': generated_at,
        'responses': entries, 'units': UNITS,
        'limitations': ['availability_unverified', 'fuel_unknown', 'traffic_track_evolution_unobserved']}
    raw = encoded(manifest)
    write_once(output_dir / 'manifest.json', raw)
    return hashlib.sha256(raw).hexdigest()


def prepare(spec_path: Path, output_dir: Path, client: OpenF1Client) -> Path:
    """准备配置是单条 mapping 记录（不含 manifest），不接受任意 URL/查询。"""
    item = exact(decode(read_bytes(spec_path)), MAPPING_KEYS - {'manifest'})
    item['manifest'] = {'path': 'manifest.json', 'sha256': '0' * 64}
    selection = Selection(json_string(item['round_id']), json_string(item['definition_sha256']), '0' * 64)
    config = parse_record(item, spec_path, selection)
    if output_dir.exists() or any(part.is_symlink() for part in (output_dir, *output_dir.parents)):
        raise RuntimeFault('OPENF1_OUTPUT_EXISTS_OR_SYMLINK')
    now = timestamp(client.clock())
    if now < config.event_cutoff_at or now > config.valid_until:
        raise RuntimeFault('OPENF1_PREPARATION_TIME')
    responses = client.collect(config)
    generated = timestamp(client.clock())
    if generated > config.valid_until:
        raise RuntimeFault('OPENF1_MAPPING_EXPIRED')
    output_dir.mkdir(parents=True)
    manifest_sha256 = freeze(responses, output_dir, generated)
    item['manifest'] = {'path': 'manifest.json', 'sha256': manifest_sha256}
    path = output_dir / 'mapping.json'
    write_once(path, encoded({'schema_version': 'OPENF1_MAPPING_V1', 'mappings': [item]}))
    return path
