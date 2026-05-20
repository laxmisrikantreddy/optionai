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


async def _backfill_orb(dhan: DhanClient) -> None:
    """Fetch today's early-morning 1-min candles and seed SpotHistory.

    This gives us VWAP / ORB / momentum even when the app starts mid-day.
    If today's data isn't available (pre-market, holiday), it's a no-op.
    """
    now_ist = datetime.now(IST)
    today_str = now_ist.strftime("%Y-%m-%d")

    for u in settings.underlyings:
        if not u.security_id:
            logger.warning("no security_id for %s; skipping ORB backfill", u.name)
            continue
        try:
            candles = await dhan.intraday_candles(
                security_id=u.security_id,
                exchange_segment=u.chart_exchange_segment,
                instrument=u.chart_instrument,
                from_date=today_str,
                to_date=today_str,
                interval=1,  # 1-minute candles
            )
            if not candles:
                logger.info("ORB backfill %s: no candles returned (pre-market?)", u.name)
                continue

            fed = 0
            for c in candles:
                # Dhan returns timestamps as epoch seconds (IST-based).
                ts_raw = c.get("timestamp")
                if ts_raw is None:
                    continue
                # Convert epoch to datetime (IST).
                ts = datetime.fromtimestamp(ts_raw, tz=IST)
                spot = float(c.get("close") or c.get("open") or 0)
                vol = float(c.get("volume") or 1)
                if spot > 0:
                    history.record(u.name, spot, vol, ts)
                    fed += 1

            snap = history.snapshot(u.name)
            logger.info(
                "ORB backfill %s: fed %d candles | spot=%s vwap=%s orb=%s/%s break=%s",
                u.name, fed,
                snap.get("spot"), snap.get("vwap"),
                snap.get("orb_high"), snap.get("orb_low"),
                snap.get("orb_break"),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("ORB backfill failed for %s: %s", u.name, e)


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

    # --- ORB Backfill: fetch today's early candles so we have VWAP + ORB
    # even when starting mid-day. ---
    logger.info("starting ORB backfill...")
    await _backfill_orb(dhan)
    logger.info("ORB backfill complete; beginning live polling.")

    logger.info(
        "worker started: %d underlying(s), every %ds, "
        "momentum 5m=%ds 15m=%ds, ORB %d min",
        len(settings.underlyings),
        settings.poll_interval_seconds,
        settings.rules.momentum_5m_seconds,
        settings.rules.momentum_15m_seconds,
        settings.rules.orb_minutes,
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
