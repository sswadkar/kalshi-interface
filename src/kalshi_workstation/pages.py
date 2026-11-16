"""Existing HTML page URLs, independent of the process working directory."""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from kalshi_workstation.config import STATIC_DIR

router = APIRouter()


@router.get("/performance", response_class=HTMLResponse)
def performance_page():
    html_path = STATIC_DIR / "performance.html"
    if not html_path.exists():
        return HTMLResponse("<h1>Performance page not found</h1>", status_code=404)
    return HTMLResponse(html_path.read_text())


@router.get("/liquidity", response_class=HTMLResponse)
def liquidity_page():
    html_path = STATIC_DIR / "liquidity.html"
    if not html_path.exists():
        return HTMLResponse("<h1>Liquidity page not found</h1>", status_code=404)
    return HTMLResponse(html_path.read_text())


@router.get("/arbitrage", response_class=HTMLResponse)
def arbitrage_page():
    """Serve the arbitrage scanner page."""
    html_path = STATIC_DIR / "arbitrage.html"
    if not html_path.exists():
        return HTMLResponse("<h1>Arbitrage page not found</h1>", status_code=404)
    return HTMLResponse(html_path.read_text())


@router.get("/", response_class=HTMLResponse)
def root():
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(html_path.read_text())
