"""Independent Python contract verifier; never imports release authoring code."""
import base64
import hashlib
import re
from .raw_json import decode, StructuralError


class Rejection(Exception):
    def __init__(self, stage, code, path=''):
        super().__init__(f'{stage}:{code}:{path}')
        self.stage, self.code, self.path = stage, code, path


def reject(stage, code, path=''):
    raise Rejection(stage, code, path)


def pointer(path, key):
    return path + '/' + str(key).replace('~', '~0').replace('/', '~1')


class Pairs(list):
    """Preserves duplicate keys until a path-aware walk has seen them."""


class Number(str):
    """Preserves JSON numeric lexemes until domain validation."""


MAX_JSON_DEPTH = 64


def parse(raw):
    if len(raw) > 2097152:
        reject('parse', 'SIZE')
    try:
        text = raw.decode('utf-8', errors='strict')
    except UnicodeDecodeError:
        reject('parse', 'ENCODING')
    if text.startswith('\ufeff'):
        reject('parse', 'ENCODING')

    try:
        value = decode(text, Pairs, Number, MAX_JSON_DEPTH)
    except StructuralError as error:
        reject('parse', error.code)

    duplicate_error = None
    unicode_error = None
    number_error = None

    def record_unicode(path):
        nonlocal unicode_error
        if unicode_error is None:
            unicode_error = Rejection('parse', 'UNICODE', path)

    def record_number(path):
        nonlocal number_error
        if number_error is None:
            number_error = Rejection('parse', 'NUMBER', path)

    def scalar(s, path):
        if any(0xD800 <= ord(c) <= 0xDFFF for c in s):
            record_unicode(path)

    def walk(node, path):
        nonlocal duplicate_error
        if isinstance(node, Pairs):
            out = {}
            for key, value_item in node:
                child_path = pointer(path, key)
                scalar(key, child_path)
                if key in out and duplicate_error is None:
                    duplicate_error = Rejection('parse', 'DUPLICATE_KEY', child_path)
                parsed_item = walk(value_item, child_path)
                if key not in out:
                    out[key] = parsed_item
            return out
        if isinstance(node, list):
            return [walk(value_item, pointer(path, index)) for index, value_item in enumerate(node)]
        if isinstance(node, Number):
            valid = bool(re.fullmatch(r'-?(0|[1-9][0-9]*)', node)) and node != '-0' and len(node) <= 11
            if not valid:
                record_number(path)
                return 0
            number = int(node)
            if not -2147483648 <= number <= 2147483647:
                record_number(path)
                return 0
            return number
        if isinstance(node, str):
            scalar(node, path)
        return node

    try:
        result = walk(value, '')
    except RecursionError:
        reject('parse', 'JSON')
    if duplicate_error is not None:
        raise duplicate_error
    if unicode_error is not None:
        raise unicode_error
    if number_error is not None:
        raise number_error
    return result


def canonical(value):
    # Deliberately independent of the release author's json.dumps oracle.
    escapes = {'"': '\\"', '\\': '\\\\', '\b': '\\b', '\f': '\\f', '\n': '\\n', '\r': '\\r', '\t': '\\t'}
    def quote(s):
        return '"' + ''.join(escapes.get(c, '\\u%04x' % ord(c) if ord(c) < 32 else c) for c in s) + '"'
    def encode(v):
        if v is None: return 'null'
        if v is True: return 'true'
        if v is False: return 'false'
        if isinstance(v, str): return quote(v)
        if type(v) is int: return str(v)
        if isinstance(v, list): return '[' + ','.join(encode(x) for x in v) + ']'
        if isinstance(v, dict): return '{' + ','.join(quote(k) + ':' + encode(v[k]) for k in sorted(v)) + '}'
        raise TypeError('not in canonical domain')
    return encode(value).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def evaluate(raw, *, kind='event', contract=None, context=None):
    try:
        value = parse(raw)
        computed = {}
        if kind == 'event':
            from .semantics import event
            computed = event(value, contract, context)
        elif kind == 'definition_projection':
            from .semantics import definition
            value = value.get('definition') if isinstance(value, dict) else None
            contract.validate(value, {'$ref': 'common.schema.json#/$defs/definition'}, path='/definition', current='common.schema.json')
            definition(value, [q['question_id'] for q in value['questions']], path='/definition', association_path='/definition/questions')
        elif kind != 'canonical':
            raise ValueError('unknown fixture kind')
        c = canonical(value)
        return {'accepted': True, 'stage': 'accepted', 'error': None,
                'canonical_base64': base64.b64encode(c).decode('ascii'),
                'canonical_sha256': hashlib.sha256(c).hexdigest(), 'computed': computed}
    except Rejection as exc:
        return {'accepted': False, 'stage': exc.stage,
                'error': {'code': exc.code, 'path': exc.path},
                'canonical_base64': None, 'canonical_sha256': None, 'computed': {}}
