import asyncio
import threading


def test_startup_and_shutdown_own_every_background_job(client):
    started, stopped = [], []

    async def worker(name):
        started.append(name)
        try:
            await asyncio.Event().wait()
        finally:
            stopped.append(name)

    jobs = client.app.state.background_jobs
    names = [name for name, _ in jobs]
    assert {"balance", "liquidity-programs", "trading-markets", "resting-orders", "arbitrage"} <= set(names)
    client.app.state.background_jobs = [(name, lambda name=name: worker(name)) for name in names]

    from kalshi_workstation import runtime

    async def scenario():
        async with runtime.lifespan(client.app):
            await asyncio.sleep(0)
            assert sorted(started) == sorted(names)
        assert all(task.done() for task in client.app.state.background_tasks)

    asyncio.run(scenario())
    assert sorted(started) == sorted(stopped)


def test_a_slow_exchange_call_does_not_stall_other_jobs(client, monkeypatch):
    """Jobs are separate tasks and exchange calls run in threads: a balance request
    that hangs must not hold up the arbitrage loop."""
    state = client.app.state
    release = threading.Event()

    def hanging_balance():
        release.wait(timeout=5)
        return {"balance": 0, "portfolio_value": 0}

    polls = []

    async def counting_poll():
        polls.append(1)

    monkeypatch.setattr(state.portfolio, "_fetch", hanging_balance)
    monkeypatch.setattr(state.arbitrage, "poll_once", counting_poll)
    monkeypatch.setattr(state.arbitrage, "poll_interval_s", 0.01)
    state.background_jobs = [(name, start) for name, start in state.background_jobs
                             if name in ("balance", "arbitrage")]

    from kalshi_workstation import runtime

    async def scenario():
        async with runtime.lifespan(client.app):
            await asyncio.sleep(0.2)
            hung, poll_count = not release.is_set(), len(polls)
            release.set()
        assert hung and poll_count >= 5

    asyncio.run(scenario())
