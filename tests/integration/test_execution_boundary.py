import pytest

from kalshi_workstation.core.exchange import kalshi
from kalshi_workstation.core.execution import service


def test_order_payload_and_response_survive_the_boundary(monkeypatch):
    sent = []
    response = {"order_id": "synthetic", "fill_count": "0.00", "remaining_count": "2.00"}
    monkeypatch.setattr(kalshi, "kalshi_post", lambda path, payload: sent.append((path, payload)) or response)
    payload = {"ticker": "SYNTHETIC", "client_order_id": "intent-1", "count": "2.00"}
    assert service.submit_order(payload) is response
    assert sent == [(kalshi.ORDER_V2_ENDPOINT, payload)]


def test_uncertain_submission_is_not_silently_retried(monkeypatch):
    calls = []
    def timeout(path, payload):
        calls.append(payload)
        raise TimeoutError("synthetic response lost")
    monkeypatch.setattr(kalshi, "kalshi_post", timeout)
    with pytest.raises(TimeoutError):
        service.submit_order({"client_order_id": "intent-1"})
    assert len(calls) == 1


def test_arbitrage_submits_via_shared_execution(monkeypatch):
    from kalshi_workstation.config import global_settings
    from kalshi_workstation.features.arbitrage import planner
    from kalshi_workstation.features.arbitrage.state import arb_state
    monkeypatch.setitem(global_settings, "dry_run", False)
    monkeypatch.setitem(arb_state, "groups", [{"markets": [
        {"ticker": "SYNTHETIC-A", "yes_ask": 40},
        {"ticker": "SYNTHETIC-B", "no_ask": 40},
    ]}])
    monkeypatch.setitem(arb_state, "messages", [])
    calls = []
    monkeypatch.setattr(service, "submit_order", lambda payload: calls.append(payload) or {
        "order_id": "synthetic", "fill_count": "2.00", "remaining_count": "0.00"})
    result = planner.execute_arb_internal("SYNTHETIC-A", "yes", "SYNTHETIC-B", "no", 2)
    assert not result["errors"]
    assert [order["ticker"] for order in calls] == ["SYNTHETIC-A", "SYNTHETIC-B"]
