"""Account value, existing history, and estimated reward integration.

JSON persistence is retained; no personal historical backfill is bundled.
These files are not a durable order/fill journal.
"""
import asyncio
import datetime
import json
import time
from kalshi_workstation.config import DATA_DIR
from kalshi_workstation.core.exchange.kalshi import kalshi_get
from kalshi_workstation.core.liquidity import _lp_reward_daily


LP_REWARDS_FILE = DATA_DIR / "lp_rewards_history.json"


lp_rewards = {"lifetime_est": 0.0, "current_daily": 0.0, "started": None,
              "last_ts": None, "history": []}


def load_lp_rewards():
    if LP_REWARDS_FILE.exists():
        try:
            with open(LP_REWARDS_FILE) as f:
                lp_rewards.update(json.load(f))
        except Exception as e:
            print(f"[LP rewards] load error: {e}")
    lp_rewards["last_ts"] = None   # don't accumulate across restart/downtime gaps


def save_lp_rewards():
    try:
        with open(LP_REWARDS_FILE, "w") as f:
            json.dump(lp_rewards, f)
    except Exception as e:
        print(f"[LP rewards] save error: {e}")


_portfolio_cache = {"ts": 0.0, "data": {"total_value": None, "cash": None, "position_value": None}}


def get_portfolio():
    now = time.time()
    if now - _portfolio_cache["ts"] < 3 and _portfolio_cache["data"]["cash"] is not None:
        return _portfolio_cache["data"]
    try:
        b = kalshi_get("/trade-api/v2/portfolio/balance")
        equity = (b.get("portfolio_value") or 0) / 100.0   # position (equity) value
        cash = (b.get("balance") or 0) / 100.0             # free cash
        data = {"total_value": round(equity + cash, 2), "cash": round(cash, 2),
                "position_value": round(equity, 2)}
        _portfolio_cache.update(ts=now, data=data)
    except Exception:
        pass
    return _portfolio_cache["data"]


PORTFOLIO_HISTORY_FILE = DATA_DIR / "portfolio_history.json"


portfolio_history = {}   # live-logged points (today onward): "YYYY-MM-DD" -> {"value", "lp"}


# Public source contains no personal historical account values.
PORTFOLIO_BACKFILL = {}


def load_portfolio_history():
    if PORTFOLIO_HISTORY_FILE.exists():
        try:
            with open(PORTFOLIO_HISTORY_FILE) as f:
                loaded = json.load(f)
                portfolio_history.clear()
                portfolio_history.update(loaded)
        except Exception as e:
            print(f"[portfolio history] load error: {e}")


def record_portfolio_point():
    """Upsert today's total value (equity + cash + pending LP rewards). Read-merge-write so a
    stale in-memory dict can never clobber the persisted history."""
    pf = get_portfolio()
    if pf.get("total_value") is None:
        return
    today = datetime.date.today().isoformat()
    total = round(pf["total_value"] + lp_rewards.get("pending_lp", 0.0), 2)
    hist = {}
    if PORTFOLIO_HISTORY_FILE.exists():
        try:
            with open(PORTFOLIO_HISTORY_FILE) as f:
                hist = json.load(f)
        except Exception:
            hist = dict(portfolio_history)
    hist[today] = {"value": total, "lp": round(lp_rewards["lifetime_est"], 2)}
    portfolio_history.clear()
    portfolio_history.update(hist)
    try:
        with open(PORTFOLIO_HISTORY_FILE, "w") as f:
            json.dump(hist, f)
    except Exception as e:
        print(f"[portfolio history] save error: {e}")


def compute_reward_rate(programs):
    """Account-wide estimated LP reward rate ($/day) across all resting orders. Blocking."""
    orders, cursor = [], None
    while True:
        params = {"status": "resting", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        d = kalshi_get("/trade-api/v2/portfolio/orders", params=params)
        b = d.get("orders", [])
        orders += b
        cursor = d.get("next_cursor")
        if not cursor or not b:
            break
    ob_cache, total = {}, 0.0
    for o in orders:
        t = o.get("ticker")
        prog = programs.get(t)
        if not prog:
            continue
        if t not in ob_cache:
            try:
                ob_cache[t] = kalshi_get(f"/trade-api/v2/markets/{t}/orderbook").get("orderbook_fp", {})
            except Exception:
                ob_cache[t] = {}
        try:
            rem = float(o.get("remaining_count_fp") or 0)
        except (TypeError, ValueError):
            rem = 0.0
        total += _lp_reward_daily((o.get("action") or "").lower(), (o.get("side") or "").lower(),
                                  o, rem, prog, ob_cache[t]) or 0
    return total


async def poll_lp_rewards(programs):
    """Every 5 min: sample the account-wide reward rate and integrate it into a lifetime estimate."""
    await asyncio.sleep(25)   # let lp_maker.programs load first
    while True:
        try:
            rate = await asyncio.to_thread(compute_reward_rate, programs)
            now = time.time()
            if lp_rewards["started"] is None:
                lp_rewards["started"] = datetime.datetime.now(datetime.UTC).isoformat()
            if lp_rewards["last_ts"] is not None:
                dt = now - lp_rewards["last_ts"]
                avg = (lp_rewards["current_daily"] + rate) / 2.0     # trapezoidal
                accrued = avg * (dt / 86400.0)
                lp_rewards["lifetime_est"] += accrued
                lp_rewards["pending_lp"] = lp_rewards.get("pending_lp", 0.0) + accrued
            lp_rewards["current_daily"] = rate
            lp_rewards["last_ts"] = now
            lp_rewards["history"].append(
                {"ts": datetime.datetime.now(datetime.UTC).isoformat(), "daily": round(rate, 4)})
            lp_rewards["history"] = lp_rewards["history"][-2000:]
            save_lp_rewards()
            await asyncio.to_thread(record_portfolio_point)   # daily portfolio-value point
        except Exception as e:
            print(f"[LP rewards] poll failed: {e}")
        await asyncio.sleep(300)
