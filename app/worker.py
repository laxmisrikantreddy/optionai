"""Background poller: fetches option chains and feeds signals into the store."""
import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from .config import settings
from .dhan_client import DhanClient
from .signals import PriceActionContext, evaluate
from .spot_history import SpotHistory
from .store import store

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

# Cache the expiry list per (scrip, segment). Expiries don't change intraday.
EXPIRY_TTL = timedelta(hours=6)
_expiry_cache: Dict[Tuple[int, str], Tuple[datetime, List[str]]] = {}

# ORB end time from config (9:15 + orb_minutes).
_orb_end = (
    datetime.combine(datetime.today(), time(9, 15))
    + timedelta(minutes=settings.rules.orb_minutes)
).time()

# Global spot history used by all rules.
history = SpotHistory(orb_end_time=_orb_end)


class WorkerState:
    def __init__(self) -> None:
        self.last_poll_at: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.running: bool = False
        self.poll_count: int = 0


state = WorkerState()


async def _get_expiries(dhan: DhanClient, scrip: int, segment: str) -> List[str]:
    """Return cached expiries when fresh, otherwise refetch."""
    key = (scrip, segment)
    now = datetime.now(timezone.utc)
    cached = _expiry_cache.get(key)
    if cached and (now - cached[0]) < EXPIRY_TTL:
        return cached[1]
    expiries = await dhan.expiry_list(scrip, segment)
    _expiry_cache[key] = (now, expiries)
    return expiries


async def _poll_once(dhan: DhanClient) -> None:
    for u in settings.underlyings:
        try:
            expiries = await _get_expiries(dhan, u.scrip, u.segment)
            if not expiries:
                logger.warning("no expiries returned for %s", u.name)
                continue
            expiry = expiries[0]  # nearest expiry
            chain = await dhan.option_chain(u.scrip, u.segment, expiry)

            spot = float(chain.get("last_price") or 0.0)
            volume = float(chain.get("volume") or 0.0)
            now = datetime.now(timezone.utc)

            # Feed spot into history for VWAP / ORB / multi-TF.
            history.record(u.name, spot, volume, now)

            # Build price-action context for the rule engine.
            pa = PriceActionContext(history, u.name, settings.rules)

            sigs = evaluate(u.name, expiry, chain, settings.rules, settings.risk, pa)
            added = sum(1 for s in sigs if store.add(s))
            if added:
                logger.info(
                    "%s %s: %d signal(s) | spot=%.2f vwap=%s orb=%s 5m=%s 15m=%s",
                    u.name, expiry, added, spot,
                    f"{pa.vwap:.2f}" if pa.vwap else "-",
                    pa.orb_break or "-",
                    f"{pa.mom_5m:+.2f}%" if pa.mom_5m is not None else "-",
                    f"{pa.mom_15m:+.2f}%" if pa.mom_15m is not None else "-",
                )
            state.last_error = None
        except Exception as e:  # noqa: BLE001
            state.last_error = f"{u.name}: {type(e).__name__}: {e}"
            logger.exception("poll failed for %s", u.name)
            await asyncio.sleep(2)


async def run_forever() -> None:
    if not settings.dhan_client_id or not settings.dhan_access_token:
        logger.error("DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN missing; worker idle.")
        state.last_error = "Dhan credentials not configured (.env)"
        return

    dhan = DhanClient(settings.dhan_client_id, settings.dhan_access_token)
    state.running = True
    logger.info(
        "worker started: %d underlying(s), every %ds, "
        "momentum 5m=%ds 15m=%ds, ORB %d min, min-mom ±%.2f%%",
        len(settings.underlyings),
        settings.poll_interval_seconds,
        settings.rules.momentum_5m_seconds,
        settings.rules.momentum_15m_seconds,
        settings.rules.orb_minutes,
        settings.rules.spot_momentum_min_pct,
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
