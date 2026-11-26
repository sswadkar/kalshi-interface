"""Runtime settings, independent of the launch directory.

Three sources, each for one kind of value:

- Environment (or `.env` in the repository root): credentials and deployment
  values. `ENV`, the key ID and key file for that exchange, and `KALSHI_DATA_DIR`.
- `config.json` in the data directory: app settings (see `DEFAULT_SETTINGS`).
- Constants below: values that should not need changing.
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = PROJECT_ROOT / "static"
# Tests point this at an absent file so a developer's own .env never applies to them.
load_dotenv(os.environ.get("KALSHI_ENV_FILE", str(PROJECT_ROOT / ".env")))

ENV = os.getenv("ENV", "DEMO")
DATA_DIR = Path(os.environ.get("KALSHI_DATA_DIR", PROJECT_ROOT))
SETTINGS_FILE = DATA_DIR / "config.json"

DEFAULT_SETTINGS = {
    "event_ticker": None,   # event shown in the live trader
    "dry_run": True,        # simulate manual and arbitrage orders
}


def load_settings(path: Path) -> dict:
    """App settings from `path`, with defaults for a missing file or missing keys.

    Dry run stays on unless the file says exactly `"dry_run": false`, so a typo
    can never turn on live orders. An unreadable file stops startup rather than
    silently running with defaults.
    """
    if not path.exists():
        return dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Could not read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} must contain a JSON object")
    unknown = set(data) - set(DEFAULT_SETTINGS)
    if unknown:
        raise RuntimeError(f"Unknown setting(s) in {path}: {', '.join(sorted(unknown))}")
    settings = {**DEFAULT_SETTINGS, **data}
    settings["dry_run"] = settings["dry_run"] is not False
    return settings


_settings = load_settings(SETTINGS_FILE)
EVENT_TICKER = _settings["event_ticker"]
DRY_RUN = _settings["dry_run"]

# Environment variables earlier versions read; they are ignored now. Startup warns.
RETIRED_ENV = [name for name in (
    "EVENT_TICKER", "DRY_RUN", "KALSHI_ALLOWED_HOSTS", "KALSHI_READS_PER_SECOND",
    "KALSHI_WRITES_PER_SECOND", "KALSHI_BALANCE_PER_SECOND", "KALSHI_LOG_LEVEL",
) if os.getenv(name) is not None]

# Host names the browser may use to reach this server (DNS-rebinding protection).
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
# Client-side request budgets per second, below Kalshi's Basic tier
# (20 reads, 10 writes, and 1 balance request with no burst).
READS_PER_SECOND = 15
WRITES_PER_SECOND = 8
BALANCE_PER_SECOND = 1
LOG_LEVEL = "INFO"
