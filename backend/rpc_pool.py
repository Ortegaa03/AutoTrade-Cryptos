from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import List, Optional

import httpx
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from .config import ROOT, WORLDCHAIN_RPC_URL

# World Chain tiene pocas RPCs públicas free reales.
# Arrancamos con las ya verificadas; el resto se prueban en background.
VERIFIED_RPCS: List[str] = [
    "https://worldchain-mainnet.g.alchemy.com/public",
    "https://480.rpc.thirdweb.com",
    "https://worldchain-mainnet.gateway.tenderly.co",
    "https://worldchain.drpc.org",
    "https://api.uniblock.dev/uni/v1/json-rpc?chainId=480",
]

CANDIDATE_RPCS: List[str] = VERIFIED_RPCS + [
    "https://rpc.ankr.com/worldchain",
    "https://rpc.ankr.com/world_chain",
    "https://1rpc.io/worldchain",
    "https://worldchain.publicnode.com",
    "https://worldchain-rpc.publicnode.com",
    "https://rpc.publicnode.com/worldchain",
    "https://worldchain-mainnet.public.blastapi.io",
    "https://endpoints.omniatech.io/v1/worldchain/mainnet/public",
    "https://lb.drpc.live/ogrpc?network=worldchain",
    "https://world-chain.drpc.org",
]

POOL_FILE = ROOT / "data" / "rpc_pool.json"
CHAIN_ID = 480
CHAIN_ID_HEX = hex(CHAIN_ID)


class RpcPool:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._working: List[str] = list(VERIFIED_RPCS)
        self._index = 0
        self._last_probe = 0.0
        self._bootstrap()
        # probe no bloqueante
        threading.Thread(target=self._bg_probe, daemon=True).start()

    def _extra_from_env(self) -> List[str]:
        import os

        raw = os.getenv("WORLDCHAIN_RPC_URLS", "").strip()
        extras: List[str] = []
        if WORLDCHAIN_RPC_URL:
            extras.append(WORLDCHAIN_RPC_URL)
        if raw:
            extras.extend([u.strip() for u in raw.split(",") if u.strip()])
        return extras

    def _all_candidates(self) -> List[str]:
        seen = set()
        out: List[str] = []
        for u in self._extra_from_env() + CANDIDATE_RPCS:
            if u.startswith("http") and u not in seen:
                seen.add(u)
                out.append(u)
        if POOL_FILE.exists():
            try:
                data = json.loads(POOL_FILE.read_text(encoding="utf-8"))
                for u in (data.get("candidates") or []) + (data.get("working") or []):
                    if isinstance(u, str) and u.startswith("http") and u not in seen:
                        seen.add(u)
                        out.append(u)
            except Exception:
                pass
        return out

    def _probe_one_http(self, url: str, timeout: float = 3.5) -> bool:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, json=payload)
                if resp.status_code >= 400:
                    return False
                data = resp.json()
                result = data.get("result")
                if not result:
                    return False
                return int(result, 16) == CHAIN_ID
        except Exception:
            return False

    def _bootstrap(self) -> None:
        extras = self._extra_from_env()
        seed = []
        for u in extras + VERIFIED_RPCS:
            if u not in seed:
                seed.append(u)
        # quick check only first 3 to avoid startup hang
        ok: List[str] = []
        for u in seed[:5]:
            if self._probe_one_http(u, timeout=2.5):
                ok.append(u)
        with self._lock:
            self._working = ok or list(VERIFIED_RPCS)
            self._index = 0
            self._last_probe = time.time()
            self._save()

    def _bg_probe(self) -> None:
        try:
            time.sleep(0.2)
            self.probe_all(force=True)
        except Exception:
            pass

    def probe_all(self, *, force: bool = False) -> List[str]:
        now = time.time()
        if not force and self._working and now - self._last_probe < 300:
            return list(self._working)
        working: List[str] = []
        for url in self._all_candidates():
            if self._probe_one_http(url):
                working.append(url)
        with self._lock:
            self._working = working or list(VERIFIED_RPCS)
            self._index = 0
            self._last_probe = now
            self._save()
        return list(self._working)

    def _save(self) -> None:
        POOL_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "working": self._working,
            "candidates": self._all_candidates(),
            "probed_at": time.time(),
            "note": (
                "World Chain solo expone pocas RPCs públicas free verificables. "
                "Añade más en WORLDCHAIN_RPC_URLS (coma-separadas)."
            ),
        }
        POOL_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def current(self) -> str:
        with self._lock:
            if not self._working:
                return WORLDCHAIN_RPC_URL
            return self._working[self._index % len(self._working)]

    def mark_bad(self, url: Optional[str] = None) -> str:
        with self._lock:
            bad = url or (self._working[self._index] if self._working else None)
            if bad and bad in self._working and len(self._working) > 1:
                self._working = [u for u in self._working if u != bad]
            if self._working:
                self._index = (self._index + 1) % len(self._working)
                nxt = self._working[self._index]
            else:
                nxt = WORLDCHAIN_RPC_URL
            self._save()
            return nxt

    def get_web3(self) -> Web3:
        last_err: Exception | None = None
        tried = set()
        attempts = max(3, len(self._working) or 1)
        for _ in range(attempts):
            url = self.current()
            if url in tried and len(tried) >= len(self._working or [url]):
                break
            tried.add(url)
            try:
                w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 12}))
                try:
                    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
                except Exception:
                    pass
                if not w3.is_connected():
                    raise RuntimeError("not connected")
                if int(w3.eth.chain_id) != CHAIN_ID:
                    raise RuntimeError(f"bad chain id on {url}")
                _ = w3.eth.block_number
                return w3
            except Exception as exc:
                last_err = exc
                self.mark_bad(url)
        raise RuntimeError(f"Ningún RPC de World Chain disponible: {last_err}")

    def status(self) -> dict:
        with self._lock:
            working = list(self._working)
            idx = self._index
            last = self._last_probe
        current = working[idx % len(working)] if working else WORLDCHAIN_RPC_URL
        return {
            "working": working,
            "current": current,
            "candidates": len(self._all_candidates()),
            "working_count": len(working),
            "last_probe": last,
        }


rpc_pool = RpcPool()
