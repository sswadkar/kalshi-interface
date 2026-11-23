"""Arbitrage groups, settings, quote polling, and pair execution. Prices are cents.

Pair execution makes blocking exchange calls, so it runs in a worker thread and
the event loop (other background jobs and the API) keeps running. One lock
serializes executions, so automatic and manual pairs never interleave.
"""
import asyncio
import datetime
import json
import logging
import time
import uuid
from pathlib import Path

from kalshi_workstation.core.execution.mode import ExecutionMode
from kalshi_workstation.core.market_data import fetch_markets, market_quote
from kalshi_workstation.core.persistence import write_json_atomic
from .analysis import find_opportunity
from .execution import execute_pair

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS = {
    "threshold": 5,             # cents, minimum gross edge for a candidate
    "buffer": 1,                # cents added to each leg's ask
    "skip_confirmation": False,
    "auto_arb": False,          # automatic execution (verified groups only)
    "auto_arb_quantity": 10,
}
AUTO_COOLDOWN_S = 5
EMPTY_QUOTE = {"yes_bid": 0, "yes_ask": 0, "no_bid": 0, "no_ask": 0, "last_price": 0}


class Arbitrage:
    def __init__(self, mode: ExecutionMode, config_path: Path, poll_interval_s: float = 0.5):
        self.mode = mode
        self.config_path = Path(config_path)
        self.poll_interval_s = poll_interval_s
        self.groups = []
        self.settings = dict(DEFAULT_SETTINGS)
        self.messages = []
        self.last_pull = None
        self._cooldown = {}           # group id -> monotonic time of last automatic pair
        self._notified = set()        # (kind, key) notices already logged
        self._poll_error = None
        self._execution_lock = asyncio.Lock()
        self.load()

    # ---------- configuration ----------
    def load(self):
        if not self.config_path.exists():
            return
        try:
            data = json.loads(self.config_path.read_text())
        except Exception as exc:
            logger.error("Could not load %s: %s", self.config_path.name, exc)
            return
        self.groups = data.get("groups", [])
        for group in self.groups:
            group.setdefault("payout_verified", False)
        self.settings = {**DEFAULT_SETTINGS, **data.get("settings", {})}
        logger.info("Arbitrage configuration loaded: %d groups, auto_arb=%s",
                    len(self.groups), self.settings["auto_arb"])

    def save(self, *, groups=None, settings=None):
        """Persist a proposed configuration before making it active in memory."""
        next_groups = self.groups if groups is None else groups
        next_settings = self.settings if settings is None else settings
        write_json_atomic(self.config_path, {"groups": next_groups, "settings": next_settings})
        self.groups, self.settings = next_groups, next_settings

    def group(self, group_id):
        return next((g for g in self.groups if g["id"] == group_id), None)

    def add_message(self, type_, text, **details):
        self.messages.insert(0, {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "type": type_, "text": text, "details": details,
        })
        self.messages = self.messages[:100]

    def _notify_once(self, kind, key, type_, text):
        if (kind, key) not in self._notified:
            self._notified.add((kind, key))
            self.add_message(type_, text)

    def forget_notice(self, kind, key):
        self._notified.discard((kind, key))

    # ---------- quotes and candidates ----------
    def compute_opportunities(self):
        threshold = self.settings["threshold"]
        for group in self.groups:
            group["opportunity"] = find_opportunity(group.get("markets", []), threshold)

    def clear_opportunities(self):
        for group in self.groups:
            group["opportunity"] = None

    async def refresh_quotes(self) -> bool:
        """Update every grouped market with one batched request. False if it failed."""
        groups = self.groups
        tickers = [m["ticker"] for g in groups for m in g.get("markets", [])]
        if not tickers:
            return True
        try:
            found = await asyncio.to_thread(fetch_markets, tickers)
        except Exception as exc:
            text = f"Arbitrage quote refresh failed: {exc}"
            if text != self._poll_error:
                self.add_message("ERROR", text)
            self._poll_error = text
            return False
        if self._poll_error:
            self.add_message("INFO", "Arbitrage quote refresh recovered")
            self._poll_error = None
        for group in groups:
            for market in group.get("markets", []):
                exchange_market = found.get(market["ticker"])
                if exchange_market is None:
                    market.update(EMPTY_QUOTE, status="not_found")
                    self._notify_once("missing", market["ticker"], "ERROR",
                                      f"Market {market['ticker']} was not returned by the exchange; "
                                      f"it is excluded from candidates.")
                else:
                    market.update(market_quote(exchange_market))
                    self.forget_notice("missing", market["ticker"])
        return True

    async def poll_once(self):
        if not await self.refresh_quotes():
            # Never compute or execute from quotes that failed to refresh.
            self.clear_opportunities()
            return
        self.compute_opportunities()
        self.last_pull = datetime.datetime.now(datetime.UTC).isoformat()
        if self.settings["auto_arb"]:
            await self._auto_execute()

    async def run(self):
        while True:
            try:
                await self.poll_once()
            except Exception as exc:
                logger.exception("Arbitrage polling failed")
                self.add_message("ERROR", f"Arbitrage polling failed: {exc}")
            await asyncio.sleep(self.poll_interval_s)

    # ---------- execution ----------
    def _ask(self, ticker, side):
        for group in self.groups:
            for market in group.get("markets", []):
                if market["ticker"] == ticker:
                    return market.get(f"{side}_ask") or None
        return None

    async def execute(self, ticker_a, side_a, ticker_b, side_b, quantity, is_auto=False):
        async with self._execution_lock:
            price_a, price_b = self._ask(ticker_a, side_a), self._ask(ticker_b, side_b)
            if price_a is None or price_b is None:
                return {"order1": None, "order2": None, "notes": [], "unhedged": None,
                        "errors": ["Could not find current prices for markets"]}
            return await asyncio.to_thread(
                execute_pair, ticker_a=ticker_a, side_a=side_a, price_a=price_a,
                ticker_b=ticker_b, side_b=side_b, price_b=price_b, quantity=quantity,
                buffer=self.settings["buffer"], dry_run=self.mode.dry_run,
                log=self.add_message, is_auto=is_auto)

    async def _auto_execute(self):
        quantity = self.settings["auto_arb_quantity"]
        for group in list(self.groups):
            opportunity = group.get("opportunity")
            if not opportunity:
                continue
            if not group.get("payout_verified"):
                self._notify_once("unverified", group["id"], "INFO",
                                  f"Auto-arb skipped “{group['name']}”: payouts are not verified as "
                                  f"complementary. Verify the group to allow automatic execution.")
                continue
            last = self._cooldown.get(group["id"])
            if last is not None and time.monotonic() - last < AUTO_COOLDOWN_S:
                continue
            kind = "reverse " if opportunity.get("is_reverse_arb") else ""
            self.add_message("INFO", f"🤖 Auto-{kind}arb triggered for {group['name']}: "
                                     f"{opportunity['profit']}¢ gross")
            self._cooldown[group["id"]] = time.monotonic()
            await self.execute(opportunity["market_a"], opportunity["market_a_side"],
                               opportunity["market_b"], opportunity["market_b_side"],
                               quantity, is_auto=True)

    def status(self):
        return {"groups": self.groups, "settings": self.settings, "messages": self.messages,
                "last_pull": self.last_pull, "dry_run": self.mode.dry_run}
