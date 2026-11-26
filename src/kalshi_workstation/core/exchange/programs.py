"""Active liquidity incentive programs, refreshed by one owner and shared by readers."""
import asyncio
import datetime
import logging
from collections.abc import Mapping

import requests

from kalshi_workstation.core.exchange.kalshi import kalshi_get

logger = logging.getLogger(__name__)


class ProgramCatalog(Mapping):
    """{market ticker: program} for programs running now.

    A refresh replaces the whole mapping at once, so readers never see it empty
    or half-built; a failed refresh keeps the last good catalog.
    """

    def __init__(self, interval_s: float = 300):
        self._programs = {}
        self.interval_s = interval_s
        self.error = None

    def replace(self, programs):
        self._programs = dict(programs)

    def refresh(self):
        """Fetch active programs (blocking). Returns True on success."""
        try:
            self.replace({p["ticker"]: p for p in load_active_liquidity_programs()})
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            self.error = f"network unreachable ({type(exc).__name__}); keeping last-known programs"
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
        else:
            self.error = None
            logger.info("Loaded %d active liquidity programs", len(self._programs))
            return True
        logger.warning("Liquidity program refresh failed: %s", self.error)
        return False

    async def run(self):
        while True:
            await asyncio.to_thread(self.refresh)
            await asyncio.sleep(self.interval_s)

    def snapshot(self):
        return dict(self._programs)

    def __getitem__(self, ticker):
        return self._programs[ticker]

    def __iter__(self):
        return iter(self._programs)

    def __len__(self):
        return len(self._programs)


def load_active_liquidity_programs():
    """All liquidity incentive programs whose [start,end] window contains now."""
    now = datetime.datetime.now(datetime.timezone.utc)
    progs, cursor = [], None
    while True:
        params = {"limit": 100, "status": "active"}  # server-side filter (128k -> ~2.9k)
        if cursor:
            params["cursor"] = cursor
        r = kalshi_get("/trade-api/v2/incentive_programs", params=params)
        progs += r.get("incentive_programs", [])
        cursor = r.get("next_cursor")
        if not cursor:
            break

    out = []
    for p in progs:
        if p.get("incentive_type") != "liquidity":
            continue
        s = datetime.datetime.fromisoformat(p["start_date"].replace("Z", "+00:00"))
        e = datetime.datetime.fromisoformat(p["end_date"].replace("Z", "+00:00"))
        if not (s <= now <= e):
            continue
        days = max((e - s).total_seconds() / 86400, 1e-9)
        out.append({
            "ticker": p["market_ticker"],
            "series": p["market_ticker"].split("-")[0],
            "reward_dollars": p["period_reward"] / 10000.0,
            "daily_dollars": (p["period_reward"] / 10000.0) / days,
            "days_left": max((e - now).total_seconds() / 86400, 0),
            "target_size": float(p.get("target_size_fp") or 0),
            "discount_factor": (p.get("discount_factor_bps") or 0) / 10000.0,
        })
    return out
