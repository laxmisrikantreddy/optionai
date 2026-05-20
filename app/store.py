"""Thread-safe in-memory signal store with time-based deduplication."""
from collections import deque
from datetime import timedelta
from threading import Lock
from typing import Deque, List

from .config import settings
from .signals import Signal


class SignalStore:
    def __init__(self, capacity: int = 500, dedup_window_seconds: int = 300):
        self._buf: Deque[Signal] = deque(maxlen=capacity)
        self._lock = Lock()
        self._dedup_window = timedelta(seconds=dedup_window_seconds)

    def _is_duplicate(self, sig: Signal) -> bool:
        cutoff = sig.timestamp - self._dedup_window
        for existing in reversed(self._buf):
            if existing.timestamp < cutoff:
                return False
            if (
                existing.underlying == sig.underlying
                and existing.strike == sig.strike
                and existing.side == sig.side
                and existing.rule == sig.rule
            ):
                return True
        return False

    def add(self, sig: Signal) -> bool:
        """Return True if added, False if suppressed as duplicate."""
        with self._lock:
            if self._is_duplicate(sig):
                return False
            self._buf.append(sig)
            return True

    def list(self, limit: int = 100) -> List[Signal]:
        with self._lock:
            items = list(self._buf)
        # Newest first.
        return list(reversed(items))[:limit]


store = SignalStore(
    capacity=settings.max_signals_in_store,
    dedup_window_seconds=settings.signal_dedup_window_seconds,
)
