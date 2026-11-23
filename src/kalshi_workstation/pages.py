"""Browser pages, served independently of the process working directory."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from kalshi_workstation.config import STATIC_DIR

router = APIRouter()


def _page(name: str) -> HTMLResponse:
    path = STATIC_DIR / name
    if not path.exists():
        return HTMLResponse("<h1>Page not found</h1>", status_code=404)
    return HTMLResponse(path.read_text())


@router.get("/", response_class=HTMLResponse)
def trader_page():
    return _page("index.html")


@router.get("/arbitrage", response_class=HTMLResponse)
def arbitrage_page():
    return _page("arbitrage.html")


@router.get("/liquidity", response_class=HTMLResponse)
def liquidity_page():
    return _page("liquidity.html")


@router.get("/api/pages")
def extra_pages(request: Request):
    """Navigation links contributed by extensions (empty without any)."""
    return {"pages": request.app.state.extra_pages}
