from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

import httpx

from .config import ROOT

GECKO_BASE = "https://api.geckoterminal.com/api/v2"
NETWORK = "world-chain"
CACHE_DIR = ROOT / "data" / "ohlcv_cache"
CACHE_TTL_SEC = 10 * 60  # 10 minutos

_MEM: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_LOCK = asyncio.Lock()


def _cache_path(key: str) -> Path:
    safe = key.replace(":", "_").replace("/", "_")
    return CACHE_DIR / f"{safe}.json"


def _read_disk(key: str) -> Optional[Dict[str, Any]]:
    path = _cache_path(key)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw
    except Exception:
        return None


def _write_disk(key: str, payload: Dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(key)
    envelope = {
        "saved_at": time.time(),
        "payload": payload,
    }
    path.write_text(json.dumps(envelope), encoding="utf-8")


def _fresh_from_disk(key: str) -> Optional[Dict[str, Any]]:
    envelope = _read_disk(key)
    if not envelope:
        return None
    age = time.time() - float(envelope.get("saved_at") or 0)
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        return None
    if age <= CACHE_TTL_SEC:
        out = dict(payload)
        out["cache"] = "hit"
        out["cache_age_sec"] = int(age)
        out["cache_ttl_sec"] = CACHE_TTL_SEC
        return out
    return None


def _stale_from_disk(key: str) -> Optional[Dict[str, Any]]:
    envelope = _read_disk(key)
    if not envelope:
        return None
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        return None
    age = time.time() - float(envelope.get("saved_at") or 0)
    out = dict(payload)
    out["cache"] = "stale"
    out["cache_age_sec"] = int(age)
    out["cache_ttl_sec"] = CACHE_TTL_SEC
    return out


async def _get_json(client: httpx.AsyncClient, url: str, params: Optional[dict] = None) -> dict:
    last: Exception | None = None
    for attempt in range(4):
        resp = await client.get(url, params=params)
        if resp.status_code == 429:
            await asyncio.sleep(1.5 * (attempt + 1))
            last = RuntimeError("GeckoTerminal 429")
            continue
        resp.raise_for_status()
        return resp.json()
    raise last or RuntimeError("GeckoTerminal request failed")


async def resolve_pool_side(pair_address: str, token_address: str) -> Literal["base", "quote"]:
    url = f"{GECKO_BASE}/networks/{NETWORK}/pools/{pair_address}"
    async with httpx.AsyncClient(timeout=25.0, headers={"Accept": "application/json"}) as client:
        data = await _get_json(client, url)
    rel = (data.get("data") or {}).get("relationships") or {}
    base_id = ((rel.get("base_token") or {}).get("data") or {}).get("id") or ""
    quote_id = ((rel.get("quote_token") or {}).get("data") or {}).get("id") or ""
    token = token_address.lower()
    if token in base_id.lower():
        return "base"
    if token in quote_id.lower():
        return "quote"
    return "quote"


async def fetch_ohlcv(
    *,
    pair_address: str,
    token_address: str,
    timeframe: str = "24h",
    limit: int = 200,
) -> Dict[str, Any]:
    """
    OHLCV vía GeckoTerminal con cache local 10 min.
    Si el fetch falla, devuelve cache caducado si existe.
    """
    cache_key = f"{pair_address.lower()}:{token_address.lower()}:{timeframe}:{limit}"

    mem = _MEM.get(cache_key)
    if mem and time.monotonic() - mem[0] < CACHE_TTL_SEC:
        out = dict(mem[1])
        out["cache"] = "memory"
        out["cache_ttl_sec"] = CACHE_TTL_SEC
        return out

    disk_fresh = _fresh_from_disk(cache_key)
    if disk_fresh:
        _MEM[cache_key] = (time.monotonic(), disk_fresh)
        return disk_fresh

    async with _LOCK:
        mem = _MEM.get(cache_key)
        if mem and time.monotonic() - mem[0] < CACHE_TTL_SEC:
            out = dict(mem[1])
            out["cache"] = "memory"
            return out
        disk_fresh = _fresh_from_disk(cache_key)
        if disk_fresh:
            _MEM[cache_key] = (time.monotonic(), disk_fresh)
            return disk_fresh

        tf = timeframe.lower().replace(" ", "")
        # Vistas UI: 24h / 7d / 31d
        if tf in ("24h",):
            endpoint = "hour"
            aggregate = 1
            limit = min(max(limit, 24), 48)
        elif tf in ("7d", "7day", "week"):
            endpoint = "hour"
            aggregate = 4
            limit = min(max(limit, 42), 168)
        elif tf in ("31d", "30d", "month"):
            endpoint = "day"
            aggregate = 1
            limit = min(max(limit, 31), 60)
        elif tf in ("1m", "5m", "15m"):
            endpoint = "minute"
            aggregate = {"1m": 1, "5m": 5, "15m": 15}[tf]
        elif tf in ("1h", "4h"):
            endpoint = "hour"
            aggregate = {"1h": 1, "4h": 4}[tf]
        elif tf in ("1d", "d", "day"):
            endpoint = "day"
            aggregate = 1
        else:
            endpoint = "hour"
            aggregate = 1
            limit = 24

        try:
            side = await resolve_pool_side(pair_address, token_address)
            url = f"{GECKO_BASE}/networks/{NETWORK}/pools/{pair_address}/ohlcv/{endpoint}"
            params = {
                "aggregate": aggregate,
                "limit": min(limit, 1000),
                "currency": "usd",
                "token": side,
            }
            async with httpx.AsyncClient(timeout=30.0, headers={"Accept": "application/json"}) as client:
                raw = await _get_json(client, url, params)

            rows: List[List[float]] = (
                ((raw.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
            )
            candles = []
            for row in reversed(rows):
                if not row or len(row) < 5:
                    continue
                ts, o, h, l, c = row[0], row[1], row[2], row[3], row[4]
                vol = row[5] if len(row) > 5 else 0
                candles.append(
                    {
                        "time": int(ts),
                        "open": float(o),
                        "high": float(h),
                        "low": float(l),
                        "close": float(c),
                        "volume": float(vol or 0),
                    }
                )

            payload = {
                "pair_address": pair_address,
                "token_address": token_address,
                "network": NETWORK,
                "timeframe": timeframe,
                "side": side,
                "source": "geckoterminal",
                "candles": candles,
                "cache": "miss",
                "cache_ttl_sec": CACHE_TTL_SEC,
            }
            _MEM[cache_key] = (time.monotonic(), payload)
            _write_disk(cache_key, payload)
            return payload
        except Exception as exc:
            stale = _stale_from_disk(cache_key)
            if stale:
                stale["cache_error"] = str(exc)
                return stale
            raise
