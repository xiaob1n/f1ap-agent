"""Deterministic B1 tests using real local files and a counted fake executor."""
import json
from decimal import Decimal
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from f1ap_agent.contracts.verifier import canonical, digest, evaluate, parse
from f1ap_agent.messaging import LocalSender
from f1ap_agent.runtime import RuntimeFault, Store, atomic_json
from f1ap_agent.worker.engine import FakeExecutor, SimulatedInterruption, Worker
from f1ap_agent.worker.models import Task, load_contract


class WorkerTest(unittest.TestCase):
    def __init__(self, methodName='runTest'):
        super().__init__(methodName)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.store = Store(self.root / 'runtime')
        self.store.initialize()
        self.contract = load_contract()
        self.request = json.loads(self.contract.read_bytes('fixtures/valid/prediction-valid.json'))
        self.counter = self.store.root / 'fake-executor.json'
        self.worker = self.make_worker()

    def make_worker(self, accept=True, interrupt=False):
        return Worker(self.store, self.contract, FakeExecutor(self.counter, interrupt),
                      LocalSender(self.store.root, accept))

    def raw(self):
        self.request['payload_sha256'] = digest(self.request['payload'])
        return canonical(self.request)

    def calls(self):
        return json.loads(self.counter.read_bytes())['calls'] if self.counter.exists() else 0

    def log(self):
        return json.loads(json.dumps(self.store.registry_log(self.request['payload']['run_id'])))

    def assert_events_valid(self, log):
        context = {'request': self.request['payload'], 'received_results': [
            {key: result[key] for key in ('question_id', 'result_sha256', 'status')}
            for result in log['results']], 'usage_history': []}
        for event in log['events']:
            verdict = evaluate(canonical(event), contract=self.contract, context=context)
            self.assertTrue(verdict['accepted'], verdict)
        self.assertEqual(log['finished']['results'], context['received_results'])

    def test_three_deliveries_execute_once_and_keep_fixed_events(self):
        raw = self.raw()
        deliveries = [self.worker.run(raw) for _ in range(3)]
        self.assertEqual(self.calls(), 1)
        self.assertTrue(all(accepted for _, accepted in deliveries))
        self.assertEqual(len(self.log()['events']), 12)
        self.assertEqual(self.log()['finished']['run_status'], 'SUCCEEDED')
        self.assertTrue(all(result['status'] == 'VALID' for result in self.log()['results']))
        self.assert_events_valid(self.log())

    def test_new_run_retry_is_not_deduplicated_by_task_key(self):
        self.worker.run(self.raw())
        prior = self.request['payload']['run_id']
        self.request['payload'].update(run_id='101', retry_of_id=prior, attempt_no=2)
        self.worker.run(self.raw())
        self.assertEqual(self.calls(), 2)

    def test_new_window_new_task_executes(self):
        self.worker.run(self.raw())
        payload = self.request['payload']
        payload.update(run_id='102', data_cutoff_at='2026-01-01T00:40:00.000Z',
                       expires_at='2026-01-01T00:50:00.000Z')
        payload['refresh_window']['start_at'] = '2026-01-01T00:30:00.000Z'
        payload['task_key'] = digest([payload['task_type'], payload['purpose'],
            payload['definition']['round_id'], payload['definition']['definition_version'],
            payload['profile_sha256'], payload['refresh_window']])
        payload['input_sha256'] = digest({key: payload[key] for key in (
            'definition_sha256', 'question_ids', 'profile_sha256', 'data_cutoff_at')})
        self.worker.run(self.raw())
        self.assertEqual(self.calls(), 2)

    def test_call_started_interruption_recovers_unknown_without_repeating(self):
        raw = self.raw()
        with self.assertRaises(SimulatedInterruption):
            self.make_worker(interrupt=True).run(raw)
        self.assertEqual(self.log()['call_status'], 'CALL_STARTED')
        self.assertTrue(list((self.store.root / 'calls').glob('*.json')))
        task, accepted = self.worker.run(raw)
        self.assertTrue(accepted)
        self.assertEqual(self.calls(), 1)
        self.assertEqual(self.log()['call_status'], 'UNKNOWN')
        self.assert_events_valid(self.log())

    def test_expired_task_has_zero_calls_no_usage_and_valid_terminal_events(self):
        self.worker.run(self.raw(), now='2026-01-01T02:00:00.000Z')
        self.assertEqual(self.calls(), 0)
        self.assertEqual(len(self.log()['events']), 11)
        self.assertEqual(self.log()['finished']['run_status'], 'TIMED_OUT')
        self.assert_events_valid(self.log())

    def test_invalid_payload_has_zero_calls(self):
        self.request['payload']['run_id'] = 12
        with self.assertRaises(RuntimeFault):
            self.worker.run(self.raw())
        self.assertEqual(self.calls(), 0)
        self.assertEqual(self.store.registry(), {})

    def test_known_missing_log_fails_closed_with_zero_new_calls(self):
        self.worker.run(self.raw())
        self.counter.unlink()
        self.store.log_path(self.request['payload']['run_id']).unlink()
        with self.assertRaises(RuntimeFault):
            self.worker.run(self.raw())
        self.assertEqual(self.calls(), 0)

    def test_corrupt_log_fails_closed_with_zero_new_calls(self):
        self.worker.run(self.raw())
        self.counter.unlink()
        atomic_json(self.store.log_path(self.request['payload']['run_id']), {'log': {}})
        with self.assertRaises(RuntimeFault):
            self.worker.run(self.raw())
        self.assertEqual(self.calls(), 0)

    def test_pending_events_keep_identity_payload_and_resend_without_execution(self):
        raw = self.raw()
        _, accepted = self.make_worker(accept=False).run(raw)
        before = {p.name: p.read_bytes() for p in (self.store.root / 'pending').glob('*.json')}
        _, accepted_again = self.make_worker(accept=False).run(raw)
        self.assertFalse(accepted or accepted_again)
        self.assertEqual(before, {p.name: p.read_bytes() for p in (self.store.root / 'pending').glob('*.json')})
        self.assertTrue(self.worker.run(raw)[1])
        self.assertEqual(self.calls(), 1)
        self.assertEqual(list((self.store.root / 'pending').glob('*.json')), [])

    def test_unknown_cost_is_null_and_does_not_block_another_run(self):
        self.worker.run(self.raw())
        pricing = self.log()['events'][0]['payload']['pricing']
        self.assertEqual(pricing['status'], 'UNKNOWN')
        self.assertIsNone(pricing['charged_cny'])
        self.request['payload']['run_id'] = '103'
        self.worker.run(self.raw())
        self.assertEqual(self.calls(), 2)

    def test_call_limit_zero_has_no_usage_and_zero_calls(self):
        self.request['payload']['call_limit'] = 0
        self.worker.run(self.raw())
        self.assertEqual(self.calls(), 0)
        self.assertEqual(len(self.log()['events']), 11)
        self.assertEqual(self.log()['finished']['last_usage_revision'], 0)
        self.assert_events_valid(self.log())

    def test_boundary_preserves_large_string_ids_integer_limits_decimal_utc(self):
        task = Task.parse(self.raw(), self.contract)
        self.assertIsInstance(task.run_id, str)
        self.assertGreater(int(task.question_ids[0]), 2**53)
        self.assertIs(type(task.call_limit), int)
        self.assertTrue(all(isinstance(point, Decimal) for point in task.points))
        offset = task.now.utcoffset()
        self.assertIsNotNone(offset)
        assert offset is not None
        self.assertEqual(offset.total_seconds(), 0)
        self.assertEqual(task.stamp(), '2026-01-01T00:10:00.000Z')

    def test_uninitialized_runtime_and_reinitialization_fail_closed(self):
        with self.assertRaises(RuntimeFault):
            Store(self.root / 'missing').registry()
        with self.assertRaises(RuntimeFault):
            self.store.initialize()

    def test_cli_exports_full_envelopes_pending_nonzero_and_duplicate_recovery(self):
        fixture = self.root / 'fixture.json'
        atomic_json(fixture, self.request)
        runtime = self.root / 'cli-runtime'
        output = self.root / 'export'
        command = [sys.executable, '-m', 'f1ap_agent.worker', '--fixture', str(fixture),
                   '--runtime-dir', str(runtime), '--output', str(output)]
        first = subprocess.run(command + ['--init-runtime', '--reject-send'], capture_output=True, text=True)
        second_command = command[:-1] + [str(self.root / 'export-second')]
        second = subprocess.run(second_command, capture_output=True, text=True)
        repeated_output = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(first.returncode, 2, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(repeated_output.returncode, 1)
        self.assertEqual(json.loads((runtime / 'fake-executor.json').read_bytes())['calls'], 1)
        self.assertEqual(len(json.loads((output / 'events.json').read_bytes())), 12)
        self.assertEqual(json.loads((output / 'request.json').read_bytes()), self.request)

    def test_finished_log_bad_event_hash_fails_closed_even_with_valid_log_checksum(self):
        self.worker.run(self.raw())
        log = self.log()
        log['events'][0]['payload_sha256'] = '0' * 64
        self.store.save(self.request['payload']['run_id'], log)
        self.counter.unlink()
        with self.assertRaises(RuntimeFault):
            self.worker.run(self.raw())
        self.assertEqual(self.calls(), 0)

    def test_writer_exclusion_rejects_second_worker(self):
        with self.store.exclusive():
            with self.assertRaisesRegex(RuntimeFault, 'WRITER_BUSY'):
                self.worker.run(self.raw())
        self.assertEqual(self.calls(), 0)

    def test_explicit_now_at_live_deadline_never_calls(self):
        self.worker.run(self.raw(), now=self.request['payload']['definition']['deadline_at'])
        self.assertEqual(self.calls(), 0)
        self.assertEqual(self.log()['finished']['run_status'], 'TIMED_OUT')

    def test_unknown_pending_restart_preserves_original_envelopes(self):
        raw = self.raw()
        with self.assertRaises(SimulatedInterruption):
            self.make_worker(interrupt=True).run(raw)
        self.make_worker(accept=False).run(raw, now='2026-01-01T00:15:00.000Z')
        before = self.log()['events']
        self.worker.run(raw, now='2026-01-01T00:20:00.000Z')
        self.assertEqual(before, self.log()['events'])
        self.assertEqual(self.calls(), 1)
