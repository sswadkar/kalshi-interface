"""Quote selection from supplied state; no exchange writes or credential access."""
from kalshi_workstation.core.liquidity import qualify_side


RISK_OFFSET = {"conservative": 1, "moderate": 0, "aggressive": 0}  # ticks behind best


RISK_SIZE_MULT = {"conservative": 0.5, "moderate": 1.0, "aggressive": 1.5}


def desired_quotes(market, program, book, settings, inventory):
    """Return {'yes': price_cents|None, 'no': price_cents|None} for where we WANT to rest.
    Gate-aware PER SIDE: quote a side only if that side's book reaches Target Size (else it
    scores 0). Sides are independent — there is NO two-sided requirement."""
    ticker = market["ticker"]
    prog = program
    if not prog or not book or not book.ready:
        return {"yes": None, "no": None}
    ts, df = prog["target_size"], prog["discount_factor"]
    qual = {"yes": qualify_side([[f"{c/100:.4f}", s] for c, s in book.yes.items()], ts, df),
            "no": qualify_side([[f"{c/100:.4f}", s] for c, s in book.no.items()], ts, df)}

    offset = RISK_OFFSET.get(settings["risk_level"], 1)
    net = inventory.get(ticker, 0)
    band = market.get("flatten_band", 300)

    want = {"yes": None, "no": None}
    sides = market.get("sides", "both")
    for side in ("yes", "no"):
        if sides not in ("both", side):
            continue
        q = qual[side]
        if q["ref_cents"] is None or not q["reached"]:
            continue                             # side missing or Target-Size gate unmet -> $0
        price = q["ref_cents"] - offset          # at/behind best on that bid book
        # comfort band
        cap = market.get(f"{side}_max_bid", 60)
        if price > cap or price < 1:
            continue
        # inventory skew: if already long this side's outcome past the band, stop adding
        if side == "yes" and net >= band:
            continue
        if side == "no" and -net >= band:
            continue
        want[side] = price
    return want
