"""Offline verification CLI. Any drift, missing fixture, or mismatch is fatal."""
import argparse
import base64
import json
import os
import sys
from pathlib import Path

from .artifact import Contract, safe_file
from .verifier import evaluate, parse


def _absolute(path):
    path = Path(path)
    if not path.is_absolute():
        path = Path.cwd() / path
    return Path(os.path.abspath(os.fspath(path)))


def _reject_symlinks(path, code='OUTPUT_SYMLINK'):
    path = Path(path)
    if not path.is_absolute():
        path = Path.cwd() / path
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError(f'{code}: {path}')


def _overlaps(left, right):
    left = _absolute(left)
    right = _absolute(right)
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _check_tree(path, code):
    _reject_symlinks(path, code)
    path = Path(path)
    if not path.exists() or not path.is_dir():
        raise ValueError(f'{code}: {path}')
    entries = list(path.rglob('*'))
    if any(entry.is_symlink() for entry in entries):
        raise ValueError(f'{code}: {path}')
    return entries


def _prepare_paths(contract, report, output, roundtrip_in, accepted_ids):
    protected = [contract.root]
    lock_path = getattr(contract, 'lock_path', None)
    if lock_path is not None:
        protected.append(lock_path)
    if roundtrip_in is not None:
        _check_tree(roundtrip_in, 'ROUNDTRIP_SYMLINK')
        _check_roundtrip_set(roundtrip_in, accepted_ids)
        protected.append(roundtrip_in)
    for candidate in (report, output):
        _reject_symlinks(candidate)
        if any(_overlaps(candidate, path) for path in protected):
            raise ValueError(f'OUTPUT_OVERLAP: {candidate}')
    if _overlaps(report, output):
        raise ValueError('OUTPUT_OVERLAP: report and roundtrip-out')
    output = Path(output)
    if output.exists():
        entries = _check_tree(output, 'OUTPUT_SYMLINK')
        actual = {
            entry.relative_to(output).as_posix()
            for entry in entries
            if entry.is_file()
        }
        if actual - {fixture_id + '.json' for fixture_id in accepted_ids} or len(entries) != len(actual):
            raise ValueError('OUTPUT_SET')
    else:
        output.mkdir(parents=True, exist_ok=True)
        _reject_symlinks(output)
    report = Path(report)
    _reject_symlinks(report)
    if report.exists() and not report.is_file():
        raise ValueError('OUTPUT_PATH: report is not a regular file')
    report.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlinks(report)


def _check_roundtrip_set(roundtrip_in, accepted_ids):
    entries = _check_tree(roundtrip_in, 'ROUNDTRIP_SYMLINK')
    actual = {
        entry.relative_to(roundtrip_in).as_posix()
        for entry in entries
        if entry.is_file()
    }
    expected = {fixture_id + '.json' for fixture_id in accepted_ids}
    if actual != expected or len(entries) != len(actual):
        raise ValueError('ROUNDTRIP_SET')


def run(contract, report, roundtrip_out, roundtrip_in=None):
    output = Path(roundtrip_out)
    peer = Path(roundtrip_in) if roundtrip_in is not None else None
    accepted_ids = {f['id'] for f in contract.fixtures if f['expected']['accepted']}
    _prepare_paths(contract, report, output, peer, accepted_ids)
    results, failures = [], []
    for fixture in contract.fixtures:
        context = parse(contract.read_bytes(fixture['context'])) if 'context' in fixture else None
        raw = contract.read_bytes(fixture['input'])
        if peer is not None and fixture['expected']['accepted']:
            raw = safe_file(peer, fixture['id'] + '.json').read_bytes()
            expected_raw = base64.b64decode(fixture['expected']['canonical_base64'], validate=True)
            if raw != expected_raw:
                raise ValueError('ROUNDTRIP_CANONICAL: ' + fixture['id'])
            if fixture['kind'] == 'definition_projection':
                raw = b'{"definition":' + raw + b'}'
        result = evaluate(raw, kind=fixture['kind'], contract=contract, context=context)
        results.append({'id': fixture['id'], **result})
        if result != fixture['expected']:
            failures.append({'id': fixture['id'], 'actual_error': result['error'], 'expected_error': fixture['expected']['error']})
        if result['accepted']:
            target = output / (fixture['id'] + '.json')
            _reject_symlinks(target)
            target.write_bytes(base64.b64decode(result['canonical_base64'], validate=True))
    report_value = {'language': 'python', 'release': contract.release,
                    'manifest_sha256': contract.manifest_sha256,
                    'fixture_count': len(results), 'results': results}
    report = Path(report)
    _reject_symlinks(report)
    report.write_text(json.dumps(report_value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if failures:
        raise ValueError('FIXTURE_MISMATCH: ' + json.dumps(failures, ensure_ascii=False))
    return len(results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    agent_root = Path(__file__).resolve().parents[3]
    parser.add_argument('--lock', type=Path, default=agent_root / 'contracts.lock.json')
    parser.add_argument('--contracts', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--roundtrip-out', type=Path, required=True)
    parser.add_argument('--roundtrip-in', type=Path)
    args = parser.parse_args()
    try:
        if args.contracts is None:
            lock = parse(args.lock.read_bytes())
            args.contracts = (args.lock.parent / lock['source']).resolve()
        contract = Contract(args.contracts, args.lock)
        count = run(contract, args.report, args.roundtrip_out, args.roundtrip_in)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f'python: {count}/{count} fixtures matched; {contract.manifest_sha256}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
