"""Price units. Per-contract prices are integer cents; money totals are dollars.

Kalshi sends prices as dollar strings ('0.5860'). Converting to whole cents assumes
1-cent tick markets; sub-cent ticks are rounded to the nearest cent.
"""


def dollars_to_cents(value, default=0):
    """'0.5860' -> 59. Missing or unparseable values return `default`."""
    if value is None or value == "":
        return default
    try:
        return int(round(float(value) * 100))
    except (ValueError, TypeError):
        return default


def taker_fee_cents(price_cents: float) -> float:
    """Estimated Kalshi taker fee per contract, in (fractional) cents.

    Formula: 0.07 * P * (1 - P) with P in dollars. This is an unrounded per-contract
    estimate; the exchange rounds an order's total fee up to the next cent.
    """
    return 0.07 * price_cents * (100 - price_cents) / 100
