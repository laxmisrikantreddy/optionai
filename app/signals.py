"""Signal model and rule engine.

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
from datetime import datetime, timezone
from typing import Dict, List

from pydantic import BaseModel

from .config import RiskCfg, RulesCfg
from .risk import sl_target


class Signal(BaseModel):
    timestamp: datetime
    underlying: str
    expiry: str
    side: str            # "CE" or "PE"
    strike: float
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
) -> List[Signal]:
    """Run all rules over a Dhan option-chain payload, return signals."""
    out: List[Signal] = []
    oc = chain.get("oc") or {}
    now = datetime.now(timezone.utc)

    for strike_key, sides in oc.items():
        try:
            strike = float(strike_key)
        except (TypeError, ValueError):
            continue
        ce = sides.get("ce") or {}
        pe = sides.get("pe") or {}
        if ce:
            out += _eval_side(underlying, expiry, _row(strike, "ce", ce), rules, risk, now)
        if pe:
            out += _eval_side(underlying, expiry, _row(strike, "pe", pe), rules, risk, now)
    return out


def _eval_side(
    underlying: str,
    expiry: str,
    r: dict,
    rules: RulesCfg,
    risk: RiskCfg,
    now: datetime,
) -> List[Signal]:
    sigs: List[Signal] = []

    # Delta filter: only ATM-ish strikes with decent gamma.
    abs_delta = abs(r["delta"])
    if not (rules.delta_min <= abs_delta <= rules.delta_max):
        return sigs
    if r["ltp"] <= 0:
        return sigs

    # Rule 1: long buildup on this side (price up + OI up). Buy this side.
    if (
        r["oi_change_pct"] >= rules.oi_change_pct_min
        and r["price_change_pct"] >= rules.price_change_pct_min
    ):
        sl, tgt = sl_target(r["ltp"], risk.sl_pct, risk.rr)
        sigs.append(
            Signal(
                timestamp=now,
                underlying=underlying,
                expiry=expiry,
                side=r["side"],
                strike=r["strike"],
                ltp=r["ltp"],
                sl=sl,
                target=tgt,
                delta=r["delta"],
                iv=r["iv"],
                oi=r["oi"],
                oi_change_pct=round(r["oi_change_pct"], 2),
                price_change_pct=round(r["price_change_pct"], 2),
                rule="LONG_BUILDUP",
                note=f"OI +{r['oi_change_pct']:.1f}%, LTP +{r['price_change_pct']:.1f}%",
            )
        )

    # Rule 2: short covering on this side (price up + OI dropping). Buy this side.
    if (
        r["oi_change_pct"] <= -rules.short_cover_oi_drop_pct
        and r["price_change_pct"] >= rules.price_change_pct_min
    ):
        sl, tgt = sl_target(r["ltp"], risk.sl_pct, risk.rr)
        sigs.append(
            Signal(
                timestamp=now,
                underlying=underlying,
                expiry=expiry,
                side=r["side"],
                strike=r["strike"],
                ltp=r["ltp"],
                sl=sl,
                target=tgt,
                delta=r["delta"],
                iv=r["iv"],
                oi=r["oi"],
                oi_change_pct=round(r["oi_change_pct"], 2),
                price_change_pct=round(r["price_change_pct"], 2),
                rule="SHORT_COVERING",
                note=f"OI {r['oi_change_pct']:.1f}%, LTP +{r['price_change_pct']:.1f}%",
            )
        )

    return sigs
