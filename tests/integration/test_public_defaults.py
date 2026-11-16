import os
from pathlib import Path
import subprocess
import sys


def test_clean_install_defaults_to_dry_run_without_personal_history(tmp_path):
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=str(root / "src"),
               KALSHI_ENV_FILE=str(tmp_path / "absent.env"),
               KALSHI_DATA_DIR=str(tmp_path))
    env.pop("DRY_RUN", None)
    script = "\n".join([
        "from kalshi_workstation.config import DRY_RUN",
        "from kalshi_workstation.core.portfolio import PORTFOLIO_BACKFILL",
        "assert DRY_RUN is True",
        "assert PORTFOLIO_BACKFILL == {}",
    ])
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
