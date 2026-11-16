"""Read-only program scanning and rankings using the existing reward assumptions."""
import sys
from kalshi_workstation.core.exchange.kalshi import kalshi_get
from kalshi_workstation.core.exchange.programs import load_active_liquidity_programs
from kalshi_workstation.core.liquidity import qualify_side, SIDE_POOL_FRACTION


def classify_risk(series):
    """Rough adverse-selection tag from the series prefix (measured base: single-name/
    news markets have the worst informed flow; broad-based macro the least)."""
    s = series.upper()
    macro = ("KXGDP", "KXCPI", "KXBOND", "KXEURUSD", "KXUSDJPY", "KXFED", "KXRATE",
             "KXH100", "KXB200", "KXA100", "KXH200", "KXRTX")  # low informed flow
    weather = ("KXTEMP", "KXRAIN", "KXAAAGAS", "KXDIESEL", "KXHIGH", "KXSNOW")  # forecast-driven
    if any(s.startswith(m) for m in macro):
        return "SAFE"
    if any(s.startswith(w) for w in weather):
        return "WEATHER"
    return "NEWS"   # earnings/sports/politics/player markets -> toxic


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


def scan(tickers=None, series=None, my_size=100, top=25, require_qualified=True):
    progs = load_active_liquidity_programs()
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
    print(f"  analyzing order books for {len(progs)} market(s)...")

    results = []
    for p in progs:
        try:
            results.append(analyze_market(p, my_size))
        except Exception as e:
            print(f"  ! {p['ticker']}: {e}")
    if require_qualified:
        results = [r for r in results if r["any_side"]]
    results.sort(key=lambda r: r["est_daily"], reverse=True)
    return results[:top]


def _fmt(rows, my_size):
    print(f"\n{'ticker':40s} {'risk':7s} {'2s':2s} {'pool$/d':>7s} {'cheap':>5s} {'ref¢':>4s} "
          f"{'qualSz':>7s} {'cap$':>8s} {'maxEV$/d':>8s}")
    print("-" * 100)
    for r in rows:
        print(f"{r['ticker']:40s} {r['risk']:7s} {'Y' if r['two_sided'] else 'n':^2s} "
              f"{r['daily_dollars']:7.2f} {r['cheap_side']:>5s} {(r['cheap_ref_price']*100):4.0f} "
              f"{(r['yes_qual_size'] if r['cheap_side']=='yes' else r['no_qual_size']):7.0f} "
              f"{r['capital']:8.2f} {r['est_daily']:8.2f}")
    print(f"""
NOTE: maxEV$/d is a NAIVE UPPER BOUND — it assumes you post {my_size} contracts at the
cheap-side reference and capture your full geometric share of that side with NO competition
dilution and NO adverse selection. Real earnings are much lower (others farm the same
reference; steady-state share collapses) and WEATHER/NEWS markets can lose money to informed
fills. Use this to (a) confirm the cheap side's Target-Size gate passes and (b) rank RELATIVELY
within a risk tier. The tiny live probe is what turns this into real numbers.
  risk: SAFE=low informed flow (macro)  WEATHER=forecast-driven  NEWS=earnings/sports/politics (toxic)""")


if __name__ == "__main__":
    args = sys.argv[1:]
    size = 100
    if args and args[0].isdigit():
        size = int(args[0]); args = args[1:]
    tickers = args or None
    print(f"Scanning active liquidity programs (my_size={size})...")
    rows = scan(tickers=tickers, my_size=size, top=30)
    _fmt(rows, size)
