"""Existing authenticated stream; protocol handling is separate from book state."""
import asyncio
import datetime
import json
import time
import websockets
from kalshi_workstation.config import ENV
from kalshi_workstation.core.exchange.kalshi import create_signature, get_credentials
from kalshi_workstation.core.market_data import MarketBook

WS_PATH = "/trade-api/ws/v2"
WS_URL = ("wss://external-api-ws.kalshi.com/trade-api/ws/v2" if ENV != "DEMO"
          else "wss://demo-api-ws.kalshi.co/trade-api/ws/v2")


class LiquidityWS:
    """Manages one authenticated WS connection subscribing N markets.

    Each market is subscribed with its own command id so we can map the
    server-assigned `sid` -> ticker and track a per-sid sequence counter for
    gap detection (a gap triggers a resubscribe -> fresh snapshot).
    """

    def __init__(self, tickers, on_trade=None, on_book=None, on_fill=None, on_order=None):
        self.tickers = list(tickers)
        self.books = {t: MarketBook(t) for t in self.tickers}
        self.on_trade = on_trade          # callback(ticker, msg)
        self.on_book = on_book            # callback(ticker) after each book change
        self.on_fill = on_fill            # callback(msg) on our own fills (private)
        self.on_order = on_order          # callback(msg) on our order lifecycle (private)
        self._ws = None
        self._connected = False
        self._last_seq = None             # single per-connection sequence counter
        self._gap = False                 # set on a detected gap -> force reconnect
        self.last_msg_ms = 0

    def _auth_headers(self):
        ts = str(int(datetime.datetime.now().timestamp() * 1000))
        api_key_id, private_key = get_credentials()
        sig = create_signature(private_key, ts, "GET", WS_PATH)
        return {
            "KALSHI-ACCESS-KEY": api_key_id,
            "KALSHI-ACCESS-SIGNATURE": sig,
            "KALSHI-ACCESS-TIMESTAMP": ts,
        }

    async def _subscribe_all(self):
        """Subscribe public book/trade (seq'd, one stream) and, separately, the private
        fill + user_orders channels (no seq -> excluded from gap detection)."""
        self._last_seq = None
        if not self.tickers:
            return  # no enabled markets -> nothing to subscribe
        await self._ws.send(json.dumps({
            "id": 1,
            "cmd": "subscribe",
            "params": {"channels": ["orderbook_delta", "trade"],
                       "market_tickers": self.tickers},
        }))
        # Private channels: our own fills and resting-order lifecycle. Scoped to our
        # markets; the engine further filters to LP-owned orders by client_order_id.
        await self._ws.send(json.dumps({
            "id": 2,
            "cmd": "subscribe",
            "params": {"channels": ["fill", "user_orders"],
                       "market_tickers": self.tickers},
        }))

    def _handle(self, d):
        t = d.get("type")
        self.last_msg_ms = int(time.time() * 1000)

        if t == "subscribed":
            return
        if t == "error":
            print(f"[LP-WS] error: {d.get('msg')}")
            return

        # ---- private channels (no seq; scoped to us) ----
        if t == "fill":
            if self.on_fill:
                self.on_fill(d.get("msg") or {})
            return
        if t == "user_order":
            if self.on_order:
                self.on_order(d.get("msg") or {})
            return

        seq = d.get("seq")
        # ---- per-connection sequence gap detection ----
        if seq is not None:
            if self._last_seq is not None and seq != self._last_seq + 1:
                print(f"[LP-WS] seq gap {self._last_seq} -> {seq}; forcing reconnect")
                self._gap = True
                return
            self._last_seq = seq

        msg = d.get("msg") or {}
        ticker = msg.get("market_ticker")
        if ticker is None or ticker not in self.books:
            return  # not a market we track (or a control frame)
        book = self.books[ticker]

        if t == "orderbook_snapshot":
            book.apply_snapshot(msg)
        elif t == "orderbook_delta":
            if not book.ready:
                return
            book.apply_delta(msg)
        elif t == "trade":
            book.add_trade(msg)
            if self.on_trade:
                self.on_trade(ticker, msg)
            return
        else:
            return

        if self.on_book:
            self.on_book(ticker)

    async def run(self):
        """Connect + consume forever, reconnecting with backoff."""
        backoff = 1
        while True:
            try:
                async with websockets.connect(
                    WS_URL, additional_headers=self._auth_headers(),
                    open_timeout=10, ping_interval=10, ping_timeout=10,
                ) as ws:
                    self._ws = ws
                    self._connected = True
                    backoff = 1
                    self._gap = False
                    for tk in self.tickers:
                        self.books[tk] = MarketBook(tk)
                    await self._subscribe_all()
                    print(f"[LP-WS] connected {WS_URL}, subscribed {len(self.tickers)} market(s)")
                    async for raw in ws:
                        self._handle(json.loads(raw))
                        if self._gap:
                            break  # clean reconnect -> fresh snapshots
            except Exception as e:
                self._connected = False
                print(f"[LP-WS] disconnected ({type(e).__name__}: {e}); reconnect in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    # ---- external control ----
    async def set_markets(self, tickers):
        """Replace the tracked market set (used when config changes). Re-subscribes
        the full set so the single seq stream stays consistent."""
        self.tickers = list(tickers)
        self.books = {t: self.books.get(t, MarketBook(t)) for t in self.tickers}
        if self._connected and self._ws is not None:
            for tk in self.tickers:
                self.books[tk] = MarketBook(tk)
            await self._subscribe_all()
