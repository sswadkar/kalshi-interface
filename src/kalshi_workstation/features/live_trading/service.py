"""Manual trading view: event quotes, positions, resting orders, and activity.

Prices are integer cents; money totals are dollars. One instance per application.
"""
import asyncio
import datetime
import json
import logging
import uuid

import numpy as np
import pandas as pd

from kalshi_workstation.core.exchange.kalshi import get_resting_orders
from kalshi_workstation.core.execution.mode import ExecutionMode
from kalshi_workstation.core.market_data import get_market_summary
from kalshi_workstation.core.portfolio import Portfolio
from kalshi_workstation.core.positions import compute_positions

logger = logging.getLogger(__name__)


def sanitize_json(df: pd.DataFrame):
    """Records with JSON-safe numbers (NaN/inf become 0)."""
    clean = df.replace([np.inf, -np.inf, None], np.nan).fillna(0)
    return json.loads(clean.to_json(orient="records"))


class LiveTrading:
    def __init__(self, mode: ExecutionMode, portfolio: Portfolio, event_ticker: str | None,
                 poll_interval_s: float = 0.5, resting_interval_s: float = 3.0):
        self.mode = mode
        self.portfolio = portfolio
        self.event_ticker = event_ticker
        self.poll_interval_s = poll_interval_s
        self.resting_interval_s = resting_interval_s
        self.markets = None
        self.positions = None
        self.resting_orders = []
        self.messages = []
        self.last_pull = None
        self._errors = {}

    def add_message(self, type_, text, **details):
        self.messages.insert(0, {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "type": type_, "text": text, "details": details,
        })
        self.messages = self.messages[:100]

    def _report(self, source, error=None):
        """Log a loop failure once per distinct message, and its recovery."""
        text = None if error is None else f"{source} failed: {error}"
        if text == self._errors.get(source):
            return
        self._errors[source] = text
        if text:
            self.add_message("ERROR", text)
        else:
            self.add_message("INFO", f"{source} recovered")

    def market(self, ticker):
        return next((m for m in self.markets or [] if m["market_ticker"] == ticker), None)

    def status(self):
        return {
            "markets": self.markets,
            "positions": self.positions,
            "messages": self.messages,
            "last_pull": self.last_pull,
            "user_info": self.portfolio.balance(),
            "dry_run": self.mode.dry_run,
        }

    async def poll_markets_once(self):
        markets = await asyncio.to_thread(get_market_summary, self.event_ticker)
        positions = await asyncio.to_thread(compute_positions, self.event_ticker, markets)
        self.markets = sanitize_json(markets)
        self.positions = sanitize_json(positions)
        self.last_pull = datetime.datetime.now(datetime.UTC).isoformat()

    async def run_markets(self):
        if not self.event_ticker:
            self.add_message("ERROR", "No event_ticker in config.json; the live trader has no markets to show.")
            return
        while True:
            try:
                await self.poll_markets_once()
                self._report("Market polling")
            except Exception as exc:
                self._report("Market polling", exc)
            await asyncio.sleep(self.poll_interval_s)

    async def run_resting(self):
        if not self.event_ticker:
            return
        while True:
            try:
                self.resting_orders = await asyncio.to_thread(get_resting_orders, self.event_ticker)
                self._report("Resting order polling")
            except Exception as exc:
                self._report("Resting order polling", exc)
            await asyncio.sleep(self.resting_interval_s)
