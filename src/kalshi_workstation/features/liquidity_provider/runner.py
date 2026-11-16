"""Existing LP scheduling and local order tracking.

The prototype's simulation, inventory, and cancellation semantics are retained;
shared durable execution/reconciliation is a later migration milestone.
"""
import asyncio
import datetime
import json
import time
import uuid
import requests
from kalshi_workstation.config import DATA_DIR
from kalshi_workstation.core.exchange.stream import LiquidityWS
from kalshi_workstation.core.exchange.programs import ProgramCatalog, load_active_liquidity_programs
from kalshi_workstation.core.exchange.kalshi import kalshi_get
from kalshi_workstation.core.liquidity import qualify_side, level_weight
from kalshi_workstation.core.execution import service as execution
from kalshi_workstation.core.execution.orders import LP_PREFIX
from .policy import desired_quotes, RISK_SIZE_MULT


CONFIG_FILE = DATA_DIR / "liquidity_config.json"


DEFAULT_SETTINGS = {
    "enabled": False,             # master switch for the whole wing
    "dry_run": True,              # simulate; place NO real orders
    "capital_budget": 500.0,      # max $ the wing may commit at once
    "risk_level": "conservative", # conservative | moderate | aggressive
    "requeue_mode": "both",       # timer | queue | both | off
    "requeue_interval_s": 20,     # timer trigger
    "requeue_queue_threshold": 200,  # re-queue when contracts-ahead < this
    "min_order_lifetime_s": 5,    # compliance guardrail: never cancel younger than this
    "max_daily_loss": 50.0,       # kill switch ($ realized loss)
    "max_inventory_dollars": 200.0,  # kill switch (abs open exposure)
    "poll_interval_s": 1.0,
}


DEFAULT_MARKET = {
    "enabled": False, "sides": "both", "size": 100,
    "yes_max_bid": 60, "no_max_bid": 60, "flatten_band": 300,
}


def _now():
    return time.time()


class LiquidityMaker:
    def __init__(self, programs=None):
        self.settings = dict(DEFAULT_SETTINGS)
        self.markets = []                 # list of per-market config dicts
        self.programs = programs if programs is not None else ProgramCatalog()
        self.orders = {}                  # client_order_id -> our resting order record
        self.inventory = {}               # ticker -> net YES contracts (mirror of positions)
        self.positions = {}               # ticker -> {"net": contracts, "basis": avg YES cost ¢}
        self.realized_pnl = 0.0
        self.day = datetime.date.today().isoformat()
        self.score_accrued = {}           # ticker -> estimated qualifying contract-seconds
        self.messages = []
        self.last_pull = None
        self._ws = None
        self._running = False
        self._last_requeue = {}           # (ticker, side) -> ts of last requeue
        self.load_config()

    # ---------- config persistence ----------
    def load_config(self):
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text())
                for k, v in data.get("settings", {}).items():
                    self.settings[k] = v
                self.markets = data.get("markets", [])
            except Exception as e:
                print(f"[LP] config load error: {e}")

    def save_config(self):
        try:
            CONFIG_FILE.write_text(json.dumps(
                {"settings": self.settings, "markets": self.markets}, indent=2))
        except Exception as e:
            print(f"[LP] config save error: {e}")

    def log(self, type_, text, **details):
        self.messages.insert(0, {
            "id": str(uuid.uuid4()), "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "type": type_, "text": text, "details": details,
        })
        self.messages = self.messages[:200]

    # ---------- program metadata ----------
    def refresh_programs(self):
        try:
            progs = load_active_liquidity_programs()
            self.programs.replace({p["ticker"]: p for p in progs})
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            # transient: machine lost connectivity (WiFi sleep / DNS / VPN). Keep
            # last-known-good programs and retry next cycle — nothing is wiped.
            self.log("WARN", "program refresh skipped: network unreachable (keeping last-known programs)")
        except Exception as e:
            self.log("ERROR", f"program refresh failed: {e}")

    def _enabled_tickers(self):
        return [m["ticker"] for m in self.markets if m.get("enabled")]

    # ---------- capital accounting ----------
    def committed_capital(self):
        total = 0.0
        for o in self.orders.values():
            if o["status"] == "resting":
                total += o["size"] * o["price_cents"] / 100.0
        return total

    def inventory_dollars(self):
        # value open positions at cost (contracts * avg price ~ use last known ref)
        total = 0.0
        for t, net in self.inventory.items():
            if net == 0:
                continue
            b = self._ws.books.get(t) if self._ws else None
            px = (b.best_yes_bid() if b and net > 0 else (b.best_yes_ask() if b else 50)) or 50
            total += abs(net) * px / 100.0
        return total

    # ---------- order helpers (V2 unified book) ----------
    def _v2_maker_payload(self, ticker, bid_side, price_cents, count, coid):
        """A resting maker order. bid_side 'yes' -> side=bid@price; 'no' bid -> side=ask@(100-price)."""
        if bid_side == "yes":
            side, price = "bid", price_cents / 100.0
        else:
            side, price = "ask", (100 - price_cents) / 100.0
        return {
            "ticker": ticker, "side": side, "count": f"{int(count)}.00",
            "price": f"{price:.4f}", "time_in_force": "good_till_canceled",
            "post_only": True, "self_trade_prevention_type": "taker_at_cross",
            "client_order_id": coid,
        }

    async def _place(self, ticker, bid_side, price_cents, size):
        """Place (or simulate) a resting LP order. Returns the client_order_id or None."""
        # capital gate
        cost = size * price_cents / 100.0
        if self.committed_capital() + cost > self.settings["capital_budget"]:
            return None
        coid = LP_PREFIX + str(uuid.uuid4())
        rec = {"coid": coid, "order_id": None, "ticker": ticker, "bid_side": bid_side,
               "price_cents": price_cents, "size": size, "remaining": size,
               "status": "resting", "created": _now(), "sim": False}

        if self.settings["dry_run"]:
            rec["order_id"] = "DRY-" + coid[-8:]
            rec["sim"] = True
            self.orders[coid] = rec
            self.log("DRY_RUN", f"🔸 would REST {bid_side.upper()} bid {size}@{price_cents}¢ ({ticker})")
            return coid

        try:
            payload = self._v2_maker_payload(ticker, bid_side, price_cents, size, coid)
            res = await asyncio.to_thread(execution.submit_order, payload)
            if res and res.get("order_id"):
                rec["order_id"] = res["order_id"]
                self.orders[coid] = rec
                self.log("ORDER", f"⬆️ REST {bid_side.upper()} bid {size}@{price_cents}¢ ({ticker})")
                return coid
            self.log("ERROR", f"place failed {ticker} {bid_side}@{price_cents}: {res}")
        except Exception as e:
            self.log("ERROR", f"place error {ticker} {bid_side}@{price_cents}: {e}")
        return None

    async def _cancel(self, coid):
        """Cancel (or simulate) one of OUR orders. Only ever touches LP-tagged orders."""
        rec = self.orders.get(coid)
        if not rec or rec["status"] != "resting":
            return
        rec["status"] = "canceling"
        if rec["sim"] or self.settings["dry_run"]:
            rec["status"] = "canceled"
            self.orders.pop(coid, None)
            return
        try:
            await asyncio.to_thread(execution.cancel_order, rec["order_id"])
            rec["status"] = "canceled"
            self.orders.pop(coid, None)
        except Exception as e:
            rec["status"] = "resting"  # cancel failed; keep tracking
            self.log("ERROR", f"cancel error {rec['ticker']}: {e}")

    def _my_order(self, ticker, bid_side):
        for o in self.orders.values():
            if o["ticker"] == ticker and o["bid_side"] == bid_side and o["status"] == "resting":
                return o
        return None

    # ---------- desired quotes (gate-aware) ----------
    def desired_quotes(self, market):
        ticker = market["ticker"]
        book = self._ws.books.get(ticker) if self._ws else None
        return desired_quotes(market, self.programs.get(ticker), book, self.settings, self.inventory)

    # ---------- maintain quotes for one market ----------
    async def maintain(self, market):
        ticker = market["ticker"]
        # flatten first if we're beyond the band (skew stops adding; this actively reduces)
        if abs(self.inventory.get(ticker, 0)) > market.get("flatten_band", 300):
            await self._flatten(ticker)
        want = self.desired_quotes(market)
        min_life = self.settings["min_order_lifetime_s"]
        for side in ("yes", "no"):
            target = want[side]
            size = max(1, int(market.get("size", 100) * RISK_SIZE_MULT.get(self.settings["risk_level"], 1.0)))
            resting = [o for o in self.orders.values()
                       if o["ticker"] == ticker and o["bid_side"] == side and o["status"] == "resting"]

            if target is None:                       # pull the side entirely
                for o in resting:
                    await self._cancel(o["coid"])
                continue

            at_target = [o for o in resting if o["price_cents"] == target]
            stale = [o for o in resting if o["price_cents"] != target]

            if not at_target:
                # make-before-break: establish the quote at target, THEN retire stale ones
                await self._place(ticker, side, target, size)
                self._last_requeue[(ticker, side)] = _now()
                for o in stale:
                    if _now() - o["created"] >= min_life:
                        await self._cancel(o["coid"])
                continue

            keep = at_target[0]
            # invariant: at most ONE resting order per side -> cancel every other (dupes + stale)
            for o in at_target[1:] + stale:
                if _now() - o["created"] >= min_life:
                    await self._cancel(o["coid"])
            # re-queue the kept order to the back (make-before-break)
            if self._should_requeue(ticker, side, keep):
                await self._place(ticker, side, target, size)
                await self._cancel(keep["coid"])
                self._last_requeue[(ticker, side)] = _now()

    def _should_requeue(self, ticker, side, cur):
        mode = self.settings["requeue_mode"]
        if mode == "off":
            return False
        if _now() - cur["created"] < self.settings["min_order_lifetime_s"]:
            return False
        last = self._last_requeue.get((ticker, side), 0)
        timer_ok = (_now() - last) >= self.settings["requeue_interval_s"]
        # queue trigger: contracts ahead of us at our price level (aggregate depth we joined)
        book = self._ws.books.get(ticker)
        ahead = book.size_at(side, cur["price_cents"]) - cur["size"] if book else 0
        queue_ok = ahead < self.settings["requeue_queue_threshold"]
        if mode == "timer":
            return timer_ok
        if mode == "queue":
            return queue_ok
        return timer_ok or queue_ok  # both

    # ---------- fills / inventory ----------
    def _apply_fill(self, ticker, yes_delta, yes_price_cents):
        """Update net position + avg cost basis (YES terms) and realize P&L on reductions."""
        pos = self.positions.setdefault(ticker, {"net": 0.0, "basis": 0.0})
        net, basis = pos["net"], pos["basis"]
        new = net + yes_delta
        if net == 0 or (net > 0) == (yes_delta > 0):        # opening / adding same direction
            denom = abs(new) if new != 0 else 1
            pos["basis"] = (abs(net) * basis + abs(yes_delta) * yes_price_cents) / denom
        else:                                               # reducing / closing / flipping
            closed = min(abs(yes_delta), abs(net))
            if net > 0:                                     # closing long YES by selling at price
                self.realized_pnl += (yes_price_cents - basis) * closed / 100.0
            else:                                           # closing short YES by buying at price
                self.realized_pnl += (basis - yes_price_cents) * closed / 100.0
            if abs(yes_delta) > abs(net):                   # flipped through zero
                pos["basis"] = yes_price_cents
        pos["net"] = new
        self.inventory[ticker] = new

    def on_fill(self, msg):
        coid = msg.get("client_order_id", "")
        if not coid.startswith(LP_PREFIX):
            return  # not ours
        cnt = float(msg.get("count_fp") or 0)
        if cnt <= 0:
            return
        t = msg["market_ticker"]
        rec = self.orders.get(coid)
        bid_side = rec["bid_side"] if rec else ("yes" if msg.get("side") == "yes" else "no")
        if rec:
            cnt = min(cnt, rec["remaining"])                # a fill can't exceed our order
        # YES-terms price of this fill: a YES bid fills at its price; a NO bid at q = YES at 100-q
        if rec:
            yes_price = rec["price_cents"] if bid_side == "yes" else 100 - rec["price_cents"]
        else:
            yes_price = int(round(float(msg.get("yes_price_dollars") or 0) * 100))
        self._apply_fill(t, cnt if bid_side == "yes" else -cnt, yes_price)
        fee = float(msg.get("fee_cost") or 0)
        self.realized_pnl -= fee
        self.log("FILL", f"✅ filled {bid_side.upper()} {cnt:.0f}@{yes_price}¢ ({t}) fee=${fee:.4f} "
                         f"net={self.inventory.get(t, 0):.0f} rpnl=${self.realized_pnl:.2f}")
        if rec:
            rec["remaining"] = max(0, rec["remaining"] - cnt)
            if rec["remaining"] <= 0:
                rec["status"] = "filled"
                self.orders.pop(coid, None)

    def _unrealized(self):
        """Mark open positions at the price we could realize them at (depth-naive best)."""
        tot = 0.0
        for t, pos in self.positions.items():
            net, basis = pos["net"], pos["basis"]
            if net == 0 or not self._ws:
                continue
            b = self._ws.books.get(t)
            if not b:
                continue
            cur = b.best_yes_bid() if net > 0 else b.best_yes_ask()
            if cur is None:
                continue
            tot += (cur - basis) * net / 100.0
        return tot

    async def _flatten(self, ticker):
        """Reduce a position beyond its band to flat via a marketable IOC order."""
        net = self.inventory.get(ticker, 0)
        book = self._ws.books.get(ticker) if self._ws else None
        if net == 0 or not book:
            return
        if net > 0:
            px = book.best_yes_bid(); side = "ask"           # sell YES
        else:
            px = book.best_yes_ask(); side = "bid"           # buy YES
        if px is None:
            return
        self.log("FLATTEN", f"⚖️ flatten {net:+.0f} ({ticker}) via {side}@{px}¢")
        if self.settings["dry_run"]:
            self._apply_fill(ticker, -net, px)               # simulate close at marketable price
            return
        coid = LP_PREFIX + str(uuid.uuid4())
        payload = {"ticker": ticker, "side": side, "count": f"{int(abs(net))}.00",
                   "price": f"{px/100:.4f}", "time_in_force": "immediate_or_cancel",
                   "self_trade_prevention_type": "taker_at_cross", "client_order_id": coid}
        try:
            await asyncio.to_thread(execution.submit_order, payload)
        except Exception as e:
            self.log("ERROR", f"flatten failed {ticker}: {e}")

    def on_order(self, msg):
        coid = msg.get("client_order_id", "")
        if not coid.startswith(LP_PREFIX):
            return
        rec = self.orders.get(coid)
        if rec and msg.get("status") in ("canceled", "executed"):
            rec["status"] = msg["status"]
            self.orders.pop(coid, None)

    def _simulate_fills(self, ticker, trade_msg):
        """Dry-run only: if a taker trade would have hit our resting quote, sim a fill."""
        if not self.settings["dry_run"]:
            return
        price = int(round(float(trade_msg.get("yes_price_dollars") or 0) * 100))
        taker = trade_msg.get("taker_side")   # 'yes' or 'no'
        cnt = float(trade_msg.get("count_fp") or 0)
        for o in list(self.orders.values()):
            if o["ticker"] != ticker or o["status"] != "resting":
                continue
            # our YES bid at p is hit when a NO-taker trades at yes price <= p
            # our NO bid at q (=YES ask 100-q) is hit when a YES-taker trades at yes price >= 100-q
            hit = ((o["bid_side"] == "yes" and taker == "no" and price <= o["price_cents"]) or
                   (o["bid_side"] == "no" and taker == "yes" and price >= 100 - o["price_cents"]))
            if hit:
                fill = min(cnt, o["remaining"])
                self.on_fill({"client_order_id": o["coid"], "market_ticker": ticker,
                              "count_fp": fill, "side": o["bid_side"], "fee_cost": 0})

    # ---------- measurement ----------
    def _measure(self, dt):
        """Estimate our qualifying score (contract-seconds) per market under the 200-anchor
        model. Sides are INDEPENDENT — a side contributes iff its own Target-Size gate is met."""
        for m in self.markets:
            if not m.get("enabled"):
                continue
            t = m["ticker"]
            prog = self.programs.get(t)
            book = self._ws.books.get(t) if self._ws else None
            if not prog or not book or not book.ready:
                continue
            ts, df = prog["target_size"], prog["discount_factor"]
            qy = qualify_side([[f"{c/100:.4f}", s] for c, s in book.yes.items()], ts, df)
            qn = qualify_side([[f"{c/100:.4f}", s] for c, s in book.no.items()], ts, df)
            mine = 0.0
            for o in self.orders.values():
                if o["ticker"] != t or o["status"] != "resting":
                    continue
                q = qy if o["bid_side"] == "yes" else qn
                if not q["reached"]:
                    continue  # this side's gate fails -> no reward on this side
                cutoff = q.get("cutoff_cents")
                if cutoff is not None and o["price_cents"] < cutoff:
                    continue  # deeper than the top Target-Size band -> counts 0
                mine += level_weight(o["price_cents"], q["anchor_cents"], q["df"]) * o["size"]
            self.score_accrued[t] = self.score_accrued.get(t, 0.0) + mine * dt

    # ---------- kill switches ----------
    def _check_kill(self):
        today = datetime.date.today().isoformat()
        if today != self.day:                       # reset daily
            self.day, self.realized_pnl = today, 0.0
        drawdown = -(self.realized_pnl + self._unrealized())   # realized fees/P&L + mark-to-market
        if drawdown >= self.settings["max_daily_loss"]:
            self.log("KILL", f"🛑 drawdown ${drawdown:.2f} (incl. unrealized) ≥ limit; disabling wing")
            return True
        if self.inventory_dollars() > self.settings["max_inventory_dollars"]:
            self.log("KILL", f"🛑 inventory ${self.inventory_dollars():.2f} > limit; disabling wing")
            return True
        return False

    # ---------- lifecycle ----------
    async def reconcile_startup(self):
        """Adopt/clear any pre-existing LP-owned resting orders so a crash never orphans capital."""
        if self.settings["dry_run"]:
            return
        try:
            data = await asyncio.to_thread(
                kalshi_get, "/trade-api/v2/portfolio/orders", {"status": "resting"})
            for o in data.get("orders", []):
                coid = o.get("client_order_id", "") or ""
                if coid.startswith(LP_PREFIX):
                    await asyncio.to_thread(
                        execution.cancel_order, o["order_id"])
                    self.log("RECONCILE", f"🧹 cleared orphan LP order ({o.get('ticker')})")
        except Exception as e:
            self.log("ERROR", f"startup reconcile failed: {e}")

    async def cancel_all(self, reason="manual"):
        for coid in list(self.orders.keys()):
            await self._cancel(coid)
        self.log("PANIC", f"🧹 cancelled all LP orders ({reason})")

    async def panic(self):
        self.settings["enabled"] = False
        self.save_config()
        await self.cancel_all("panic")

    async def run(self):
        self._running = True
        await asyncio.to_thread(self.refresh_programs)   # blocking REST off the event loop
        tickers = self._enabled_tickers()
        self._ws = LiquidityWS(
            tickers, on_trade=self._simulate_fills, on_fill=self.on_fill, on_order=self.on_order)
        ws_task = asyncio.create_task(self._ws.run())
        await self.reconcile_startup()
        self.log("INFO", f"LP wing started (enabled={self.settings['enabled']}, "
                         f"dry_run={self.settings['dry_run']}, markets={len(tickers)})")
        last = _now()
        prog_refresh = _now()
        try:
            while self._running:
                await asyncio.sleep(self.settings["poll_interval_s"])
                dt = _now() - last
                last = _now()
                if _now() - prog_refresh > 300:      # refresh program metadata every 5 min
                    await asyncio.to_thread(self.refresh_programs); prog_refresh = _now()

                # keep WS market set in sync with config
                want = set(self._enabled_tickers())
                if want != set(self._ws.tickers):
                    await self._ws.set_markets(list(want))

                self._measure(dt)
                self.last_pull = datetime.datetime.now(datetime.UTC).isoformat()

                if not self.settings["enabled"]:
                    if self.orders:
                        await self.cancel_all("wing disabled")
                    continue
                if self._check_kill():
                    self.settings["enabled"] = False
                    self.save_config()
                    await self.cancel_all("kill switch")
                    continue
                for m in self.markets:
                    if m.get("enabled"):
                        try:
                            await self.maintain(m)
                        except Exception as e:
                            self.log("ERROR", f"maintain {m['ticker']} failed: {e}")
        finally:
            ws_task.cancel()
            await asyncio.gather(ws_task, return_exceptions=True)

    # ---------- status for the API ----------
    def status(self):
        return {
            "settings": self.settings,
            "markets": self.markets,
            "orders": list(self.orders.values()),
            "inventory": self.inventory,
            "realized_pnl": round(self.realized_pnl, 4),
            "unrealized_pnl": round(self._unrealized(), 4) if self._ws else 0,
            "committed_capital": round(self.committed_capital(), 2),
            "inventory_dollars": round(self.inventory_dollars(), 2) if self._ws else 0,
            "score_accrued": {k: round(v, 1) for k, v in self.score_accrued.items()},
            "programs": self.programs.snapshot(),
            "messages": self.messages,
            "last_pull": self.last_pull,
        }


if __name__ == "__main__":
    lm = LiquidityMaker()
    print(f"LP maker standalone — enabled={lm.settings['enabled']} dry_run={lm.settings['dry_run']} "
          f"markets={len(lm.markets)}")
    try:
        asyncio.run(lm.run())
    except KeyboardInterrupt:
        print("\nstopped.")
