"""研究参数与显式会话边界。"""
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from f1ap_agent.runtime import Json, RuntimeFault, json_integer, json_object


def utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise RuntimeFault('CUTOFF_MUST_BE_UTC')
    return parsed


@dataclass(frozen=True, slots=True)
class Config:
    timeout_seconds: int = 60
    max_model_rounds: int = 6
    max_tokens: int = 4096
    row_limit: int = 100
    byte_limit: int = 65536

    @classmethod
    def load(cls, path: Path) -> 'Config':
        with path.open('rb') as stream:
            raw: Json = tomllib.load(stream)
        return cls.from_config(json_object(raw))

    @classmethod
    def from_config(cls, values: dict[str, Json]) -> 'Config':
        """本地文件和启动配置共用同一校验。"""
        if set(values) != set(cls.__dataclass_fields__):
            raise RuntimeFault('CONFIG_FIELDS')
        bounds = {'timeout_seconds': 300, 'max_model_rounds': 30,
                  'max_tokens': 16384, 'row_limit': 1000, 'byte_limit': 1048576}
        integers = {key: json_integer(value) for key, value in values.items()}
        if any(not 1 <= value <= bounds[key] for key, value in integers.items()):
            raise RuntimeFault('CONFIG_LIMITS')
        return cls(**integers)


@dataclass(frozen=True, slots=True)
class Request:
    question: str
    session_key: int
    cutoff: str
    output: Path
    definition_path: Path | None = None

    def validate(self) -> None:
        if not self.question.strip() or len(self.question) > 10000 or self.session_key <= 0:
            raise RuntimeFault('RESEARCH_INPUT')
        if utc(self.cutoff) > datetime.now(UTC):
            raise RuntimeFault('CUTOFF_IN_FUTURE')
