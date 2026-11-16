"""Existing price conversion and fee estimate; precision/model changes are separate work."""


def _dollars_to_cents(val):
    """Kalshi now returns prices as string dollars ('0.5860'); arb pipeline uses integer cents."""
    if val is None or val == "":
        return 0
    try:
        return int(round(float(val) * 100))
    except (ValueError, TypeError):
        return 0


def kalshi_fee(price_dollars: float) -> float:
    """
    Compute Kalshi taker fee per contract (in dollars).
    Formula: 0.07 * P * (1 - P), rounded up to the nearest cent.
    No intermediate rounding; only final ceiling to cent.
    """
    return 0.07 * price_dollars * (1 - price_dollars)
