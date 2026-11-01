"""Tests at the approved raw-byte/contract-loading seams, not internals."""
import json
from pathlib import Path
import unittest
from f1ap_agent.contracts import evaluate


class CanonicalBoundaryTest(unittest.TestCase):
    def test_canonical_empty_has_fixed_sha256(self):
        result = evaluate(b' { }\n', kind='canonical')
        self.assertTrue(result['accepted'])
        self.assertEqual(result['canonical_base64'], 'e30=')
        self.assertEqual(result['canonical_sha256'], '44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a')


class EventBoundaryTest(unittest.TestCase):
    def test_json_depth_limit_classifies_canonical_and_event_consistently(self):
        from f1ap_agent.contracts.artifact import SchemaSet
        root = Path(__file__).resolve().parents[3] / 'f1ap-api/contracts/v1'
        raw = b'[' * 65 + b'0' + b']' * 65
        expected = {
            'accepted': False,
            'stage': 'parse',
            'error': {'code': 'DEPTH', 'path': ''},
            'canonical_base64': None,
            'canonical_sha256': None,
            'computed': {},
        }
        contract = SchemaSet(root)
        self.assertEqual(evaluate(raw, kind='canonical'), expected)
        self.assertEqual(evaluate(raw, kind='event', contract=contract), expected)

        canonical64 = b'[' * 64 + b'0' + b']' * 64
        accepted = evaluate(canonical64, kind='canonical')
        self.assertEqual(accepted['canonical_sha256'], 'f0244ae1938270f711155b0a26a4307cde7050495140dd858a88a1cfc378cf40')
        self.assertTrue(accepted['accepted'])
        event64 = (root / 'fixtures/invalid/event-depth-64.json').read_bytes()
        event64_result = evaluate(event64, contract=contract)
        self.assertEqual(event64_result['error'], {'code': 'UNKNOWN_FIELD', 'path': '/nested'})
        self.assertEqual(event64_result['stage'], 'schema')
        event65 = (root / 'fixtures/invalid/event-depth-65.json').read_bytes()
        self.assertEqual(evaluate(event65, contract=contract), expected)

    def test_news_refresh_context_is_accepted_for_progress(self):
        from f1ap_agent.contracts.artifact import SchemaSet
        root = Path(__file__).resolve().parents[3] / 'f1ap-api/contracts/v1'
        contract = SchemaSet(root)
        request_event = json.loads((root / 'fixtures/valid/news-refresh-valid.json').read_text())
        context = {'request': request_event['payload'], 'received_results': [], 'usage_history': []}
        progress = (root / 'fixtures/valid/progress-valid.json').read_bytes()
        fixture = next(f for f in json.loads((root / 'fixtures/index.json').read_text())['fixtures'] if f['id'] == 'progress-valid')

        self.assertEqual(evaluate(progress, contract=contract, context=context), fixture['expected'])

    def test_news_refresh_context_is_accepted_for_usage(self):
        from f1ap_agent.contracts.artifact import SchemaSet
        root = Path(__file__).resolve().parents[3] / 'f1ap-api/contracts/v1'
        contract = SchemaSet(root)
        request_event = json.loads((root / 'fixtures/valid/news-refresh-valid.json').read_text())
        context = {'request': request_event['payload'], 'received_results': [], 'usage_history': []}
        usage = (root / 'fixtures/valid/usage-valid.json').read_bytes()
        fixture = next(f for f in json.loads((root / 'fixtures/index.json').read_text())['fixtures'] if f['id'] == 'usage-valid')

        self.assertEqual(evaluate(usage, contract=contract, context=context), fixture['expected'])

    def test_invalid_news_refresh_context_is_rejected_for_progress_and_usage(self):
        from f1ap_agent.contracts.artifact import SchemaSet
        root = Path(__file__).resolve().parents[3] / 'f1ap-api/contracts/v1'
        contract = SchemaSet(root)
        request_event = json.loads((root / 'fixtures/valid/news-refresh-valid.json').read_text())
        request = request_event['payload']
        request['task_key'] = '0' * 64
        context = {'request': request, 'received_results': [], 'usage_history': []}
        expected = {
            'accepted': False,
            'stage': 'semantic',
            'error': {'code': 'CONTEXT', 'path': ''},
            'canonical_base64': None,
            'canonical_sha256': None,
            'computed': {},
        }

        for name in ('progress-valid', 'usage-valid'):
            fixture = next(f for f in json.loads((root / 'fixtures/index.json').read_text())['fixtures'] if f['id'] == name)
            with self.subTest(event=name):
                self.assertEqual(evaluate((root / fixture['input']).read_bytes(), contract=contract, context=context), expected)

    def test_news_refresh_context_is_rejected_for_prediction_result_events(self):
        from f1ap_agent.contracts.artifact import SchemaSet
        root = Path(__file__).resolve().parents[3] / 'f1ap-api/contracts/v1'
        contract = SchemaSet(root)
        request_event = json.loads((root / 'fixtures/valid/news-refresh-valid.json').read_text())
        context = {'request': request_event['payload'], 'received_results': [], 'usage_history': []}
        expected = {
            'accepted': False,
            'stage': 'semantic',
            'error': {'code': 'CONTEXT', 'path': ''},
            'canonical_base64': None,
            'canonical_sha256': None,
            'computed': {},
        }

        for name in ('completed-valid', 'finished-valid'):
            fixture = next(f for f in json.loads((root / 'fixtures/index.json').read_text())['fixtures'] if f['id'] == name)
            with self.subTest(event=name):
                self.assertEqual(evaluate((root / fixture['input']).read_bytes(), contract=contract, context=context), expected)

    def test_numeric_multiple_errors_use_duplicate_before_range(self):
        from f1ap_agent.contracts.artifact import SchemaSet
        root = Path(__file__).resolve().parents[3] / 'f1ap-api/contracts/v1'
        contract = SchemaSet(root)
        raw = (root / 'fixtures/invalid/numeric-multi-error.json').read_bytes()
        context = json.loads((root / 'fixtures/contexts/numeric-multi-error.json').read_text())
        result = evaluate(raw, contract=contract, context=context)
        self.assertEqual(result['error'], {'code': 'DUPLICATE', 'path': '/payload/recommendations/0/picks'})

    def test_fixed_prediction_request_matches_independent_expected(self):
        from f1ap_agent.contracts.artifact import SchemaSet
        root = Path(__file__).resolve().parents[3] / 'f1ap-api/contracts/v1'
        contract = SchemaSet(root)
        fixture = next(f for f in json.loads((root / 'fixtures/index.json').read_text())['fixtures'] if f['id'] == 'prediction-valid')
        self.assertEqual(evaluate((root / fixture['input']).read_bytes(), contract=contract), fixture['expected'])

    def test_all_frozen_vectors(self):
        from f1ap_agent.contracts.artifact import SchemaSet
        root = Path(__file__).resolve().parents[3] / 'f1ap-api/contracts/v1'
        contract = SchemaSet(root)
        fixtures = json.loads((root / 'fixtures/index.json').read_text())['fixtures']
        self.assertGreater(len(fixtures), 0)
        for fixture in fixtures:
            with self.subTest(fixture=fixture['id']):
                context = json.loads((root / fixture['context']).read_text()) if 'context' in fixture else None
                result = evaluate((root / fixture['input']).read_bytes(), kind=fixture['kind'], contract=contract, context=context)
                self.assertEqual(result, fixture['expected'])


if __name__ == '__main__':
    unittest.main()
