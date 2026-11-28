"""正式运行时的冻结契约加载入口。"""
import os
from pathlib import Path

from f1ap_agent.contracts.artifact import Contract
from f1ap_agent.runtime import RuntimeFault


def load_contract() -> Contract:
    contracts_dir = os.environ.get('F1AP_CONTRACTS_DIR', '')
    contracts_lock = os.environ.get('F1AP_CONTRACTS_LOCK', '')
    if bool(contracts_dir) != bool(contracts_lock):
        raise RuntimeFault('CONTRACTS_ENV_PAIR_REQUIRED')
    if contracts_dir:
        contract = Contract(Path(contracts_dir), Path(contracts_lock))
    else:
        root = Path(__file__).resolve().parents[3]
        contract = Contract(root.parent / 'f1ap-api/contracts/v1', root / 'contracts.lock.json')
    if contract.release != '1.0.2':
        raise RuntimeFault('RELEASE_NOT_1_0_2')
    return contract
