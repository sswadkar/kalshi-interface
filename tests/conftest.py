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
for name in ("DEMO_KEYID", "DEMO_KEYFILE", "PROD_KEYID", "PROD_KEYFILE"):
    os.environ.pop(name, None)


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Unexpected network access in an offline test")
    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from kalshi_workstation.app import create_app
    from kalshi_workstation.config import global_settings
    from kalshi_workstation.features.trading.state import state

    monkeypatch.setitem(global_settings, "dry_run", True)
    monkeypatch.setitem(state, "markets", [{
        "market_ticker": "SYNTHETIC", "yes_ask": .60, "yes_bid": .58,
        "no_ask": .42, "no_bid": .40,
    }])
    monkeypatch.setitem(state, "messages", [])
    # Without the context-manager form, TestClient does not start live lifespan tasks.
    with_client = TestClient(create_app())
    yield with_client
    with_client.close()
