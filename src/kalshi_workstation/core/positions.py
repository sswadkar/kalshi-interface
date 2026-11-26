"""Account positions for one event, valued at fee-adjusted bids.

Prices are cents (average price per contract); money totals are dollars.
"""
import logging
import time

import pandas as pd

from kalshi_workstation.core.exchange.kalshi import kalshi_get

logger = logging.getLogger(__name__)

COLUMNS = ["ticker", "net_yes_position", "avg_share_price", "fees_paid_dollars",
           "realized_pnl_dollars", "market_exposure_dollars", "liquidation_value"]


def compute_positions(event_ticker: str, df_markets: pd.DataFrame):
    """Exchange positions for an event plus a liquidation estimate.

    avg_share_price: cents per contract (exposure / |position|).
    liquidation_value: dollars, selling at the fee-adjusted bid of the held side.
    """
    started = time.perf_counter()
    data = kalshi_get("/trade-api/v2/portfolio/positions", params={"event_ticker": event_ticker})
    positions = pd.DataFrame(data.get("market_positions", []))
    if positions.empty:
        return pd.DataFrame(columns=COLUMNS)

    for col in ("fees_paid_dollars", "market_exposure_dollars", "realized_pnl_dollars",
                "total_traded_dollars"):
        if col in positions.columns:
            positions[col] = positions[col].astype(float)

    positions["ticker"] = positions["ticker"].astype(str)
    # Kalshi migrated the integer `position` field to the fixed-point string `position_fp`.
    position_column = "position_fp" if "position_fp" in positions.columns else "position"
    positions["net_yes_position"] = positions[position_column].astype(float)
    positions["avg_share_price"] = [
        round(exposure * 100 / abs(count), 2) if count else 0.0
        for exposure, count in zip(positions["market_exposure_dollars"], positions["net_yes_position"])
    ]

    merged = pd.merge(
        positions,
        df_markets[["market_ticker", "yes_bid_effective", "no_bid_effective"]],
        left_on="ticker", right_on="market_ticker", how="left",
    ).drop(columns=["market_ticker"], errors="ignore")

    def liquidation(row):
        count = row["net_yes_position"]
        if count == 0:
            return 0.0
        # Long YES sells YES at its bid; short YES (long NO) sells NO at its bid.
        price_cents = row["yes_bid_effective"] if count > 0 else row["no_bid_effective"]
        return abs(count) * price_cents / 100

    merged["liquidation_value"] = merged.apply(liquidation, axis=1).round(2)
    logger.debug("Positions for %s in %.3fs", event_ticker, time.perf_counter() - started)
    return merged[COLUMNS]
