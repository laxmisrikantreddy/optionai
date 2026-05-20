"""FastAPI app: hosts the dashboard and JSON APIs, runs the poller in lifespan."""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import worker
from .config import settings
from .store import store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(worker.run_forever())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="OptionAI Signal Generator", lifespan=lifespan)


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "running": worker.state.running,
        "last_poll_at": worker.state.last_poll_at,
        "poll_count": worker.state.poll_count,
        "last_error": worker.state.last_error,
        "underlyings": [u.name for u in settings.underlyings],
        "poll_interval_seconds": settings.poll_interval_seconds,
        "spot": worker.state.spot,
        "spot_momentum_pct": worker.state.spot_momentum_pct,
        "spot_lookback_seconds": settings.rules.spot_lookback_seconds,
        "spot_momentum_min_pct": settings.rules.spot_momentum_min_pct,
    }


@app.get("/api/signals")
def list_signals(limit: int = 100):
    return [s.model_dump(mode="json") for s in store.list(limit=limit)]


@app.get("/api/config")
def get_config():
    return {
        "risk": settings.risk.model_dump(),
        "rules": settings.rules.model_dump(),
        "underlyings": [u.model_dump() for u in settings.underlyings],
        "poll_interval_seconds": settings.poll_interval_seconds,
    }


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
