"""Single-writer durable JSON storage for the local B1 worker."""
import json
import os
import tempfile
import hashlib
import fcntl
from contextlib import contextmanager
from collections.abc import Iterator
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

type Json = str | int | bool | None | Sequence[Json] | Mapping[str, Json]


def json_object(value: Json) -> dict[str, Json]:
    if not isinstance(value, dict):
        raise RuntimeFault('EXPECTED_OBJECT')
    return value


def json_events(value: Json) -> list[dict[str, Json]]:
    if not isinstance(value, list):
        raise RuntimeFault('EXPECTED_EVENTS')
    return [json_object(item) for item in value]


def json_string(value: Json) -> str:
    if not isinstance(value, str):
        raise RuntimeFault('EXPECTED_STRING')
    return value


def json_integer(value: Json) -> int:
    if type(value) is not int:
        raise RuntimeFault('EXPECTED_INTEGER')
    return value


def json_array(value: Json) -> list[Json]:
    if not isinstance(value, list):
        raise RuntimeFault('EXPECTED_ARRAY')
    return value


class RuntimeFault(Exception):
    """Typed boundary failure that remains traceback-compatible."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code

    def __str__(self) -> str:
        return self.code


def atomic_json(path: Path, value: Json) -> None:
    """Flush content before replacement, then flush the containing directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True).encode())
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)


def read_json(path: Path) -> Json:
    """Read persisted JSON; missing and corrupt state are never fresh history."""
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError) as error:
        raise RuntimeFault('STATE_MISSING_OR_CORRUPT') from error


@dataclass(frozen=True, slots=True)
class Store:
    root: Path

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        """Reject concurrent writers without waiting or changing run history."""
        if not self.root.is_dir():
            raise RuntimeFault('RUNTIME_NOT_INITIALIZED')
        with (self.root / '.writer.lock').open('a+b') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeFault('WRITER_BUSY') from error
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def initialize(self) -> None:
        """Initialize only a new or empty root, never reset existing history."""
        if self.root.exists() and any(self.root.iterdir()):
            raise RuntimeFault('RUNTIME_NOT_EMPTY')
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_json(self.root / 'runs.json', {})

    def registry(self) -> dict[str, Json]:
        value = read_json(self.root / 'runs.json')
        if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
            raise RuntimeFault('REGISTRY_CORRUPT')
        return value

    def log_path(self, run_id: str) -> Path:
        return self.root / 'runs' / run_id / 'log.json'

    def save(self, run_id: str, value: dict[str, Json]) -> None:
        checksum = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        atomic_json(self.log_path(run_id), {'sha256': checksum, 'log': value})

    def registry_log(self, run_id: str) -> dict[str, Json]:
        value = read_json(self.log_path(run_id))
        if not isinstance(value, dict) or set(value) != {'sha256', 'log'}:
            raise RuntimeFault('LOG_CORRUPT')
        log = json_object(value['log'])
        checksum = hashlib.sha256(json.dumps(log, sort_keys=True).encode()).hexdigest()
        if checksum != value['sha256'] or log.get('run_id') != run_id:
            raise RuntimeFault('LOG_CORRUPT')
        return log
