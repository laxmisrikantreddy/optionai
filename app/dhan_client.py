"""Async wrapper around Dhan v2 option-chain endpoints.

Rate limits per Dhan v2 docs (subject to change):
  POST /optionchain             ~1 request / 3 seconds
  POST /optionchain/expirylist  ~1 request / 3 seconds

This client serializes all calls AND enforces a minimum gap of MIN_GAP_SECONDS
between any two calls, so the worker can fire requests back-to-back without
breaching Dhan's per-endpoint rate limit.

Auth headers: access-token, client-id.
"""
import asyncio
import logging
import time
from typing import Any, Dict, List

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.dhan.co/v2"


class DhanClient:
    # Slightly above Dhan's documented 1 req / 3s limit to leave a safety margin.
    MIN_GAP_SECONDS = 3.2

    def __init__(self, client_id: str, access_token: str, timeout: float = 10.0):
        self.client_id = client_id
        self.access_token = access_token
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=timeout,
            headers={
                "access-token": access_token,
                "client-id": client_id,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        # Serialize calls so we never exceed the per-second limits even if
        # multiple coroutines fire simultaneously.
        self._lock = asyncio.Lock()
        self._last_call_at = 0.0

    async def close(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        async with self._lock:
            # Enforce a minimum gap between any two calls.
            wait = self.MIN_GAP_SECONDS - (time.monotonic() - self._last_call_at)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                r = await self._client.post(path, json=body)
                # If we still get rate-limited, honour Retry-After then raise so
                # the worker logs and retries on the next cycle.
                if r.status_code == 429:
                    retry_after = float(r.headers.get("Retry-After", 5))
                    logger.warning(
                        "dhan 429 on %s; sleeping %.1fs before next call",
                        path, retry_after,
                    )
                    await asyncio.sleep(retry_after)
                r.raise_for_status()
                return r.json()
            finally:
                self._last_call_at = time.monotonic()

    async def expiry_list(self, scrip: int, segment: str) -> List[str]:
        data = await self._post(
            "/optionchain/expirylist",
            {"UnderlyingScrip": scrip, "UnderlyingSeg": segment},
        )
        return data.get("data", []) or []

    async def option_chain(
        self, scrip: int, segment: str, expiry: str
    ) -> Dict[str, Any]:
        data = await self._post(
            "/optionchain",
            {
                "UnderlyingScrip": scrip,
                "UnderlyingSeg": segment,
                "Expiry": expiry,
            },
        )
        return data.get("data", {}) or {}
