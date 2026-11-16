"""Refresh the manual trading view using the existing polling cadence."""
import asyncio
import datetime
from kalshi_workstation.config import EVENT_TICKER
from kalshi_workstation.core.market_data import get_market_summary
from kalshi_workstation.core.positions import compute_positions
from kalshi_workstation.core.exchange.kalshi import get_user_info, get_queue_positions
from .state import state, sanitize_json, add_message


async def poll_markets():
    """Poll markets + positions every 500ms."""
    while True:
        try:
            # blocking REST off the event loop so the API stays responsive
            df_markets = await asyncio.to_thread(get_market_summary, EVENT_TICKER)
            df_positions = await asyncio.to_thread(compute_positions, EVENT_TICKER, df_markets)

            state["markets"] = sanitize_json(df_markets)
            state["positions"] = sanitize_json(df_positions)
            state["last_pull"] = datetime.datetime.now(datetime.UTC).isoformat()
            state["user_info"] = await asyncio.to_thread(get_user_info)

        except Exception as e:
            add_message("ERROR", f"Polling failed: {e}")

        await asyncio.sleep(0.5)


async def poll_resting_orders():
    """Poll resting orders every few seconds and cache them."""
    while True:
        try:
            resting = await asyncio.to_thread(get_queue_positions, EVENT_TICKER)

            state["resting_orders"] = resting
        except Exception as e:
            add_message("ERROR", f"Resting order polling failed: {e}")

        await asyncio.sleep(3)
