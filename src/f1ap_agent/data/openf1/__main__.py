"""受控 OpenF1 准备；无 --run 时不建立网络连接。"""
import argparse
import hashlib
import sys
from pathlib import Path

from f1ap_agent.data.openf1.boundary import decode, exact, read_bytes
from f1ap_agent.data.openf1.client import OpenF1Client
from f1ap_agent.data.openf1.config import MAPPING_KEYS, Selection, parse_record
from f1ap_agent.data.openf1.prepare import prepare
from f1ap_agent.runtime import RuntimeFault, json_string, json_object


class Options(argparse.Namespace):
    spec: Path = Path()
    output: Path = Path()
    base_url: str = ''
    run: bool = False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument('--spec', type=Path, required=True)
    _ = parser.add_argument('--output', type=Path, required=True)
    _ = parser.add_argument('--base-url', required=True)
    _ = parser.add_argument('--run', action='store_true')
    args = parser.parse_args(namespace=Options())
    try:
        client = OpenF1Client(args.base_url)
        raw = json_object(decode(read_bytes(args.spec)))
        extra = {'question_bindings', 'qualifying_session'} if 'question_bindings' in raw else set()
        if extra and 'pit_session' in raw:
            extra.add('pit_session')
        spec = exact(raw, (MAPPING_KEYS | extra) - {'manifest'})
        spec['manifest'] = {'path': 'manifest.json', 'sha256': '0' * 64}
        _ = parse_record(spec, args.output / 'mapping.json', Selection(
            json_string(spec['round_id']), json_string(spec['definition_sha256']), '0' * 64))
        if not args.run:
            print('CONFIG_VALID; network disabled without --run')
            return 0
        path = prepare(args.spec, args.output, client)
        print('FROZEN mapping_sha256=' + hashlib.sha256(read_bytes(path)).hexdigest())
        return 0
    except (RuntimeFault, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
