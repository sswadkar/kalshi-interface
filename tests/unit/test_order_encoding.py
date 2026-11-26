import pytest
from kalshi_workstation.core.execution.orders import _build_order_v2


@pytest.mark.parametrize("action,outcome,expected_side,expected_price", [
    ("buy", "yes", "bid", "0.4300"),
    ("sell", "yes", "ask", "0.3700"),
    ("buy", "no", "ask", "0.5700"),
    ("sell", "no", "bid", "0.6300"),
])
def test_existing_order_mapping(action, outcome, expected_side, expected_price):
    order = _build_order_v2("SYNTHETIC", action, outcome, 40, 2, 3, "intent-1")
    assert order["side"] == expected_side
    assert order["price"] == expected_price
    assert order["count"] == "2.00"
    assert order["client_order_id"] == "intent-1"
