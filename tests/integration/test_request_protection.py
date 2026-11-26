"""Web pages in the operator's browser must not be able to drive the trading API."""
import pytest

from kalshi_workstation.core.execution import service as execution

ORDER = '{"ticker": "SYNTHETIC", "side": "yes", "quantity": 3}'


@pytest.fixture
def live_orders(client, monkeypatch):
    """Live mode with a recording exchange, so any order that gets through is visible."""
    client.app.state.mode.dry_run = False
    calls = []
    monkeypatch.setattr(execution, "submit_order", lambda payload: calls.append(payload) or {
        "order_id": "synthetic", "fill_count": "3.00", "remaining_count": "0.00"})
    monkeypatch.setattr(execution, "cancel_order", lambda order_id: calls.append(order_id) or {})
    return calls


@pytest.mark.parametrize("headers", [
    {"Content-Type": "text/plain"},  # a cross-site fetch/form needs no CORS preflight
    {"Content-Type": "application/x-www-form-urlencoded"},
])
def test_non_json_order_body_is_rejected(client, live_orders, headers):
    response = client.post("/api/order/buy", content=ORDER, headers=headers)
    assert response.status_code == 422
    assert live_orders == []


@pytest.mark.parametrize("headers", [
    {"Origin": "https://attacker.example"},
    {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site", "Origin": "https://attacker.example"},
    {"Sec-Fetch-Site": "same-site"},
])
@pytest.mark.parametrize("method,path,body", [
    ("POST", "/api/order/buy", {"ticker": "SYNTHETIC", "quantity": 3}),
    ("POST", "/api/arb/execute", {"ticker_a": "A", "ticker_b": "B", "quantity": 1}),
    ("PUT", "/api/settings/dry_run", {"dry_run": False}),
    ("POST", "/api/arb/groups", {"name": "Injected"}),
    ("POST", "/api/orders/reset/synthetic", None),
    ("DELETE", "/api/orders/cancel/synthetic", None),
])
def test_cross_origin_mutations_are_rejected(client, live_orders, headers, method, path, body):
    client.app.state.mode.dry_run = True
    response = client.request(method, path, json=body, headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "cross_origin_rejected"
    assert live_orders == []
    assert client.app.state.mode.dry_run is True
    assert client.app.state.arbitrage.groups == []


@pytest.mark.parametrize("headers", [
    {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
    {"Origin": "http://testserver"},  # older browsers send Origin without Sec-Fetch-Site
    {},                               # non-browser clients send neither
])
def test_same_origin_and_scripted_orders_are_accepted(client, live_orders, headers):
    response = client.post("/api/order/buy", json={"ticker": "SYNTHETIC", "quantity": 3},
                           headers=headers)
    assert response.status_code == 200
    assert len(live_orders) == 1


def test_cross_origin_reads_are_not_blocked_by_origin_check(client):
    # Browsers stop other sites from reading responses; only mutations need the guard.
    response = client.get("/api/status", headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 200


@pytest.mark.parametrize("host", ["attacker.example", "rebind.attacker.example:8000"])
def test_unknown_host_names_are_rejected(client, live_orders, host):
    """DNS rebinding: the attacker's name resolves to 127.0.0.1, so it looks same-origin."""
    for method, path, body in (("GET", "/api/status", None), ("POST", "/api/order/buy", ORDER)):
        response = client.request(method, path, content=body, headers={
            "Host": host, "Origin": f"http://{host}", "Content-Type": "application/json"})
        assert response.status_code == 400
    assert live_orders == []


@pytest.mark.parametrize("body", [
    {"ticker": "SYNTHETIC", "quantity": 0},
    {"ticker": "SYNTHETIC", "quantity": "many"},
    {"ticker": "SYNTHETIC", "side": "maybe"},
    {"ticker": "", "quantity": 1},
])
def test_invalid_order_fields_are_rejected_before_execution(client, live_orders, body):
    assert client.post("/api/order/buy", json=body).status_code == 422
    assert live_orders == []


@pytest.mark.parametrize("path,body", [
    ("/api/arb/settings", {"buffer": 100}),
    ("/api/arb/settings", {"auto_arb_quantity": 0}),
    ("/api/arb/settings", {"threshold": "high"}),
])
def test_invalid_settings_are_rejected_without_saving(client, path, body):
    assert client.put(path, json=body).status_code == 422
    assert not client.app.state.arbitrage.config_path.exists()


def test_side_is_case_insensitive(client):
    response = client.post("/api/order/buy", json={"ticker": "SYNTHETIC", "side": "NO", "quantity": 1})
    assert response.status_code == 200
    assert response.json()["order_data"]["side"] == "ask"  # buy NO = ask on the YES book
