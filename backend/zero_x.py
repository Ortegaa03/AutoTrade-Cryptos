from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from .config import CHAIN_ID, ZERO_X_API_KEY, ZERO_X_BASE
from .logger import logs


class ZeroXError(RuntimeError):
    pass


def _headers() -> Dict[str, str]:
    if not ZERO_X_API_KEY:
        raise ZeroXError("Falta ZERO_X_API_KEY en .env")
    return {
        "0x-api-key": ZERO_X_API_KEY,
        "0x-version": "v2",
        "Accept": "application/json",
    }


async def get_price(
    *,
    sell_token: str,
    buy_token: str,
    sell_amount: str,
    taker: Optional[str] = None,
) -> Dict[str, Any]:
    params: Dict[str, str] = {
        "chainId": str(CHAIN_ID),
        "sellToken": sell_token,
        "buyToken": buy_token,
        "sellAmount": sell_amount,
    }
    if taker:
        params["taker"] = taker

    url = f"{ZERO_X_BASE}/swap/allowance-holder/price"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, params=params, headers=_headers())
        data = resp.json()
        if resp.status_code >= 400:
            raise ZeroXError(f"0x price error {resp.status_code}: {data}")
        return data


async def get_quote(
    *,
    sell_token: str,
    buy_token: str,
    sell_amount: str,
    taker: str,
) -> Dict[str, Any]:
    params = {
        "chainId": str(CHAIN_ID),
        "sellToken": sell_token,
        "buyToken": buy_token,
        "sellAmount": sell_amount,
        "taker": taker,
    }
    url = f"{ZERO_X_BASE}/swap/allowance-holder/quote"
    await logs.emit("Pediendo quote firme a 0x…", kind="swap", level="info")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, params=params, headers=_headers())
        data = resp.json()
        if resp.status_code >= 400:
            raise ZeroXError(f"0x quote error {resp.status_code}: {data}")
        return data