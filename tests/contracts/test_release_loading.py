"""Explicitly pinned patch releases load; mismatched release metadata fails closed."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from f1ap_agent.contracts.artifact import Contract


class ReleaseLoadingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('PI_SCRATCH_DIR'))
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        source = Path(__file__).resolve().parents[3] / 'f1ap-api/contracts/v1'
        self.root = self.base / 'contracts'
        shutil.copytree(source, self.root)
        self.lock = self.base / 'contracts.lock.json'
        for name in ('fixtures/index.json', 'limits.json'):
            file = self.root / name
            value = json.loads(file.read_bytes())
            value['release'] = '1.0.2'
            file.write_text(json.dumps(value))
        self.seal()

    def seal(self):
        files = [{'path': p.relative_to(self.root).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                 for p in sorted(self.root.rglob('*')) if p.is_file() and p.name != 'manifest.json']
        manifest = self.root / 'manifest.json'
        manifest.write_text(json.dumps({'release': '1.0.2', 'schema_version': '1.0', 'files': files}))
        self.lock.write_text(json.dumps({'release': '1.0.2', 'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
                                        'source': '../f1ap-api/contracts/v1', 'acquisition': 'controlled-local-sibling'}))

    def test_explicit_patch_pin_loads_without_rewriting_lock(self):
        original = self.lock.read_bytes()
        contract = Contract(self.root, self.lock)
        self.assertEqual(contract.release, '1.0.2')
        self.assertEqual(self.lock.read_bytes(), original)

    def test_metadata_release_mismatch_fails(self):
        for name in ('limits.json', 'fixtures/index.json'):
            with self.subTest(name=name):
                file = self.root / name
                original = file.read_bytes()
                value = json.loads(original); value['release'] = '1.0.1'
                file.write_text(json.dumps(value)); self.seal()
                with self.assertRaises(ValueError):
                    Contract(self.root, self.lock)
                file.write_bytes(original)

    def test_unapproved_release_or_hash_does_not_follow_latest(self):
        for field, value in (('release', '1.0.3'), ('manifest_sha256', '0' * 64)):
            self.seal()
            lock = json.loads(self.lock.read_bytes()); lock[field] = value
            self.lock.write_text(json.dumps(lock))
            before = self.lock.read_bytes()
            with self.assertRaises(ValueError):
                Contract(self.root, self.lock)
            self.assertEqual(self.lock.read_bytes(), before)
