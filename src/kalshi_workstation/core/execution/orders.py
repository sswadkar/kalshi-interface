"""Order encoding for Kalshi's V2 unified book, and parsing of order-create responses.

These helpers are stateless: they are not a durable order state machine, and a
parsed response is not a guarantee about anything the exchange did afterwards.
"""
# client_order_id prefix of orders placed by automated strategies; they manage
# their own orders, so manual tools leave them alone.
AUTOMATED_ORDER_PREFIX = "LP-"


def _v2_parse_fill(result, requested_qty):
    """Parse a Kalshi V2 order-create response.

    V2 success shape is flat (no {"order": ...} wrapper):
      {order_id, client_order_id, fill_count, remaining_count,
       average_fill_price, average_fee_paid, ts_ms}
    Failure shape: {error: {code, message, details}}

    Returns {status, filled, price, fees, ts_ms, error_msg} where status ∈
    {"filled", "partial", "canceled", "error"}.
    """
    if not result:
        return {"status": "error", "filled": 0, "price": None, "fees": None,
                "ts_ms": None, "error_msg": "No response from API"}
    if "error" in result:
        err = result.get("error")
        err_msg = err.get("message") if isinstance(err, dict) else str(err)
        return {"status": "error", "filled": 0, "price": None, "fees": None,
                "ts_ms": None, "error_msg": err_msg}
    try:
        filled = float(result.get("fill_count") or 0)
    except (ValueError, TypeError):
        filled = 0.0
    if filled == 0:
        status = "canceled"
    elif filled + 1e-9 < requested_qty:
        status = "partial"
    else:
        status = "filled"
    return {
        "status": status,
        "filled": filled,
        "price": result.get("average_fill_price"),
        "fees": result.get("average_fee_paid"),
        "ts_ms": result.get("ts_ms"),
        "error_msg": None,
    }


def _build_order_v2(ticker, action, side, ref_price_cents, count, buffer_cents,
                    client_order_id, time_in_force="immediate_or_cancel"):
    """Build a Kalshi V2 unified-book order payload from (action, yes/no side, cents) inputs.

    Kalshi V2 only quotes the YES leg (side=bid/ask, price in dollar-string). Mapping:
      BUY YES  at yes_ask → side=bid, price=(yes_ask + buffer)
      SELL YES at yes_bid → side=ask, price=(yes_bid − buffer)
      BUY NO   at no_ask  → side=ask, price=(100 − no_ask − buffer)   [= sell YES at (1 − no_ask − buf)]
      SELL NO  at no_bid  → side=bid, price=(100 − no_bid + buffer)   [= buy YES at (1 − no_bid + buf)]

    ref_price_cents is the quote (in cents) on the caller's chosen side.
    buffer_cents is always positive; direction is applied per intent.
    """
    pad = buffer_cents if action == "buy" else -buffer_cents

    if side == "yes":
        v2_side = "bid" if action == "buy" else "ask"
        v2_price_cents = ref_price_cents + pad
    else:  # side == "no": mirror through YES
        v2_side = "ask" if action == "buy" else "bid"
        v2_price_cents = (100 - ref_price_cents) - pad

    v2_price_cents = max(1, min(99, int(round(v2_price_cents))))

    return {
        "ticker": ticker,
        "client_order_id": client_order_id,
        "side": v2_side,
        "count": f"{int(count)}.00",
        "price": f"{v2_price_cents / 100:.4f}",
        "time_in_force": time_in_force,
        "self_trade_prevention_type": "taker_at_cross",
    }
