"""LP controls and status; the runner is supplied by application composition."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from kalshi_workstation.core.portfolio import get_portfolio, lp_rewards, save_lp_rewards
from .runner import LiquidityMaker, DEFAULT_MARKET

router = APIRouter()

def get_provider(request: Request):
    return request.app.state.liquidity_provider


@router.get("/api/lp/status")
def api_lp_status(lp_maker: LiquidityMaker = Depends(get_provider)):
    return {**lp_maker.status(),
            "portfolio": get_portfolio(),
            "rewards": {
                "lifetime_est": round(lp_rewards["lifetime_est"], 2),
                "current_daily": round(lp_rewards["current_daily"], 2),
                "pending_lp": round(lp_rewards.get("pending_lp", 0.0), 2),
                "started": lp_rewards["started"],
            }}


@router.post("/api/lp/rewards/pending")
async def api_lp_set_pending(request: Request):
    """Set the pending (accrued, unpaid) LP-rewards figure — e.g. reset to 0 after a payout."""
    data = await request.json()
    try:
        lp_rewards["pending_lp"] = float(data.get("value", 0))
    except (TypeError, ValueError):
        return JSONResponse({"error": "value must be a number"}, status_code=400)
    save_lp_rewards()
    return {"pending_lp": lp_rewards["pending_lp"]}


@router.post("/api/lp/rewards/baseline")
async def api_lp_set_baseline(request: Request):
    """Set the lifetime-rewards starting value (e.g. actual rewards earned to date)."""
    data = await request.json()
    try:
        lp_rewards["lifetime_est"] = float(data.get("value", 0))
    except (TypeError, ValueError):
        return JSONResponse({"error": "value must be a number"}, status_code=400)
    save_lp_rewards()
    return {"lifetime_est": lp_rewards["lifetime_est"]}


@router.put("/api/lp/settings")
async def api_lp_settings(request: Request, lp_maker: LiquidityMaker = Depends(get_provider)):
    data = await request.json()
    allowed = set(lp_maker.settings.keys())
    for k, v in data.items():
        if k in allowed:
            lp_maker.settings[k] = v
    lp_maker.save_config()
    lp_maker.log("INFO", f"settings updated: {list(data.keys())}")
    return {"settings": lp_maker.settings}


@router.post("/api/lp/markets")
async def api_lp_add_market(request: Request, lp_maker: LiquidityMaker = Depends(get_provider)):
    data = await request.json()
    ticker = (data.get("ticker") or "").strip().upper()
    if not ticker:
        return JSONResponse({"error": "ticker required"}, status_code=400)
    if any(m["ticker"] == ticker for m in lp_maker.markets):
        return JSONResponse({"error": "market already added"}, status_code=400)
    m = dict(DEFAULT_MARKET)
    for k in ("sides", "size", "yes_max_bid", "no_max_bid", "flatten_band", "enabled"):
        if k in data:
            m[k] = data[k]
    m["ticker"] = ticker
    lp_maker.markets.append(m)
    lp_maker.save_config()
    lp_maker.refresh_programs()
    lp_maker.log("INFO", f"added market {ticker}")
    return {"market": m}


@router.put("/api/lp/markets/{ticker}")
async def api_lp_update_market(ticker: str, request: Request, lp_maker: LiquidityMaker = Depends(get_provider)):
    data = await request.json()
    m = next((x for x in lp_maker.markets if x["ticker"] == ticker), None)
    if not m:
        return JSONResponse({"error": "market not found"}, status_code=404)
    for k in ("sides", "size", "yes_max_bid", "no_max_bid", "flatten_band", "enabled"):
        if k in data:
            m[k] = data[k]
    lp_maker.save_config()
    return {"market": m}


@router.delete("/api/lp/markets/{ticker}")
async def api_lp_remove_market(ticker: str, lp_maker: LiquidityMaker = Depends(get_provider)):
    m = next((x for x in lp_maker.markets if x["ticker"] == ticker), None)
    if not m:
        return JSONResponse({"error": "market not found"}, status_code=404)
    # cancel any of our resting orders on it first
    for o in [o for o in lp_maker.orders.values() if o["ticker"] == ticker]:
        await lp_maker._cancel(o["coid"])
    lp_maker.markets = [x for x in lp_maker.markets if x["ticker"] != ticker]
    lp_maker.save_config()
    lp_maker.log("INFO", f"removed market {ticker}")
    return {"success": True}


@router.post("/api/lp/panic")
async def api_lp_panic(lp_maker: LiquidityMaker = Depends(get_provider)):
    await lp_maker.panic()
    return {"success": True, "enabled": lp_maker.settings["enabled"]}
