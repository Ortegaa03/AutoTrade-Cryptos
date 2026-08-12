from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from .bot import bot
from .config import CHAIN_ID, USDC_ADDRESS, WALLET, ZERO_X_API_KEY
from .logger import logs
from .ohlcv import fetch_ohlcv
from .operations import estimate_profit, ops
from .rpc_pool import rpc_pool
from .token_info import fetch_token_info
from .wallet import WalletError, get_usdc_balance_human, require_credentials, wallet_usdc_snapshot

app = FastAPI(title="AutoTrade Cryptos", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConfigureBody(BaseModel):
    token_address: str = Field(..., min_length=42, max_length=42)
    buy_price_usd: float = Field(..., gt=0)
    sell_price_usd: float = Field(..., gt=0)
    usdc_amount: float = Field(..., gt=0)
    cycle_seconds: Optional[int] = Field(default=600, ge=60)
    refresh_token: bool = True


class TokenBody(BaseModel):
    token_address: str = Field(..., min_length=42, max_length=42)


class CreateOpBody(BaseModel):
    token_address: str = Field(..., min_length=42, max_length=42)
    buy_price_usd: float = Field(..., gt=0)
    sell_price_usd: float = Field(..., gt=0)
    usdc_amount: float = Field(..., gt=0)
    demo: bool = True
    cycle_seconds: int = Field(default=600, ge=30)
    start: bool = True
    mode: str = "classic"
    grid_count: int = Field(default=0, ge=0, le=200)


class CycleConfigBody(BaseModel):
    cycle_seconds: int = Field(..., ge=30)
    cycle_minutes: Optional[float] = None


class EstimateBody(BaseModel):
    usdc_amount: float = Field(..., gt=0)
    buy_price_usd: float = Field(..., gt=0)
    sell_price_usd: float = Field(..., gt=0)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "chain_id": CHAIN_ID,
        "usdc": USDC_ADDRESS,
        "has_api_key": bool(ZERO_X_API_KEY),
        "has_wallet": bool(WALLET),
        "operations": len(ops.list()),
        "rpc": rpc_pool.status(),
    }


@app.get("/api/wallet")
async def wallet_info() -> dict[str, Any]:
    return wallet_usdc_snapshot()


@app.post("/api/rpc/probe")
async def probe_rpcs() -> dict[str, Any]:
    working = rpc_pool.probe_all(force=True)
    return {"working": working, "count": len(working), "rpc": rpc_pool.status()}


@app.post("/api/estimate")
async def estimate(body: EstimateBody) -> dict[str, Any]:
    return estimate_profit(body.usdc_amount, body.buy_price_usd, body.sell_price_usd)


@app.get("/api/operations")
async def list_operations() -> dict[str, Any]:
    await ops.ensure_all_running()
    return {"operations": ops.list()}


@app.post("/api/operations")
async def create_operation(body: CreateOpBody) -> dict[str, Any]:
    try:
        return await ops.create(
            token_address=body.token_address,
            buy_price_usd=body.buy_price_usd,
            sell_price_usd=body.sell_price_usd,
            usdc_amount=body.usdc_amount,
            demo=body.demo,
            cycle_seconds=body.cycle_seconds,
            start=body.start,
            mode=body.mode,
            grid_count=body.grid_count,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/operations/{op_id}")
async def get_operation(op_id: str) -> dict[str, Any]:
    try:
        return ops.get(op_id).to_dict()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/operations/{op_id}/pause")
async def pause_operation(op_id: str) -> dict[str, Any]:
    try:
        return await ops.get(op_id).pause()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/operations/{op_id}/resume")
async def resume_operation(op_id: str) -> dict[str, Any]:
    try:
        return await ops.get(op_id).start()
    except (KeyError, ValueError, WalletError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/operations/{op_id}/stop")
async def stop_operation(op_id: str) -> dict[str, Any]:
    try:
        return await ops.get(op_id).stop()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/operations/{op_id}/cycle")
async def cycle_operation(op_id: str) -> dict[str, Any]:
    try:
        return await ops.get(op_id).run_cycle_now()
    except (KeyError, Exception) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/operations/{op_id}/cycle-config")
async def update_cycle_config(op_id: str, body: CycleConfigBody) -> dict[str, Any]:
    try:
        seconds = body.cycle_seconds
        if body.cycle_minutes is not None:
            seconds = max(30, int(round(float(body.cycle_minutes) * 60)))
        return await ops.get(op_id).set_cycle_seconds(seconds)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/operations/{op_id}")
async def delete_operation(op_id: str) -> dict[str, Any]:
    try:
        await ops.delete(op_id)
        return {"ok": True}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/status")
async def status() -> dict[str, Any]:
    data = bot.status()
    if not bot.runtime.demo:
        try:
            data["runtime"]["usdc_balance"] = get_usdc_balance_human()
        except Exception:
            pass
    return data


@app.post("/api/token")
async def resolve_token(body: TokenBody) -> dict[str, Any]:
    try:
        return await bot.resolve_token(body.token_address)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/token/{address}")
async def get_token(address: str) -> dict[str, Any]:
    try:
        return await fetch_token_info(address)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/configure")
async def configure(body: ConfigureBody) -> dict[str, Any]:
    try:
        return await bot.configure(
            token_address=body.token_address,
            buy_price_usd=body.buy_price_usd,
            sell_price_usd=body.sell_price_usd,
            usdc_amount=body.usdc_amount,
            cycle_seconds=body.cycle_seconds,
            refresh_token=body.refresh_token,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/start")
async def start() -> dict[str, Any]:
    try:
        require_credentials()
        return await bot.start(demo=False)
    except (WalletError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/start-demo")
async def start_demo() -> dict[str, Any]:
    try:
        return await bot.start(demo=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/stop")
async def stop() -> dict[str, Any]:
    return await bot.stop()


@app.post("/api/cycle")
async def cycle_now() -> dict[str, Any]:
    try:
        return await bot.run_cycle_now()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/ohlcv")
async def ohlcv(
    pair_address: str,
    token_address: str,
    timeframe: str = "15m",
    limit: int = 200,
) -> dict[str, Any]:
    try:
        return await fetch_ohlcv(
            pair_address=pair_address,
            token_address=token_address,
            timeframe=timeframe,
            limit=limit,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/logs")
async def get_logs() -> dict[str, Any]:
    return {"logs": logs.history()}


@app.get("/api/logs/stream")
async def stream_logs(history: int = 50) -> EventSourceResponse:
    async def event_generator():
        q = logs.subscribe()
        try:
            if history > 0:
                for entry in logs.history()[-history:]:
                    yield {"event": "log", "data": json.dumps(entry)}
            while True:
                entry = await q.get()
                yield {"event": "log", "data": json.dumps(entry)}
        finally:
            logs.unsubscribe(q)

    return EventSourceResponse(event_generator())


@app.on_event("startup")
async def on_startup() -> None:
    await logs.emit(
        "AutoTrade listo · World Chain · multi-operaciones locales",
        kind="system",
        level="success",
    )
    await ops.resume_interrupted()

    async def _watchdog() -> None:
        while True:
            try:
                await ops.ensure_all_running()
            except Exception as exc:
                await logs.emit(f"Watchdog: {exc}", kind="ops", level="warn")
            await asyncio.sleep(15)

    asyncio.create_task(_watchdog(), name="ops-watchdog")
