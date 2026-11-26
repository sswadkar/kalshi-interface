"""Manual trading and dashboard endpoints. Prices are integer cents."""
import asyncio
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from kalshi_workstation.core.exchange.kalshi import kalshi_get
from kalshi_workstation.core.execution import service as execution
from kalshi_workstation.core.execution.orders import AUTOMATED_ORDER_PREFIX, _build_order_v2, _v2_parse_fill
from .service import LiveTrading

router = APIRouter()
TAKER_BUFFER_CENTS = 10  # IOC limit is the reference quote ± this buffer


def get_live_trading(request: Request) -> LiveTrading:
    return request.app.state.live_trading


class DryRunUpdate(BaseModel):
    dry_run: bool


class OrderRequest(BaseModel):
    ticker: str = Field(min_length=1)
    side: Literal["yes", "no"] = "yes"
    quantity: int = Field(1, ge=1)

    @field_validator("side", mode="before")
    @classmethod
    def lowercase_side(cls, value):
        return value.lower() if isinstance(value, str) else value


@router.get("/api/status")
def api_status(live: LiveTrading = Depends(get_live_trading)):
    return live.status()


@router.put("/api/settings/dry_run")
def api_set_dry_run(body: DryRunUpdate, live: LiveTrading = Depends(get_live_trading)):
    """Toggle the shared manual + arbitrage dry-run mode."""
    live.mode.dry_run = body.dry_run
    return {"dry_run": live.mode.dry_run}


def _manual_order(live: LiveTrading, action: str, order: OrderRequest):
    """IOC order at the cached quote ± the taker buffer. Runs in a worker thread."""
    side, ticker, quantity = order.side, order.ticker, order.quantity
    label = f"{action.upper()} {side.upper()}"
    try:
        market = live.market(ticker)
        if market is None:
            raise ValueError(f"Market data for {ticker} not found in cache.")
        reference = market[f"{side}_ask" if action == "buy" else f"{side}_bid"]
        if not reference:
            raise ValueError(f"No {'ask' if action == 'buy' else 'bid'} price available for "
                             f"{side.upper()} side of {ticker}")
        limit = (min(reference + TAKER_BUFFER_CENTS, 99) if action == "buy"
                 else max(reference - TAKER_BUFFER_CENTS, 1))
        live.add_message("INFO", f"⏱️ REQUESTING {label} @ {limit}¢ x{quantity} ({ticker})",
                         ticker=ticker, side=side.upper(), quantity=quantity, price=limit)
        payload = _build_order_v2(
            ticker=ticker, action=action, side=side, ref_price_cents=reference, count=quantity,
            buffer_cents=TAKER_BUFFER_CENTS, client_order_id=str(uuid.uuid4()))

        if live.mode.dry_run:
            live.add_message("DRY_RUN", f"🔸 [DRY RUN] Would {label} @ {limit}¢ x{quantity} ({ticker})",
                             ticker=ticker, side=side.upper(), quantity=quantity, price=limit)
            return JSONResponse({"dry_run": True, "order_data": payload})

        result = execution.submit_order(payload)
        outcome = _v2_parse_fill(result, quantity)
        if outcome["status"] == "filled":
            text = (f"✅ {label} filled {outcome['filled']:.0f}@${outcome['price']} "
                    f"(fees: ${outcome['fees']}) ({ticker})")
        elif outcome["status"] == "partial":
            text = (f"⚠️ {label} partial {outcome['filled']:.0f}/{quantity}@${outcome['price']} "
                    f"(fees: ${outcome['fees']}) ({ticker})")
        elif outcome["status"] == "canceled":
            text = f"❌ {label} canceled with no fill x{quantity} ({ticker})"
        else:
            text = f"❌ {label} failed x{quantity} ({ticker}): {outcome['error_msg']}"
        live.add_message("TRADE_RESULT", text, ticker=ticker)
        return JSONResponse(result)
    except Exception as exc:
        live.add_message("ERROR", f"❌ {label} failed for {ticker}: {exc}")
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/order/buy")
def api_buy(order: OrderRequest, live: LiveTrading = Depends(get_live_trading)):
    """Buy at the cached ask plus the taker buffer (IOC)."""
    return _manual_order(live, "buy", order)


@router.post("/api/order/sell")
def api_sell(order: OrderRequest, live: LiveTrading = Depends(get_live_trading)):
    """Sell at the cached bid minus the taker buffer (IOC)."""
    return _manual_order(live, "sell", order)


@router.get("/api/orders/resting")
def api_resting_orders(live: LiveTrading = Depends(get_live_trading)):
    return {"resting_orders": live.resting_orders}


@router.delete("/api/orders/cancel/{order_id}")
def api_cancel_order(order_id: str, live: LiveTrading = Depends(get_live_trading)):
    try:
        order = kalshi_get(f"/trade-api/v2/portfolio/orders/{order_id}").get("order", {})
        if order.get("status") != "resting":
            raise ValueError(f"Order {order_id} is not cancelable (status={order.get('status')})")
        result = execution.cancel_order(order_id)
        live.add_message("ORDER_CANCEL", f"🧹 Cancelled order {order_id} ({order.get('ticker')})",
                         order_id=order_id, ticker=order.get("ticker"))
        return result
    except Exception as exc:
        live.add_message("ERROR", f"Cancel failed for {order_id}: {exc}")
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/orders/reset/{order_id}")
async def api_reset_order(order_id: str):
    """Cancel a resting order and re-place an identical post-only limit order, which puts
    it at the back of its price level. Orders placed by automated strategies are
    excluded: they manage their own."""
    try:
        data = await asyncio.to_thread(kalshi_get, f"/trade-api/v2/portfolio/orders/{order_id}")
        o = data.get("order", {})
        if o.get("status") != "resting":
            return JSONResponse({"error": f"order not resting (status={o.get('status')})"}, status_code=400)
        if (o.get("client_order_id") or "").startswith(AUTOMATED_ORDER_PREFIX):
            return JSONResponse({"error": "Order is managed by an automated strategy"}, status_code=400)

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
