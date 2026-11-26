"""Arbitrage polling, automatic execution eligibility, and execution off the event loop."""
import asyncio
import threading

import pytest

from kalshi_workstation.core.execution import service as execution
from kalshi_workstation.features.arbitrage import service as arbitrage_service


def exchange_market(ticker, yes_ask, no_ask):
    return {"ticker": ticker, "yes_ask_dollars": f"{yes_ask / 100:.4f}",
            "no_ask_dollars": f"{no_ask / 100:.4f}", "yes_bid_dollars": "0.0100",
            "no_bid_dollars": "0.0100", "status": "active", "title": ticker}


@pytest.fixture
def arbitrage(app, monkeypatch):
    """Two markets whose asks sum to 90¢: a 10¢ gross candidate on every poll."""
    arb = app.state.arbitrage
    arb.save(groups=[{"id": "g1", "name": "Pair", "payout_verified": False, "markets": [
        {"ticker": "A", "is_reverse": False}, {"ticker": "B", "is_reverse": False}]}],
        settings={**arb.settings, "auto_arb": True, "auto_arb_quantity": 2})
    requests = []

    def fetch(tickers):
        requests.append(list(tickers))
        return {"A": exchange_market("A", 40, 62), "B": exchange_market("B", 55, 50)}

    monkeypatch.setattr(arbitrage_service, "fetch_markets", fetch)
    arb.fetch_requests = requests
    return arb


@pytest.fixture
def orders(monkeypatch):
    sent = []
    monkeypatch.setattr(execution, "submit_order", lambda payload: sent.append(payload) or {
        "order_id": "o", "fill_count": payload["count"], "remaining_count": "0.00"})
    return sent


def test_quotes_for_all_markets_come_from_one_request(arbitrage, orders):
    asyncio.run(arbitrage.poll_once())
    assert arbitrage.fetch_requests == [["A", "B"]]
    opportunity = arbitrage.groups[0]["opportunity"]
    assert (opportunity["market_a_side"], opportunity["market_b_side"], opportunity["profit"]) == ("yes", "no", 10)


def test_auto_arb_skips_groups_without_verified_payouts(arbitrage, orders):
    arbitrage.mode.dry_run = False
    asyncio.run(arbitrage.poll_once())
    asyncio.run(arbitrage.poll_once())
    assert orders == []
    notices = [m["text"] for m in arbitrage.messages if "not verified" in m["text"]]
    assert len(notices) == 1  # logged once, not on every poll


def test_auto_arb_executes_verified_groups(arbitrage, orders):
    arbitrage.mode.dry_run = False
    arbitrage.groups[0]["payout_verified"] = True
    asyncio.run(arbitrage.poll_once())
    assert [o["ticker"] for o in orders] == ["A", "B"]
    asyncio.run(arbitrage.poll_once())
    assert len(orders) == 2  # cooldown


def test_failed_quote_refresh_clears_candidates_and_never_executes(arbitrage, orders, monkeypatch):
    arbitrage.mode.dry_run = False
    arbitrage.groups[0]["payout_verified"] = True
    asyncio.run(arbitrage.poll_once())
    arbitrage._cooldown.clear()
    del orders[:]

    def fail(tickers):
        raise ConnectionError("synthetic outage")
    monkeypatch.setattr(arbitrage_service, "fetch_markets", fail)
    asyncio.run(arbitrage.poll_once())
    asyncio.run(arbitrage.poll_once())
    assert arbitrage.groups[0]["opportunity"] is None
    assert orders == []
    assert sum("quote refresh failed" in m["text"] for m in arbitrage.messages) == 1


def test_markets_missing_from_the_exchange_are_excluded_not_deleted(arbitrage, monkeypatch):
    monkeypatch.setattr(arbitrage_service, "fetch_markets", lambda tickers: {
        "A": exchange_market("A", 40, 62)})
    asyncio.run(arbitrage.poll_once())
    group = arbitrage.groups[0]
    assert [m["ticker"] for m in group["markets"]] == ["A", "B"]
    assert group["markets"][1]["status"] == "not_found"
    assert group["opportunity"] is None


def test_execution_runs_off_the_event_loop_and_serializes_pairs(arbitrage, monkeypatch):
    arbitrage.mode.dry_run = False
    asyncio.run(arbitrage.poll_once())
    threads, active, overlap = [], [], []
    loop_thread = threading.get_ident()

    def submit(payload):
        threads.append(threading.get_ident())
        active.append(payload)
        if len(active) > 1:
            overlap.append(True)
        threading.Event().wait(0.02)
        active.remove(payload)
        return {"order_id": "o", "fill_count": payload["count"], "remaining_count": "0.00"}

    monkeypatch.setattr(execution, "submit_order", submit)

    async def scenario():
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.001)

        beat = asyncio.create_task(heartbeat())
        await asyncio.gather(*(arbitrage.execute("A", "yes", "B", "no", 1) for _ in range(2)))
        beat.cancel()
        return ticks

    ticks = asyncio.run(scenario())
    assert len(threads) == 4 and loop_thread not in threads
    assert not overlap          # the two pairs did not interleave
    assert ticks > 10           # the event loop kept running during execution


def test_adding_a_market_clears_verification(client, monkeypatch):
    from kalshi_workstation.features.arbitrage import routes
    monkeypatch.setattr(routes, "kalshi_get", lambda path: {"market": exchange_market("C", 30, 70)})
    group = client.post("/api/arb/groups", json={"name": "Pair"}).json()["group"]
    verified = client.put(f"/api/arb/groups/{group['id']}", json={"payout_verified": True})
    assert verified.json()["group"]["payout_verified"] is True
    assert verified.json()["group"]["name"] == "Pair"
    added = client.post(f"/api/arb/groups/{group['id']}/markets", json={"ticker": "C"})
    assert added.status_code == 200
    assert added.json()["market"]["yes_ask"] == 30
    assert client.app.state.arbitrage.group(group["id"])["payout_verified"] is False


def test_manual_execution_does_not_require_verification(client, arbitrage, orders):
    client.app.state.mode.dry_run = False
    asyncio.run(arbitrage.poll_once())
    response = client.post("/api/arb/execute", json={
        "ticker_a": "A", "side_a": "yes", "ticker_b": "B", "side_b": "no", "quantity": 1})
    assert response.status_code == 200
    assert response.json()["errors"] == []
    assert [o["ticker"] for o in orders] == ["A", "B"]
