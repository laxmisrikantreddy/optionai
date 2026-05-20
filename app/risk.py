"""Stop-loss and target calculation for option BUY trades."""
from typing import Tuple


def sl_target(entry: float, sl_pct: float, rr: float) -> Tuple[float, float]:
    """Return (sl, target) prices for an option BUY.

    sl_pct: fraction of premium risked (0.30 -> SL is 30% below entry)
    rr:     risk-reward multiple; target = entry * (1 + sl_pct * rr)
    """
    sl = max(entry * (1 - sl_pct), 0.05)
    target = entry * (1 + sl_pct * rr)
    return round(sl, 2), round(target, 2)
