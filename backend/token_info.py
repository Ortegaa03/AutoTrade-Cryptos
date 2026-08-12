from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional, Tuple

import httpx

from .config import CHAIN_NAME, DEXSCREENER_TOKEN_URL

GECKO_BASE = "https://api.geckoterminal.com/api/v2"
GECKO_NETWORK = "world-chain"

_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_CACHE_TTL_SEC = 10 * 60  # alineado con chart: 10 min
_LOCK = asyncio.Lock()


def _cache_get(address: str) -> Optional[Dict[str, Any]]:
    key = address.lower()
    hit = _CACHE.get(key)
    if not hit:
        return None
    ts, data = hit
    if time.monotonic() - ts > _CACHE_TTL_SEC:
        return None
    return dict(data)


def _cache_set(address: str, data: Dict[str, Any]) -> None:
    _CACHE[address.lower()] = (time.monotonic(), dict(data))


def _stale(address: str) -> Optional[Dict[str, Any]]:
    hit = _CACHE.get(address.lower())
    if not hit:
        return None
    data = dict(hit[1])
    data["_stale"] = True
    return data


async def _http_get(client: httpx.AsyncClient, url: str, params: Optional[dict] = None) -> httpx.Response:
    last: Exception | None = None
    for attempt in range(3):
        try:
            resp = await client.get(url, params=params)
            if resp.status_code == 429:
                await asyncio.sleep(1.2 * (attempt + 1))
                last = RuntimeError(f"429 on {url}")
                continue
            return resp
        except httpx.HTTPError as exc:
            last = exc
            await asyncio.sleep(0.6 * (attempt + 1))
    raise last or RuntimeError("HTTP failed")


async def _from_gecko(address: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    token_url = f"{GECKO_BASE}/networks/{GECKO_NETWORK}/tokens/{address}"
    pools_url = f"{GECKO_BASE}/networks/{GECKO_NETWORK}/tokens/{address}/pools"

    token_resp = await _http_get(client, token_url)
    if token_resp.status_code >= 400:
        raise ValueError(f"Gecko token {token_resp.status_code}")
    attrs = (token_resp.json().get("data") or {}).get("attributes") or {}

    pair_address = None
    liquidity = 0.0
    volume_24h = 0.0
    price_change = 0.0
    pools_resp = await _http_get(client, pools_url, params={"page": 1})
    if pools_resp.status_code < 400:
        pools = pools_resp.json().get("data") or []
        best = None
        best_liq = -1.0
        for p in pools:
            a = p.get("attributes") or {}
            try:
                liq = float(a.get("reserve_in_usd") or 0)
            except (TypeError, ValueError):
                liq = 0.0
            if liq > best_liq:
                best_liq = liq
                best = a
        if best:
            pair_address = best.get("address")
            liquidity = best_liq
            try:
                volume_24h = float((best.get("volume_usd") or {}).get("h24") or 0)
            except (TypeError, ValueError):
                volume_24h = 0.0
            try:
                price_change = float((best.get("price_change_percentage") or {}).get("h24") or 0)
            except (TypeError, ValueError):
                price_change = 0.0

    try:
        price = float(attrs["price_usd"]) if attrs.get("price_usd") is not None else None
    except (TypeError, ValueError):
        price = None

    return {
        "address": address,
        "name": attrs.get("name") or "Unknown",
        "symbol": attrs.get("symbol") or "???",
        "image": attrs.get("image_url"),
        "price_usd": price,
        "pair_address": pair_address,
        "dex_id": "geckoterminal",
        "url": f"https://www.geckoterminal.com/{GECKO_NETWORK}/pools/{pair_address}" if pair_address else None,
        "liquidity_usd": liquidity,
        "volume_24h": volume_24h,
        "price_change_24h": price_change,
        "chart_embed": None,
        "source": "gecko",
    }


def _parse_dex_pairs(address: str, pairs: Any) -> Dict[str, Any]:
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("Token no encontrado en DexScreener (World Chain)")

    def liq(p: Dict[str, Any]) -> float:
        try:
            return float((p.get("liquidity") or {}).get("usd") or 0)
        except (TypeError, ValueError):
            return 0.0

    best = sorted(pairs, key=liq, reverse=True)[0]
    base = best.get("baseToken") or {}
    quote = best.get("quoteToken") or {}
    token_meta = quote if (quote.get("address") or "").lower() == address.lower() else base

    price_usd = best.get("priceUsd")
    try:
        price = float(price_usd) if price_usd is not None else None
    except (TypeError, ValueError):
        price = None

    info = best.get("info") or {}
    image = info.get("imageUrl") or info.get("header") or None

    return {
        "address": address,
        "name": token_meta.get("name") or "Unknown",
        "symbol": token_meta.get("symbol") or "???",
        "image": image,
        "price_usd": price,
        "pair_address": best.get("pairAddress"),
        "dex_id": best.get("dexId"),
        "url": best.get("url"),
        "liquidity_usd": liq(best),
        "volume_24h": float((best.get("volume") or {}).get("h24") or 0),
        "price_change_24h": float((best.get("priceChange") or {}).get("h24") or 0),
        "chart_embed": (
            f"https://dexscreener.com/{CHAIN_NAME}/{best.get('pairAddress')}"
            "?embed=1&theme=dark&trades=0&info=0"
            if best.get("pairAddress")
            else None
        ),
        "source": "dexscreener",
    }


async def _from_dex(address: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    url = DEXSCREENER_TOKEN_URL.format(chain=CHAIN_NAME, address=address)
    resp = await _http_get(client, url)
    if resp.status_code == 429:
        raise ValueError("DexScreener 429")
    resp.raise_for_status()
    return _parse_dex_pairs(address, resp.json())


async def fetch_token_info(
    token_address: str,
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """GeckoTerminal primero; si falla → DexScreener. Cache 10 min."""
    address = token_address.strip()
    if not force:
        cached = _cache_get(address)
        if cached:
            return cached

    async with _LOCK:
        if not force:
            cached = _cache_get(address)
            if cached:
                return cached

        errors: list[str] = []
        async with httpx.AsyncClient(timeout=25.0, headers={"Accept": "application/json"}) as client:
            # 1) Gecko
            try:
                info = await _from_gecko(address, client)
                if info.get("price_usd") is None and not info.get("pair_address"):
                    raise ValueError("Gecko sin precio/pair")
                _cache_set(address, info)
                return info
            except Exception as exc:
                errors.append(f"gecko: {exc}")

            # 2) Dex fallback
            try:
                info = await _from_dex(address, client)
                _cache_set(address, info)
                return info
            except Exception as exc:
                errors.append(f"dex: {exc}")

        stale = _stale(address)
        if stale:
            stale["_errors"] = errors
            return stale

        raise ValueError("No se pudo resolver el token (Gecko y Dex fallaron): " + " | ".join(errors))


async def fetch_price_usd(token_address: str) -> Optional[float]:
    info = await fetch_token_info(token_address)
    return info.get("price_usd")
