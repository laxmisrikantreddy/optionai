"""Signal model, direction filters, and rule engine.

Filters applied in order (ALL must pass for a signal to emit):
  1. Delta band — only ATM-ish strikes (default 0.35–0.55)
  2. Multi-timeframe momentum — both 5min AND 15min trending same way
  3. VWAP — CE only above VWAP, PE only below VWAP
  4. ORB — CE only after ORB-high breakout, PE only after ORB-low breakdown
  5. OI + LTP rules — LONG_BUILDUP or SHORT_COVERING

If any filter is unavailable (warm-up period), it's skipped gracefully and
the engine stays quiet until enough history accumulates.
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel

from .config import RiskCfg, RulesCfg
from .risk import sl_target
from .spot_history import SpotHistory


class Signal(BaseModel):
    timestamp: datetime
    underlying: str
    expiry: str
    side: str            # "CE" or "PE"
    strike: float
    spot: float
    spot_momentum_5m_pct: Optional[float] = None
    spot_momentum_15m_pct: Optional[float] = None
    vwap: Optional[float] = None
    orb_high: Optional[float] = None
    orb_low: Optional[float] = None
    ltp: float
    sl: float
    target: float
    delta: float
    iv: float
    oi: int
    oi_change_pct: float
    price_change_pct: float
    rule: str
    filters_passed: str = ""  # e.g. "VWAP+ORB+MTF"
    note: str = ""


class PriceActionContext:
    """Snapshot of all price-action indicators at signal-generation time."""

    def __init__(self, history: SpotHistory, underlying: str, rules: RulesCfg):
        snap = history.snapshot(underlying)
        self.spot: Optional[float] = snap["spot"]
        self.vwap: Optional[float] = snap["vwap"]
        self.orb_high: Optional[float] = snap["orb_high"]
        self.orb_low: Optional[float] = snap["orb_low"]
        self.mom_5m: Optional[float] = snap["momentum_5m_pct"]
        self.mom_15m: Optional[float] = snap["momentum_15m_pct"]
        self.above_vwap: Optional[bool] = snap["above_vwap"]
        self.orb_break: Optional[str] = snap["orb_break"]  # "HIGH", "LOW", "INSIDE", None
        self.rules = rules

    def ce_allowed(self) -> bool:
        """Can we emit a CE (bullish) signal right now?"""
        # Multi-timeframe: both 5m and 15m must be positive.
        if self.mom_5m is not None and self.mom_15m is not None:
            if self.mom_5m < self.rules.spot_momentum_min_pct:
                return False
            if self.mom_15m < self.rules.spot_momentum_min_pct:
                return False
        else:
            # Not enough history for multi-TF — block signals.
            return False

        # VWAP: spot must be above VWAP.
        if self.vwap is not None and self.spot is not None:
            if self.spot <= self.vwap:
                return False

        # ORB: spot must have broken ORB high (or ORB not yet formed — pass).
        if self.orb_break is not None:
            if self.orb_break != "HIGH":
                return False

        return True

    def pe_allowed(self) -> bool:
        """Can we emit a PE (bearish) signal right now?"""
        # Multi-timeframe: both 5m and 15m must be negative.
        if self.mom_5m is not None and self.mom_15m is not None:
            if self.mom_5m > -self.rules.spot_momentum_min_pct:
                return False
            if self.mom_15m > -self.rules.spot_momentum_min_pct:
                return False
        else:
            return False

        # VWAP: spot must be below VWAP.
        if self.vwap is not None and self.spot is not None:
            if self.spot >= self.vwap:
                return False

        # ORB: spot must have broken ORB low.
        if self.orb_break is not None:
            if self.orb_break != "LOW":
                return False

        return True

    def filters_label(self, side: str) -> str:
        """Summarize which filters passed (for display)."""
        parts = []
        if side == "CE":
            if self.mom_5m is not None and self.mom_15m is not None:
                parts.append("MTF")
            if self.vwap is not None and self.spot and self.spot > self.vwap:
                parts.append("VWAP")
            if self.orb_break == "HIGH":
                parts.append("ORB")
        else:
            if self.mom_5m is not None and self.mom_15m is not None:
                parts.append("MTF")
            if self.vwap is not None and self.spot and self.spot < self.vwap:
                parts.append("VWAP")
            if self.orb_break == "LOW":
                parts.append("ORB")
        return "+".join(parts) if parts else "MOM"


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
    pa: PriceActionContext,
) -> List[Signal]:
    """Run all rules over a Dhan option-chain payload, return signals.

    Direction + price-action filters are applied BEFORE scanning strikes,
    so in choppy/sideways markets the engine stays completely silent.
    """
    out: List[Signal] = []

    ce_ok = pa.ce_allowed()
    pe_ok = pa.pe_allowed()

    if not ce_ok and not pe_ok:
        return out

    oc = chain.get("oc") or {}
    now = datetime.now(timezone.utc)

    for strike_key, sides in oc.items():
        try:
            strike = float(strike_key)
        except (TypeError, ValueError):
            continue
        ce = sides.get("ce") or {}
        pe = sides.get("pe") or {}
        if ce and ce_ok:
            out += _eval_side(underlying, expiry, _row(strike, "ce", ce), rules, risk, now, pa)
        if pe and pe_ok:
            out += _eval_side(underlying, expiry, _row(strike, "pe", pe), rules, risk, now, pa)
    return out


def _eval_side(
    underlying: str,
    expiry: str,
    r: dict,
    rules: RulesCfg,
    risk: RiskCfg,
    now: datetime,
    pa: PriceActionContext,
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
        spot=round(pa.spot, 2) if pa.spot else 0.0,
        spot_momentum_5m_pct=round(pa.mom_5m, 3) if pa.mom_5m is not None else None,
        spot_momentum_15m_pct=round(pa.mom_15m, 3) if pa.mom_15m is not None else None,
        vwap=round(pa.vwap, 2) if pa.vwap else None,
        orb_high=round(pa.orb_high, 2) if pa.orb_high else None,
        orb_low=round(pa.orb_low, 2) if pa.orb_low else None,
        ltp=r["ltp"],
        delta=r["delta"],
        iv=r["iv"],
        oi=r["oi"],
        oi_change_pct=round(r["oi_change_pct"], 2),
        price_change_pct=round(r["price_change_pct"], 2),
        filters_passed=pa.filters_label(r["side"]),
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
            note=(
                f"5m {pa.mom_5m:+.2f}% 15m {pa.mom_15m:+.2f}% | "
                f"OI +{r['oi_change_pct']:.1f}% | LTP +{r['price_change_pct']:.1f}%"
                if pa.mom_5m is not None and pa.mom_15m is not None else ""
            ),
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
            note=(
                f"5m {pa.mom_5m:+.2f}% 15m {pa.mom_15m:+.2f}% | "
                f"OI {r['oi_change_pct']:.1f}% | LTP +{r['price_change_pct']:.1f}%"
                if pa.mom_5m is not None and pa.mom_15m is not None else ""
            ),
        ))

    return sigs
