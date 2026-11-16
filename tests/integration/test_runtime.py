import asyncio
import json

from kalshi_workstation import runtime
from kalshi_workstation.core import portfolio


def test_startup_and_shutdown_own_exactly_five_tasks(client, monkeypatch):
    started = []
    stopped = []
    async def worker(name):
        started.append(name)
        try:
            await asyncio.Event().wait()
        finally:
            stopped.append(name)
    for name in ("load_arb_groups", "load_lp_rewards", "load_portfolio_history"):
        monkeypatch.setattr(runtime, name, lambda: None)
    for name in ("poll_markets", "poll_resting_orders", "poll_arb_markets"):
        monkeypatch.setattr(runtime, name, lambda name=name: worker(name))
    monkeypatch.setattr(runtime, "poll_lp_rewards", lambda programs: worker("rewards"))
    monkeypatch.setattr(client.app.state.liquidity_provider, "run", lambda: worker("lp"))
    async def scenario():
        async with runtime.lifespan(client.app):
            await asyncio.sleep(0)
            assert len(started) == 5
        assert all(task.done() for task in client.app.state.background_tasks)
    asyncio.run(scenario())
    assert sorted(started) == sorted(stopped)


def test_provider_shutdown_awaits_its_stream(client, monkeypatch):
    from kalshi_workstation.features.liquidity_provider import runner
    stopped = []
    started = asyncio.Event()
    class Stream:
        def __init__(self, tickers, **kwargs):
            self.tickers = tickers
        async def run(self):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.append("stream")
    provider = client.app.state.liquidity_provider
    monkeypatch.setattr(runner, "LiquidityWS", Stream)
    monkeypatch.setattr(provider, "refresh_programs", lambda: None)
    async def scenario():
        task = asyncio.create_task(provider.run())
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
    assert stopped == ["stream"]


def test_history_reload_preserves_references_used_by_routes(monkeypatch, tmp_path):
    from kalshi_workstation.features.trading import routes
    path = tmp_path / "history.json"
    path.write_text(json.dumps({"2020-01-01": {"value": 10, "lp": 0}}))
    monkeypatch.setattr(portfolio, "PORTFOLIO_HISTORY_FILE", path)
    previous = dict(portfolio.portfolio_history)
    try:
        portfolio.load_portfolio_history()
        assert routes.portfolio_history is portfolio.portfolio_history
        assert routes.portfolio_history["2020-01-01"]["value"] == 10
    finally:
        portfolio.portfolio_history.clear()
        portfolio.portfolio_history.update(previous)
