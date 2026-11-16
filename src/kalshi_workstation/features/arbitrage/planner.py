"""Existing two-leg orchestration; atomicity and partial-fill recovery are not added here."""
import time
import uuid
from kalshi_workstation.config import global_settings
from kalshi_workstation.core.execution import service as execution
from kalshi_workstation.core.execution.orders import _build_order_v2, _v2_parse_fill
from .state import arb_state, add_arb_message


def execute_arb_internal(ticker_a, side_a, ticker_b, side_b, quantity, is_auto=False):
    """Internal function to execute arbitrage trade. Returns dict with results."""
    _exec_start = time.perf_counter()
    try:
        return _execute_arb_internal_impl(ticker_a, side_a, ticker_b, side_b, quantity, is_auto)
    finally:
        _dur_ms = (time.perf_counter() - _exec_start) * 1000
        print(f"ARB_EXEC total={_dur_ms:.0f}ms {ticker_a}+{ticker_b} auto={is_auto}")


def _execute_arb_internal_impl(ticker_a, side_a, ticker_b, side_b, quantity, is_auto=False):
    buffer = arb_state["settings"]["buffer"]
    results = {"order1": None, "order2": None, "errors": []}
    prefix = "AUTO " if is_auto else ""

    # Find current prices from arb_state
    price_a = None
    price_b = None
    for group in arb_state["groups"]:
        for market in group.get("markets", []):
            if market["ticker"] == ticker_a:
                price_a = market.get(f"{side_a}_ask", 0)
            if market["ticker"] == ticker_b:
                price_b = market.get(f"{side_b}_ask", 0)

    if price_a is None or price_b is None:
        results["errors"].append("Could not find current prices for markets")
        return results

    total_cost = price_a + price_b
    profit = 100 - total_cost

    # Check for dry run mode
    if global_settings["dry_run"]:
        add_arb_message(
            "DRY_RUN",
            f"🔸 [DRY RUN] {prefix}Arb: BUY {side_a.upper()} @ {price_a}¢ ({ticker_a}) + BUY {side_b.upper()} @ {price_b}¢ ({ticker_b}) x{quantity} = {profit}¢ profit",
        )
        return {
            "dry_run": True,
            "order1": {"ticker": ticker_a, "side": side_a, "price": price_a + buffer, "quantity": quantity},
            "order2": {"ticker": ticker_b, "side": side_b, "price": price_b + buffer, "quantity": quantity},
            "expected_profit": profit * quantity,
            "errors": []
        }

    # Place first order
    try:
        order1_data = _build_order_v2(
            ticker=ticker_a,
            action="buy",
            side=side_a,
            ref_price_cents=price_a,
            count=quantity,
            buffer_cents=buffer,
            client_order_id=str(uuid.uuid4()),
        )
        result1 = execution.submit_order(order1_data)
        results["order1"] = result1
        o1 = _v2_parse_fill(result1, quantity)

        if o1["status"] == "filled":
            add_arb_message("TRADE_RESULT",
                f"✅ {prefix}BUY {side_a.upper()} filled {o1['filled']:.0f}@${o1['price']} ({ticker_a})")
        elif o1["status"] == "partial":
            add_arb_message("TRADE_RESULT",
                f"⚠️ {prefix}BUY {side_a.upper()} partial {o1['filled']:.0f}/{quantity}@${o1['price']} ({ticker_a})")
            results["errors"].append(f"Order 1 partial: {o1['filled']:.0f}/{quantity}")
        else:
            reason = o1["error_msg"] or o1["status"]
            add_arb_message("ERROR", f"❌ {prefix}BUY {side_a.upper()} failed for {ticker_a}: {reason}")
            results["errors"].append(f"Order 1 failed: {reason}")
    except Exception as e:
        add_arb_message("ERROR", f"❌ {prefix}BUY {side_a.upper()} failed for {ticker_a}: {e}")
        results["errors"].append(f"Order 1 error: {e}")

    # Place second order
    try:
        order2_data = _build_order_v2(
            ticker=ticker_b,
            action="buy",
            side=side_b,
            ref_price_cents=price_b,
            count=quantity,
            buffer_cents=buffer,
            client_order_id=str(uuid.uuid4()),
        )
        result2 = execution.submit_order(order2_data)
        results["order2"] = result2
        o2 = _v2_parse_fill(result2, quantity)

        if o2["status"] == "filled":
            add_arb_message("TRADE_RESULT",
                f"✅ {prefix}BUY {side_b.upper()} filled {o2['filled']:.0f}@${o2['price']} ({ticker_b})")
        elif o2["status"] == "partial":
            add_arb_message("TRADE_RESULT",
                f"⚠️ {prefix}BUY {side_b.upper()} partial {o2['filled']:.0f}/{quantity}@${o2['price']} ({ticker_b})")
            results["errors"].append(f"Order 2 partial: {o2['filled']:.0f}/{quantity}")
        else:
            reason = o2["error_msg"] or o2["status"]
            add_arb_message("ERROR", f"❌ {prefix}BUY {side_b.upper()} failed for {ticker_b}: {reason}")
            results["errors"].append(f"Order 2 failed: {reason}")
    except Exception as e:
        add_arb_message("ERROR", f"❌ {prefix}BUY {side_b.upper()} failed for {ticker_b}: {e}")
        results["errors"].append(f"Order 2 error: {e}")

    # Log summary
    if not results["errors"]:
        add_arb_message("INFO", f"✅ {prefix}Arb executed: {profit}¢ profit x{quantity} contracts")
    else:
        add_arb_message("ERROR", f"⚠️ {prefix}Arb partially failed - check positions!")

    return results
