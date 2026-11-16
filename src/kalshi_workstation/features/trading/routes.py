"""Manual trading, account history, and existing dashboard endpoints."""
import asyncio
import math
import uuid
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from kalshi_workstation.config import global_settings
from kalshi_workstation.core.exchange.kalshi import kalshi_get
from kalshi_workstation.core.execution import service as execution
from kalshi_workstation.core.execution.orders import LP_PREFIX, _build_order_v2, _v2_parse_fill
from kalshi_workstation.core.portfolio import (get_portfolio, lp_rewards, portfolio_history,
    PORTFOLIO_BACKFILL, record_portfolio_point)
from .state import state, add_message

router = APIRouter()


@router.get("/api/status")
def api_status():
    return {
        "markets": state["markets"],
        "positions": state["positions"],
        "messages": state["messages"],
        "last_pull": state["last_pull"],
        "user_info": state["user_info"],
        "dry_run": global_settings["dry_run"],
    }


@router.put("/api/settings/dry_run")
async def api_set_dry_run(request: Request):
    """Toggle global dry run mode."""
    data = await request.json()
    global_settings["dry_run"] = bool(data.get("dry_run", False))
    return {"dry_run": global_settings["dry_run"]}


@router.post("/api/order/buy")
async def api_buy(request: Request):
    """Place a BUY order using the cached ask price."""
    data = await request.json()
    ticker = data.get("ticker")
    side = data.get("side", "yes").lower()
    quantity = int(data.get("quantity", 1))

    try:
        # Ensure we have recent market data
        markets = state.get("markets")
        if not markets:
            raise ValueError("No cached market data available yet.")

        # Find this market entry
        market_entry = next((m for m in markets if m["market_ticker"] == ticker), None)
        if not market_entry:
            raise ValueError(f"Market data for {ticker} not found in cache.")

        ref_raw = market_entry["yes_ask"] if side == "yes" else market_entry["no_ask"]
        if ref_raw is None:
            raise ValueError(f"No ask price available for {side.upper()} side of {ticker}")

        ref_price_cents = math.ceil(ref_raw * 100)
        adj_buy = 10  # taker padding in cents
        submitted_ref = min(ref_price_cents + adj_buy, 99)

        add_message(
            "INFO",
            f"⏱️ REQUESTING BUY {side.upper()} @ {submitted_ref}¢ x{quantity} ({ticker})",
            ticker=ticker,
            side=side.upper(),
            quantity=quantity,
            price=submitted_ref
        )

        order_data = _build_order_v2(
            ticker=ticker,
            action="buy",
            side=side,
            ref_price_cents=ref_price_cents,
            count=quantity,
            buffer_cents=adj_buy,
            client_order_id=str(uuid.uuid4()),
        )

        if global_settings["dry_run"]:
            add_message(
                "DRY_RUN",
                f"🔸 [DRY RUN] Would BUY {side.upper()} @ {submitted_ref}¢ x{quantity} ({ticker})",
                ticker=ticker,
                side=side.upper(),
                quantity=quantity,
                price=submitted_ref
            )
            return JSONResponse({"dry_run": True, "order_data": order_data})

        result = execution.submit_order(order_data)
        outcome = _v2_parse_fill(result, quantity)

        if outcome["status"] == "filled":
            message = (f"✅ BUY {side.upper()} filled {outcome['filled']:.0f}@${outcome['price']} "
                       f"(fees: ${outcome['fees']}) ({ticker})")
        elif outcome["status"] == "partial":
            message = (f"⚠️ BUY {side.upper()} partial {outcome['filled']:.0f}/{quantity}@${outcome['price']} "
                       f"(fees: ${outcome['fees']}) ({ticker})")
        elif outcome["status"] == "canceled":
            message = f"❌ BUY {side.upper()} canceled with no fill x{quantity} ({ticker})"
        else:
            message = f"❌ BUY {side.upper()} failed x{quantity} ({ticker}): {outcome['error_msg']}"

        add_message(
            "TRADE_RESULT",
            message,
            ticker=ticker,
        )

        return JSONResponse(result)

    except Exception as e:
        add_message("ERROR", f"❌ BUY {side.upper()} failed for {ticker}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/order/sell")
async def api_sell(request: Request):
    """Place a SELL order using the cached bid price."""
    data = await request.json()
    ticker = data.get("ticker")
    side = data.get("side", "yes").lower()
    quantity = int(data.get("quantity", 1))

    try:
        # Ensure we have recent market data
        markets = state.get("markets")
        if not markets:
            raise ValueError("No cached market data available yet.")

        # Find this market entry
        market_entry = next((m for m in markets if m["market_ticker"] == ticker), None)
        if not market_entry:
            raise ValueError(f"Market data for {ticker} not found in cache.")

        ref_raw = market_entry["yes_bid"] if side == "yes" else market_entry["no_bid"]
        if ref_raw is None:
            raise ValueError(f"No bid price available for {side.upper()} side of {ticker}")

        ref_price_cents = math.floor(ref_raw * 100)
        adj_sell = 10  # taker padding in cents (subtracted for sells)
        submitted_ref = max(ref_price_cents - adj_sell, 1)

        add_message(
            "INFO",
            f"⏱️ REQUESTING SELL {side.upper()} @ {submitted_ref}¢ x{quantity} ({ticker})",
            ticker=ticker,
            side=side.upper(),
            quantity=quantity,
            price=submitted_ref
        )

        order_data = _build_order_v2(
            ticker=ticker,
            action="sell",
            side=side,
            ref_price_cents=ref_price_cents,
            count=quantity,
            buffer_cents=adj_sell,
            client_order_id=str(uuid.uuid4()),
        )

        if global_settings["dry_run"]:
            add_message(
                "DRY_RUN",
                f"🔸 [DRY RUN] Would SELL {side.upper()} @ {submitted_ref}¢ x{quantity} ({ticker})",
                ticker=ticker,
                side=side.upper(),
                quantity=quantity,
                price=submitted_ref
            )
            return JSONResponse({"dry_run": True, "order_data": order_data})

        result = execution.submit_order(order_data)
        outcome = _v2_parse_fill(result, quantity)

        if outcome["status"] == "filled":
            message = (f"✅ SELL {side.upper()} filled {outcome['filled']:.0f}@${outcome['price']} "
                       f"(fees: ${outcome['fees']}) ({ticker})")
        elif outcome["status"] == "partial":
            message = (f"⚠️ SELL {side.upper()} partial {outcome['filled']:.0f}/{quantity}@${outcome['price']} "
                       f"(fees: ${outcome['fees']}) ({ticker})")
        elif outcome["status"] == "canceled":
            message = f"❌ SELL {side.upper()} canceled with no fill x{quantity} ({ticker})"
        else:
            message = f"❌ SELL {side.upper()} failed x{quantity} ({ticker}): {outcome['error_msg']}"

        add_message(
            "TRADE_RESULT",
            message,
            ticker=ticker,
        )

        return JSONResponse(result)

    except Exception as e:
        add_message("ERROR", f"❌ SELL {side.upper()} failed for {ticker}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/orders/resting")
def api_resting_orders():
    return {"resting_orders": state.get("resting_orders", [])}


@router.delete("/api/orders/cancel/{order_id}")
def api_cancel_order(order_id: str):
    try:
        result = kalshi_get(f"/trade-api/v2/portfolio/orders/{order_id}")
        order = result.get("order", {})
        if order.get("status") != "resting":
            raise ValueError(f"Order {order_id} is not cancelable (status={order.get('status')})")

        cancel_result = execution.cancel_order(order_id)

        add_message(
            "ORDER_CANCEL",
            f"🧹 Cancelled order {order_id} ({order.get('ticker')})",
            order_id=order_id,
            ticker=order.get("ticker")
        )
        return cancel_result
    except Exception as e:
        add_message("ERROR", f"Cancel failed for {order_id}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/orders/reset/{order_id}")
async def api_reset_order(order_id: str):
    """Cancel a resting order and re-place an identical maker-only (post_only) limit order —
    pushing it to the back of the queue. One-click re-queue for manual/arb orders.
    LP-managed orders are excluded (the LP engine re-queues its own)."""
    try:
        data = await asyncio.to_thread(kalshi_get, f"/trade-api/v2/portfolio/orders/{order_id}")
        o = data.get("order", {})
        if o.get("status") != "resting":
            return JSONResponse({"error": f"order not resting (status={o.get('status')})"}, status_code=400)
        if (o.get("client_order_id") or "").startswith(LP_PREFIX):
            return JSONResponse({"error": "LP-managed order; the liquidity engine re-queues its own"}, status_code=400)

        book_side = (o.get("book_side") or "").lower()
        price = o.get("yes_price_dollars")           # canonical YES-perspective price for V2
        try:
            count = float(o.get("remaining_count_fp") or 0)
        except (TypeError, ValueError):
            count = 0.0
        if book_side not in ("bid", "ask") or not price or count <= 0:
            return JSONResponse({"error": "order is missing fields needed to replace it"}, status_code=400)

        # cancel first, then re-place an identical maker-only limit order (post_only)
        await asyncio.to_thread(execution.cancel_order, order_id)
        payload = {
            "ticker": o.get("ticker"),
            "side": book_side,
            "price": f"{float(price):.4f}",
            "count": f"{count:.2f}",
            "time_in_force": "good_till_canceled",
            "post_only": True,
            "self_trade_prevention_type": "taker_at_cross",
            "client_order_id": str(uuid.uuid4()),
        }
        res = await asyncio.to_thread(execution.submit_order, payload)
        new_id = res.get("order_id") if isinstance(res, dict) else None
        if new_id:
            return {"success": True, "order_id": new_id, "ticker": o.get("ticker")}
        # post_only would have crossed (market moved through the price) -> left flat, by design
        return JSONResponse({"success": False,
                             "error": "re-place rejected (price would cross the book) — order left cancelled",
                             "detail": res})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/portfolio/history")
def api_portfolio_history():
    """Daily total portfolio value (incl. rewards as they're paid) + lifetime LP reward estimate."""
    record_portfolio_point()   # update today's live point
    # merge immutable backfill with live-logged points (live overrides for overlapping dates)
    merged = {d: {"value": float(val), "lp": None} for d, val in PORTFOLIO_BACKFILL.items()}
    merged.update(portfolio_history)
    days = []
    for d in sorted(merged.keys()):
        e = merged[d]
        _, m, dd = d.split("-")
        days.append({"date": f"{int(m)}/{int(dd)}", "value": e.get("value"), "lp": e.get("lp", 0)})
    pf = get_portfolio()
    pending = round(lp_rewards.get("pending_lp", 0.0), 2)
    current_total = round((pf.get("total_value") or 0) + pending, 2)
    return {"days": days, "current": pf, "current_total": current_total,
            "pending_lp": pending, "lp_lifetime": round(lp_rewards["lifetime_est"], 2)}
