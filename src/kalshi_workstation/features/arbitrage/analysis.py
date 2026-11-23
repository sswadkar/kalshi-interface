"""Gross-edge candidates from quoted asks (cents). Fees and buffers are excluded.

A pair is only a real arbitrage when its payouts are complementary; that cannot be
checked from prices, which is why automatic execution requires a verified group.
"""


def find_opportunity(markets, threshold):
    """Best pair in one group whose gross edge is positive and >= threshold, or None."""
    best, best_profit = None, 0
    for i, market_a in enumerate(markets):
        for market_b in markets[i + 1:]:
            if not all((market_a.get("yes_ask"), market_a.get("no_ask"),
                        market_b.get("yes_ask"), market_b.get("no_ask"))):
                continue
            reverse = market_a.get("is_reverse", False) != market_b.get("is_reverse", False)
            # Same mapping: YES on one + NO on the other. Reverse mapping: same side on both.
            options = ((("yes", "yes"), ("no", "no")) if reverse
                       else (("yes", "no"), ("no", "yes")))
            for side_a, side_b in options:
                price_a, price_b = market_a[f"{side_a}_ask"], market_b[f"{side_b}_ask"]
                profit = 100 - (price_a + price_b)
                if profit > best_profit and profit >= threshold:
                    best_profit = profit
                    best = {
                        "market_a": market_a["ticker"], "market_a_side": side_a,
                        "market_a_price": price_a,
                        "market_b": market_b["ticker"], "market_b_side": side_b,
                        "market_b_price": price_b,
                        "profit": profit, "total_cost": price_a + price_b,
                        "is_reverse_arb": reverse,
                    }
    return best
