"""Existing market summaries and in-memory order books.

Sharing subscriptions and enforcing freshness across all features remains future work.
"""
import time
from collections import deque
import pandas as pd
from kalshi_workstation.core.exchange.kalshi import kalshi_get
from kalshi_workstation.core.prices import kalshi_fee


def get_market_summary(event_ticker: str):
    """Fetch and summarize active Kalshi markets for a given event."""
    t0 = time.perf_counter()

    t0_0 = time.perf_counter()
    markets = kalshi_get("/trade-api/v2/markets", params={"event_ticker": event_ticker})
    print(f"Markets req time taken: {time.perf_counter() - t0_0:.3f} seconds")
    active_markets = [m for m in markets["markets"] if m["status"] == "active"]

    summary = []
    for m in active_markets:
        # Kalshi migrated to *_dollars string fields; old integer-cent fields are now None.
        def _d(key):
            v = m.get(key)
            if v is None or v == "":
                return 0.0
            try:
                return float(v)
            except (ValueError, TypeError):
                return 0.0

        yes_bid = _d("yes_bid_dollars")
        yes_ask = _d("yes_ask_dollars")
        no_bid = _d("no_bid_dollars")
        no_ask = _d("no_ask_dollars")

        # Fees
        fee_yes_bid = kalshi_fee(yes_bid)
        fee_yes_ask = kalshi_fee(yes_ask)
        fee_no_bid = kalshi_fee(no_bid)
        fee_no_ask = kalshi_fee(no_ask)

        # Effective prices
        yes_bid_eff = yes_bid - fee_yes_bid
        yes_ask_eff = yes_ask + fee_yes_ask
        no_bid_eff = no_bid - fee_no_bid
        no_ask_eff = no_ask + fee_no_ask

        summary.append({
            "market_ticker": m["ticker"],
            "team": m.get("yes_sub_title", ""),
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "no_bid": no_bid,
            "no_ask": no_ask,
            "last_price": _d("last_price_dollars"),
            "yes_bid_effective": yes_bid_eff,
            "yes_ask_effective": yes_ask_eff,
            "no_bid_effective": no_bid_eff,
            "no_ask_effective": no_ask_eff,
            "fee_yes_bid": fee_yes_bid,
            "fee_yes_ask": fee_yes_ask,
            "fee_no_bid": fee_no_bid,
            "fee_no_ask": fee_no_ask,
        })

    df_markets = pd.DataFrame(summary)

    t1 = time.perf_counter()
    print(f"✅ Market summary computed in {t1 - t0:.3f} seconds")
    return df_markets


def _cents(price_str) -> int:
    """'0.6600' -> 66. Assumes linear_cent (1c) tick markets."""
    return int(round(float(price_str) * 100))


def _size(size_str) -> float:
    try:
        return float(size_str)
    except (TypeError, ValueError):
        return 0.0


class MarketBook:
    """Local reconstruction of one market's order book + recent trade tape."""
    __slots__ = ("ticker", "yes", "no", "last_update_ms", "ready", "trades")

    def __init__(self, ticker):
        self.ticker = ticker
        self.yes = {}   # {cents: size} resting YES bids
        self.no = {}    # {cents: size} resting NO bids
        self.last_update_ms = 0
        self.ready = False
        self.trades = deque(maxlen=200)  # (ts_ms, count, taker_side) rolling tape

    # ---- snapshot / delta application ----
    def apply_snapshot(self, msg):
        self.yes = {_cents(p): _size(s) for p, s in (msg.get("yes_dollars_fp") or [])}
        self.no = {_cents(p): _size(s) for p, s in (msg.get("no_dollars_fp") or [])}
        self.last_update_ms = msg.get("ts_ms", 0)
        self.ready = True

    def apply_delta(self, msg):
        side = self.yes if msg["side"] == "yes" else self.no
        c = _cents(msg["price_dollars"])
        new = side.get(c, 0.0) + _size(msg["delta_fp"])
        if new <= 1e-9:
            side.pop(c, None)
        else:
            side[c] = new
        self.last_update_ms = msg.get("ts_ms", self.last_update_ms)

    def add_trade(self, msg):
        self.trades.append((
            msg.get("ts_ms", 0),
            _size(msg.get("count_fp")),
            msg.get("taker_side"),
        ))

    # ---- accessors (integer cents; None if that side is empty) ----
    def best_yes_bid(self):
        return max(self.yes) if self.yes else None

    def best_no_bid(self):
        return max(self.no) if self.no else None

    def best_yes_ask(self):
        b = self.best_no_bid()
        return (100 - b) if b is not None else None

    def spread(self):
        bid, ask = self.best_yes_bid(), self.best_yes_ask()
        if bid is None or ask is None:
            return None
        return ask - bid

    def size_at(self, side, cents):
        """Resting size at a price level. side='yes' bid-book, or 'no' bid-book."""
        return (self.yes if side == "yes" else self.no).get(cents, 0.0)

    def yes_depth_ahead_of_ask(self, cents):
        """Contracts resting ahead of a YES ask we'd place at `cents` = NO bid size at (100-cents)."""
        return self.no.get(100 - cents, 0.0)

    def trade_flow(self, window_ms=3000):
        """(yes_taker_volume, no_taker_volume) over the recent window — toxic-flow signal."""
        now = self.last_update_ms or int(time.time() * 1000)
        yv = nv = 0.0
        for ts, cnt, taker in self.trades:
            if now - ts <= window_ms:
                if taker == "yes":
                    yv += cnt
                elif taker == "no":
                    nv += cnt
        return yv, nv
