"""Intraday spot-price history for VWAP, ORB, and multi-timeframe momentum.

Each poll, the worker calls `record(underlying, spot, volume, timestamp)`.
This module aggregates ticks into 1-minute candles and computes:
  - VWAP (volume-weighted average price from market open)
  - ORB high/low (the range of the first N minutes, default 9:15–9:30)
  - Multi-timeframe momentum (% change over configurable windows)

All times are in IST (UTC+05:30) because NSE market hours are IST-based.
"""
from collections import defaultdict, deque
from datetime import datetime, time, timedelta, timezone
from typing import Deque, Dict, List, NamedTuple, Optional, Tuple

IST = timezone(timedelta(hours=5, minutes=30))
MARKET_OPEN = time(9, 15)
DEFAULT_ORB_END = time(9, 30)


class Candle(NamedTuple):
    timestamp: datetime  # start of the minute (IST-aware)
    open: float
    high: float
    low: float
    close: float
    volume: float  # cumulative volume in that minute (approx from ticks)


class SpotHistory:
    """Per-underlying intraday spot tracker.

    Usage:
        history = SpotHistory(orb_end_time=time(9, 30))
        # on each poll:
        history.record("NIFTY", spot=22500.0, volume=123456, ts=now_utc)
        # then query:
        history.vwap("NIFTY")
        history.orb("NIFTY")  -> (high, low) or None during warm-up
        history.momentum_pct("NIFTY", window_seconds=300)
    """

    def __init__(self, orb_end_time: time = DEFAULT_ORB_END):
        self._orb_end_time = orb_end_time
        # Per underlying: list of 1-min candles, ordered by time.
        self._candles: Dict[str, List[Candle]] = defaultdict(list)
        # Per underlying: the "current" (not yet closed) candle being built.
        self._current_candle: Dict[str, Optional[Candle]] = {}
        # Per underlying: raw ticks (for sub-minute momentum). Capped at 1000.
        self._ticks: Dict[str, Deque[Tuple[datetime, float]]] = defaultdict(
            lambda: deque(maxlen=1000)
        )
        # Per underlying: cumulative volume seen so far today (for VWAP).
        self._cum_vol: Dict[str, float] = defaultdict(float)
        self._cum_vol_price: Dict[str, float] = defaultdict(float)
        # Track current day to reset at market open.
        self._last_date: Dict[str, Optional[datetime]] = {}

    def _ist_now(self, ts: datetime) -> datetime:
        return ts.astimezone(IST)

    def _maybe_reset(self, underlying: str, ist_ts: datetime) -> None:
        """Reset history if a new trading day has started."""
        today = ist_ts.date()
        last = self._last_date.get(underlying)
        if last is None or last != today:
            self._candles[underlying] = []
            self._current_candle[underlying] = None
            self._ticks[underlying].clear()
            self._cum_vol[underlying] = 0.0
            self._cum_vol_price[underlying] = 0.0
            self._last_date[underlying] = today

    def record(
        self, underlying: str, spot: float, volume: float, ts: datetime
    ) -> None:
        """Record a spot observation. Call this every poll (every ~10s)."""
        if spot <= 0:
            return
        ist_ts = self._ist_now(ts)
        self._maybe_reset(underlying, ist_ts)

        # Raw tick for momentum.
        self._ticks[underlying].append((ist_ts, spot))

        # VWAP accumulation (use volume delta if available, else use 1 as proxy).
        vol_delta = max(volume, 1.0)
        self._cum_vol[underlying] += vol_delta
        self._cum_vol_price[underlying] += spot * vol_delta

        # Candle aggregation: bucket by minute.
        minute_start = ist_ts.replace(second=0, microsecond=0)
        cur = self._current_candle.get(underlying)
        if cur is None or cur.timestamp != minute_start:
            # Close previous candle and start a new one.
            if cur is not None:
                self._candles[underlying].append(cur)
            self._current_candle[underlying] = Candle(
                timestamp=minute_start,
                open=spot,
                high=spot,
                low=spot,
                close=spot,
                volume=vol_delta,
            )
        else:
            # Update current candle in place (NamedTuple is immutable, replace).
            self._current_candle[underlying] = Candle(
                timestamp=cur.timestamp,
                open=cur.open,
                high=max(cur.high, spot),
                low=min(cur.low, spot),
                close=spot,
                volume=cur.volume + vol_delta,
            )

    def vwap(self, underlying: str) -> Optional[float]:
        """Return current intraday VWAP, or None if no data."""
        cv = self._cum_vol.get(underlying, 0.0)
        if cv <= 0:
            return None
        return self._cum_vol_price[underlying] / cv

    def orb(self, underlying: str) -> Optional[Tuple[float, float]]:
        """Return (orb_high, orb_low) for the opening range.

        Returns None if the ORB period hasn't completed yet.
        """
        candles = self._candles.get(underlying, [])
        cur = self._current_candle.get(underlying)
        all_candles = candles + ([cur] if cur else [])
        if not all_candles:
            return None

        orb_high = -float("inf")
        orb_low = float("inf")
        orb_found = False

        for c in all_candles:
            ct = c.timestamp.time()
            if ct < MARKET_OPEN:
                continue
            if ct >= self._orb_end_time:
                break
            orb_high = max(orb_high, c.high)
            orb_low = min(orb_low, c.low)
            orb_found = True

        if not orb_found:
            return None

        # Only return ORB if the period is complete (current time past orb_end).
        last_tick = self._ticks.get(underlying)
        if not last_tick:
            return None
        current_ist_time = last_tick[-1][0].time()
        if current_ist_time < self._orb_end_time:
            return None  # ORB still forming

        return (orb_high, orb_low)

    def momentum_pct(
        self, underlying: str, window_seconds: int, now: Optional[datetime] = None
    ) -> Optional[float]:
        """% change of spot over the last `window_seconds`.

        Returns None if insufficient history.
        """
        ticks = self._ticks.get(underlying)
        if not ticks or len(ticks) < 2:
            return None
        if now is None:
            now_ist = ticks[-1][0]
        else:
            now_ist = self._ist_now(now)
        cutoff = now_ist - timedelta(seconds=window_seconds)

        # Find the first tick at or after the cutoff.
        past_spot: Optional[float] = None
        for t, sp in ticks:
            if t >= cutoff:
                past_spot = sp
                break
        if past_spot is None or past_spot <= 0:
            return None

        # Need the oldest sample to actually be old enough.
        if ticks[0][0] > cutoff:
            return None  # not enough history yet

        current_spot = ticks[-1][1]
        return (current_spot - past_spot) / past_spot * 100.0

    def current_spot(self, underlying: str) -> Optional[float]:
        """Latest spot price."""
        ticks = self._ticks.get(underlying)
        if not ticks:
            return None
        return ticks[-1][1]

    def snapshot(self, underlying: str) -> Dict:
        """Return a summary dict for the dashboard."""
        spot = self.current_spot(underlying)
        vw = self.vwap(underlying)
        orb_levels = self.orb(underlying)
        mom_5 = self.momentum_pct(underlying, 300)
        mom_15 = self.momentum_pct(underlying, 900)
        return {
            "spot": round(spot, 2) if spot else None,
            "vwap": round(vw, 2) if vw else None,
            "orb_high": round(orb_levels[0], 2) if orb_levels else None,
            "orb_low": round(orb_levels[1], 2) if orb_levels else None,
            "momentum_5m_pct": round(mom_5, 3) if mom_5 is not None else None,
            "momentum_15m_pct": round(mom_15, 3) if mom_15 is not None else None,
            "above_vwap": (spot > vw) if (spot and vw) else None,
            "orb_break": (
                "HIGH" if (orb_levels and spot and spot > orb_levels[0])
                else "LOW" if (orb_levels and spot and spot < orb_levels[1])
                else "INSIDE" if orb_levels
                else None
            ),
        }
