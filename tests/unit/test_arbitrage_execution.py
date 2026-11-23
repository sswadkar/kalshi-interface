"""Leg 2 depends on leg 1's confirmed fill; any unmatched quantity is reported."""
import pytest

from kalshi_workstation.core.execution import service
from kalshi_workstation.features.arbitrage.execution import execute_pair


def run(monkeypatch, responses, quantity=10):
    """Execute a live pair against scripted exchange responses (or exceptions)."""
    sent, log = [], []
    script = iter(responses)

    def submit(payload):
        sent.append(payload)
        outcome = next(script)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(service, "submit_order", submit)
    result = execute_pair(ticker_a="A", side_a="yes", price_a=40, ticker_b="B", side_b="no",
                          price_b=50, quantity=quantity, buffer=1, dry_run=False,
                          log=lambda kind, text: log.append((kind, text)))
    return result, sent, log


def filled(count, requested=10):
    return {"order_id": "o", "fill_count": f"{count}.00", "remaining_count": f"{requested - count}.00",
            "average_fill_price": "0.41"}


def test_full_fill_sends_matching_leg_two(monkeypatch):
    result, sent, _ = run(monkeypatch, [filled(10), filled(10)])
    assert [o["ticker"] for o in sent] == ["A", "B"]
    assert sent[1]["count"] == "10.00"
    assert result["errors"] == [] and result["unhedged"] is None


@pytest.mark.parametrize("leg_one", [
    filled(0),
    {"error": {"code": "insufficient_balance", "message": "insufficient balance"}},
    TimeoutError("response lost"),
])
def test_leg_two_is_not_sent_without_a_confirmed_leg_one_fill(monkeypatch, leg_one):
    result, sent, _ = run(monkeypatch, [leg_one])
    assert [o["ticker"] for o in sent] == ["A"]
    assert result["errors"] and "leg 2 not sent" in result["errors"][0]
    assert result["unhedged"] is None


def test_partial_leg_one_sizes_leg_two_to_the_fill(monkeypatch):
    result, sent, _ = run(monkeypatch, [filled(4), filled(4, requested=4)])
    assert sent[1]["count"] == "4.00"
    assert result["errors"] == []
    assert result["notes"] == ["Leg 1 filled 4/10; leg 2 sized to 4."]


@pytest.mark.parametrize("leg_two,exposed", [
    (filled(1, requested=4), 3),
    (filled(0, requested=4), 4),
    (TimeoutError("response lost"), 4),
])
def test_leg_two_shortfall_is_reported_as_unhedged(monkeypatch, leg_two, exposed):
    result, sent, log = run(monkeypatch, [filled(4), leg_two])
    assert len(sent) == 2
    assert result["unhedged"] == {"ticker": "A", "side": "yes", "quantity": exposed}
    assert result["errors"]
    assert any(kind == "ERROR" for kind, _ in log)


def test_dry_run_sends_nothing(monkeypatch):
    monkeypatch.setattr(service, "submit_order", lambda payload: pytest.fail("order sent in dry run"))
    result = execute_pair(ticker_a="A", side_a="yes", price_a=40, ticker_b="B", side_b="no",
                          price_b=50, quantity=2, buffer=1, dry_run=True, log=lambda *a: None)
    assert result["dry_run"] is True
    assert result["expected_profit"] == 20
