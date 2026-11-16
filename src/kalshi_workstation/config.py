"""Runtime locations and existing settings, independent of the launch directory.

Data defaults to the repository root to preserve the existing JSON files. Tests
can select an empty data directory and an absent dotenv file before importing.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(os.environ.get("KALSHI_PROJECT_ROOT", Path(__file__).resolve().parents[2]))
load_dotenv(os.environ.get("KALSHI_ENV_FILE", str(PROJECT_ROOT / ".env")))
DATA_DIR = Path(os.environ.get("KALSHI_DATA_DIR", PROJECT_ROOT))
STATIC_DIR = PROJECT_ROOT / "static"
ENV = os.getenv("ENV", "DEMO")
EVENT_TICKER = os.getenv("EVENT_TICKER")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
global_settings = {"dry_run": DRY_RUN}
