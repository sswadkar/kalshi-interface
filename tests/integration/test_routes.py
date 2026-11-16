import asyncio
import json
from pathlib import Path

import pytest

from kalshi_workstation.core.execution import service as execution


def test_all_existing_routes_are_registered(client):
    expected = {tuple(pair) for pair in json.loads(
        (Path(__file__).parents[1] / "fixtures/routes.json").read_text())}
    # Check the public route contract instead of FastAPI's router internals.
    paths = client.get("/openapi.json").json()["paths"]
    actual = {(method.upper(), path) for path, operations in paths.items()
              for method in operations if method in {"get", "post", "put", "delete", "patch"}}
    assert actual == expected


@pytest.mark.parametrize("path", ["/", "/arbitrage", "/liquidity", "/performance"])
def test_pages_resolve_from_another_working_directory(client, monkeypatch, tmp_path, path):
    monkeypatch.chdir(tmp_path)
    response = client.get(path)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<html" in response.text.lower()


def test_manual_dry_run_does_not_reach_execution(client, monkeypatch):
    def forbidden(payload):
        raise AssertionError("Dry-run order reached exchange execution")
    monkeypatch.setattr(execution, "submit_order", forbidden)
    for action in ("buy", "sell"):
        response = client.post(f"/api/order/{action}", json={"ticker": "SYNTHETIC", "quantity": 2})
        assert response.status_code == 200
        assert response.json()["dry_run"] is True


def test_manual_order_uses_execution_boundary(client, monkeypatch):
    from kalshi_workstation.config import global_settings
    calls = []
    result = {"order_id": "synthetic-order", "fill_count": "2.00", "remaining_count": "0.00"}
    monkeypatch.setitem(global_settings, "dry_run", False)
    monkeypatch.setattr(execution, "submit_order", lambda payload: calls.append(payload) or result)
    response = client.post("/api/order/buy", json={"ticker": "SYNTHETIC", "quantity": 2})
    assert response.status_code == 200
    assert response.json() == result
    assert len(calls) == 1
    assert calls[0]["ticker"] == "SYNTHETIC"


def test_provider_and_monitor_share_program_metadata(client, monkeypatch):
    from kalshi_workstation.features.liquidity_provider import runner
    from kalshi_workstation.features.liquidity_monitor import routes
    program = {"ticker": "SYNTHETIC", "target_size": 200, "discount_factor": .5, "daily_dollars": 2}
    monkeypatch.setattr(runner, "load_active_liquidity_programs", lambda: [program])
    provider = client.app.state.liquidity_provider
    provider.refresh_programs()
    assert provider.programs is client.app.state.programs
    assert client.app.state.programs["SYNTHETIC"] == program
    from kalshi_workstation.features.liquidity_provider import routes as provider_routes
    monkeypatch.setattr(provider_routes, "get_portfolio", lambda: {"total_value": 0})
    status = client.get("/api/lp/status")
    assert status.status_code == 200
    assert status.json()["programs"] == {"SYNTHETIC": program}
    monkeypatch.setattr(routes, "kalshi_get", lambda *args, **kwargs: {"orders": []})
    assert client.get("/api/orders/all_resting").json()["count"] == 0


def test_provider_place_and_cancel_use_execution_boundary(client, monkeypatch):
    provider = client.app.state.liquidity_provider
    provider.settings["dry_run"] = False
    calls = []
    monkeypatch.setattr(execution, "submit_order", lambda payload: calls.append(("submit", payload)) or {"order_id": "synthetic"})
    monkeypatch.setattr(execution, "cancel_order", lambda order_id: calls.append(("cancel", order_id)) or {})
    async def scenario():
        identity = await provider._place("SYNTHETIC", "yes", 40, 2)
        assert identity in provider.orders
        await provider._cancel(identity)
        assert identity not in provider.orders
    asyncio.run(scenario())
    assert [operation for operation, _ in calls] == ["submit", "cancel"]


def test_cancel_failure_is_reported_by_manual_route(client, monkeypatch):
    from kalshi_workstation.features.trading import routes
    monkeypatch.setattr(routes, "kalshi_get", lambda *args: {"order": {"status": "resting"}})
    def fail(order_id):
        raise TimeoutError("synthetic cancellation timeout")
    monkeypatch.setattr(execution, "cancel_order", fail)
    response = client.delete("/api/orders/cancel/synthetic")
    assert response.status_code == 500
    assert "timeout" in response.json()["error"]
