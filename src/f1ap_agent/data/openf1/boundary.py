"""映射及冻结文件共用的严格边界。"""
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Final

from f1ap_agent.runtime import Json, RuntimeFault, json_object, json_string

MAX_BYTES: Final = 8 * 1024 * 1024


def timestamp(value: Json) -> str:
    text = json_string(value)
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z', text):
        raise RuntimeFault('OPENF1_TIME_FORMAT')
    try:
        _ = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError as error:
        raise RuntimeFault('OPENF1_TIME_FORMAT') from error
    return text


def sha(value: Json) -> str:
    text = json_string(value)
    if not re.fullmatch(r'[0-9a-f]{64}', text):
        raise RuntimeFault('OPENF1_HASH_FORMAT')
    return text


def exact(value: Json, keys: set[str]) -> dict[str, Json]:
    item = json_object(value)
    if set(item) != keys:
        raise RuntimeFault('OPENF1_FIELDS')
    return item


def safe_path(root: Path, reference: str) -> Path:
    relative = PurePosixPath(reference)
    if not reference or relative.is_absolute() or '..' in relative.parts or '\\' in reference:
        raise RuntimeFault('OPENF1_PATH_ESCAPE')
    candidate = root / relative
    if any(part.is_symlink() for part in (candidate, *candidate.parents)):
        raise RuntimeFault('OPENF1_PATH_SYMLINK')
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise RuntimeFault('OPENF1_PATH_ESCAPE')
    return candidate


def read_bytes(path: Path) -> bytes:
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise RuntimeFault('OPENF1_PATH_SYMLINK')
    try:
        with path.open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
    except OSError as error:
        raise RuntimeFault('OPENF1_FILE_UNAVAILABLE') from error
    if len(raw) > MAX_BYTES:
        raise RuntimeFault('OPENF1_RESPONSE_SIZE')
    return raw


def unique_pairs(pairs: list[tuple[str, Json]]) -> dict[str, Json]:
    result: dict[str, Json] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeFault('OPENF1_DUPLICATE_KEY')
        result[key] = value
    return result


def decode(raw: bytes) -> Json:
    try:
        value: Json = json.loads(raw, parse_float=str, parse_constant=str, object_pairs_hook=unique_pairs)
        return value
    except (ValueError, UnicodeError, RecursionError) as error:
        raise RuntimeFault('OPENF1_JSON') from error


def checked_file(path: Path, expected: str) -> Json:
    raw = read_bytes(path)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise RuntimeFault('OPENF1_HASH_MISMATCH')
    return decode(raw)
