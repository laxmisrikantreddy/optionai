"""Background poller: fetches option chains and feeds signals into the store."""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from .config import settings
from .dhan_client import DhanClient
from .signals import evaluate
from .store import store

logger = logging.getLogger(__name__)


class WorkerState:
    def __init__(self) -> None:
        self.last_poll_at: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.running: bool = False
        self.poll_count: int = 0


state = WorkerState()


async def _poll_once(dhan: DhanClient) -> None:
    for u in settings.underlyings:
        try:
            expiries = await dhan.expiry_list(u.scrip, u.segment)
            if not expiries:
                logger.warning("no expiries returned for %s", u.name)
                continue
            expiry = expiries[0]  # nearest expiry
            chain = await dhan.option_chain(u.scrip, u.segment, expiry)
            sigs = evaluate(u.name, expiry, chain, settings.rules, settings.risk)
            added = sum(1 for s in sigs if store.add(s))
            if added:
                logger.info("%s %s: %d new signal(s)", u.name, expiry, added)
            # Clear last error on a clean cycle for this underlying.
            state.last_error = None
        except Exception as e:  # noqa: BLE001
            state.last_error = f"{u.name}: {type(e).__name__}: {e}"
            logger.exception("poll failed for %s", u.name)
            await asyncio.sleep(2)  # back off briefly on error


async def run_forever() -> None:
    if not settings.dhan_client_id or not settings.dhan_access_token:
        logger.error("DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN missing; worker idle.")
        state.last_error = "Dhan credentials not configured (.env)"
        return

    dhan = DhanClient(settings.dhan_client_id, settings.dhan_access_token)
    state.running = True
    logger.info(
        "worker started: %d underlying(s), every %ds",
        len(settings.underlyings),
        settings.poll_interval_seconds,
    )
    try:
        while True:
            await _poll_once(dhan)
            state.last_poll_at = datetime.now(timezone.utc)
            state.poll_count += 1
            await asyncio.sleep(settings.poll_interval_seconds)
    except asyncio.CancelledError:
        logger.info("worker cancelled")
        raise
    finally:
        state.running = False
        await dhan.close()
