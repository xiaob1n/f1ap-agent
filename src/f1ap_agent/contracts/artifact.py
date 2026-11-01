"""Fail-closed local artifact loading and deterministic draft-2020-12 subset."""
import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath

from jsonschema import Draft202012Validator
from .verifier import parse, pointer, reject, Rejection

KEYWORDS = {'$schema', '$defs', '$ref', 'type', 'properties', 'required',
            'additionalProperties', 'items', 'minItems', 'maxItems', 'minLength',
            'maxLength', 'minimum', 'maximum', 'pattern', 'enum', 'const',
            'oneOf', 'format', 'description'}
FORMATS = {'f1ap-id', 'f1ap-decimal', 'f1ap-nonnegative', 'f1ap-probability',
           'f1ap-utc-ms', 'f1ap-uuid', 'f1ap-sha256'}


def safe_file(root, relative):
    path = PurePosixPath(relative)
    if not relative or path.is_absolute() or '\\' in relative or any(p in {'.', '..'} for p in relative.split('/')):
        raise ValueError('ARTIFACT_PATH: ' + relative)
    target = root / relative
    current = root
    for part in path.parts:
        current /= part
        if current.is_symlink():
            raise ValueError('ARTIFACT_SYMLINK: ' + relative)
    if not target.is_file() or not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('ARTIFACT_MISSING: ' + relative)
    return target


def check_format(name, value, path):
    if value is None:
        return
    if name == 'f1ap-id':
        if not re.fullmatch(r'[1-9][0-9]{0,18}', value) or int(value) > 9223372036854775807:
            reject('schema', 'ID', path)
    elif name in {'f1ap-decimal', 'f1ap-nonnegative', 'f1ap-probability'}:
        if not re.fullmatch(r'-?(0|[1-9][0-9]{0,13})(\.[0-9]{0,5}[1-9])?', value) or value == '-0':
            reject('schema', 'DECIMAL', path)
        d = Decimal(value)
        if name != 'f1ap-decimal' and d < 0 or name == 'f1ap-probability' and d > 1:
            reject('schema', 'DECIMAL', path)
    elif name == 'f1ap-utc-ms':
        if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z', value):
            reject('schema', 'TIME', path)
        try:
            stamp = datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%fZ')
            if stamp.year < 1970:
                raise ValueError('year')
        except ValueError:
            reject('schema', 'TIME', path)
    elif name == 'f1ap-uuid':
        if not re.fullmatch(r'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}', value):
            reject('schema', 'FORMAT', path)
    elif name == 'f1ap-sha256':
        if not re.fullmatch(r'[0-9a-f]{64}', value):
            reject('schema', 'FORMAT', path)
    else:
        raise ValueError('unknown format: ' + name)


class SchemaSet:
    def __init__(self, root, files=None):
        self.root = Path(root)
        self._files = dict(files) if files is not None else None
        if self._files is None:
            schema_names = sorted(p.name for p in (self.root / 'schemas').glob('*.json'))
        else:
            schema_names = sorted(
                PurePosixPath(name).name
                for name in self._files
                if len(PurePosixPath(name).parts) == 2
                and PurePosixPath(name).parts[0] == 'schemas'
                and PurePosixPath(name).suffix == '.json'
            )
        self.schemas = {
            name: parse(self.read_bytes('schemas/' + name)) for name in schema_names
        }
        if not self.schemas:
            raise ValueError('no schemas')
        for name, node in self.schemas.items():
            Draft202012Validator.check_schema(node)
            self._inspect(node, name, set())
        self.limits = parse(self.read_bytes('limits.json'))

    def read_bytes(self, relative):
        if self._files is not None:
            try:
                return self._files[relative]
            except KeyError:
                raise ValueError('ARTIFACT_MISSING: ' + relative) from None
        return safe_file(self.root, relative).read_bytes()

    def resolve(self, ref, current):
        file, _, fragment = ref.partition('#')
        file = file or current
        if '/' in file or '\\' in file or ':' in file or file not in self.schemas:
            raise ValueError('LOCAL_REF: ' + ref)
        node = self.schemas[file]
        if fragment:
            if not fragment.startswith('/'):
                raise ValueError('LOCAL_REF fragment')
            for part in fragment[1:].split('/'):
                node = node[part.replace('~1', '/').replace('~0', '~')]
        return node, file

    def _inspect(self, node, current, stack):
        if not isinstance(node, dict) or set(node) - KEYWORDS:
            raise ValueError('unsupported schema keywords')
        if 'format' in node and node['format'] not in FORMATS:
            raise ValueError('unsupported schema format')
        if node.get('additionalProperties', False) is not False:
            raise ValueError('open schema object')
        if '$ref' in node:
            key = (current, node['$ref'])
            if key in stack:
                raise ValueError('cyclic ref')
            resolved, filename = self.resolve(node['$ref'], current)
            self._inspect(resolved, filename, stack | {key})
        for group in ('$defs', 'properties'):
            for child in node.get(group, {}).values():
                self._inspect(child, current, stack)
        if 'items' in node:
            self._inspect(node['items'], current, stack)
        for child in node.get('oneOf', []):
            self._inspect(child, current, stack)

    def validate(self, value, schema='envelope.schema.json', path='', current=None):
        if isinstance(schema, str):
            current, node = schema, self.schemas[schema]
        else:
            node = schema
        if '$ref' in node:
            resolved, filename = self.resolve(node['$ref'], current)
            self.validate(value, resolved, path, filename)
        if 'oneOf' in node:
            successes = 0
            for branch in node['oneOf']:
                try:
                    self.validate(value, branch, path, current)
                    successes += 1
                except Rejection:
                    pass
            if successes != 1:
                reject('schema', 'SCHEMA', path)
        actual = 'null' if value is None else 'boolean' if type(value) is bool else 'integer' if type(value) is int else 'string' if isinstance(value, str) else 'array' if isinstance(value, list) else 'object'
        if 'type' in node:
            types = node['type'] if isinstance(node['type'], list) else [node['type']]
            if actual not in types:
                reject('schema', 'TYPE', path)
        if 'const' in node and (type(value) is not type(node['const']) or value != node['const']):
            reject('schema', 'VERSION' if path == '/schema_version' else 'CONST', path)
        if 'enum' in node and value not in node['enum']:
            reject('schema', 'EVENT' if path == '/event_type' else 'ENUM', path)
        if 'format' in node:
            check_format(node['format'], value, path)
        if actual == 'integer':
            if 'minimum' in node and value < node['minimum'] or 'maximum' in node and value > node['maximum']:
                reject('schema', 'BOUNDS', path)
        if actual in {'string', 'array'}:
            lo, hi = ('minLength', 'maxLength') if actual == 'string' else ('minItems', 'maxItems')
            if lo in node and len(value) < node[lo] or hi in node and len(value) > node[hi]:
                reject('schema', 'BOUNDS', path)
        if actual == 'string' and 'pattern' in node and not re.search(node['pattern'], value):
            reject('schema', 'PATTERN', path)
        if actual == 'object':
            for key in sorted(node.get('required', [])):
                if key not in value:
                    reject('schema', 'REQUIRED', pointer(path, key))
            props = node.get('properties', {})
            if node.get('additionalProperties') is False:
                for key in sorted(set(value) - set(props)):
                    reject('schema', 'UNKNOWN_FIELD', pointer(path, key))
            for key in sorted(props):
                if key in value:
                    self.validate(value[key], props[key], pointer(path, key), current)
        if actual == 'array' and 'items' in node:
            for i, item in enumerate(value):
                self.validate(item, node['items'], pointer(path, i), current)


class Contract(SchemaSet):
    def __init__(self, root, lock):
        root = Path(root).absolute()
        lock_path = Path(lock).absolute()
        if root.is_symlink():
            raise ValueError('ARTIFACT_SYMLINK root')
        if lock_path.is_symlink():
            raise ValueError('ARTIFACT_SYMLINK lock')
        root = root.resolve()
        lock_path = lock_path.resolve()
        lock = parse(lock_path.read_bytes())
        if lock.get('source') != '../f1ap-api/contracts/v1' or lock.get('acquisition') != 'controlled-local-sibling':
            raise ValueError('ARTIFACT_LOCK source')
        manifest_raw = safe_file(root, 'manifest.json').read_bytes()
        self.manifest_sha256 = hashlib.sha256(manifest_raw).hexdigest()
        manifest = parse(manifest_raw)
        pinned_release = lock.get('release')
        if not isinstance(pinned_release, str) or not re.fullmatch(r'1\.0\.(0|[1-9][0-9]*)', pinned_release) or manifest.get('release') != pinned_release or lock.get('manifest_sha256') != self.manifest_sha256 or manifest.get('schema_version') != '1.0':
            raise ValueError('ARTIFACT_LOCK')
        self.release = manifest['release']
        self.lock_path = lock_path
        registered = set()
        verified = {}
        for item in manifest['files']:
            name = item['path']
            if name in registered or name == 'manifest.json':
                raise ValueError('ARTIFACT_DUPLICATE')
            registered.add(name)
            raw = safe_file(root, name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != item['sha256']:
                raise ValueError('ARTIFACT_HASH: ' + name)
            verified[name] = raw
        all_paths = list(root.rglob('*'))
        if any(p.is_symlink() for p in all_paths):
            raise ValueError('ARTIFACT_SYMLINK')
        actual = {p.relative_to(root).as_posix() for p in all_paths if p.is_file()} - {'manifest.json'}
        if actual != registered or not registered:
            raise ValueError('ARTIFACT_FILE_SET')
        self.files = verified
        super().__init__(root, verified)
        expected_limits = {'release': self.release, 'raw_message_bytes': 2097152,
                           'payload_canonical_bytes': 524288, 'evidence_per_question': 8,
                           'excerpt_unicode_scalars': 1000, 'refresh_interval_minutes': 30,
                           'json_nesting_depth': 64}
        if self.limits != expected_limits or any(type(self.limits.get(key)) is not type(value) for key, value in expected_limits.items()):
            raise ValueError('ARTIFACT_LIMITS')
        index = parse(self.read_bytes('fixtures/index.json'))
        self.fixtures = index['fixtures']
        ids = [f['id'] for f in self.fixtures]
        if index['release'] != self.release or not ids or len(ids) != len(set(ids)):
            raise ValueError('FIXTURE_SET')
        for fixture in self.fixtures:
            if not re.fullmatch(r'[a-z0-9-]+', fixture['id']) or fixture['source'] != 'TEST_FIXTURE':
                raise ValueError('FIXTURE_ID')
            for name in [fixture['input'], *([fixture['context']] if 'context' in fixture else [])]:
                safe_file(root, name)
                if name not in verified:
                    raise ValueError('ARTIFACT_UNREGISTERED: ' + name)
