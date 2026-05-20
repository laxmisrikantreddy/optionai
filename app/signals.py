"""Signal model, spot-momentum tracker, and rule engine.

Operates on a Dhan option-chain payload of the shape:

    {
      "last_price": 22500.0,          # underlying spot
      "oc": {
        "22500.000000": {
          "ce": {
            "last_price": 120.0, "previous_close_price": 95.0,
            "oi": 12000, "previous_oi": 9000,
            "implied_volatility": 14.2,
            "greeks": {"delta": 0.48, ...}
          },
          "pe": { ... }
        },
        ...
      }
    }
"""
from collections import deque
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Deque, Dict, List, Optional, Tuple

from pydantic import BaseModel

from .config import RiskCfg, RulesCfg
from .risk import sl_target


class Signal(BaseModel):
    timestamp: datetime
    underlying: str
    expiry: str
    side: str            # "CE" or "PE"
    strike: float
    spot: float          # underlying spot at the moment the signal fired
    spot_momentum_pct: float  # % change of spot over the lookback window
    ltp: float
    sl: float
    target: float
    delta: float
    iv: float
    oi: int
    oi_change_pct: float
    price_change_pct: float
    rule: str
    note: str = ""


class SpotTracker:
    """Tracks recent underlying spot prices to compute short-term momentum.

    momentum_pct = (current_spot - oldest_spot_in_window) / oldest_spot_in_window * 100

    Returns None until the buffer has at least one sample older than the
    lookback window (i.e., during warm-up just after startup).
    """

    def __init__(self, lookback_seconds: int = 300, capacity: int = 600):
        self._buf: Dict[str, Deque[Tuple[datetime, float]]] = {}
        self._lock = Lock()
        self._lookback = timedelta(seconds=lookback_seconds)
        self._capacity = capacity

    def update(self, underlying: str, spot: float, ts: datetime) -> None:
        if not spot or spot <= 0:
            return
        with self._lock:
            buf = self._buf.setdefault(
                underlying, deque(maxlen=self._capacity)
            )
            buf.append((ts, float(spot)))

    def momentum_pct(self, underlying: str, now: datetime) -> Optional[float]:
        with self._lock:
            buf = self._buf.get(underlying)
            if not buf or len(buf) < 2:
                return None
            # Need at least one sample older than the lookback window.
            oldest_ts = buf[0][0]
            if (now - oldest_ts) < self._lookback:
                return None
            cutoff = now - self._lookback
            past_spot: Optional[float] = None
            for ts, sp in buf:
                if ts >= cutoff:
                    past_spot = sp
                    break
            if past_spot is None or past_spot == 0:
                return None
            current_spot = buf[-1][1]
            return (current_spot - past_spot) / past_spot * 100.0


def _row(strike: float, side_key: str, side_data: dict) -> dict:
    """Flatten one side of a strike into a simple dict with derived fields."""
    greeks = side_data.get("greeks") or {}
    prev_oi = side_data.get("previous_oi") or 0
    oi = side_data.get("oi") or 0
    prev_close = side_data.get("previous_close_price") or 0
    ltp = side_data.get("last_price") or 0
    oi_chg = ((oi - prev_oi) / prev_oi * 100.0) if prev_oi else 0.0
    px_chg = ((ltp - prev_close) / prev_close * 100.0) if prev_close else 0.0
    return {
        "strike": float(strike),
        "side": "CE" if side_key == "ce" else "PE",
        "ltp": float(ltp),
        "delta": float(greeks.get("delta") or 0.0),
        "iv": float(side_data.get("implied_volatility") or 0.0),
        "oi": int(oi),
        "oi_change_pct": float(oi_chg),
        "price_change_pct": float(px_chg),
    }


def evaluate(
    underlying: str,
    expiry: str,
    chain: Dict,
    rules: RulesCfg,
    risk: RiskCfg,
    spot: float,
    spot_momentum_pct: Optional[float],
) -> List[Signal]:
    """Run all rules over a Dhan option-chain payload, return signals.

    Direction filter:
      * CE allowed only when spot_momentum_pct >= +rules.spot_momentum_min_pct
      * PE allowed only when spot_momentum_pct <= -rules.spot_momentum_min_pct
      * Sideways or warm-up (None) -> emit nothing.
    """
    out: List[Signal] = []
    if spot_momentum_pct is None:
        return out
    if abs(spot_momentum_pct) < rules.spot_momentum_min_pct:
        return out

    direction_is_up = spot_momentum_pct >= rules.spot_momentum_min_pct
    direction_is_down = spot_momentum_pct <= -rules.spot_momentum_min_pct

    oc = chain.get("oc") or {}
    now = datetime.now(timezone.utc)

    for strike_key, sides in oc.items():
        try:
            strike = float(strike_key)
        except (TypeError, ValueError):
            continue
        ce = sides.get("ce") or {}
        pe = sides.get("pe") or {}
        if ce and direction_is_up:
            out += _eval_side(
                underlying, expiry, _row(strike, "ce", ce),
                rules, risk, now, spot, spot_momentum_pct,
            )
        if pe and direction_is_down:
            out += _eval_side(
                underlying, expiry, _row(strike, "pe", pe),
                rules, risk, now, spot, spot_momentum_pct,
            )
    return out


def _eval_side(
    underlying: str,
    expiry: str,
    r: dict,
    rules: RulesCfg,
    risk: RiskCfg,
    now: datetime,
    spot: float,
    spot_momentum_pct: float,
) -> List[Signal]:
    sigs: List[Signal] = []

    # Delta filter: only ATM-ish strikes with decent gamma.
    abs_delta = abs(r["delta"])
    if not (rules.delta_min <= abs_delta <= rules.delta_max):
        return sigs
    if r["ltp"] <= 0:
        return sigs

    common = dict(
        timestamp=now,
        underlying=underlying,
        expiry=expiry,
        side=r["side"],
        strike=r["strike"],
        spot=round(spot, 2),
        spot_momentum_pct=round(spot_momentum_pct, 3),
        ltp=r["ltp"],
        delta=r["delta"],
        iv=r["iv"],
        oi=r["oi"],
        oi_change_pct=round(r["oi_change_pct"], 2),
        price_change_pct=round(r["price_change_pct"], 2),
    )

    # Rule 1: long buildup on this side (price up + OI up). Buy this side.
    if (
        r["oi_change_pct"] >= rules.oi_change_pct_min
        and r["price_change_pct"] >= rules.price_change_pct_min
    ):
        sl, tgt = sl_target(r["ltp"], risk.sl_pct, risk.rr)
        sigs.append(Signal(
            **common, sl=sl, target=tgt,
            rule="LONG_BUILDUP",
            note=f"spot {spot_momentum_pct:+.2f}% | OI +{r['oi_change_pct']:.1f}% | LTP +{r['price_change_pct']:.1f}%",
        ))

    # Rule 2: short covering on this side (price up + OI dropping). Buy this side.
    if (
        r["oi_change_pct"] <= -rules.short_cover_oi_drop_pct
        and r["price_change_pct"] >= rules.price_change_pct_min
    ):
        sl, tgt = sl_target(r["ltp"], risk.sl_pct, risk.rr)
        sigs.append(Signal(
            **common, sl=sl, target=tgt,
            rule="SHORT_COVERING",
            note=f"spot {spot_momentum_pct:+.2f}% | OI {r['oi_change_pct']:.1f}% | LTP +{r['price_change_pct']:.1f}%",
        ))

    return sigs
