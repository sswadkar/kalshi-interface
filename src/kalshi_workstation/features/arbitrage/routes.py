"""Arbitrage configuration and execution endpoints."""
import uuid
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from kalshi_workstation.config import global_settings
from kalshi_workstation.core.exchange.kalshi import kalshi_get, get_user_info
from kalshi_workstation.core.prices import _dollars_to_cents
from .analysis import compute_opportunities
from .planner import execute_arb_internal
from .state import arb_state, add_arb_message, save_arb_groups

router = APIRouter()


@router.get("/api/arb/status")
def api_arb_status():
    """Return arbitrage groups, opportunities, user info, and settings."""
    return {
        "groups": arb_state["groups"],
        "settings": arb_state["settings"],
        "messages": arb_state["messages"],
        "last_pull": arb_state["last_pull"],
        "user_info": get_user_info(),
        "dry_run": global_settings["dry_run"],
    }


@router.post("/api/arb/groups")
async def api_create_group(request: Request):
    """Create a new arbitrage group."""
    data = await request.json()
    name = data.get("name", "").strip()

    if not name:
        return JSONResponse({"error": "Group name is required"}, status_code=400)

    group_id = str(uuid.uuid4())[:8]
    new_group = {
        "id": group_id,
        "name": name,
        "markets": [],
        "opportunity": None
    }

    arb_state["groups"].append(new_group)
    save_arb_groups()
    add_arb_message("INFO", f"Created group: {name}")

    return {"group": new_group}


@router.delete("/api/arb/groups/{group_id}")
def api_delete_group(group_id: str):
    """Delete an arbitrage group."""
    group = next((g for g in arb_state["groups"] if g["id"] == group_id), None)
    if not group:
        return JSONResponse({"error": "Group not found"}, status_code=404)

    group_name = group["name"]
    arb_state["groups"] = [g for g in arb_state["groups"] if g["id"] != group_id]
    save_arb_groups()
    add_arb_message("INFO", f"Deleted group: {group_name}")

    return {"success": True}


@router.put("/api/arb/groups/{group_id}")
async def api_update_group(group_id: str, request: Request):
    """Update an arbitrage group's name."""
    data = await request.json()
    name = data.get("name", "").strip()

    if not name:
        return JSONResponse({"error": "Group name is required"}, status_code=400)

    group = next((g for g in arb_state["groups"] if g["id"] == group_id), None)
    if not group:
        return JSONResponse({"error": "Group not found"}, status_code=404)

    old_name = group["name"]
    group["name"] = name
    save_arb_groups()
    add_arb_message("INFO", f"Renamed group: {old_name} → {name}")

    return {"group": group}


@router.post("/api/arb/groups/{group_id}/markets")
async def api_add_market(group_id: str, request: Request):
    """Add a market to an arbitrage group."""
    req_data = await request.json()
    ticker = req_data.get("ticker", "").strip()
    is_reverse = bool(req_data.get("is_reverse", False))

    if not ticker:
        return JSONResponse({"error": "Market ticker is required"}, status_code=400)

    group = next((g for g in arb_state["groups"] if g["id"] == group_id), None)
    if not group:
        return JSONResponse({"error": "Group not found"}, status_code=404)

    # Check if market already exists in group
    if any(m["ticker"] == ticker for m in group["markets"]):
        return JSONResponse({"error": "Market already in group"}, status_code=400)

    # Verify the market exists on Kalshi
    try:
        data = kalshi_get(f"/trade-api/v2/markets/{ticker}")
        if not data or "market" not in data:
            return JSONResponse({"error": f"Market {ticker} not found on Kalshi"}, status_code=404)

        m = data["market"]
        new_market = {
            "ticker": ticker,
            "yes_bid": _dollars_to_cents(m.get("yes_bid_dollars")),
            "yes_ask": _dollars_to_cents(m.get("yes_ask_dollars")),
            "no_bid": _dollars_to_cents(m.get("no_bid_dollars")),
            "no_ask": _dollars_to_cents(m.get("no_ask_dollars")),
            "last_price": _dollars_to_cents(m.get("last_price_dollars")),
            "status": m.get("status", "unknown"),
            "title": m.get("title", ticker),
            "yes_sub_title": m.get("yes_sub_title", ""),
            "is_reverse": is_reverse
        }
    except Exception as e:
        return JSONResponse({"error": f"Failed to fetch market: {e}"}, status_code=500)

    group["markets"].append(new_market)
    save_arb_groups()
    reverse_label = " (REVERSE)" if is_reverse else ""
    add_arb_message("INFO", f"Added market {ticker}{reverse_label} to group {group['name']}")

    # Recompute opportunities
    compute_opportunities()

    return {"market": new_market}


@router.delete("/api/arb/groups/{group_id}/markets/{ticker}")
def api_remove_market(group_id: str, ticker: str):
    """Remove a market from an arbitrage group."""
    group = next((g for g in arb_state["groups"] if g["id"] == group_id), None)
    if not group:
        return JSONResponse({"error": "Group not found"}, status_code=404)

    original_count = len(group["markets"])
    group["markets"] = [m for m in group["markets"] if m["ticker"] != ticker]

    if len(group["markets"]) == original_count:
        return JSONResponse({"error": "Market not found in group"}, status_code=404)

    save_arb_groups()
    add_arb_message("INFO", f"Removed market {ticker} from group {group['name']}")

    # Recompute opportunities
    compute_opportunities()

    return {"success": True}


@router.put("/api/arb/settings")
async def api_update_settings(request: Request):
    """Update arbitrage settings."""
    data = await request.json()

    if "threshold" in data:
        arb_state["settings"]["threshold"] = int(data["threshold"])
    if "buffer" in data:
        arb_state["settings"]["buffer"] = int(data["buffer"])
    if "skip_confirmation" in data:
        arb_state["settings"]["skip_confirmation"] = bool(data["skip_confirmation"])
    if "auto_arb" in data:
        arb_state["settings"]["auto_arb"] = bool(data["auto_arb"])
    if "auto_arb_quantity" in data:
        arb_state["settings"]["auto_arb_quantity"] = max(1, int(data["auto_arb_quantity"]))

    save_arb_groups()

    # Recompute opportunities with new threshold
    compute_opportunities()

    return {"settings": arb_state["settings"]}


@router.post("/api/arb/execute")
async def api_execute_arbitrage(request: Request):
    """Execute both sides of an arbitrage trade."""
    data = await request.json()
    ticker_a = data.get("ticker_a")
    side_a = data.get("side_a", "yes").lower()
    ticker_b = data.get("ticker_b")
    side_b = data.get("side_b", "no").lower()
    quantity = int(data.get("quantity", 1))

    if not ticker_a or not ticker_b:
        return JSONResponse({"error": "Both ticker_a and ticker_b are required"}, status_code=400)
    if side_a not in ("yes", "no") or side_b not in ("yes", "no"):
        return JSONResponse({"error": "Sides must be 'yes' or 'no'"}, status_code=400)
    if quantity < 1:
        return JSONResponse({"error": "Quantity must be at least 1"}, status_code=400)

    results = execute_arb_internal(ticker_a, side_a, ticker_b, side_b, quantity, is_auto=False)
    return results
