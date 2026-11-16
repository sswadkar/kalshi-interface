"""Arbitrage polling and optional execution with the existing cadence."""
import asyncio
import datetime
import time
import requests
from kalshi_workstation.core.exchange.kalshi import kalshi_get
from kalshi_workstation.core.prices import _dollars_to_cents
from .analysis import compute_opportunities
from .planner import execute_arb_internal
from .state import arb_state, auto_arb_cooldown, add_arb_message, save_arb_groups


async def poll_arb_markets():
    """Poll all markets in arbitrage groups every 500ms."""
    while True:
        tick_start = time.perf_counter()
        per_market_times = []
        try:
            expired = []  # (group, ticker) pairs whose markets 404'd
            for group in arb_state["groups"]:
                for market in group.get("markets", []):
                    fetch_start = time.perf_counter()
                    try:
                        # Fetch fresh price data for this market ticker (off the event loop)
                        data = await asyncio.to_thread(kalshi_get, f"/trade-api/v2/markets/{market['ticker']}")
                        if data and "market" in data:
                            m = data["market"]
                            market["yes_bid"] = _dollars_to_cents(m.get("yes_bid_dollars"))
                            market["yes_ask"] = _dollars_to_cents(m.get("yes_ask_dollars"))
                            market["no_bid"] = _dollars_to_cents(m.get("no_bid_dollars"))
                            market["no_ask"] = _dollars_to_cents(m.get("no_ask_dollars"))
                            market["last_price"] = _dollars_to_cents(m.get("last_price_dollars"))
                            market["status"] = m.get("status", "unknown")
                            market["title"] = m.get("title", market["ticker"])
                            market["yes_sub_title"] = m.get("yes_sub_title", "")
                    except requests.HTTPError as e:
                        if e.response is not None and e.response.status_code == 404:
                            expired.append((group, market["ticker"]))
                        else:
                            print(f"Error polling market {market['ticker']}: {e}")
                    except Exception as e:
                        # Log but continue polling other markets
                        print(f"Error polling market {market['ticker']}: {e}")
                    per_market_times.append(time.perf_counter() - fetch_start)

            # Auto-remove expired markets so we stop spamming 404s next tick
            if expired:
                for grp, ticker in expired:
                    grp["markets"] = [m for m in grp["markets"] if m["ticker"] != ticker]
                    add_arb_message("INFO", f"🗑️ Auto-removed expired market {ticker} from group {grp['name']} (404 from Kalshi)")
                save_arb_groups()

            # Recompute opportunities after all markets updated
            compute_opportunities()
            arb_state["last_pull"] = datetime.datetime.now(datetime.UTC).isoformat()

            # Auto-execute arbitrage if enabled
            if arb_state["settings"]["auto_arb"]:
                quantity = arb_state["settings"]["auto_arb_quantity"]
                now = datetime.datetime.now(datetime.UTC)

                for group in arb_state["groups"]:
                    opp = group.get("opportunity")
                    if not opp:
                        continue

                    group_id = group["id"]

                    # Check cooldown (5 seconds between auto-arbs on same group)
                    last_exec = auto_arb_cooldown.get(group_id)
                    if last_exec and (now - last_exec).total_seconds() < 5:
                        continue

                    # Execute the arbitrage
                    arb_type = "reverse " if opp.get("is_reverse_arb") else ""
                    add_arb_message("INFO", f"🤖 Auto-{arb_type}arb triggered for {group['name']}: {opp['profit']}¢ profit")
                    execute_arb_internal(
                        opp["market_a"], opp["market_a_side"],
                        opp["market_b"], opp["market_b_side"],
                        quantity, is_auto=True
                    )
                    auto_arb_cooldown[group_id] = now

        except Exception as e:
            add_arb_message("ERROR", f"Arbitrage polling failed: {e}")

        if per_market_times:
            tick_ms = (time.perf_counter() - tick_start) * 1000
            fetch_total_ms = 1000 * sum(per_market_times)
            avg_ms = fetch_total_ms / len(per_market_times)
            max_ms = 1000 * max(per_market_times)
            print(f"SCAN tick={tick_ms:.0f}ms fetch_total={fetch_total_ms:.0f}ms markets={len(per_market_times)} avg={avg_ms:.0f}ms max={max_ms:.0f}ms")

        await asyncio.sleep(0.5)
