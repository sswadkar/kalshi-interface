import asyncio


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
