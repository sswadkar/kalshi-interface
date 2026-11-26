"""Run the application's background jobs and await their shutdown."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from kalshi_workstation.config import ENV, LOG_LEVEL

logger = logging.getLogger("kalshi_workstation")


def configure_logging():
    """Send application logs to stderr beside Uvicorn's, once per process."""
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(LOG_LEVEL)
    logger.propagate = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    state = app.state
    logger.info("Starting: ENV=%s, manual/arbitrage dry_run=%s, auto_arb=%s, extensions=%s",
                ENV, state.mode.dry_run, state.arbitrage.settings["auto_arb"],
                ", ".join(state.extensions) or "none")
    tasks = [asyncio.create_task(start(), name=name) for name, start in state.background_jobs]
    state.background_tasks = tasks
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
