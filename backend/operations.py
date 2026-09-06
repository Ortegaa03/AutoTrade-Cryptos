from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import (
    CYCLE_SECONDS,
    OPERATIONS_FILE,
    USDC_ADDRESS,
    USDC_DECIMALS,
)
from .grid import build_grid_levels, estimate_grid
from .logger import logs
from .token_info import fetch_price_usd, fetch_token_info
from .wallet import (
    WalletError,
    approve_and_swap,
    get_balance,
    get_token_decimals,
    require_credentials,
)
from .zero_x import ZeroXError, get_quote


class OpStatus(str, Enum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    STOPPED = "stopped"


class Phase(str, Enum):
    IDLE = "idle"
    WAITING_BUY = "waiting_buy"
    HOLDING = "holding"
    WAITING_SELL = "waiting_sell"
    GRID = "grid"


def estimate_profit(usdc_amount: float, buy_price: float, sell_price: float) -> Dict[str, float]:
    if usdc_amount <= 0 or buy_price <= 0 or sell_price <= 0:
        return {"tokens": 0, "total_usd": 0, "profit_usd": 0, "roi_pct": 0}
    tokens = usdc_amount / buy_price
    total = tokens * sell_price
    profit = total - usdc_amount
    roi = (profit / usdc_amount) * 100
    return {
        "tokens": tokens,
        "total_usd": total,
        "profit_usd": profit,
        "roi_pct": roi,
    }


@dataclass
class Operation:
    id: str
    token_address: str
    buy_price_usd: float
    sell_price_usd: float
    usdc_amount: float
    demo: bool = True
    cycle_seconds: int = CYCLE_SECONDS
    status: str = OpStatus.DRAFT.value
    phase: str = Phase.WAITING_BUY.value
    token: Dict[str, Any] = field(default_factory=dict)
    last_price_usd: Optional[float] = None
    bought_token_amount_raw: int = 0
    buy_amount_out: Optional[float] = None  # tokens recibidos (human)
    sell_amount_out: Optional[float] = None  # USDC recibidos (human)
    buy_tx: Optional[str] = None
    sell_tx: Optional[str] = None
    demo_usdc_balance: float = 1000.0
    estimated: Dict[str, float] = field(default_factory=dict)
    realized_profit_usd: Optional[float] = None
    realized_total_usd: Optional[float] = None
    error: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    completed_at: Optional[str] = None
    last_cycle_at: Optional[str] = None
    events: List[Dict[str, Any]] = field(default_factory=list)
    mode: str = "classic"  # classic | grid
    grid_count: int = 0
    grid_levels: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now
        if not self.estimated:
            if self.mode == "grid" and self.grid_count >= 2:
                self.estimated = estimate_grid(
                    self.usdc_amount,
                    self.buy_price_usd,
                    self.sell_price_usd,
                    self.grid_count,
                    self.cycle_seconds,
                )
            else:
                self.estimated = estimate_profit(
                    self.usdc_amount, self.buy_price_usd, self.sell_price_usd
                )
        if not self.events:
            self.events = []
        if self.grid_levels is None:
            self.grid_levels = []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_event(
    op: Operation,
    *,
    kind: str,
    title: str,
    detail: str = "",
    data: Optional[Dict[str, Any]] = None,
) -> None:
    op.events.append(
        {
            "id": str(uuid.uuid4())[:8],
            "ts": _now(),
            "kind": kind,
            "title": title,
            "detail": detail,
            "data": data or {},
        }
    )
    # keep last 400 events (ciclos frecuentes)
    if len(op.events) > 400:
        op.events = op.events[-400:]
    op.updated_at = _now()


class OperationRunner:
    def __init__(self, op: Operation, manager: "OperationManager") -> None:
        self.op = op
        self.manager = manager
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._spawn_lock = asyncio.Lock()

    def to_dict(self) -> Dict[str, Any]:
        self._hydrate_amounts()
        d = asdict(self.op)
        d["running"] = bool(
            self.op.status == OpStatus.RUNNING.value
            and self._task is not None
            and not self._task.done()
        )
        d["task_alive"] = d["running"]
        return d

    def _hydrate_amounts(self) -> None:
        """Rellena amount_out desde events si falta (ops creadas con código antiguo)."""
        if self.op.buy_amount_out is None:
            for ev in reversed(self.op.events or []):
                if ev.get("kind") != "buy":
                    continue
                data = ev.get("data") or {}
                raw = data.get("amount_out", data.get("tokens"))
                if raw is not None:
                    try:
                        self.op.buy_amount_out = float(raw)
                    except (TypeError, ValueError):
                        pass
                break
        if self.op.sell_amount_out is None:
            for ev in reversed(self.op.events or []):
                if ev.get("kind") != "sell":
                    continue
                data = ev.get("data") or {}
                raw = data.get("amount_out", data.get("proceeds"))
                if raw is not None:
                    try:
                        self.op.sell_amount_out = float(raw)
                    except (TypeError, ValueError):
                        pass
                break

    def has_live_task(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _spawn_loop(self) -> None:
        """Una sola task por op — evita ciclos duplicados por race del watchdog."""
        async with self._spawn_lock:
            if self.op.status != OpStatus.RUNNING.value:
                return
            if self.has_live_task():
                return
            self._task = asyncio.create_task(self._loop(), name=f"op-{self.op.id[:8]}")

    async def ensure_running(self) -> None:
        if self.op.status != OpStatus.RUNNING.value:
            return
        await self._spawn_loop()

    async def start(self) -> Dict[str, Any]:
        if self.op.status == OpStatus.COMPLETED.value:
            raise ValueError("Operación ya completada")
        if not self.op.demo:
            require_credentials()
        if self.op.status == OpStatus.RUNNING.value and self.has_live_task():
            return self.to_dict()

        if self.op.phase in (Phase.IDLE.value,):
            self.op.phase = Phase.GRID.value if self.op.mode == "grid" else Phase.WAITING_BUY.value
        prev = self.op.status
        if self.op.status in (OpStatus.DRAFT.value, OpStatus.STOPPED.value, OpStatus.PAUSED.value):
            if self.op.phase == Phase.IDLE.value:
                self.op.phase = (
                    Phase.GRID.value if self.op.mode == "grid" else Phase.WAITING_BUY.value
                )

        already = self.op.status == OpStatus.RUNNING.value
        self.op.status = OpStatus.RUNNING.value
        self.op.error = None
        self.op.updated_at = datetime.now(timezone.utc).isoformat()
        if not already:
            reason = "reanudada" if prev in (OpStatus.PAUSED.value, OpStatus.STOPPED.value) else "iniciada"
            add_event(
                self.op,
                kind="status",
                title=f"Operación {reason}",
                detail=(
                    f"Modo {'DEMO' if self.op.demo else 'LIVE'} · "
                    f"ciclo {self.op.cycle_seconds}s ({max(1, self.op.cycle_seconds // 60)} min) · "
                    f"fase {self.op.phase}"
                ),
            )
            await logs.emit(
                f"[{self.op.token.get('symbol', 'OP')}] Operación {self.op.id[:8]} en curso "
                f"({'DEMO' if self.op.demo else 'LIVE'}) · cada {max(1, self.op.cycle_seconds // 60)} min",
                kind="ops",
                level="success",
                data={"id": self.op.id},
            )
        await self._spawn_loop()
        self.manager.save()
        return self.to_dict()

    async def set_cycle_seconds(self, cycle_seconds: int) -> Dict[str, Any]:
        sec = max(30, int(cycle_seconds))
        prev = self.op.cycle_seconds
        self.op.cycle_seconds = sec
        self.op.updated_at = datetime.now(timezone.utc).isoformat()
        add_event(
            self.op,
            kind="status",
            title="Ciclo actualizado",
            detail=f"{prev}s → {sec}s ({max(1, sec // 60)} min)",
            data={"from": prev, "to": sec},
        )
        await logs.emit(
            f"[{self.op.token.get('symbol', 'OP')}] Ciclo {prev}s → {sec}s",
            kind="ops",
            level="info",
            data={"id": self.op.id},
        )
        self.manager.save()
        return self.to_dict()

    async def pause(self) -> Dict[str, Any]:
        self.op.status = OpStatus.PAUSED.value
        self.op.updated_at = datetime.now(timezone.utc).isoformat()
        add_event(
            self.op,
            kind="status",
            title="Operación pausada",
            detail=f"Pausada manualmente en fase {self.op.phase}",
        )
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        await logs.emit(
            f"[{self.op.token.get('symbol', 'OP')}] Pausada {self.op.id[:8]}",
            kind="ops",
            level="warn",
            data={"id": self.op.id},
        )
        self.manager.save()
        return self.to_dict()

    async def stop(self) -> Dict[str, Any]:
        self.op.status = OpStatus.STOPPED.value
        self.op.updated_at = datetime.now(timezone.utc).isoformat()
        add_event(
            self.op,
            kind="status",
            title="Operación detenida",
            detail=f"Stop manual en fase {self.op.phase}",
        )
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self.manager.save()
        return self.to_dict()

    async def run_cycle_now(self) -> Dict[str, Any]:
        if self.op.status == OpStatus.COMPLETED.value:
            raise ValueError("Operación ya completada")
        await self._cycle(manual=True)
        return self.to_dict()

    async def _loop(self) -> None:
        try:
            while self.op.status == OpStatus.RUNNING.value:
                await self._cycle(manual=False)
                if self.op.status != OpStatus.RUNNING.value:
                    break
                wait = max(5, int(self.op.cycle_seconds))
                mins = wait / 60
                wait_label = f"{wait}s" if wait < 120 else f"{mins:.0f} min"
                await logs.emit(
                    f"[{self.op.token.get('symbol', 'OP')}] Esperando {wait_label}…",
                    kind="cycle",
                    level="info",
                    data={"id": self.op.id, "cycle_seconds": wait},
                )
                await asyncio.sleep(wait)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.op.error = str(exc)
            self.op.status = OpStatus.PAUSED.value
            add_event(
                self.op,
                kind="error",
                title="Error — operación pausada",
                detail=str(exc),
            )
            await logs.emit(
                f"[{self.op.token.get('symbol', 'OP')}] Error: {exc}",
                kind="error",
                level="error",
                data={"id": self.op.id},
            )
            self.manager.save()

    async def _cycle(self, *, manual: bool = False) -> None:
        async with self._lock:
            self.op.last_cycle_at = datetime.now(timezone.utc).isoformat()
            sym = self.op.token.get("symbol", "TOKEN")
            tag = "manual" if manual else "auto"
            try:
                price = await fetch_price_usd(self.op.token_address)
            except Exception as exc:
                add_event(
                    self.op,
                    kind="cycle",
                    title=f"Ciclo {tag} · error de precio",
                    detail=str(exc),
                    data={"manual": manual},
                )
                await logs.emit(
                    f"[{sym}] Error precio: {exc}",
                    kind="price",
                    level="error",
                    data={"id": self.op.id},
                )
                self.op.updated_at = datetime.now(timezone.utc).isoformat()
                self.manager.save()
                return
            if price is None:
                add_event(
                    self.op,
                    kind="cycle",
                    title=f"Ciclo {tag} · sin precio",
                    detail="No se obtuvo precio USD",
                    data={"manual": manual},
                )
                self.op.updated_at = datetime.now(timezone.utc).isoformat()
                self.manager.save()
                return

            self.op.last_price_usd = price
            if self.op.token:
                self.op.token["price_usd"] = price

            decision = f"fase {self.op.phase}"
            traded = False
            try:
                if self.op.mode == "grid":
                    traded, decision = await self._cycle_grid(price, tag=tag)
                elif self.op.phase == Phase.WAITING_BUY.value:
                    if price <= self.op.buy_price_usd:
                        await self._execute_buy(price)
                        traded = True
                        decision = f"BUY ejecutado @ ${price:.8f}"
                    else:
                        decision = (
                            f"${price:.8f} · esperando buy ≤ ${self.op.buy_price_usd}"
                        )
                elif self.op.phase in (Phase.HOLDING.value, Phase.WAITING_SELL.value):
                    self.op.phase = Phase.WAITING_SELL.value
                    if price >= self.op.sell_price_usd:
                        await self._execute_sell(price)
                        traded = True
                        decision = f"SELL ejecutado @ ${price:.8f}"
                    else:
                        decision = (
                            f"${price:.8f} · esperando sell ≥ ${self.op.sell_price_usd}"
                        )
                else:
                    decision = f"${price:.8f} · fase {self.op.phase}"
            except (ZeroXError, WalletError, ValueError) as exc:
                self.op.error = str(exc)
                add_event(
                    self.op,
                    kind="error",
                    title="Error en trade",
                    detail=str(exc),
                )
                await logs.emit(str(exc), kind="error", level="error", data={"id": self.op.id})
                decision = f"error: {exc}"
            finally:
                if not traded:
                    add_event(
                        self.op,
                        kind="cycle",
                        title=f"Ciclo {tag}",
                        detail=decision,
                        data={
                            "manual": manual,
                            "price_usd": price,
                            "phase": self.op.phase,
                            "status": self.op.status,
                        },
                    )
                await logs.emit(
                    f"[{sym}] {decision}",
                    kind=("grid" if self.op.mode == "grid" else ("cycle" if not traded else "trade")),
                    level="info" if not traded else "success",
                    data={"id": self.op.id, "price_usd": price, "manual": manual},
                )
                self.op.updated_at = datetime.now(timezone.utc).isoformat()
                self.manager.save()

    async def _cycle_grid(self, price: float, *, tag: str) -> tuple[bool, str]:
        """
        Grid continuo: opera dentro de [buy, sell] sin completar nunca.
        Si el precio sale de la franja, espera a que vuelva.
        """
        self.op.phase = Phase.GRID.value
        lower = float(self.op.buy_price_usd)
        upper = float(self.op.sell_price_usd)

        if price < lower or price > upper:
            return (
                False,
                f"fuera de franja ${price:.8f} · esperando volver a ${lower:.8f}–${upper:.8f}",
            )

        if not self.op.grid_levels:
            self.op.grid_levels = build_grid_levels(
                lower=lower,
                upper=upper,
                grid_count=self.op.grid_count or 10,
                spot=price,
                usdc_amount=self.op.usdc_amount,
            )

        actions: List[str] = []
        traded = False
        for level in self.op.grid_levels:
            status = level.get("status")
            if status == "waiting_buy" and price <= float(level["buy_price"]):
                await self._grid_buy_level(level, price)
                traded = True
                actions.append(
                    f"G{level['index']} BUY ${level['buy_price']:.8f} · "
                    f"{level.get('alloc_pct', 0):.1f}%"
                )
            elif status == "waiting_sell" and price >= float(level["sell_price"]):
                await self._grid_sell_level(level, price)
                traded = True
                actions.append(
                    f"G{level['index']} SELL ${level['sell_price']:.8f} · "
                    f"+${float(level.get('last_profit') or 0):.4f}"
                )

        waiting_buy = sum(1 for lv in self.op.grid_levels if lv.get("status") == "waiting_buy")
        waiting_sell = sum(1 for lv in self.op.grid_levels if lv.get("status") == "waiting_sell")
        if traded:
            decision = " · ".join(actions)
            add_event(
                self.op,
                kind="grid",
                title=f"Grid {tag}",
                detail=decision,
                data={"price": price, "actions": actions},
            )
        else:
            decision = (
                f"grid ${price:.8f} · {waiting_buy} buy armados · {waiting_sell} sell armados"
            )
        return traded, decision

    async def _grid_buy_level(self, level: Dict[str, Any], price: float) -> None:
        sym = self.op.token.get("symbol", "TOKEN")
        alloc = float(level.get("usdc_alloc") or 0)
        if alloc <= 0:
            return
        if self.op.demo:
            tokens = alloc / price
            level["tokens"] = tokens
            level["status"] = "waiting_sell"
            level["buys"] = int(level.get("buys") or 0) + 1
            level["last_buy_tx"] = f"demo-grid-buy-{level['index']}-{int(datetime.now(timezone.utc).timestamp())}"
            self.op.demo_usdc_balance -= alloc
            self.op.buy_amount_out = (self.op.buy_amount_out or 0) + tokens
            self.op.buy_tx = level["last_buy_tx"]
            add_event(
                self.op,
                kind="buy",
                title=f"Grid BUY G{level['index']} (demo)",
                detail=(
                    f"amount out {tokens:.8f} {sym} · {alloc:.4f} USDC "
                    f"({level.get('alloc_pct', 0):.1f}%) @ ${price:.8f} → sell ≥ ${level['sell_price']:.8f}"
                ),
                data={
                    "grid_index": level["index"],
                    "amount_out": tokens,
                    "amount_in_usdc": alloc,
                    "price": price,
                    "tx": level["last_buy_tx"],
                },
            )
            return

        wallet, _ = require_credentials()
        sell_amount = int(alloc * (10**USDC_DECIMALS))
        bal = get_balance(USDC_ADDRESS, wallet)
        if bal < sell_amount:
            raise WalletError(f"USDC insuficiente para grid G{level['index']}")
        decimals = get_token_decimals(self.op.token_address)
        before = get_balance(self.op.token_address, wallet)
        quote = await get_quote(
            sell_token=USDC_ADDRESS,
            buy_token=self.op.token_address,
            sell_amount=str(sell_amount),
            taker=wallet,
        )
        tx = await approve_and_swap(quote)
        after = get_balance(self.op.token_address, wallet)
        bought = max(0, after - before) or int(quote.get("buyAmount") or 0)
        tokens = bought / (10**decimals)
        level["tokens"] = tokens
        level["tokens_raw"] = bought
        level["status"] = "waiting_sell"
        level["buys"] = int(level.get("buys") or 0) + 1
        level["last_buy_tx"] = tx
        self.op.buy_amount_out = (self.op.buy_amount_out or 0) + tokens
        self.op.buy_tx = tx
        add_event(
            self.op,
            kind="buy",
            title=f"Grid BUY G{level['index']} (live)",
            detail=f"amount out {tokens:.8f} {sym} · {alloc:.4f} USDC @ ${price:.8f}",
            data={
                "grid_index": level["index"],
                "amount_out": tokens,
                "amount_in_usdc": alloc,
                "price": price,
                "tx": tx,
            },
        )

    async def _grid_sell_level(self, level: Dict[str, Any], price: float) -> None:
        sym = self.op.token.get("symbol", "TOKEN")
        tokens = float(level.get("tokens") or 0)
        alloc = float(level.get("usdc_alloc") or 0)
        if tokens <= 0:
            level["status"] = "waiting_buy"
            return
        if self.op.demo:
            proceeds = tokens * price
            profit = proceeds - alloc
            level["last_profit"] = profit
            level["realized_usd"] = float(level.get("realized_usd") or 0) + profit
            level["tokens"] = 0.0
            level["status"] = "waiting_buy"  # re-arm
            level["sells"] = int(level.get("sells") or 0) + 1
            level["last_sell_tx"] = f"demo-grid-sell-{level['index']}-{int(datetime.now(timezone.utc).timestamp())}"
            self.op.demo_usdc_balance += proceeds
            self.op.sell_amount_out = (self.op.sell_amount_out or 0) + proceeds
            self.op.realized_profit_usd = (self.op.realized_profit_usd or 0) + profit
            self.op.realized_total_usd = (self.op.realized_total_usd or self.op.usdc_amount) + profit
            self.op.sell_tx = level["last_sell_tx"]
            add_event(
                self.op,
                kind="sell",
                title=f"Grid SELL G{level['index']} (demo)",
                detail=(
                    f"amount out ${proceeds:.6f} USDC · +${profit:.4f} · "
                    f"re-arma buy ≤ ${level['buy_price']:.8f}"
                ),
                data={
                    "grid_index": level["index"],
                    "amount_out": proceeds,
                    "profit": profit,
                    "price": price,
                    "tx": level["last_sell_tx"],
                },
            )
            return

        wallet, _ = require_credentials()
        raw = int(level.get("tokens_raw") or 0)
        bal = get_balance(self.op.token_address, wallet)
        sell_amount = min(raw or bal, bal)
        if sell_amount <= 0:
            raise WalletError(f"Sin tokens en grid G{level['index']}")
        usdc_before = get_balance(USDC_ADDRESS, wallet)
        quote = await get_quote(
            sell_token=self.op.token_address,
            buy_token=USDC_ADDRESS,
            sell_amount=str(sell_amount),
            taker=wallet,
        )
        tx = await approve_and_swap(quote)
        usdc_after = get_balance(USDC_ADDRESS, wallet)
        buy_amt = max(0, usdc_after - usdc_before) or int(quote.get("buyAmount") or 0)
        proceeds = buy_amt / (10**USDC_DECIMALS)
        profit = proceeds - alloc
        level["last_profit"] = profit
        level["realized_usd"] = float(level.get("realized_usd") or 0) + profit
        level["tokens"] = 0.0
        level["tokens_raw"] = 0
        level["status"] = "waiting_buy"
        level["sells"] = int(level.get("sells") or 0) + 1
        level["last_sell_tx"] = tx
        self.op.sell_amount_out = (self.op.sell_amount_out or 0) + proceeds
        self.op.realized_profit_usd = (self.op.realized_profit_usd or 0) + profit
        self.op.realized_total_usd = (self.op.realized_total_usd or self.op.usdc_amount) + profit
        self.op.sell_tx = tx
        add_event(
            self.op,
            kind="sell",
            title=f"Grid SELL G{level['index']} (live)",
            detail=f"amount out ${proceeds:.6f} USDC · +${profit:.4f}",
            data={
                "grid_index": level["index"],
                "amount_out": proceeds,
                "profit": profit,
                "price": price,
                "tx": tx,
            },
        )

    async def _execute_buy(self, price: float) -> None:
        sym = self.op.token.get("symbol", "TOKEN")
        if self.op.demo:
            tokens = self.op.usdc_amount / price
            self.op.bought_token_amount_raw = int(tokens * (10**18))
            self.op.buy_amount_out = tokens
            self.op.demo_usdc_balance -= self.op.usdc_amount
            self.op.buy_tx = f"demo-buy-{int(datetime.now(timezone.utc).timestamp())}"
            self.op.phase = Phase.WAITING_SELL.value
            add_event(
                self.op,
                kind="buy",
                title="Compra ejecutada (demo)",
                detail=(
                    f"amount out {tokens:.8f} {sym} · "
                    f"por {self.op.usdc_amount} USDC @ ${price:.8f}"
                ),
                data={
                    "price": price,
                    "tokens": tokens,
                    "amount_out": tokens,
                    "amount_in_usdc": self.op.usdc_amount,
                    "tx": self.op.buy_tx,
                },
            )
            await logs.emit(
                f"[{sym}] COMPRA demo · amount out {tokens:.8f} @ ${price:.8f}",
                kind="demo",
                level="success",
                data={"id": self.op.id, "tx": self.op.buy_tx, "amount_out": tokens},
            )
            return

        wallet, _ = require_credentials()
        sell_amount = int(self.op.usdc_amount * (10**USDC_DECIMALS))
        bal = get_balance(USDC_ADDRESS, wallet)
        if bal < sell_amount:
            raise WalletError("USDC insuficiente")
        decimals = get_token_decimals(self.op.token_address)
        before = get_balance(self.op.token_address, wallet)
        quote = await get_quote(
            sell_token=USDC_ADDRESS,
            buy_token=self.op.token_address,
            sell_amount=str(sell_amount),
            taker=wallet,
        )
        tx = await approve_and_swap(quote)
        after = get_balance(self.op.token_address, wallet)
        bought = max(0, after - before) or int(quote.get("buyAmount") or 0)
        amount_out = bought / (10**decimals)
        self.op.bought_token_amount_raw = bought
        self.op.buy_amount_out = amount_out
        self.op.buy_tx = tx
        self.op.phase = Phase.WAITING_SELL.value
        add_event(
            self.op,
            kind="buy",
            title="Compra ejecutada (live)",
            detail=(
                f"amount out {amount_out:.8f} {sym} · "
                f"{self.op.usdc_amount} USDC in @ ~${price:.8f}"
            ),
            data={
                "price": price,
                "tx": tx,
                "bought_raw": bought,
                "amount_out": amount_out,
                "amount_in_usdc": self.op.usdc_amount,
                "decimals": decimals,
            },
        )
        await logs.emit(
            f"[{sym}] COMPRA live · amount out {amount_out:.8f} · tx {tx}",
            kind="trade",
            level="success",
            data={"id": self.op.id, "tx": tx, "amount_out": amount_out},
        )

    async def _execute_sell(self, price: float) -> None:
        sym = self.op.token.get("symbol", "TOKEN")
        if self.op.demo:
            tokens = self.op.bought_token_amount_raw / (10**18)
            if self.op.buy_amount_out is not None:
                tokens = self.op.buy_amount_out
            proceeds = tokens * price
            invested = self.op.usdc_amount
            self.op.demo_usdc_balance += proceeds
            self.op.sell_amount_out = proceeds
            self.op.sell_tx = f"demo-sell-{int(datetime.now(timezone.utc).timestamp())}"
            self.op.realized_total_usd = proceeds
            self.op.realized_profit_usd = proceeds - invested
            self.op.bought_token_amount_raw = 0
            self.op.phase = Phase.IDLE.value
            self.op.status = OpStatus.COMPLETED.value
            self.op.completed_at = datetime.now(timezone.utc).isoformat()
            add_event(
                self.op,
                kind="sell",
                title="Venta ejecutada (demo) · completada",
                detail=(
                    f"amount out ${proceeds:.6f} USDC · "
                    f"{tokens:.8f} {sym} in @ ${price:.8f} · "
                    f"beneficio +${self.op.realized_profit_usd:.4f}"
                ),
                data={
                    "price": price,
                    "proceeds": proceeds,
                    "amount_out": proceeds,
                    "amount_in_tokens": tokens,
                    "profit": self.op.realized_profit_usd,
                    "tx": self.op.sell_tx,
                },
            )
            await logs.emit(
                f"[{sym}] VENTA demo · amount out ${proceeds:.6f} · +${self.op.realized_profit_usd:.4f}",
                kind="demo",
                level="success",
                data={
                    "id": self.op.id,
                    "profit": self.op.realized_profit_usd,
                    "amount_out": proceeds,
                },
            )
            return

        wallet, _ = require_credentials()
        bal = get_balance(self.op.token_address, wallet)
        sell_amount = min(self.op.bought_token_amount_raw or bal, bal)
        if sell_amount <= 0:
            raise WalletError("Sin tokens para vender")
        usdc_before = get_balance(USDC_ADDRESS, wallet)
        quote = await get_quote(
            sell_token=self.op.token_address,
            buy_token=USDC_ADDRESS,
            sell_amount=str(sell_amount),
            taker=wallet,
        )
        tx = await approve_and_swap(quote)
        usdc_after = get_balance(USDC_ADDRESS, wallet)
        buy_amt = max(0, usdc_after - usdc_before) or int(quote.get("buyAmount") or 0)
        proceeds = buy_amt / (10**USDC_DECIMALS)
        self.op.sell_amount_out = proceeds
        self.op.sell_tx = tx
        self.op.realized_total_usd = proceeds
        self.op.realized_profit_usd = proceeds - self.op.usdc_amount
        self.op.bought_token_amount_raw = 0
        self.op.phase = Phase.IDLE.value
        self.op.status = OpStatus.COMPLETED.value
        self.op.completed_at = datetime.now(timezone.utc).isoformat()
        add_event(
            self.op,
            kind="sell",
            title="Venta ejecutada (live) · completada",
            detail=(
                f"amount out ${proceeds:.6f} USDC · "
                f"beneficio +${self.op.realized_profit_usd:.4f}"
            ),
            data={
                "price": price,
                "proceeds": proceeds,
                "amount_out": proceeds,
                "profit": self.op.realized_profit_usd,
                "tx": tx,
            },
        )
        await logs.emit(
            f"[{sym}] VENTA live · amount out ${proceeds:.6f} · profit ${self.op.realized_profit_usd:.4f} · tx {tx}",
            kind="trade",
            level="success",
            data={"id": self.op.id, "tx": tx, "amount_out": proceeds},
        )


class OperationManager:
    def __init__(self) -> None:
        self._runners: Dict[str, OperationRunner] = {}
        self._load()

    def _load(self) -> None:
        path = Path(OPERATIONS_FILE)
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for item in raw.get("operations") or []:
                fields = {
                    "id",
                    "token_address",
                    "buy_price_usd",
                    "sell_price_usd",
                    "usdc_amount",
                    "demo",
                    "cycle_seconds",
                    "status",
                    "phase",
                    "token",
                    "last_price_usd",
                    "bought_token_amount_raw",
                    "buy_amount_out",
                    "sell_amount_out",
                    "buy_tx",
                    "sell_tx",
                    "demo_usdc_balance",
                    "estimated",
                    "realized_profit_usd",
                    "realized_total_usd",
                    "error",
                    "created_at",
                    "updated_at",
                    "completed_at",
                    "last_cycle_at",
                    "events",
                    "mode",
                    "grid_count",
                    "grid_levels",
                }
                data = {k: item[k] for k in fields if k in item}
                data.setdefault("id", str(uuid.uuid4()))
                data.setdefault("token_address", "")
                data.setdefault("buy_price_usd", 0.0)
                data.setdefault("sell_price_usd", 0.0)
                data.setdefault("usdc_amount", 0.0)
                was_running = data.get("status") == OpStatus.RUNNING.value
                op = Operation(**data)
                if was_running:
                    op.status = OpStatus.PAUSED.value
                    op.error = None
                runner = OperationRunner(op, self)
                runner._was_running = was_running  # type: ignore[attr-defined]
                self._runners[op.id] = runner
        except Exception:
            pass

    def save(self) -> None:
        path = Path(OPERATIONS_FILE)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            ops_out = []
            for r in self._runners.values():
                d = r.to_dict()
                d.pop("running", None)
                d.pop("task_alive", None)
                ops_out.append(d)
            payload = {
                "operations": ops_out,
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            pass

    def list(self) -> List[Dict[str, Any]]:
        items = [r.to_dict() for r in self._runners.values()]
        items.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
        return items

    def get(self, op_id: str) -> OperationRunner:
        if op_id not in self._runners:
            raise KeyError("Operación no encontrada")
        return self._runners[op_id]

    async def ensure_all_running(self) -> None:
        for runner in list(self._runners.values()):
            try:
                await runner.ensure_running()
            except Exception as exc:
                await logs.emit(
                    f"Watchdog falló {runner.op.id[:8]}: {exc}",
                    kind="ops",
                    level="warn",
                )

    async def create(
        self,
        *,
        token_address: str,
        buy_price_usd: float,
        sell_price_usd: float,
        usdc_amount: float,
        demo: bool = True,
        cycle_seconds: int = 600,
        start: bool = False,
        mode: str = "classic",
        grid_count: int = 0,
    ) -> Dict[str, Any]:
        if sell_price_usd <= buy_price_usd:
            raise ValueError("Sell debe ser mayor que buy")
        mode_norm = "grid" if mode == "grid" else "classic"
        gcount = max(2, int(grid_count)) if mode_norm == "grid" else 0
        if mode_norm == "grid" and gcount < 2:
            raise ValueError("AutonomousTrade requiere al menos 2 grinds")

        info = await fetch_token_info(token_address)
        spot = info.get("price_usd")
        levels: List[Dict[str, Any]] = []
        estimated: Dict[str, float]
        phase = Phase.WAITING_BUY.value
        if mode_norm == "grid":
            levels = build_grid_levels(
                lower=float(buy_price_usd),
                upper=float(sell_price_usd),
                grid_count=gcount,
                spot=float(spot) if spot else None,
                usdc_amount=float(usdc_amount),
            )
            estimated = estimate_grid(
                float(usdc_amount),
                float(buy_price_usd),
                float(sell_price_usd),
                gcount,
                max(30, int(cycle_seconds)),
            )
            phase = Phase.GRID.value
        else:
            estimated = estimate_profit(
                float(usdc_amount), float(buy_price_usd), float(sell_price_usd)
            )

        op = Operation(
            id=str(uuid.uuid4()),
            token_address=token_address.strip(),
            buy_price_usd=float(buy_price_usd),
            sell_price_usd=float(sell_price_usd),
            usdc_amount=float(usdc_amount),
            demo=demo,
            cycle_seconds=max(30, int(cycle_seconds)),
            status=OpStatus.DRAFT.value,
            phase=phase,
            token=info,
            last_price_usd=spot,
            demo_usdc_balance=max(1000.0, usdc_amount * 5),
            estimated=estimated,
            mode=mode_norm,
            grid_count=gcount,
            grid_levels=levels,
        )
        runner = OperationRunner(op, self)
        runner._was_running = False  # type: ignore[attr-defined]
        self._runners[op.id] = runner
        if mode_norm == "grid":
            detail = (
                f"GRID continuo {info.get('symbol')} · {gcount} grinds · "
                f"{len(levels)} buy levels · {usdc_amount} USDC · "
                f"~{levels[0]['alloc_pct']:.1f}%/nivel · "
                f"rango ${buy_price_usd}–${sell_price_usd} · sin stop en bounds"
            )
            title = "AutonomousTrade creado"
        else:
            detail = (
                f"{info.get('symbol')} · invertir {usdc_amount} USDC · "
                f"buy ≤ ${buy_price_usd} · sell ≥ ${sell_price_usd} · "
                f"est. beneficio +${op.estimated.get('profit_usd', 0):.4f} "
                f"(total ${op.estimated.get('total_usd', 0):.4f})"
            )
            title = "Operación creada"
        add_event(
            op,
            kind="created",
            title=title,
            detail=detail,
            data={"estimated": op.estimated, "mode": mode_norm, "grid_levels": levels},
        )
        self.save()
        await logs.emit(
            f"Nueva op {op.id[:8]} · {title} · {info.get('symbol')}",
            kind="ops",
            level="success",
            data={"id": op.id, "mode": mode_norm},
        )
        if start:
            return await runner.start()
        return runner.to_dict()

    async def delete(self, op_id: str) -> None:
        runner = self.get(op_id)
        await runner.stop()
        del self._runners[op_id]
        self.save()
        await logs.emit(f"Operación eliminada {op_id[:8]}", kind="ops", level="warn")

    async def resume_interrupted(self) -> None:
        for runner in list(self._runners.values()):
            if getattr(runner, "_was_running", False):
                try:
                    await runner.start()
                except Exception as exc:
                    await logs.emit(
                        f"No se pudo reanudar {runner.op.id[:8]}: {exc}",
                        kind="ops",
                        level="warn",
                    )
        # Por si quedaron running sin flag (reload parcial)
        await self.ensure_all_running()


ops = OperationManager()
