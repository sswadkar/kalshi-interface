"""Shared balance cache, cents conversions, and batched market requests."""
import pandas as pd
import pytest

from kalshi_workstation import config
from kalshi_workstation.core import market_data, positions
from kalshi_workstation.core.portfolio import Portfolio
from kalshi_workstation.core.prices import dollars_to_cents, taker_fee_cents


def test_balance_is_fetched_at_most_once_per_interval():
    now, calls = [0.0], []
    portfolio = Portfolio(fetch=lambda: calls.append(1) or {"balance": 12345, "portfolio_value": 500},
                          interval_s=2.0, clock=lambda: now[0])
    for _ in range(5):
        portfolio.refresh()
    assert len(calls) == 1
    assert portfolio.summary() == {"total_value": 128.45, "cash": 123.45, "position_value": 5.0}
    now[0] = 2.0
    portfolio.refresh()
    assert len(calls) == 2


def test_balance_failure_keeps_last_value_and_waits_before_retrying():
    now, outcomes = [0.0], [{"balance": 100, "portfolio_value": 0}, RuntimeError("429")]

    def fetch():
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    portfolio = Portfolio(fetch=fetch, interval_s=2.0, clock=lambda: now[0])
    portfolio.refresh()
    now[0] = 2.0
    portfolio.refresh()
    assert portfolio.balance() == {"balance": 100, "portfolio_value": 0}
    assert "429" in portfolio.error
    portfolio.refresh()  # within the interval of the failed attempt: no request
    assert outcomes == []


def test_status_pages_never_call_the_balance_endpoint(client):
    client.app.state.portfolio._fetch = lambda: pytest.fail("page fetched the balance")
    client.app.state.portfolio._balance = {"balance": 100, "portfolio_value": 0}
    for path in ("/api/status", "/api/arb/status"):
        assert client.get(path).status_code == 200
    assert client.get("/api/arb/status").json()["user_info"] == {"balance": 100, "portfolio_value": 0}


def test_prices_are_cents():
    assert dollars_to_cents("0.5860") == 59
    assert dollars_to_cents(None) == 0
    assert dollars_to_cents("", default=None) is None
    assert taker_fee_cents(50) == pytest.approx(1.75)


def test_market_summary_is_in_cents(monkeypatch):
    monkeypatch.setattr(market_data, "kalshi_get", lambda path, params=None: {"markets": [
        {"ticker": "T", "status": "active", "yes_sub_title": "Team", "yes_bid_dollars": "0.5000",
         "yes_ask_dollars": "0.5200", "no_bid_dollars": "0.4800", "no_ask_dollars": "0.5000"},
        {"ticker": "CLOSED", "status": "closed"}]})
    row = market_data.get_market_summary("EVENT").to_dict("records")
    assert len(row) == 1
    assert (row[0]["yes_bid"], row[0]["yes_ask"], row[0]["no_bid"], row[0]["no_ask"]) == (50, 52, 48, 50)
    assert row[0]["yes_bid_effective"] == pytest.approx(50 - 1.75)


def test_empty_event_keeps_summary_columns(monkeypatch):
    monkeypatch.setattr(market_data, "kalshi_get", lambda path, params=None: {"markets": []})
    summary = market_data.get_market_summary("EVENT")
    assert summary.empty and "yes_bid_effective" in summary.columns


def test_market_quotes_are_batched(monkeypatch):
    calls = []
    monkeypatch.setattr(market_data, "kalshi_get", lambda path, params=None: calls.append(params) or {
        "markets": [{"ticker": t} for t in params["tickers"].split(",")]})
    tickers = [f"T{i}" for i in range(120)] + ["T0"]
    found = market_data.fetch_markets(tickers)
    assert len(found) == 120
    assert [len(c["tickers"].split(",")) for c in calls] == [50, 50, 20]


def test_positions_report_average_price_in_cents_and_values_in_dollars(monkeypatch):
    monkeypatch.setattr(positions, "kalshi_get", lambda path, params=None: {"market_positions": [
        {"ticker": "T", "position_fp": "-10.00", "market_exposure_dollars": "5.9000",
         "fees_paid_dollars": "0.1400", "realized_pnl_dollars": "0"}]})
    markets = pd.DataFrame([{"market_ticker": "T", "yes_bid_effective": 40.0, "no_bid_effective": 61.0}])
    row = positions.compute_positions("EVENT", markets).to_dict("records")[0]
    assert row["avg_share_price"] == 59.0
    assert row["liquidation_value"] == 6.1


@pytest.mark.parametrize("value,expected", [(None, True), ("", True), ("true", True),
                                            ("TRUE", True), ("false", False), ("no", False)])
def test_dry_run_defaults_to_simulation(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("DRY_RUN", raising=False)
    else:
        monkeypatch.setenv("DRY_RUN", value)
    assert config.env_flag("DRY_RUN", default=True) is expected
