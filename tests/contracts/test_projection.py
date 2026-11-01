"""The synthetic projection seam must reject malformed input consistently."""
import copy
import json
from pathlib import Path
import unittest

from f1ap_agent.contracts import evaluate
from f1ap_agent.contracts.artifact import SchemaSet


class ProjectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[3] / 'f1ap-api/contracts/v1'
        cls.contract = SchemaSet(cls.root)
        cls.definition = json.loads((cls.root / 'fixtures/valid/prediction-valid.json').read_bytes())['payload']['definition']

    def test_missing_or_wrong_definition_is_structured_rejection(self):
        for value in ({}, [], {'definition': None}, {'definition': []}):
            with self.subTest(value=value):
                result = evaluate(json.dumps(value).encode(), kind='definition_projection', contract=self.contract)
                self.assertFalse(result['accepted'])
                self.assertEqual(result['stage'], 'schema')
                self.assertEqual(result['error'], {'code': 'TYPE', 'path': '/definition'})

    def test_duplicate_question_uses_projection_path(self):
        definition = copy.deepcopy(self.definition)
        definition['questions'][0]['question_id'] = definition['questions'][1]['question_id']
        result = evaluate(json.dumps({'definition': definition}).encode(), kind='definition_projection', contract=self.contract)
        self.assertEqual(result['error'], {'code': 'ASSOCIATION', 'path': '/definition/questions'})

    def test_duplicate_option_uses_projection_path(self):
        definition = copy.deepcopy(self.definition)
        definition['questions'][0]['options'][1] = definition['questions'][0]['options'][0]
        result = evaluate(json.dumps({'definition': definition}).encode(), kind='definition_projection', contract=self.contract)
        self.assertEqual(result['error'], {'code': 'DUPLICATE', 'path': '/definition/questions/0/options'})

    def test_nested_schema_error_uses_projection_path(self):
        definition = copy.deepcopy(self.definition)
        del definition['name']
        result = evaluate(json.dumps({'definition': definition}).encode(), kind='definition_projection', contract=self.contract)
        self.assertEqual(result['error'], {'code': 'REQUIRED', 'path': '/definition/name'})
