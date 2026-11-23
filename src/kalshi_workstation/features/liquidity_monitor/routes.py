"""Read-only liquidity tools: incentive-program scanner and account-wide order monitor."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from kalshi_workstation.core.exchange.kalshi import kalshi_get
from kalshi_workstation.core.exchange.programs import ProgramCatalog
from kalshi_workstation.core.execution.orders import AUTOMATED_ORDER_PREFIX
from kalshi_workstation.core.liquidity import estimated_daily_reward
from kalshi_workstation.core.market_data import fetch_markets
from kalshi_workstation.core.prices import dollars_to_cents
from . import analysis as liquidity_analytics

router = APIRouter()

def get_programs(request: Request):
    return request.app.state.programs


@router.get("/api/liquidity/scan")
def api_liquidity_scan(series: str = "", tickers: str = "", my_size: int = 100, top: int = 30,
                       programs: ProgramCatalog = Depends(get_programs)):
    """Rank incentivized markets by estimated daily reward (upper bound)."""
    ser = [s.strip().upper() for s in series.split(",") if s.strip()] or None
    tks = [t.strip().upper() for t in tickers.split(",") if t.strip()] or None
    if not len(programs) and not programs.refresh():
        return JSONResponse({"error": f"Incentive programs unavailable: {programs.error}"}, status_code=503)
    try:
        rows = liquidity_analytics.scan(programs.snapshot().values(), tickers=tks, series=ser,
                                        my_size=my_size, top=top)
        return {"rows": rows}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/liquidity/orders")
def api_liquidity_orders(programs: ProgramCatalog = Depends(get_programs)):
    """Every resting order on the account with its top-of-book status, queue position,
    and estimated incentive reward. An order at the inside price is next in line to be
    filled; queue_ahead == 0 means it is first."""
    try:
        orders, cursor = [], None
        while True:
            params = {"status": "resting", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            data = kalshi_get("/trade-api/v2/portfolio/orders", params=params)
            batch = data.get("orders", [])
            orders += batch
            cursor = data.get("next_cursor")
            if not cursor or not batch:
                break

        # One batched request for every market with a resting order.
        try:
            mkt_cache = fetch_markets([o.get("ticker") for o in orders if o.get("ticker")])
        except Exception:
            mkt_cache = {}
        ob_cache = {}

        def market(t):
            return mkt_cache.get(t, {})

        def orderbook(t):
            if t not in ob_cache:
                try:
                    ob_cache[t] = kalshi_get(f"/trade-api/v2/markets/{t}/orderbook").get("orderbook_fp", {})
                except Exception:
                    ob_cache[t] = {}
            return ob_cache[t]

        rows = []
        for o in orders:
            t = o.get("ticker")
            m = market(t)
            side = (o.get("side") or "").lower()
            action = (o.get("action") or "").lower()
            price = dollars_to_cents(o.get("yes_price_dollars") if side == "yes" else o.get("no_price_dollars"))
            yb = dollars_to_cents(m.get("yes_bid_dollars")); ya = dollars_to_cents(m.get("yes_ask_dollars"))
            nb = dollars_to_cents(m.get("no_bid_dollars")); na = dollars_to_cents(m.get("no_ask_dollars"))
            # "at top" is judged by ACTION on the order's own outcome: a resting BUY is at the
            # top if it's at/above the best bid; a resting SELL if at/below the best ask.
            # (book_side is reported in unified-YES-book terms, so it's the wrong key here.)
            if action == "buy":
                best = yb if side == "yes" else nb
                at_top = price > 0 and price >= best
            else:
                best = ya if side == "yes" else na
                at_top = price > 0 and price <= best
            try:
                rem = float(o.get("remaining_count_fp") or 0)
            except (TypeError, ValueError):
                rem = 0.0

            # total size at our inside level: buy-yes/sell-no rest at the yes-bid inside,
            # buy-no/sell-yes rest at the yes-ask inside (unified YES book).
            def _sz(v):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None
            at_yes_bid = (action == "buy" and side == "yes") or (action == "sell" and side == "no")
            level_size = _sz(m.get("yes_bid_size_fp")) if at_yes_bid else _sz(m.get("yes_ask_size_fp"))
            # ONLY = at top AND we are essentially the whole level (no other size there)
            is_only = bool(at_top and level_size is not None and rem > 0
                           and (level_size - rem) <= max(1.0, 0.02 * rem))
            automated = (o.get("client_order_id") or "").startswith(AUTOMATED_ORDER_PREFIX)

            # Estimated reward ($/day), only for markets with an active incentive program
            reward_daily = None
            prog = programs.get(t)
            if prog:
                try:
                    reward_daily = estimated_daily_reward(action, side, o, rem, prog, orderbook(t))
                except Exception:
                    reward_daily = None

            rows.append({
                "order_id": o.get("order_id"),
                "ticker": t,
                "event": m.get("event_ticker") or (t.rsplit("-", 1)[0] if t else ""),
                "title": m.get("title", t),
                "sub_title": m.get("yes_sub_title", ""),
                "side": side, "action": action, "book_side": (o.get("book_side") or "").lower(),
                "price": price, "best": best, "remaining": rem, "level_size": level_size,
                "at_top": at_top, "only": is_only, "automated": automated,
                "reward_daily": reward_daily, "incentivized": prog is not None,
                "queue_ahead": None, "status": m.get("status", ""),
            })

        # queue position only for at-top orders (bounds the extra API calls); 0 = literally next
        for r in rows:
            if r["at_top"] and r["order_id"]:
                try:
                    q = kalshi_get(f"/trade-api/v2/portfolio/orders/{r['order_id']}/queue_position")
                    qp = q.get("queue_position_fp")
                    r["queue_ahead"] = float(qp) if qp is not None else None
                except Exception:
                    pass

        rows.sort(key=lambda r: (r["event"] or "", r["ticker"] or "", -r["price"]))
        top = sum(1 for r in rows if r["at_top"])
        reward_total = sum(r["reward_daily"] or 0 for r in rows)
        return {"orders": rows, "count": len(rows), "at_top": top,
                "reward_daily_total": round(reward_total, 2),
                "incentivized_count": sum(1 for r in rows if r["incentivized"])}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
