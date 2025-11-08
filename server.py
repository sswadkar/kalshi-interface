# server.py
import asyncio, uuid, datetime
import json
import math
import os

import numpy as np
import pandas as pd
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
from pathlib import Path
from kalshi_positions import get_market_summary, compute_positions, kalshi_post, kalshi_get, get_user_info, \
    kalshi_delete, get_queue_positions
from dotenv import load_dotenv

load_dotenv()
app = FastAPI()
EVENT_TICKER = os.getenv("EVENT_TICKER")

state = {
    "markets": None,
    "positions": None,
    "messages": [],
    "last_pull": None,
    "balance": None,
}

def sanitize_json(df: pd.DataFrame):
    """Ensure all numeric values are JSON-safe."""
    clean = df.replace([np.inf, -np.inf, None], np.nan).fillna(0)
    # Convert to native Python types
    return json.loads(clean.to_json(orient="records"))

def add_message(type_, text, **details):
    state["messages"].insert(0, {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "type": type_,
        "text": text,
        "details": details
    })
    state["messages"] = state["messages"][:100]

async def poll_markets():
    """Poll markets + positions every 500ms."""
    while True:
        try:
            df_markets = get_market_summary(EVENT_TICKER)
            df_positions = compute_positions(EVENT_TICKER, df_markets)

            state["markets"] = sanitize_json(df_markets)
            state["positions"] = sanitize_json(df_positions)
            state["last_pull"] = datetime.datetime.utcnow().isoformat()
            state["user_info"] = get_user_info()

        except Exception as e:
            add_message("ERROR", f"Polling failed: {e}")

        await asyncio.sleep(0.5)

async def poll_resting_orders():
    """Poll resting orders every few seconds and cache them."""
    while True:
        try:
            resting = get_queue_positions(EVENT_TICKER)

            state["resting_orders"] = resting
        except Exception as e:
            add_message("ERROR", f"Resting order polling failed: {e}")

        await asyncio.sleep(3)


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(poll_markets())
    asyncio.create_task(poll_resting_orders())

@app.get("/api/status")
def api_status():
    return {
        "markets": state["markets"],
        "positions": state["positions"],
        "messages": state["messages"],
        "last_pull": state["last_pull"],
        "user_info": state["user_info"],
    }

# ---------- Order placing ----------
@app.post("/api/order/buy")
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

        ask_price = market_entry["yes_ask"] if side == "yes" else market_entry["no_ask"]
        ask_price = math.ceil(ask_price * 100)

        if ask_price is None:
            raise ValueError(f"No ask price available for {side.upper()} side of {ticker}")

        add_message(
            "INFO",
            f"⏱️ REQUESTING BUY {side.upper()} @ {ask_price:.2f} x{quantity} ({ticker})",
            ticker=ticker,
            side=side.upper(),
            quantity=quantity,
            price=ask_price
        )

        # Construct the order payload
        order_data = {
            "ticker": ticker,
            "action": "buy",
            "side": side,
            "type": "market",
            "count": quantity,
            "client_order_id": str(uuid.uuid4()),
        }
        # Add price key
        if side == "yes":
            order_data["yes_price"] = ask_price
        else:
            order_data["no_price"] = ask_price

        result = kalshi_post("/trade-api/v2/portfolio/orders", order_data)

        if result and "order" in result and result.get("order").get("status") == "executed":
            order = result.get("order", {})
            side_str = side.upper()
            fill_time = order.get("last_update_time", "unknown time")
            taker_fill = float(order.get("taker_fill_cost_dollars", "0") or 0)
            maker_fill = float(order.get("maker_fill_cost_dollars", "0") or 0)
            fill_price = order.get("yes_price_dollars") or order.get("no_price_dollars") or "?"
            fees = order.get("taker_fees_dollars") or order.get("maker_fees_dollars") or "0.0000"
            execution_type = "TAKER" if taker_fill > 0 else "MAKER"

            message = (
                f"✅ Executed {execution_type} BUY {side_str} @ ${fill_price} per contract "
                f"(fees: ${fees}) x{quantity} ({ticker}) — filled at {fill_time}"
            )
        elif result and result["order"].get("status") == "canceled":
            message = f"❌ Failed to place BUY {side.upper()} x{quantity} ({ticker}): Canceled"
        else:
            error_msg = result.get("error", "Unknown error") if result else "No response from API"
            message = f"❌ Failed to place BUY {side.upper()} x{quantity} ({ticker}): {error_msg}"

        add_message(
            "TRADE_RESULT",
            message,
            ticker=ticker,
        )

        return JSONResponse(result)

    except Exception as e:
        add_message("ERROR", f"❌ BUY {side.upper()} failed for {ticker}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)



@app.post("/api/order/sell")
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

        bid_price = market_entry["yes_bid"] if side == "yes" else market_entry["no_bid"]
        if bid_price is None:
            raise ValueError(f"No bid price available for {side.upper()} side of {ticker}")

        # Convert from dollars to integer cents for API
        bid_price = math.floor(bid_price * 100)

        add_message(
            "INFO",
            f"⏱️ REQUESTING SELL {side.upper()} @ {bid_price:.2f} x{quantity} ({ticker})",
            ticker=ticker,
            side=side.upper(),
            quantity=quantity,
            price=bid_price
        )

        # Construct the order payload
        order_data = {
            "ticker": ticker,
            "action": "sell",
            "side": side,
            "type": "market",
            "count": quantity,
            "client_order_id": str(uuid.uuid4()),
        }

        # Add price key
        if side == "yes":
            order_data["yes_price"] = bid_price
        else:
            order_data["no_price"] = bid_price

        result = kalshi_post("/trade-api/v2/portfolio/orders", order_data)
        print(result)

        if result and "order" in result and result.get("order").get("status") == "executed":
            fill_price = result["order"].get("taker_fill_cost_dollars") or result["order"].get(
                "maker_fill_cost_dollars")
            fees = result["order"].get("taker_fees_dollars") or result["order"].get("maker_fees_dollars")
            fill_time = result["order"].get("last_update_time")
            message = (
                f"✅ Executed SELL {side.upper()} @ {fill_price} (fees: ${fees}) "
                f"x{quantity} ({ticker}) — filled at {fill_time}"
            )
        elif result and result["order"].get("status") == "canceled":
            message = f"❌ Failed to place SELL {side.upper()} x{quantity} ({ticker}): Canceled"
        else:
            error_msg = result.get("error", "Unknown error") if result else "No response from API"
            message = f"❌ Failed to place SELL {side.upper()} x{quantity} ({ticker}): {error_msg}"

        add_message(
            "TRADE_RESULT",
            message,
            ticker=ticker,
        )

        return JSONResponse(result)

    except Exception as e:
        add_message("ERROR", f"❌ SELL {side.upper()} failed for {ticker}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/orders/resting")
def api_resting_orders():
    return {"resting_orders": state.get("resting_orders", [])}

@app.delete("/api/orders/cancel/{order_id}")
def api_cancel_order(order_id: str):
    try:
        result = kalshi_get(f"/trade-api/v2/portfolio/orders/{order_id}", params={"event_ticker": EVENT_TICKER})
        order = result.get("order", {})
        if order.get("status") != "resting":
            raise ValueError(f"Order {order_id} is not cancelable (status={order.get('status')})")

        cancel_result = kalshi_delete(f"/trade-api/v2/portfolio/orders/{order_id}", params={"event_ticker": EVENT_TICKER})

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


@app.get("/", response_class=HTMLResponse)
def root():
    html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(html_path.read_text())


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)