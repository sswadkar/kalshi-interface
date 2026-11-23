"""Two-leg pair execution. Blocking; the service runs it in a worker thread.

Leg 2 is only sent after leg 1 is known to have filled, and is sized to leg 1's
confirmed fill. Any quantity left without its opposite leg is reported as unhedged.
"""
import logging
import math
import time
import uuid

from kalshi_workstation.core.execution import service as execution
from kalshi_workstation.core.execution.orders import _build_order_v2, _v2_parse_fill

logger = logging.getLogger(__name__)


def execute_pair(*, ticker_a, side_a, price_a, ticker_b, side_b, price_b, quantity, buffer,
                 dry_run, log, is_auto=False):
    """Buy `side_a` on A, then the matching quantity of `side_b` on B.

    `log(type, text)` records activity. Returns {order1, order2, errors, notes, unhedged}.
    """
    started = time.perf_counter()
    try:
        return _execute(ticker_a, side_a, price_a, ticker_b, side_b, price_b, quantity, buffer,
                        dry_run, log, "AUTO " if is_auto else "")
    finally:
        logger.info("Pair %s+%s finished in %.0fms (auto=%s)", ticker_a, ticker_b,
                    (time.perf_counter() - started) * 1000, is_auto)


def _submit(ticker, side, price, quantity, buffer):
    payload = _build_order_v2(ticker=ticker, action="buy", side=side, ref_price_cents=price,
                              count=quantity, buffer_cents=buffer, client_order_id=str(uuid.uuid4()))
    return execution.submit_order(payload)


def _execute(ticker_a, side_a, price_a, ticker_b, side_b, price_b, quantity, buffer, dry_run,
             log, prefix):
    results = {"order1": None, "order2": None, "errors": [], "notes": [], "unhedged": None}
    leg_a, leg_b = f"BUY {side_a.upper()} {ticker_a}", f"BUY {side_b.upper()} {ticker_b}"
    if not price_a or not price_b:
        results["errors"].append("No current ask for one or both markets")
        return results
    profit = 100 - (price_a + price_b)

    if dry_run:
        log("DRY_RUN", f"🔸 [DRY RUN] {prefix}Arb: BUY {side_a.upper()} @ {price_a}¢ ({ticker_a}) + "
                       f"BUY {side_b.upper()} @ {price_b}¢ ({ticker_b}) x{quantity} = {profit}¢ profit")
        return {
            "dry_run": True,
            "order1": {"ticker": ticker_a, "side": side_a, "price": price_a + buffer, "quantity": quantity},
            "order2": {"ticker": ticker_b, "side": side_b, "price": price_b + buffer, "quantity": quantity},
            "expected_profit": profit * quantity,
            "errors": [], "notes": [], "unhedged": None,
        }

    # ---- leg 1 ----
    try:
        results["order1"] = _submit(ticker_a, side_a, price_a, quantity, buffer)
    except Exception as exc:
        message = f"Leg 1 outcome unknown ({exc}); leg 2 not sent. Check {ticker_a} positions."
        log("ERROR", f"❌ {prefix}{message}")
        results["errors"].append(message)
        return results
    first = _v2_parse_fill(results["order1"], quantity)
    if first["filled"] <= 0:
        reason = first["error_msg"] or "no fill"
        message = f"Leg 1 did not fill ({reason}); leg 2 not sent."
        log("ERROR", f"❌ {prefix}{leg_a}: {message}")
        results["errors"].append(message)
        return results
    filled_a = first["filled"]
    log("TRADE_RESULT", f"✅ {prefix}{leg_a} filled {filled_a:g}/{quantity} @ ${first['price']}")

    hedge = math.floor(filled_a + 1e-9)
    if hedge < 1:
        message = f"Leg 1 filled {filled_a:g}, below one whole contract; leg 2 not sent."
        results["errors"].append(message)
        results["unhedged"] = {"ticker": ticker_a, "side": side_a, "quantity": filled_a}
        log("ERROR", f"⚠️ {prefix}UNHEDGED {filled_a:g} on {ticker_a}: {message}")
        return results
    if hedge < quantity:
        results["notes"].append(f"Leg 1 filled {filled_a:g}/{quantity}; leg 2 sized to {hedge}.")

    # ---- leg 2, sized to leg 1's fill ----
    try:
        results["order2"] = _submit(ticker_b, side_b, price_b, hedge, buffer)
    except Exception as exc:
        message = (f"Leg 2 outcome unknown ({exc}). Up to {filled_a:g} {side_a.upper()} on "
                   f"{ticker_a} may be unhedged. Check positions.")
        results["errors"].append(message)
        results["unhedged"] = {"ticker": ticker_a, "side": side_a, "quantity": filled_a}
        log("ERROR", f"⚠️ {prefix}{message}")
        return results
    second = _v2_parse_fill(results["order2"], hedge)
    if second["filled"] > 0:
        log("TRADE_RESULT", f"✅ {prefix}{leg_b} filled {second['filled']:g}/{hedge} @ ${second['price']}")
    exposed = filled_a - second["filled"]
    if exposed > 1e-9:
        reason = second["error_msg"] or ("partial fill" if second["filled"] else "no fill")
        message = f"Leg 2 {reason}: {exposed:g} {side_a.upper()} on {ticker_a} is unhedged."
        results["errors"].append(message)
        results["unhedged"] = {"ticker": ticker_a, "side": side_a, "quantity": exposed}
        log("ERROR", f"⚠️ {prefix}UNHEDGED — {message}")
    else:
        log("INFO", f"✅ {prefix}Arb executed: {profit}¢ gross x{second['filled']:g} pairs")
    return results
