"""Own the existing background tasks and await their shutdown."""
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from kalshi_workstation.core.portfolio import load_lp_rewards, load_portfolio_history, poll_lp_rewards
from kalshi_workstation.features.trading.runner import poll_markets, poll_resting_orders
from kalshi_workstation.features.arbitrage.state import load_arb_groups
from kalshi_workstation.features.arbitrage.runner import poll_arb_markets

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_arb_groups()
    load_lp_rewards()
    load_portfolio_history()
    tasks = [
        asyncio.create_task(poll_markets(), name="trading-markets"),
        asyncio.create_task(poll_resting_orders(), name="resting-orders"),
        asyncio.create_task(poll_arb_markets(), name="arbitrage"),
        asyncio.create_task(app.state.liquidity_provider.run(), name="liquidity-provider"),
        asyncio.create_task(poll_lp_rewards(app.state.programs), name="portfolio-rewards"),
    ]
    app.state.background_tasks = tasks
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
