"""Existing account position calculations and exchange-backed position view."""
import time
import pandas as pd
from kalshi_workstation.core.exchange.kalshi import kalshi_get


def average_share_cost(row):
    """
    Calculate the true average cost per contract (including fees),
    normalized into YES terms.
    """
    fill_count = row.get("fill_count", 0)
    if not fill_count or fill_count == 0:
        return None

    fill_price = row["taker_fill_cost"] / row["fill_count"] / 100
    fee_per_contract = row["taker_fees"] / row["fill_count"] / 100
    action = row["action"].lower()
    side = row["side"].lower()

    # BUY YES → pay price + fees
    if action == "buy" and side == "yes":
        return fill_price + fee_per_contract

    # SELL YES → receive (1 - price) - fees
    elif action == "sell" and side == "yes":
        return (1 - fill_price) - fee_per_contract

    # BUY NO → same as SELL YES → receive (1 - price) - fees
    elif action == "buy" and side == "no":
        return (1 - fill_price) - fee_per_contract

    # SELL NO → same as BUY YES → pay price + fees
    elif action == "sell" and side == "no":
        return fill_price + fee_per_contract

    else:
        return None


def normalized_signed_shares(row):
    """Compute signed position in YES terms."""
    action = row["action"].lower()
    side = row["side"].lower()
    count = row["fill_count"]

    if side == "yes":
        return count if action == "buy" else -count
    elif side == "no":
        # buying NO == selling YES
        return -count if action == "buy" else count
    else:
        return 0


def track_position(group):
    """
    Tracks net YES-equivalent position, cost basis, and total fees.
    Handles partial closes and flips robustly.
    """
    net_pos = 0
    avg_cost = 0.0
    total_fees = group["taker_fees_dollars"].sum()

    for _, row in group.sort_values("created_time").iterrows():
        action = row["action"].lower()
        side = row["side"].lower()
        price = row["avg_share_cost_dollars"]
        count = row["fill_count"]

        abs_pos = abs(net_pos)
        if action == "buy" and side == "yes":
            net_pos += count
            avg_cost = (avg_cost * abs_pos + price * count) / abs_pos

    return pd.Series({
        "net_yes_position": net_pos,
        "avg_share_price": avg_cost,
        "total_fees": total_fees,
    })


def realize_now(row, df_summary):
    """
    Given a position and market summary, compute:
      - liquidation value using fee-adjusted prices
      - actual Kalshi fees (from summary)
    """
    mkt = df_summary[df_summary["market_ticker"] == row["ticker"]]
    if mkt.empty:
        return pd.Series({
            "current_net_value_dollars": 0.0,
            "current_net_value_per_share": 0.0,
            "fee_component_dollars": 0.0,
            "side_to_sell": None
        })

    mkt = mkt.iloc[0]
    net_pos = row["net_yes_position"]
    avg_cost = row["avg_share_price"]  # noqa: F841 - preserve the legacy input contract
    abs_pos = abs(net_pos)

    # Determine which side to sell
    if net_pos > 0:
        # Long YES → sell YES
        sell_price = mkt["yes_bid_effective"]
        fee = mkt["fee_yes_bid"]
        side = "YES"
    elif net_pos < 0:
        # Short YES → sell NO
        sell_price = mkt["no_bid_effective"]
        fee = mkt["fee_no_bid"]
        side = "NO"
    else:
        return pd.Series({
            "current_net_value_dollars": 0.0,
            "current_net_value_per_share": 0.0,
            "fee_component_dollars": 0.0,
            "side_to_sell": None
        })

    return pd.Series({
        "current_net_value_dollars": round(sell_price * abs_pos, 2),
        "current_net_value_per_share": round(sell_price * abs_pos / abs_pos if abs_pos != 0 else 0.00, 2),
        "fee_component_dollars": round(fee * abs_pos, 4),
        "side_to_sell": side
    })


def compute_positions(event_ticker: str, df_markets: pd.DataFrame):
    """
    Fetch pre-computed positions for a given event from Kalshi and
    return them in the same format as the old manual calculation.
    """
    t0 = time.perf_counter()

    # ---- Fetch positions ----
    t1 = time.perf_counter()
    data = kalshi_get("/trade-api/v2/portfolio/positions", params={"event_ticker": event_ticker})
    print(f"Positions req time taken: {time.perf_counter() - t1:.3f} seconds")

    market_positions = pd.DataFrame(data.get("market_positions", []))
    # print(market_positions.to_string())
    if market_positions.empty:
        print("⚠️ No active market positions found for this event.")
        return pd.DataFrame(columns=[
            "ticker", "net_yes_position", "avg_share_price",
            "fees_paid_dollars", "realized_pnl_dollars",
            "market_exposure_dollars", "liquidation_value"
        ])

    # ---- Convert & clean numeric fields ----
    for col in [
        "fees_paid_dollars",
        "market_exposure_dollars",
        "realized_pnl_dollars",
        "total_traded_dollars",
    ]:
        if col in market_positions.columns:
            market_positions[col] = market_positions[col].astype(float)

    # ---- Derive legacy-compatible columns ----
    market_positions["ticker"] = market_positions["ticker"].astype(str)
    # Kalshi migrated the integer `position` field to the fixed-point string `position_fp`.
    pos_col = "position_fp" if "position_fp" in market_positions.columns else "position"
    market_positions["net_yes_position"] = market_positions[pos_col].astype(float)
    # avg cost = exposure / |position|; guard the flat-position 0/0 case (was silent NaN).
    market_positions["avg_share_price"] = [
        round(e / abs(p), 4) if p else 0.0
        for e, p in zip(market_positions["market_exposure_dollars"],
                        market_positions["net_yes_position"])
    ]

    # Optional join with market info (for price context)
    merged = pd.merge(
        market_positions,
        df_markets[
            [
                "market_ticker",
                "yes_bid_effective",
                "yes_ask_effective",
                "no_bid_effective",
                "no_ask_effective",
            ]
        ],
        left_on="ticker",
        right_on="market_ticker",
        how="left",
    ).drop(columns=["market_ticker"], errors="ignore")

    # ---- Compute liquidation value ----
    def compute_liquidation(row):
        pos = row["net_yes_position"]
        if pos == 0:
            return 0.0

        # positive → long YES → sell at yes_bid_effective
        if pos > 0:
            price = row["yes_bid_effective"]
            return pos * price

        # negative → long NO (short YES) → sell at no_bid_effective
        else:
            price = row["no_bid_effective"]
            return abs(pos) * price

    merged["liquidation_value"] = merged.apply(compute_liquidation, axis=1).round(2)

    t2 = time.perf_counter()
    print(f"✅ Position tracking retrieved in {t2 - t0:.3f} seconds")

    # ---- Return same columns as before ----
    return merged[
        [
            "ticker",
            "net_yes_position",
            "avg_share_price",
            "fees_paid_dollars",
            "realized_pnl_dollars",
            "market_exposure_dollars",
            "liquidation_value",
        ]
    ]
