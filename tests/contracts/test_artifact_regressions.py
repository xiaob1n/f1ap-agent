"""Security regressions for verified artifact bytes and CLI paths."""
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from f1ap_agent.contracts.artifact import Contract
from f1ap_agent.contracts.cli import run


EMPTY_EXPECTED = {
    'accepted': True,
    'stage': 'accepted',
    'error': None,
    'canonical_base64': 'e30=',
    'canonical_sha256': '44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a',
    'computed': {},
}
EMPTY_FIXTURE = {
    'id': 'canonical-empty',
    'kind': 'canonical',
    'input': 'fixtures/valid/canonical-empty.json',
    'source': 'TEST_FIXTURE',
    'category': 'canonical',
    'expected': EMPTY_EXPECTED,
}
LOCK_VALUE = {
    'release': '1.0.0',
    'manifest_sha256': '',
    'source': '../f1ap-api/contracts/v1',
    'acquisition': 'controlled-local-sibling',
}


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n').encode()


def make_contract(base, fixture=EMPTY_FIXTURE, files=None):
    root = base / 'f1ap-api' / 'contracts' / 'v1'
    lock_path = base / 'f1ap-agent' / 'contracts.lock.json'
    root.mkdir(parents=True)
    contents = {
        'schemas/envelope.schema.json': encoded({'type': 'object', 'additionalProperties': False}),
        'limits.json': encoded({'release': '1.0.0', 'raw_message_bytes': 2097152, 'payload_canonical_bytes': 524288, 'evidence_per_question': 8, 'excerpt_unicode_scalars': 1000, 'refresh_interval_minutes': 30, 'json_nesting_depth': 64}),
        'fixtures/index.json': encoded({'release': '1.0.0', 'fixtures': [fixture]}),
        fixture['input']: b' { }\n',
    }
    contents.update(files or {})
    for relative, raw in contents.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    registered = [
        {'path': relative, 'sha256': hashlib.sha256(raw).hexdigest()}
        for relative, raw in sorted(contents.items())
    ]
    manifest_raw = encoded({'release': '1.0.0', 'schema_version': '1.0', 'files': registered})
    (root / 'manifest.json').write_bytes(manifest_raw)
    lock_path.parent.mkdir(parents=True)
    lock_value = {**LOCK_VALUE, 'manifest_sha256': hashlib.sha256(manifest_raw).hexdigest()}
    lock_path.write_bytes(encoded(lock_value))
    return root, lock_path


class VerifiedBytesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            dir=os.environ.get('PI_SCRATCH_DIR')
        )
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def test_schema_uses_the_bytes_that_were_hashed(self):
        root, lock = make_contract(self.base)
        target = root / 'schemas/envelope.schema.json'
        changed = encoded({'type': 'string', 'additionalProperties': False})
        original_read_bytes = Path.read_bytes
        replaced = False

        def replace_after_read(path):
            nonlocal replaced
            raw = original_read_bytes(path)
            if path == target and not replaced:
                replaced = True
                path.write_bytes(changed)
            return raw

        with patch.object(Path, 'read_bytes', replace_after_read):
            contract = Contract(root, lock)
        contract.validate({}, schema='envelope.schema.json')

    def test_index_uses_the_bytes_that_were_hashed(self):
        root, lock = make_contract(self.base)
        target = root / 'fixtures/index.json'
        changed_fixture = {**EMPTY_FIXTURE, 'id': 'replacement-fixture'}
        changed = encoded({'release': '1.0.0', 'fixtures': [changed_fixture]})
        original_read_bytes = Path.read_bytes
        replaced = False

        def replace_after_read(path):
            nonlocal replaced
            raw = original_read_bytes(path)
            if path == target and not replaced:
                replaced = True
                path.write_bytes(changed)
            return raw

        with patch.object(Path, 'read_bytes', replace_after_read):
            contract = Contract(root, lock)
        self.assertEqual([fixture['id'] for fixture in contract.fixtures], ['canonical-empty'])

    def test_run_uses_hashed_fixture_and_context_bytes(self):
        source = Path(__file__).resolve().parents[3] / 'f1ap-api' / 'contracts' / 'v1'
        frozen_index = json.loads((source / 'fixtures/index.json').read_bytes())
        fixture = next(item for item in frozen_index['fixtures'] if item['id'] == 'completed-valid')
        copied = {
            path.relative_to(source).as_posix(): path.read_bytes()
            for path in (source / 'schemas').glob('*.json')
        }
        limits = json.loads((source / 'limits.json').read_bytes())
        limits['release'] = '1.0.0'
        copied['limits.json'] = encoded(limits)
        copied[fixture['input']] = (source / fixture['input']).read_bytes()
        copied[fixture['context']] = (source / fixture['context']).read_bytes()
        copied['fixtures/index.json'] = encoded({'release': '1.0.0', 'fixtures': [fixture]})
        root, lock = make_contract(self.base, fixture, copied)
        contract = Contract(root, lock)
        (root / fixture['input']).write_bytes(b'{"tampered":true}')
        (root / fixture['context']).write_bytes(b'{"tampered":true}')

        output = self.base / 'roundtrip-out'
        count = run(contract, self.base / 'report.json', output)

        self.assertEqual(count, 1)
        self.assertEqual(
            (output / 'completed-valid.json').read_bytes(),
            base64.b64decode(fixture['expected']['canonical_base64']),
        )


class RoundtripInputTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            dir=os.environ.get('PI_SCRATCH_DIR')
        )
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        root, lock = make_contract(self.base)
        self.contract = Contract(root, lock)

    def invoke(self, peer, out=None):
        return run(
            self.contract,
            self.base / 'report.json',
            out or self.base / 'roundtrip-out',
            peer,
        )

    def test_rejects_noncanonical_peer_bytes(self):
        peer = self.base / 'peer'
        peer.mkdir()
        (peer / 'canonical-empty.json').write_bytes(b' { }\n')
        with self.assertRaisesRegex(ValueError, 'ROUNDTRIP_CANONICAL'):
            self.invoke(peer)

    def test_rejects_unregistered_peer_files(self):
        peer = self.base / 'peer'
        peer.mkdir()
        (peer / 'canonical-empty.json').write_bytes(b'{}')
        (peer / 'extra.txt').write_text('not a fixture')
        with self.assertRaisesRegex(ValueError, 'ROUNDTRIP_SET'):
            self.invoke(peer)

    def test_rejects_peer_symlinks(self):
        peer = self.base / 'peer'
        peer.mkdir()
        victim = self.base / 'unrelated.txt'
        victim.write_text('sentinel')
        (peer / 'canonical-empty.json').write_bytes(b'{}')
        (peer / 'extra-link').symlink_to(victim)
        with self.assertRaisesRegex(ValueError, 'ROUNDTRIP_SYMLINK'):
            self.invoke(peer)

    def test_accepts_exact_canonical_peer_collection(self):
        peer = self.base / 'peer'
        peer.mkdir()
        (peer / 'canonical-empty.json').write_bytes(b'{}')
        self.assertEqual(self.invoke(peer), 1)


class OutputPathTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            dir=os.environ.get('PI_SCRATCH_DIR')
        )
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        root, lock = make_contract(self.base)
        self.contract = Contract(root, lock)

    def test_report_cannot_overwrite_contract_or_lock(self):
        lock_path = self.base / 'f1ap-agent' / 'contracts.lock.json'
        lock_before = lock_path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'OUTPUT_OVERLAP'):
            run(self.contract, lock_path, self.base / 'roundtrip-out')
        self.assertEqual(lock_path.read_bytes(), lock_before)

        manifest = self.contract.root / 'manifest.json'
        manifest_before = manifest.read_bytes()
        with self.assertRaisesRegex(ValueError, 'OUTPUT_OVERLAP'):
            run(self.contract, manifest, self.base / 'roundtrip-out')
        self.assertEqual(manifest.read_bytes(), manifest_before)

    def test_roundtrip_output_cannot_overlap_contract_or_lock(self):
        lock_path = self.base / 'f1ap-agent' / 'contracts.lock.json'
        for protected in (self.contract.root, lock_path):
            with self.subTest(protected=protected):
                with self.assertRaisesRegex(ValueError, 'OUTPUT_OVERLAP'):
                    run(self.contract, self.base / 'report.json', protected)

    def test_report_cannot_overlap_roundtrip_input(self):
        peer = self.base / 'peer'
        peer.mkdir()
        (peer / 'canonical-empty.json').write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'OUTPUT_OVERLAP'):
            run(self.contract, peer / 'nested' / 'report.json', self.base / 'roundtrip-out', peer)
        self.assertFalse((peer / 'nested').exists())

    def test_rejects_symlinked_output_ancestor(self):
        target = self.base / 'target'
        target.mkdir()
        link = self.base / 'linked'
        link.symlink_to(target, target_is_directory=True)
        victim = target / 'sentinel.txt'
        victim.write_text('unchanged')
        with self.assertRaisesRegex(ValueError, 'OUTPUT_SYMLINK'):
            run(self.contract, link / 'report.json', self.base / 'roundtrip-out')
        self.assertEqual(victim.read_text(), 'unchanged')

    def test_rejects_symlinked_roundtrip_output_directory(self):
        target = self.base / 'target'
        target.mkdir()
        link = self.base / 'linked-out'
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'OUTPUT_SYMLINK'):
            run(self.contract, self.base / 'report.json', link)
        self.assertEqual(list(target.iterdir()), [])

    def test_rejects_symlinked_output_file(self):
        output = self.base / 'roundtrip-out'
        output.mkdir()
        victim = self.base / 'victim.json'
        victim.write_text('sentinel')
        (output / 'canonical-empty.json').symlink_to(victim)
        with self.assertRaisesRegex(ValueError, 'OUTPUT_SYMLINK'):
            run(self.contract, self.base / 'report.json', output)
        self.assertEqual(victim.read_text(), 'sentinel')

    def test_rejects_unexpected_existing_output_files(self):
        output = self.base / 'roundtrip-out'
        output.mkdir()
        (output / 'unrelated.json').write_text('sentinel')
        with self.assertRaisesRegex(ValueError, 'OUTPUT_SET'):
            run(self.contract, self.base / 'report.json', output)
        self.assertEqual((output / 'unrelated.json').read_text(), 'sentinel')

if __name__ == '__main__':
    unittest.main()
