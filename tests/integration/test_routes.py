import json
from pathlib import Path

import pytest

from kalshi_workstation.core.execution import service as execution


def routes_of(application):
    # Check the public route contract instead of FastAPI's router internals.
    paths = application.openapi()["paths"]
    return {(method.upper(), path) for path, operations in paths.items()
            for method in operations if method in {"get", "post", "put", "delete", "patch"}}


def test_core_routes_are_registered(tmp_path):
    from kalshi_workstation.app import create_app
    expected = {tuple(pair) for pair in json.loads(
        (Path(__file__).parents[1] / "fixtures/routes.json").read_text())}
    assert routes_of(create_app(data_dir=tmp_path, with_extensions=False)) == expected


def test_extensions_only_add_routes(app, tmp_path):
    from kalshi_workstation.app import create_app
    assert routes_of(create_app(data_dir=tmp_path, with_extensions=False)) <= routes_of(app)


@pytest.mark.parametrize("path", ["/", "/arbitrage", "/liquidity"])
def test_pages_resolve_from_another_working_directory(client, monkeypatch, tmp_path, path):
    monkeypatch.chdir(tmp_path)
    response = client.get(path)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<html" in response.text.lower()


@pytest.mark.parametrize("asset,content_type", [
    ("workstation.css", "text/css"),
    ("workstation.js", "javascript"),
    ("trading.js", "javascript"),
    ("arbitrage.js", "javascript"),
    ("liquidity.js", "javascript"),
])
def test_local_ui_assets_resolve_outside_working_directory(client, monkeypatch, tmp_path, asset, content_type):
    monkeypatch.chdir(tmp_path)
    response = client.get(f"/static/{asset}")
    assert response.status_code == 200
    assert content_type in response.headers["content-type"]


@pytest.mark.parametrize("path", ["/static/.env", "/static/portfolio_history.json", "/static/%2e%2e/.env"])
def test_static_mount_does_not_expose_private_root_files(client, path):
    assert client.get(path).status_code == 404


def test_manual_dry_run_does_not_reach_execution(client, monkeypatch):
    def forbidden(payload):
        raise AssertionError("Dry-run order reached exchange execution")
    monkeypatch.setattr(execution, "submit_order", forbidden)
    for action in ("buy", "sell"):
        response = client.post(f"/api/order/{action}", json={"ticker": "SYNTHETIC", "quantity": 2})
        assert response.status_code == 200
        assert response.json()["dry_run"] is True


def test_manual_order_uses_execution_boundary(client, monkeypatch):
    calls = []
    result = {"order_id": "synthetic-order", "fill_count": "2.00", "remaining_count": "0.00"}
    client.app.state.mode.dry_run = False
    monkeypatch.setattr(execution, "submit_order", lambda payload: calls.append(payload) or result)
    response = client.post("/api/order/buy", json={"ticker": "SYNTHETIC", "quantity": 2})
    assert response.status_code == 200
    assert response.json() == result
    assert len(calls) == 1
    assert calls[0]["ticker"] == "SYNTHETIC"
    # Cached ask 60¢ + 10¢ taker buffer, in cents throughout.
    assert calls[0]["price"] == "0.7000"


def test_dry_run_toggle_is_shared_by_manual_and_arbitrage(client):
    assert client.put("/api/settings/dry_run", json={"dry_run": False}).json() == {"dry_run": False}
    assert client.get("/api/arb/status").json()["dry_run"] is False
    assert client.app.state.arbitrage.mode is client.app.state.live_trading.mode


def test_monitor_reads_the_shared_program_catalog(client, monkeypatch):
    from kalshi_workstation.core.exchange import programs as catalog_module
    from kalshi_workstation.features.liquidity_monitor import analysis, routes
    program = {"ticker": "SYNTHETIC", "series": "KXCPI", "target_size": 200,
               "discount_factor": .5, "daily_dollars": 2}
    monkeypatch.setattr(catalog_module, "load_active_liquidity_programs", lambda: [program])
    assert client.app.state.programs.refresh()
    assert client.app.state.programs["SYNTHETIC"] == program

    seen = []
    monkeypatch.setattr(analysis, "kalshi_get", lambda path, params=None: seen.append(path) or {
        "orderbook_fp": {"yes_dollars": [["0.4000", "300"]], "no_dollars": []}})
    rows = client.get("/api/liquidity/scan").json()["rows"]
    assert [row["ticker"] for row in rows] == ["SYNTHETIC"]
    assert rows[0]["risk"] == "MACRO"
    assert seen == ["/trade-api/v2/markets/SYNTHETIC/orderbook"]  # programs were not refetched

    monkeypatch.setattr(routes, "kalshi_get", lambda *args, **kwargs: {"orders": []})
    assert client.get("/api/liquidity/orders").json()["count"] == 0


def test_extension_pages_are_listed_for_navigation(client):
    client.app.state.extra_pages.append({"path": "/synthetic", "label": "Synthetic"})
    assert {"path": "/synthetic", "label": "Synthetic"} in client.get("/api/pages").json()["pages"]


def test_cancel_failure_is_reported_by_manual_route(client, monkeypatch):
    from kalshi_workstation.features.live_trading import routes
    monkeypatch.setattr(routes, "kalshi_get", lambda *args: {"order": {"status": "resting"}})
    def fail(order_id):
        raise TimeoutError("synthetic cancellation timeout")
    monkeypatch.setattr(execution, "cancel_order", fail)
    response = client.delete("/api/orders/cancel/synthetic")
    assert response.status_code == 500
    assert "timeout" in response.json()["error"]
