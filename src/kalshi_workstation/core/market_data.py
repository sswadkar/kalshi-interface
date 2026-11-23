"""Market quotes from the REST API.

Prices are integer cents. Fee-adjusted "effective" prices are fractional cents.
"""
import logging
import time
import pandas as pd
from kalshi_workstation.core.exchange.kalshi import kalshi_get
from kalshi_workstation.core.prices import dollars_to_cents, taker_fee_cents

logger = logging.getLogger(__name__)
MARKETS_PATH = "/trade-api/v2/markets"
TICKERS_PER_REQUEST = 50


def market_quote(market: dict) -> dict:
    """Top-of-book fields of one exchange market object, in cents."""
    return {
        "yes_bid": dollars_to_cents(market.get("yes_bid_dollars")),
        "yes_ask": dollars_to_cents(market.get("yes_ask_dollars")),
        "no_bid": dollars_to_cents(market.get("no_bid_dollars")),
        "no_ask": dollars_to_cents(market.get("no_ask_dollars")),
        "last_price": dollars_to_cents(market.get("last_price_dollars")),
        "status": market.get("status", "unknown"),
        "title": market.get("title", market.get("ticker")),
        "yes_sub_title": market.get("yes_sub_title", ""),
    }


def fetch_markets(tickers) -> dict:
    """{ticker: market} for many tickers, one request per 50 tickers.

    A ticker missing from the result was not returned by the exchange.
    """
    tickers = list(dict.fromkeys(tickers))
    found = {}
    for start in range(0, len(tickers), TICKERS_PER_REQUEST):
        chunk = tickers[start:start + TICKERS_PER_REQUEST]
        data = kalshi_get(MARKETS_PATH, params={"tickers": ",".join(chunk), "limit": 1000})
        for market in data.get("markets") or []:
            found[market["ticker"]] = market
    return found


def get_market_summary(event_ticker: str):
    """Active markets of one event with fee-adjusted prices (cents)."""
    started = time.perf_counter()
    markets = kalshi_get(MARKETS_PATH, params={"event_ticker": event_ticker})
    summary = []
    for m in markets.get("markets") or []:
        if m.get("status") != "active":
            continue
        quote = market_quote(m)
        row = {"market_ticker": m["ticker"], "team": m.get("yes_sub_title", ""),
               "yes_bid": quote["yes_bid"], "yes_ask": quote["yes_ask"],
               "no_bid": quote["no_bid"], "no_ask": quote["no_ask"],
               "last_price": quote["last_price"]}
        for key in ("yes_bid", "yes_ask", "no_bid", "no_ask"):
            fee = taker_fee_cents(row[key])
            row[f"fee_{key}"] = fee
            # Selling receives the bid minus the fee; buying pays the ask plus the fee.
            row[f"{key}_effective"] = row[key] - fee if key.endswith("bid") else row[key] + fee
        summary.append(row)
    logger.debug("Market summary for %s in %.3fs", event_ticker, time.perf_counter() - started)
    columns = ["market_ticker", "team", "yes_bid", "yes_ask", "no_bid", "no_ask", "last_price",
               *(f"{prefix}{key}{suffix}" for key in ("yes_bid", "yes_ask", "no_bid", "no_ask")
                 for prefix, suffix in (("fee_", ""), ("", "_effective")))]
    return pd.DataFrame(summary, columns=columns)
