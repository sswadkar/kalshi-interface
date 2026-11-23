"""Application composition. Importing does not start tasks or load private keys.

Every feature's state lives in one service object on `app.state`; routes receive
it through a dependency, and `runtime.lifespan` runs the background jobs listed
in `app.state.background_jobs`.
"""
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from kalshi_workstation import pages
from kalshi_workstation.config import ALLOWED_HOSTS, DATA_DIR, DRY_RUN, EVENT_TICKER, STATIC_DIR
from kalshi_workstation.runtime import lifespan
from kalshi_workstation.core.exchange.programs import ProgramCatalog
from kalshi_workstation.core.execution.mode import ExecutionMode
from kalshi_workstation.core.persistence import ConfigurationSaveError
from kalshi_workstation.core.portfolio import Portfolio
from kalshi_workstation.core.security import CrossOriginProtection
from kalshi_workstation.extensions import load_extensions
from kalshi_workstation.features.live_trading import routes as live_trading
from kalshi_workstation.features.live_trading.service import LiveTrading
from kalshi_workstation.features.arbitrage import routes as arbitrage
from kalshi_workstation.features.arbitrage.service import Arbitrage
from kalshi_workstation.features.liquidity_monitor import routes as liquidity_monitor


def create_app(*, data_dir=DATA_DIR, allowed_hosts=ALLOWED_HOSTS, dry_run=DRY_RUN,
               event_ticker=EVENT_TICKER, with_extensions=True):
    application = FastAPI(lifespan=lifespan)
    # Starlette runs the last-added middleware first: host check, then origin check.
    application.add_middleware(CrossOriginProtection)
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    @application.exception_handler(ConfigurationSaveError)
    async def configuration_save_failed(request, exc):
        return JSONResponse(
            {"error": str(exc), "code": "configuration_save_failed"}, status_code=503,
        )

    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    state = application.state
    state.data_dir = data_dir
    state.mode = ExecutionMode(dry_run=dry_run)
    state.portfolio = Portfolio()
    state.programs = ProgramCatalog()
    state.live_trading = LiveTrading(state.mode, state.portfolio, event_ticker)
    state.arbitrage = Arbitrage(state.mode, config_path=data_dir / "arbitrage_groups.json")
    # Factories, not coroutines: looked up when the server starts.
    state.background_jobs = [
        ("balance", lambda: state.portfolio.run()),
        ("liquidity-programs", lambda: state.programs.run()),
        ("trading-markets", lambda: state.live_trading.run_markets()),
        ("resting-orders", lambda: state.live_trading.run_resting()),
        ("arbitrage", lambda: state.arbitrage.run()),
    ]
    state.extra_pages = []

    for router in (live_trading.router, arbitrage.router, liquidity_monitor.router, pages.router):
        application.include_router(router)
    state.extensions = load_extensions(application) if with_extensions else []
    return application


app = create_app()
