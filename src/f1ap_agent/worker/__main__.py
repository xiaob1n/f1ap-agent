"""Explicit one-task CLI for local B1 verification only."""
import argparse
import sys
from pathlib import Path

from f1ap_agent.contracts.verifier import Rejection
from f1ap_agent.messaging import LocalSender
from f1ap_agent.runtime import RuntimeFault, Store, atomic_json
from f1ap_agent.worker.engine import FakeExecutor, SimulatedInterruption, Worker
from f1ap_agent.worker.models import load_contract


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--init-runtime', action='store_true')
    parser.add_argument('--runtime-dir', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--now')
    parser.add_argument('--mapping', type=Path)
    parser.add_argument('--reject-send', action='store_true')
    parser.add_argument('--interrupt-after-call-start', action='store_true')
    args = parser.parse_args()
    try:
        contract = load_contract()
        runtime = args.runtime_dir.resolve()
        output = args.output.resolve()
        protected = [contract.root, contract.lock_path, args.fixture.resolve()]
        for candidate in (runtime, output):
            if any(candidate == path or candidate.is_relative_to(path) or path.is_relative_to(candidate)
                   for path in protected):
                raise RuntimeFault('OUTPUT_OVERLAP')
        if output == runtime or output.is_relative_to(runtime) or runtime.is_relative_to(output):
            raise RuntimeFault('OUTPUT_RUNTIME_OVERLAP')
        output.mkdir(parents=True, exist_ok=False)
        store = Store(runtime)
        if args.init_runtime:
            store.initialize()
        worker = Worker(store, contract, FakeExecutor(runtime / 'fake-executor.json',
                        args.interrupt_after_call_start), LocalSender(runtime, not args.reject_send), args.mapping)
        task, accepted = worker.run(args.fixture.read_bytes(), args.now)
        log = store.registry_log(str(task.run_id))
        atomic_json(output / 'request.json', task.request)
        atomic_json(output / 'events.json', log['events'])
        atomic_json(output / 'summary.json', {'release': contract.release,
            'manifest_sha256': contract.manifest_sha256, 'run_id': str(task.run_id),
            'local_accepted': accepted, 'call_status': log['call_status'],
            'external_services': 'NOT_RUN'})
        print(f"B1 local_accepted={accepted}; external_services=NOT_RUN; output={output}")
        return 0 if accepted else 2
    except SimulatedInterruption:
        print('SIMULATED_INTERRUPTION: CALL_STARTED retained', file=sys.stderr)
        return 3
    except (RuntimeFault, Rejection, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
