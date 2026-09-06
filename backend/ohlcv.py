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


def _candle_span_days(candles: List[Dict[str, Any]]) -> float:
    if len(candles) < 2:
        return 0.0
    return (int(candles[-1]["time"]) - int(candles[0]["time"])) / 86400.0


def _is_usable_max(candles: List[Dict[str, Any]]) -> bool:
    """Rechaza series cortas (~24h) que no son historial máximo."""
    if len(candles) < 40:
        return False
    return _candle_span_days(candles) >= 7.0


async def fetch_ohlcv(
    *,
    pair_address: str,
    token_address: str,
    timeframe: str = "max",
    limit: int = 1000,
) -> Dict[str, Any]:
    """
    OHLCV vía GeckoTerminal con cache local 10 min.
    timeframe=max → velas diarias (todo el histórico del pool, 1 request).
    Si el fetch falla, devuelve cache caducado si existe.
    """
    tf = timeframe.lower().replace(" ", "")
    # v3: max = day (invalida caches viejos de ~24h/hora)
    cache_key = f"{pair_address.lower()}:{token_address.lower()}:{tf}:v3:{limit}"

    def _accept_cached(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        candles = payload.get("candles")
        if not isinstance(candles, list):
            return None
        if tf in ("max", "all", "full") and not _is_usable_max(candles):
            return None
        return payload

    mem = _MEM.get(cache_key)
    if mem and time.monotonic() - mem[0] < CACHE_TTL_SEC:
        accepted = _accept_cached(mem[1])
        if accepted:
            out = dict(accepted)
            out["cache"] = "memory"
            out["cache_ttl_sec"] = CACHE_TTL_SEC
            return out

    disk_fresh = _fresh_from_disk(cache_key)
    if disk_fresh:
        accepted = _accept_cached(disk_fresh)
        if accepted:
            _MEM[cache_key] = (time.monotonic(), accepted)
            return accepted

    async with _LOCK:
        mem = _MEM.get(cache_key)
        if mem and time.monotonic() - mem[0] < CACHE_TTL_SEC:
            accepted = _accept_cached(mem[1])
            if accepted:
                out = dict(accepted)
                out["cache"] = "memory"
                return out
        disk_fresh = _fresh_from_disk(cache_key)
        if disk_fresh:
            accepted = _accept_cached(disk_fresh)
            if accepted:
                _MEM[cache_key] = (time.monotonic(), accepted)
                return accepted

        # max = día completo del pool (hasta 1000 velas ≈ años de historia)
        if tf in ("max", "all", "full"):
            endpoint = "day"
            aggregate = 1
            page_limit = 1000
            paginate = True
        elif tf in ("24h",):
            endpoint = "hour"
            aggregate = 1
            page_limit = min(max(limit, 24), 168)
            paginate = False
        elif tf in ("7d", "7day", "week"):
            endpoint = "hour"
            aggregate = 4
            page_limit = min(max(limit, 42), 1000)
            paginate = False
        elif tf in ("31d", "30d", "month"):
            endpoint = "day"
            aggregate = 1
            page_limit = min(max(limit, 31), 365)
            paginate = False
        elif tf in ("1m", "5m", "15m"):
            endpoint = "minute"
            aggregate = {"1m": 1, "5m": 5, "15m": 15}[tf]
            page_limit = min(max(limit, 100), 1000)
            paginate = False
        elif tf in ("1h", "4h"):
            endpoint = "hour"
            aggregate = {"1h": 1, "4h": 4}[tf]
            page_limit = min(max(limit, 100), 1000)
            paginate = False
        elif tf in ("1d", "d", "day"):
            endpoint = "day"
            aggregate = 1
            page_limit = min(max(limit, 100), 1000)
            paginate = False
        else:
            endpoint = "day"
            aggregate = 1
            page_limit = 1000
            paginate = True

        try:
            side = await resolve_pool_side(pair_address, token_address)
            url = f"{GECKO_BASE}/networks/{NETWORK}/pools/{pair_address}/ohlcv/{endpoint}"
            all_rows: List[List[float]] = []
            before_ts: Optional[int] = None
            max_pages = 3 if paginate else 1

            async with httpx.AsyncClient(timeout=45.0, headers={"Accept": "application/json"}) as client:
                for page_i in range(max_pages):
                    params: Dict[str, Any] = {
                        "aggregate": aggregate,
                        "limit": page_limit,
                        "currency": "usd",
                        "token": side,
                    }
                    if before_ts is not None:
                        params["before_timestamp"] = before_ts
                    raw = await _get_json(client, url, params)
                    rows: List[List[float]] = (
                        ((raw.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
                    )
                    if not rows:
                        break
                    all_rows.extend(rows)
                    oldest = min(int(r[0]) for r in rows if r and len(r) > 0)
                    if before_ts is not None and oldest >= before_ts:
                        break
                    before_ts = oldest
                    if not paginate or len(rows) < page_limit:
                        break
                    if page_i + 1 < max_pages:
                        await asyncio.sleep(1.2)

            # Deduplicar por timestamp y ordenar asc
            by_ts: Dict[int, List[float]] = {}
            for row in all_rows:
                if not row or len(row) < 5:
                    continue
                by_ts[int(row[0])] = row

            candles = []
            for ts in sorted(by_ts.keys()):
                row = by_ts[ts]
                o, h, l, c = row[1], row[2], row[3], row[4]
                vol = row[5] if len(row) > 5 else 0
                candles.append(
                    {
                        "time": ts,
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
                "resolution": endpoint,
                "side": side,
                "source": "geckoterminal",
                "candles": candles,
                "span_days": round(_candle_span_days(candles), 2),
                "cache": "miss",
                "cache_ttl_sec": CACHE_TTL_SEC,
            }
            if tf in ("max", "all", "full") and not _is_usable_max(candles):
                raise RuntimeError(
                    f"OHLCV max demasiado corto ({len(candles)} velas, "
                    f"{payload['span_days']}d) — se esperaba historial diario"
                )
            _MEM[cache_key] = (time.monotonic(), payload)
            _write_disk(cache_key, payload)
            return payload
        except Exception as exc:
            stale = _stale_from_disk(cache_key)
            if stale and _accept_cached(stale):
                stale["cache_error"] = str(exc)
                return stale
            raise
