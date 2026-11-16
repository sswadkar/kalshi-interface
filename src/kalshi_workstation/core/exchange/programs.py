"""Read active exchange liquidity-program metadata."""
import datetime
from collections.abc import Mapping
from kalshi_workstation.core.exchange.kalshi import kalshi_get


class ProgramCatalog(Mapping):
    """Shared metadata reference; publish a complete refresh without an empty interval."""

    def __init__(self):
        self._programs = {}

    def replace(self, programs):
        self._programs = dict(programs)

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
