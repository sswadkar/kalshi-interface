"""Rank incentivized markets by estimated reward (read-only).

est_daily is an upper bound: it assumes the posted size keeps its full share of
the cheaper side's pool, with no new competition and no losses from being filled.
"""
import logging
from kalshi_workstation.core.exchange.kalshi import kalshi_get
from kalshi_workstation.core.liquidity import qualify_side, SIDE_POOL_FRACTION

logger = logging.getLogger(__name__)


def classify_risk(series):
    """Rough category for how likely a resting order is to be filled by better-informed
    traders: scheduled macro releases least, forecast-driven weather markets in
    between, news/sports/politics most."""
    s = series.upper()
    macro = ("KXGDP", "KXCPI", "KXBOND", "KXEURUSD", "KXUSDJPY", "KXFED", "KXRATE",
             "KXH100", "KXB200", "KXA100", "KXH200", "KXRTX")  # low informed flow
    weather = ("KXTEMP", "KXRAIN", "KXAAAGAS", "KXDIESEL", "KXHIGH", "KXSNOW")  # forecast-driven
    if any(s.startswith(m) for m in macro):
        return "MACRO"
    if any(s.startswith(w) for w in weather):
        return "WEATHER"
    return "NEWS"


def analyze_market(prog, my_size, book=None):
    """Compute gate status + EV estimate for one incentivized market."""
    t = prog["ticker"]
    ts, df = prog["target_size"], prog["discount_factor"]
    if book is None:
        ob = kalshi_get(f"/trade-api/v2/markets/{t}/orderbook").get("orderbook_fp", {})
    else:
        ob = book
    yes = qualify_side(ob.get("yes_dollars") or [], ts, df)   # YES bids
    no = qualify_side(ob.get("no_dollars") or [], ts, df)     # NO bids (= YES asks)

    # Sides are INDEPENDENT (no two-sided requirement) — each earns iff its own gate is met.
    any_side = yes["reached"] or no["reached"]

    # cheap side = the side whose Reference (best bid) price is lower -> less capital
    cheap = "yes" if (yes["ref_cents"] or 99) <= (no["ref_cents"] or 99) else "no"
    cheap_side = yes if cheap == "yes" else no
    ref_price = (cheap_side["ref_cents"] or 0) / 100.0

    # EV: post my_size at the cheap Reference (top band -> weight 1.0). Share of that side ~
    # my_score/(side_score+my_score) if that side's gate is met; each side caps at ~50% of pool.
    my_score = my_size * 1.0
    side_share = my_score / (cheap_side["score"] + my_score) if cheap_side["reached"] else 0.0
    est_daily = side_share * SIDE_POOL_FRACTION * prog["daily_dollars"] if cheap_side["reached"] else 0.0
    capital = my_size * ref_price
    roi_daily = (est_daily / capital) if capital > 0 else 0.0

    return {
        **prog,
        "risk": classify_risk(prog["series"]),
        "two_sided": yes["reached"] and no["reached"],   # informational only (not required)
        "any_side": any_side,
        "yes_ref": yes["ref_cents"], "yes_qual_size": yes["total_size"], "yes_reached": yes["reached"],
        "no_ref": no["ref_cents"], "no_qual_size": no["total_size"], "no_reached": no["reached"],
        "cheap_side": cheap, "cheap_ref_price": ref_price,
        "my_size": my_size, "capital": capital,
        "est_daily": est_daily, "roi_daily_pct": roi_daily * 100,
    }


def scan(programs, tickers=None, series=None, my_size=100, top=25, require_qualified=True):
    """Rank the given active programs. Order books are fetched per market."""
    progs = list(programs)
    if tickers:
        want = set(tickers)
        progs = [p for p in progs if p["ticker"] in want]
    elif series:
        want = set(series)
        progs = [p for p in progs if p["series"] in want]

    # Orderbook calls are the expensive step. When scanning broadly, only pull books
    # for the richest-pool shortlist (cheap pre-rank by daily reward, no book needed).
    scoped = bool(tickers or series)
    if not scoped:
        progs.sort(key=lambda p: p["daily_dollars"], reverse=True)
        progs = progs[: max(top * 3, 40)]
    logger.info("Analyzing order books for %d market(s)", len(progs))

    results = []
    for p in progs:
        try:
            results.append(analyze_market(p, my_size))
        except Exception as e:
            logger.warning("Scan skipped %s: %s", p["ticker"], e)
    if require_qualified:
        results = [r for r in results if r["any_side"]]
    results.sort(key=lambda r: r["est_daily"], reverse=True)
    return results[:top]
