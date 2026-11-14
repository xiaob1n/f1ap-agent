"""只读探针与原子健康快照；不获取运行目录写锁或建立网络连接。"""
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from f1ap_agent.runtime import RuntimeFault, atomic_json, json_object, json_string, read_json

type State = Literal['STARTING', 'HEALTHY', 'DEGRADED', 'DRAINING', 'FAILED']


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def process_identity(pid: int) -> str | None:
    """读取内核启动身份；Linux 无需 ps，macOS 使用带微秒的内核进程信息。"""
    try:
        os.kill(pid, 0)
        if sys.platform == 'linux':
            stat = Path(f'/proc/{pid}/stat').read_text()
            fields = stat[stat.rindex(')') + 2:].split()
            if fields[0] == 'Z':
                return None
            boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            return f'{boot}:{fields[19]}'
        if sys.platform == 'darwin':
            import ctypes

            # proc_bsdinfo 固定 136 字节，最后两个 uint64 为启动秒及微秒。
            info = ctypes.create_string_buffer(136)
            libproc = ctypes.CDLL('/usr/lib/libproc.dylib')
            size = libproc.proc_pidinfo(pid, 3, 0, info, len(info))
            if size != len(info):
                return None
            seconds = int.from_bytes(info.raw[120:128], sys.byteorder)
            micros = int.from_bytes(info.raw[128:136], sys.byteorder)
            return f'{seconds}:{micros}'
        result = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart='],
                                capture_output=True, text=True, timeout=2, check=False,
                                env={'LC_ALL': 'C', 'PATH': '/usr/bin:/bin'})
        return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


class Health:
    """同一进程生命周期内复用代际；状态更新与心跳共用原子写入。"""
    def __init__(self, path: Path) -> None:
        self.path: Path = path
        self.pid: int = os.getpid()
        self.generation: str = uuid.uuid4().hex
        self.started: str = utc_now()
        self.identity: str | None = process_identity(self.pid)
        self.state: State = 'STARTING'

    def update(self, state: State) -> None:
        self.state = state
        atomic_json(self.path, {'schema_version': '1.0', 'state': state, 'pid': self.pid,
            'generation': self.generation, 'process_started_at': self.started,
            'process_identity': self.identity, 'heartbeat_at': utc_now(),
            'consumer_registered': state == 'HEALTHY',
            'connection_state': 'CONNECTED' if state == 'HEALTHY' else 'DISCONNECTED'})


def check_health(health_file: Path, require_ready: bool, max_age_seconds: float = 15.0) -> int:
    """两种探针均要求真实就绪；进程存在本身不等于订阅存活。"""
    try:
        _ = require_ready
        value = json_object(read_json(health_file))
        if (value.get('schema_version') != '1.0' or value.get('state') != 'HEALTHY'
                or value.get('consumer_registered') is not True
                or value.get('connection_state') != 'CONNECTED'):
            return 1
        pid = value.get('pid')
        generation = value.get('generation')
        if (type(pid) is not int or pid <= 0 or not isinstance(generation, str)
                or re.fullmatch('[0-9a-f]{32}', generation) is None):
            return 1
        started = datetime.fromisoformat(json_string(value['process_started_at']))
        heartbeat = datetime.fromisoformat(json_string(value['heartbeat_at']))
        age = (datetime.now(timezone.utc) - heartbeat).total_seconds()
        identity = process_identity(pid)
        return 0 if (started <= heartbeat and 0 <= age <= max_age_seconds
                     and identity is not None and identity == value.get('process_identity')) else 1
    except (RuntimeFault, OSError, ValueError, TypeError, KeyError, OverflowError):
        return 1
