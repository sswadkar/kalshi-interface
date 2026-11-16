"""Prototype opportunity calculations, preserved pending payoff/fee validation."""
from .state import arb_state


def compute_opportunities():
    """Find best arbitrage opportunity per group."""
    threshold = arb_state["settings"]["threshold"]

    for group in arb_state["groups"]:
        best_opportunity = None
        best_profit = 0
        markets = group.get("markets", [])

        # Need at least 2 markets to find arbitrage
        if len(markets) < 2:
            group["opportunity"] = None
            continue

        # Compare all pairs of markets in group
        for i, market_a in enumerate(markets):
            for market_b in markets[i+1:]:
                # Skip if price data not available
                if not all([market_a.get("yes_ask"), market_a.get("no_ask"),
                           market_b.get("yes_ask"), market_b.get("no_ask")]):
                    continue

                # Check if markets are in same or opposite direction
                a_reverse = market_a.get("is_reverse", False)
                b_reverse = market_b.get("is_reverse", False)
                same_direction = (a_reverse == b_reverse)

                if same_direction:
                    # Standard arb: Buy YES on one + Buy NO on other
                    # Option 1: Buy YES on A, Buy NO on B
                    cost_1 = market_a["yes_ask"] + market_b["no_ask"]
                    profit_1 = 100 - cost_1

                    # Option 2: Buy NO on A, Buy YES on B
                    cost_2 = market_a["no_ask"] + market_b["yes_ask"]
                    profit_2 = 100 - cost_2

                    if profit_1 > best_profit and profit_1 >= threshold:
                        best_opportunity = {
                            "market_a": market_a["ticker"],
                            "market_a_side": "yes",
                            "market_a_price": market_a["yes_ask"],
                            "market_b": market_b["ticker"],
                            "market_b_side": "no",
                            "market_b_price": market_b["no_ask"],
                            "profit": profit_1,
                            "total_cost": cost_1,
                            "is_reverse_arb": False
                        }
                        best_profit = profit_1

                    if profit_2 > best_profit and profit_2 >= threshold:
                        best_opportunity = {
                            "market_a": market_a["ticker"],
                            "market_a_side": "no",
                            "market_a_price": market_a["no_ask"],
                            "market_b": market_b["ticker"],
                            "market_b_side": "yes",
                            "market_b_price": market_b["yes_ask"],
                            "profit": profit_2,
                            "total_cost": cost_2,
                            "is_reverse_arb": False
                        }
                        best_profit = profit_2
                else:
                    # Reverse arb: Buy same side on both (YES+YES or NO+NO)
                    # Option 1: Buy YES on both
                    cost_1 = market_a["yes_ask"] + market_b["yes_ask"]
                    profit_1 = 100 - cost_1

                    # Option 2: Buy NO on both
                    cost_2 = market_a["no_ask"] + market_b["no_ask"]
                    profit_2 = 100 - cost_2

                    if profit_1 > best_profit and profit_1 >= threshold:
                        best_opportunity = {
                            "market_a": market_a["ticker"],
                            "market_a_side": "yes",
                            "market_a_price": market_a["yes_ask"],
                            "market_b": market_b["ticker"],
                            "market_b_side": "yes",
                            "market_b_price": market_b["yes_ask"],
                            "profit": profit_1,
                            "total_cost": cost_1,
                            "is_reverse_arb": True
                        }
                        best_profit = profit_1

                    if profit_2 > best_profit and profit_2 >= threshold:
                        best_opportunity = {
                            "market_a": market_a["ticker"],
                            "market_a_side": "no",
                            "market_a_price": market_a["no_ask"],
                            "market_b": market_b["ticker"],
                            "market_b_side": "no",
                            "market_b_price": market_b["no_ask"],
                            "profit": profit_2,
                            "total_cost": cost_2,
                            "is_reverse_arb": True
                        }
                        best_profit = profit_2

        group["opportunity"] = best_opportunity
