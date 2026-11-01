"""Probability outcome identity must not depend only on an arbitrary label."""
import copy
import hashlib
import json
from pathlib import Path
import unittest

from f1ap_agent.contracts import evaluate
from f1ap_agent.contracts.artifact import SchemaSet


def raw_message(message):
    # Input construction only; expected rejection is a literal, independent of validators.
    def sha(value):
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
        return hashlib.sha256(raw).hexdigest()
    payload = message['payload']
    payload['result_sha256'] = sha({k: v for k, v in payload.items() if k != 'result_sha256'})
    message['payload_sha256'] = sha(payload)
    return json.dumps(message).encode()


class ProbabilityIdentityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[3] / 'f1ap-api/contracts/v1'
        cls.contract = SchemaSet(root)
        cls.message = json.loads((root / 'fixtures/valid/completed-valid.json').read_bytes())
        cls.context = json.loads((root / 'fixtures/contexts/completed-valid.json').read_bytes())

    def check_rejected(self, change):
        message = copy.deepcopy(self.message)
        change(message['payload']['probabilities'])
        result = evaluate(raw_message(message), contract=self.contract, context=self.context)
        self.assertFalse(result['accepted'])
        self.assertEqual(result['error'], {'code': 'PROBABILITY_EVENT', 'path': '/payload/probabilities'})

    def test_duplicate_exclusive_option_different_labels_is_rejected(self):
        def change(probabilities):
            probabilities[1]['option_keys'] = probabilities[0]['option_keys'][:]
        self.check_rejected(change)

    def test_no_award_label_with_real_option_is_rejected(self):
        self.check_rejected(lambda probabilities: probabilities[0].update(event_key='NO_AWARD'))

    def test_duplicate_zero_probability_option_is_still_rejected(self):
        def change(probabilities):
            duplicate = copy.deepcopy(probabilities[2])
            duplicate['event_key'] = 'another-label-same-outcome'
            probabilities.append(duplicate)
        self.check_rejected(change)
