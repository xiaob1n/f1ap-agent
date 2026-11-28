"""旧版 V1 任务入口已退役，请使用 ANSWERS_V2 消费入口。"""
import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    if sys.argv[1:] in (['--help'], ['-h']):
        parser.print_help()
        return 0
    print('LEGACY_V1_TASKS_RETIRED', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
