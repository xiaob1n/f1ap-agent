"""Produce replayable local CLI evidence, without contacting external services."""
import json
from pathlib import Path
import subprocess
import sys
import uuid

from f1ap_agent.contracts.verifier import canonical, digest, parse
from f1ap_agent.runtime import atomic_json
from f1ap_agent.worker.models import load_contract


def main() -> None:
    contract = load_contract()
    root = Path('docs/e02-agent-evidence') / ('b1-' + uuid.uuid4().hex)
    fixture = parse(contract.read_bytes('fixtures/valid/prediction-valid.json'))
    executions = []
    for scenario in ('normal', 'zero-call', 'unknown', 'pending'):
        base = root / scenario
        request = json.loads(canonical(fixture))
        if scenario == 'zero-call':
            request['payload']['call_limit'] = 0
            request['payload_sha256'] = digest(request['payload'])
        atomic_json(base / 'fixture.json', request)
        command = [sys.executable, '-m', 'f1ap_agent.worker', '--fixture', str(base / 'fixture.json'),
                   '--runtime-dir', str(base / 'runtime'), '--output', str(base / 'export')]
        extra = {'unknown': ['--interrupt-after-call-start'], 'pending': ['--reject-send']}.get(scenario, [])
        first = subprocess.run(command + ['--init-runtime'] + extra, capture_output=True, text=True)
        executions.append({'scenario': scenario, 'command': command + ['--init-runtime'] + extra,
                           'exit_code': first.returncode, 'stdout': first.stdout, 'stderr': first.stderr})
        if scenario in ('unknown', 'pending'):
            if scenario == 'pending':
                atomic_json(base / 'pending-before-resend.json',
                            [json.loads(path.read_bytes()) for path in sorted((base / 'runtime/pending').glob('*.json'))])
            command = command[:-1] + [str(base / 'export-recovery')]
            second = subprocess.run(command, capture_output=True, text=True)
            executions.append({'scenario': scenario + '-recovery', 'command': command,
                               'exit_code': second.returncode, 'stdout': second.stdout, 'stderr': second.stderr})
    atomic_json(root / 'cli-results.json', executions)
    print(root)
    expected = [0, 0, 3, 0, 2, 0]
    if [entry['exit_code'] for entry in executions] != expected:
        raise SystemExit('Unexpected CLI evidence result')


if __name__ == '__main__':
    main()
