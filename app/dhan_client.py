"""Async wrapper around Dhan v2 option-chain endpoints.

Rate limits per Dhan v2 docs (subject to change):
  POST /optionchain             ~1 request / 3 seconds
  POST /optionchain/expirylist  ~1 request / 3 seconds

Auth headers: access-token, client-id.
"""
import asyncio
import logging
from typing import Any, Dict, List

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.dhan.co/v2"


class DhanClient:
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

    async def close(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        async with self._lock:
            r = await self._client.post(path, json=body)
            r.raise_for_status()
            return r.json()

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
