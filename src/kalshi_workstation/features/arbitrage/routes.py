"""Arbitrage configuration and execution endpoints."""
import asyncio
import uuid
from copy import deepcopy
from typing import Literal

import requests
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from kalshi_workstation.core.exchange.kalshi import kalshi_get
from kalshi_workstation.core.market_data import market_quote
from .service import Arbitrage

router = APIRouter()


def get_arbitrage(request: Request) -> Arbitrage:
    return request.app.state.arbitrage


def _strip(value):
    return value.strip() if isinstance(value, str) else value


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    _strip_name = field_validator("name", mode="before")(_strip)


class GroupUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    payout_verified: bool | None = None
    _strip_name = field_validator("name", mode="before")(_strip)


class MarketAdd(BaseModel):
    ticker: str = Field(min_length=1)
    is_reverse: bool = False
    _strip_ticker = field_validator("ticker", mode="before")(_strip)


class SettingsUpdate(BaseModel):
    threshold: int | None = Field(None, ge=0, le=100)
    buffer: int | None = Field(None, ge=0, le=99)
    skip_confirmation: bool | None = None
    auto_arb: bool | None = None
    auto_arb_quantity: int | None = Field(None, ge=1)


class PairRequest(BaseModel):
    ticker_a: str = Field(min_length=1)
    side_a: Literal["yes", "no"] = "yes"
    ticker_b: str = Field(min_length=1)
    side_b: Literal["yes", "no"] = "no"
    quantity: int = Field(1, ge=1)

    @field_validator("side_a", "side_b", mode="before")
    @classmethod
    def lowercase(cls, value):
        return value.lower() if isinstance(value, str) else value


@router.get("/api/arb/status")
async def api_arb_status(request: Request, arb: Arbitrage = Depends(get_arbitrage)):
    """Groups, candidates, settings, and the shared cached balance."""
    return {**arb.status(), "user_info": request.app.state.portfolio.balance()}


@router.post("/api/arb/groups")
async def api_create_group(body: GroupCreate, arb: Arbitrage = Depends(get_arbitrage)):
    group = {"id": str(uuid.uuid4())[:8], "name": body.name, "markets": [],
             "opportunity": None, "payout_verified": False}
    arb.save(groups=[*arb.groups, group])
    arb.add_message("INFO", f"Created group: {body.name}")
    return {"group": group}


@router.delete("/api/arb/groups/{group_id}")
async def api_delete_group(group_id: str, arb: Arbitrage = Depends(get_arbitrage)):
    group = arb.group(group_id)
    if not group:
        return JSONResponse({"error": "Group not found"}, status_code=404)
    arb.save(groups=[g for g in arb.groups if g["id"] != group_id])
    arb.add_message("INFO", f"Deleted group: {group['name']}")
    return {"success": True}


@router.put("/api/arb/groups/{group_id}")
async def api_update_group(group_id: str, body: GroupUpdate, arb: Arbitrage = Depends(get_arbitrage)):
    """Rename a group and/or mark its payouts as verified for automatic execution."""
    groups = deepcopy(arb.groups)
    group = next((g for g in groups if g["id"] == group_id), None)
    if not group:
        return JSONResponse({"error": "Group not found"}, status_code=404)
    old_name = group["name"]
    if body.name is not None:
        group["name"] = body.name
    if body.payout_verified is not None:
        group["payout_verified"] = body.payout_verified
    arb.save(groups=groups)
    if body.name is not None and body.name != old_name:
        arb.add_message("INFO", f"Renamed group: {old_name} → {body.name}")
    if body.payout_verified is not None:
        arb.forget_notice("unverified", group_id)
        arb.add_message("INFO", f"Group “{group['name']}” payouts "
                                f"{'verified: eligible' if body.payout_verified else 'unverified: not eligible'}"
                                f" for automatic execution")
    return {"group": group}


@router.post("/api/arb/groups/{group_id}/markets")
async def api_add_market(group_id: str, body: MarketAdd, arb: Arbitrage = Depends(get_arbitrage)):
    """Add a market. Adding a market clears the group's payout verification."""
    if not arb.group(group_id):
        return JSONResponse({"error": "Group not found"}, status_code=404)
    try:
        data = await asyncio.to_thread(kalshi_get, f"/trade-api/v2/markets/{body.ticker}")
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return JSONResponse({"error": f"Market {body.ticker} not found on Kalshi"}, status_code=404)
        return JSONResponse({"error": f"Failed to fetch market: {exc}"}, status_code=502)
    except Exception as exc:
        return JSONResponse({"error": f"Failed to fetch market: {exc}"}, status_code=502)
    if not data or "market" not in data:
        return JSONResponse({"error": f"Market {body.ticker} not found on Kalshi"}, status_code=404)

    # Re-read after the await: the configuration may have changed meanwhile.
    groups = deepcopy(arb.groups)
    group = next((g for g in groups if g["id"] == group_id), None)
    if not group:
        return JSONResponse({"error": "Group not found"}, status_code=404)
    if any(m["ticker"] == body.ticker for m in group["markets"]):
        return JSONResponse({"error": "Market already in group"}, status_code=400)
    market = {"ticker": body.ticker, **market_quote(data["market"]), "is_reverse": body.is_reverse}
    group["markets"].append(market)
    was_verified = group.get("payout_verified")
    group["payout_verified"] = False
    arb.save(groups=groups)
    arb.add_message("INFO", f"Added market {body.ticker}{' (REVERSE)' if body.is_reverse else ''} "
                            f"to group {group['name']}"
                            f"{'; payout verification cleared' if was_verified else ''}")
    arb.compute_opportunities()
    return {"market": market}


@router.delete("/api/arb/groups/{group_id}/markets/{ticker}")
async def api_remove_market(group_id: str, ticker: str, arb: Arbitrage = Depends(get_arbitrage)):
    groups = deepcopy(arb.groups)
    group = next((g for g in groups if g["id"] == group_id), None)
    if not group:
        return JSONResponse({"error": "Group not found"}, status_code=404)
    remaining = [m for m in group["markets"] if m["ticker"] != ticker]
    if len(remaining) == len(group["markets"]):
        return JSONResponse({"error": "Market not found in group"}, status_code=404)
    group["markets"] = remaining
    arb.save(groups=groups)
    arb.add_message("INFO", f"Removed market {ticker} from group {group['name']}")
    arb.compute_opportunities()
    return {"success": True}


@router.put("/api/arb/settings")
async def api_update_settings(body: SettingsUpdate, arb: Arbitrage = Depends(get_arbitrage)):
    arb.save(settings={**arb.settings, **body.model_dump(exclude_none=True)})
    arb.compute_opportunities()
    return {"settings": arb.settings}


@router.post("/api/arb/execute")
async def api_execute_arbitrage(body: PairRequest, arb: Arbitrage = Depends(get_arbitrage)):
    """Execute a pair: leg 2 is sent only after leg 1 fills, sized to that fill."""
    return await arb.execute(body.ticker_a, body.side_a, body.ticker_b, body.side_b,
                             body.quantity, is_auto=False)
