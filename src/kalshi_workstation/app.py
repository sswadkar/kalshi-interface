"""Application composition. Importing does not start tasks or load private keys."""
from fastapi import FastAPI
from kalshi_workstation import pages
from kalshi_workstation.runtime import lifespan
from kalshi_workstation.core.exchange.programs import ProgramCatalog
from kalshi_workstation.features.trading import routes as trading
from kalshi_workstation.features.arbitrage import routes as arbitrage
from kalshi_workstation.features.liquidity_monitor import routes as liquidity_monitor
from kalshi_workstation.features.liquidity_provider import routes as liquidity_provider
from kalshi_workstation.features.liquidity_provider.runner import LiquidityMaker

def create_app():
    application = FastAPI(lifespan=lifespan)
    application.state.programs = ProgramCatalog()
    application.state.liquidity_provider = LiquidityMaker(programs=application.state.programs)
    for router in (trading.router, arbitrage.router, liquidity_monitor.router,
                   liquidity_provider.router, pages.router):
        application.include_router(router)
    return application

app = create_app()
