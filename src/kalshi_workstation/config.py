"""Runtime locations and settings, independent of the launch directory.

Data defaults to the repository root to preserve the existing JSON files. Tests
can select an empty data directory and an absent dotenv file before importing.
"""
import os
from pathlib import Path

from dotenv import load_dotenv


def env_flag(name: str, default: bool) -> bool:
    """Read a true/false environment value; anything other than 'true' is false."""
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() == "true"


def env_list(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


PROJECT_ROOT = Path(os.environ.get("KALSHI_PROJECT_ROOT", Path(__file__).resolve().parents[2]))
load_dotenv(os.environ.get("KALSHI_ENV_FILE", str(PROJECT_ROOT / ".env")))
DATA_DIR = Path(os.environ.get("KALSHI_DATA_DIR", PROJECT_ROOT))
STATIC_DIR = PROJECT_ROOT / "static"
ENV = os.getenv("ENV", "DEMO")
EVENT_TICKER = os.getenv("EVENT_TICKER")
# Manual + arbitrage simulation. Defaults to simulation when the setting is absent.
DRY_RUN = env_flag("DRY_RUN", default=True)
# Host names the browser may use to reach this server (DNS-rebinding protection).
ALLOWED_HOSTS = env_list("KALSHI_ALLOWED_HOSTS", "localhost,127.0.0.1")
# Client-side request budgets, kept below Kalshi's Basic tier (20 reads/s, 10 writes/s,
# balance 1/s with no burst). Raise these only for a higher API tier.
READS_PER_SECOND = env_float("KALSHI_READS_PER_SECOND", 15.0)
WRITES_PER_SECOND = env_float("KALSHI_WRITES_PER_SECOND", 8.0)
BALANCE_PER_SECOND = env_float("KALSHI_BALANCE_PER_SECOND", 1.0)
LOG_LEVEL = os.getenv("KALSHI_LOG_LEVEL", "INFO").upper()
