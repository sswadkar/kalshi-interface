"""Offline-only tests: isolate local data and reject unexpected network access."""
import os
import socket
import tempfile
from pathlib import Path

import pytest
import requests

_test_data = tempfile.TemporaryDirectory(prefix="kalshi-tests-")
os.environ["KALSHI_ENV_FILE"] = str(Path(_test_data.name) / "absent.env")
os.environ["KALSHI_DATA_DIR"] = _test_data.name
os.environ["ENV"] = "DEMO"
os.environ["DRY_RUN"] = "true"
# The test client addresses requests to http://testserver.
os.environ["KALSHI_ALLOWED_HOSTS"] = "localhost,127.0.0.1,testserver"
for name in ("DEMO_KEYID", "DEMO_KEYFILE", "PROD_KEYID", "PROD_KEYFILE", "EVENT_TICKER"):
    os.environ.pop(name, None)

SYNTHETIC_MARKET = {"market_ticker": "SYNTHETIC", "yes_ask": 60, "yes_bid": 58,
                    "no_ask": 42, "no_bid": 40}


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Unexpected network access in an offline test")
    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.fixture
def app(tmp_path):
    """A fresh application whose configuration files live in an empty directory."""
    from kalshi_workstation.app import create_app
    application = create_app(data_dir=tmp_path, dry_run=True, event_ticker="SYNTHETIC-EVENT")
    application.state.live_trading.markets = [dict(SYNTHETIC_MARKET)]
    return application


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    # Without the context-manager form, TestClient does not start live lifespan tasks.
    with_client = TestClient(app)
    yield with_client
    with_client.close()
