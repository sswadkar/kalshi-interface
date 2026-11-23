"""Account balance, fetched by one owner and shared by every page.

Kalshi limits GET /portfolio/balance to one request per second with no burst, so
pages read this cache instead of calling the exchange themselves.
"""
import asyncio
import logging
import threading
import time

from kalshi_workstation.core.exchange.kalshi import get_balance

logger = logging.getLogger(__name__)


class Portfolio:
    def __init__(self, fetch=get_balance, interval_s: float = 2.0, clock=time.monotonic):
        self._fetch = fetch
        self.interval_s = interval_s
        self._clock = clock
        self._lock = threading.Lock()
        self._attempted = None
        self._balance = None          # raw exchange response, integer cents
        self.updated_at = None        # monotonic time of the last success
        self.error = None

    def balance(self):
        """Last known raw balance ({balance, portfolio_value} in cents) or None."""
        return self._balance

    def summary(self):
        """Last known account values in dollars."""
        raw = self._balance or {}
        if raw.get("balance") is None:
            return {"total_value": None, "cash": None, "position_value": None}
        cash = raw.get("balance", 0) / 100
        positions = (raw.get("portfolio_value") or 0) / 100
        return {"total_value": round(cash + positions, 2), "cash": round(cash, 2),
                "position_value": round(positions, 2)}

    def refresh(self):
        """Fetch unless the last attempt was within the interval. Blocking; thread-safe."""
        with self._lock:
            now = self._clock()
            if self._attempted is not None and now - self._attempted < self.interval_s:
                return
            self._attempted = now
            try:
                self._balance = self._fetch()
                self.updated_at = now
                if self.error:
                    logger.info("Balance updates recovered")
                self.error = None
            except Exception as exc:  # keep the last value; report once per distinct error
                message = f"{type(exc).__name__}: {exc}"
                if message != self.error:
                    logger.warning("Balance update failed: %s", message)
                self.error = message

    async def run(self):
        while True:
            await asyncio.to_thread(self.refresh)
            await asyncio.sleep(self.interval_s)
