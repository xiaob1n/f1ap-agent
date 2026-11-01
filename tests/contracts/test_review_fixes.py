"""Regression tests for the reviewed public raw-byte and artifact seams."""
import ast
import json
import unittest
from pathlib import Path

from f1ap_agent.contracts import evaluate

ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = ROOT / 'f1ap-api/contracts/v1'


class RawErrorOrderTest(unittest.TestCase):
    def test_syntax_error_before_excessive_depth_is_json(self):
        deep = b'[' * 65 + b'0' + b']' * 65
        for prefix in (b'x', b'[,', b'{"x" ', b'{"x":NaN,"y":'):
            with self.subTest(prefix=prefix):
                result = evaluate(prefix + deep, kind='canonical')
                self.assertEqual(result['error'], {'code': 'JSON', 'path': ''})
                self.assertEqual(result['stage'], 'parse')

    def test_excessive_depth_before_later_syntax_error_is_depth(self):
        result = evaluate(b'[' * 65 + b'x', kind='canonical')
        self.assertEqual(result['error'], {'code': 'DEPTH', 'path': ''})

    def test_strings_do_not_contribute_depth(self):
        result = evaluate(json.dumps({'text': '[' * 100 + ']' * 100}).encode(), kind='canonical')
        self.assertTrue(result['accepted'])


class PortableTestsTest(unittest.TestCase):
    def test_temp_directories_are_not_developer_absolute_paths(self):
        tree = ast.parse((Path(__file__).parent / 'test_artifact_regressions.py').read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'TemporaryDirectory':
                for keyword in node.keywords:
                    if keyword.arg == 'dir' and isinstance(keyword.value, ast.Constant):
                        self.assertFalse(isinstance(keyword.value.value, str) and keyword.value.value.startswith('/Users/'))


if __name__ == '__main__':
    unittest.main()
