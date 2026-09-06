from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from .config import (
    CYCLE_SECONDS,
    STATE_FILE,
    USDC_ADDRESS,
    USDC_DECIMALS,
    WALLET,
)
from .logger import logs
from .token_info import fetch_price_usd, fetch_token_info
from .wallet import (
    WalletError,
    approve_and_swap,
    get_balance,
    get_usdc_balance_human,
    require_credentials,
)
from .zero_x import ZeroXError, get_quote


class Phase(str, Enum):
    IDLE = "idle"
    WAITING_BUY = "waiting_buy"
    HOLDING = "holding"
    WAITING_SELL = "waiting_sell"
    STOPPED = "stopped"


@dataclass
class BotConfig:
    token_address: str = ""
    buy_price_usd: float = 0.0
    sell_price_usd: float = 0.0
    usdc_amount: float = 0.0  # how much USDC to spend on buy
    cycle_seconds: int = CYCLE_SECONDS


@dataclass
class BotRuntime:
    running: bool = False
    demo: bool = False
    phase: Phase = Phase.IDLE
    token: Dict[str, Any] = field(default_factory=dict)
    last_price_usd: Optional[float] = None
    last_cycle_at: Optional[str] = None
    next_cycle_at: Optional[str] = None
    bought_token_amount_raw: int = 0
    buy_tx: Optional[str] = None
    sell_tx: Optional[str] = None
    usdc_balance: Optional[float] = None
    demo_usdc_balance: float = 1000.0
    error: Optional[str] = None


class TradingBot:
    def __init__(self) -> None:
        self.config = BotConfig()
        self.runtime = BotRuntime()
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._load_state()

    def _load_state(self) -> None:
        path = Path(STATE_FILE)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            cfg = data.get("config") or {}
            rt = data.get("runtime") or {}
            self.config = BotConfig(**{**asdict(BotConfig()), **cfg})
            phase = rt.get("phase", Phase.IDLE.value)
            self.runtime = BotRuntime(
                running=False,
                demo=bool(rt.get("demo")),
                phase=Phase(phase) if phase in Phase._value2member_map_ else Phase.IDLE,
                token=rt.get("token") or {},
                last_price_usd=rt.get("last_price_usd"),
                last_cycle_at=rt.get("last_cycle_at"),
                bought_token_amount_raw=int(rt.get("bought_token_amount_raw") or 0),
                buy_tx=rt.get("buy_tx"),
                sell_tx=rt.get("sell_tx"),
                demo_usdc_balance=float(rt.get("demo_usdc_balance") or 1000.0),
            )
        except Exception:
            pass

    def _save_state(self) -> None:
        path = Path(STATE_FILE)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "config": asdict(self.config),
                "runtime": {
                    "phase": self.runtime.phase.value,
                    "demo": self.runtime.demo,
                    "token": self.runtime.token,
                    "last_price_usd": self.runtime.last_price_usd,
                    "last_cycle_at": self.runtime.last_cycle_at,
                    "bought_token_amount_raw": self.runtime.bought_token_amount_raw,
                    "buy_tx": self.runtime.buy_tx,
                    "sell_tx": self.runtime.sell_tx,
                    "demo_usdc_balance": self.runtime.demo_usdc_balance,
                },
            }
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            pass

    def status(self) -> Dict[str, Any]:
        runtime = {
            **asdict(self.runtime),
            "phase": self.runtime.phase.value,
        }
        if self.runtime.demo:
            runtime["usdc_balance"] = self.runtime.demo_usdc_balance
        return {
            "config": asdict(self.config),
            "runtime": runtime,
            "wallet": (
                "DEMO"
                if self.runtime.demo
                else (WALLET[:6] + "…" + WALLET[-4:] if WALLET and len(WALLET) > 10 else WALLET)
            ),
            "network": "World Chain (480)",
            "base_token": "USDC",
            "mode": "demo" if self.runtime.demo else "live",
        }

    async def resolve_token(self, address: str) -> Dict[str, Any]:
        info = await fetch_token_info(address)
        self.runtime.token = info
        self.runtime.last_price_usd = info.get("price_usd")
        self.config.token_address = address
        self._save_state()
        await logs.emit(
            f"Token: {info['name']} ({info['symbol']}) @ ${info.get('price_usd')}",
            kind="token",
            level="success",
            data=info,
        )
        return info

    async def configure(
        self,
        *,
        token_address: str,
        buy_price_usd: float,
        sell_price_usd: float,
        usdc_amount: float,
        cycle_seconds: Optional[int] = None,
        refresh_token: bool = True,
    ) -> Dict[str, Any]:
        if buy_price_usd <= 0 or sell_price_usd <= 0:
            raise ValueError("Precios de compra/venta deben ser > 0")
        if sell_price_usd <= buy_price_usd:
            raise ValueError("El precio de venta debe ser mayor que el de compra")
        if usdc_amount <= 0:
            raise ValueError("Cantidad USDC debe ser > 0")

        same = (self.config.token_address or "").lower() == token_address.strip().lower()
        if refresh_token or not same or not self.runtime.token:
            # Cache de DexScreener evita 429 si acabas de buscar el token
            await self.resolve_token(token_address)
        else:
            self.config.token_address = token_address.strip()

        self.config.buy_price_usd = float(buy_price_usd)
        self.config.sell_price_usd = float(sell_price_usd)
        self.config.usdc_amount = float(usdc_amount)
        if cycle_seconds:
            self.config.cycle_seconds = max(60, int(cycle_seconds))

        if self.runtime.phase in (Phase.IDLE, Phase.STOPPED):
            self.runtime.phase = Phase.WAITING_BUY
            self.runtime.bought_token_amount_raw = 0
            self.runtime.buy_tx = None
            self.runtime.sell_tx = None

        self._save_state()
        await logs.emit(
            f"Config lista · comprar ≤ ${buy_price_usd} · vender ≥ ${sell_price_usd} · {usdc_amount} USDC",
            kind="config",
            level="success",
        )
        return self.status()

    async def start(self, *, demo: bool = False) -> Dict[str, Any]:
        if not demo:
            require_credentials()
        if not self.config.token_address:
            raise ValueError("Configura el contrato del token primero")
        if self.runtime.running:
            return self.status()

        self.runtime.demo = demo
        self.runtime.running = True
        self.runtime.error = None
        if demo:
            # Saldo virtual para probar el flujo sin wallet
            self.runtime.demo_usdc_balance = max(1000.0, self.config.usdc_amount * 5)
            # Ciclo más corto en demo para probar sin esperar 10 min
            if self.config.cycle_seconds >= 600:
                self.config.cycle_seconds = 60
        if self.runtime.phase in (Phase.IDLE, Phase.STOPPED):
            self.runtime.phase = Phase.WAITING_BUY
            self.runtime.bought_token_amount_raw = 0
            self.runtime.buy_tx = None
            self.runtime.sell_tx = None
        self._task = asyncio.create_task(self._loop(), name="trading-loop")
        mode = "DEMO" if demo else "LIVE"
        await logs.emit(
            f"Bot iniciado [{mode}] · ciclo cada {self.config.cycle_seconds}s · fase {self.runtime.phase.value}",
            kind="bot",
            level="success",
        )
        if demo:
            await logs.emit(
                "Modo demo: precios reales, trades simulados (sin gastar gas ni USDC)",
                kind="demo",
                level="warn",
            )
        self._save_state()
        return self.status()

    async def stop(self) -> Dict[str, Any]:
        was_demo = self.runtime.demo
        self.runtime.running = False
        self.runtime.phase = Phase.STOPPED
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        # Restaurar ciclo 10 min al salir de demo
        if was_demo:
            self.config.cycle_seconds = CYCLE_SECONDS
        self._save_state()
        await logs.emit(
            f"Bot detenido{' [DEMO]' if was_demo else ''}",
            kind="bot",
            level="warn",
        )
        return self.status()

    async def run_cycle_now(self) -> Dict[str, Any]:
        await self._cycle()
        return self.status()

    async def _loop(self) -> None:
        try:
            while self.runtime.running:
                await self._cycle()
                if not self.runtime.running:
                    break
                wait = self.config.cycle_seconds
                from datetime import timedelta

                nxt = datetime.now(timezone.utc) + timedelta(seconds=wait)
                self.runtime.next_cycle_at = nxt.isoformat()
                wait_label = f"{wait}s" if wait < 120 else f"{wait // 60} min"
                await logs.emit(
                    f"Esperando {wait_label} hasta el próximo ciclo…",
                    kind="cycle",
                    level="info",
                )
                await asyncio.sleep(wait)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.runtime.error = str(exc)
            self.runtime.running = False
            await logs.emit(f"Loop caído: {exc}", kind="bot", level="error")

    async def _cycle(self) -> None:
        async with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            self.runtime.last_cycle_at = now
            await logs.emit("── Ciclo de precio ──", kind="cycle", level="info")

            if self.runtime.demo:
                self.runtime.usdc_balance = self.runtime.demo_usdc_balance
            else:
                try:
                    self.runtime.usdc_balance = get_usdc_balance_human()
                except Exception as exc:
                    await logs.emit(
                        f"No se pudo leer balance USDC: {exc}",
                        kind="wallet",
                        level="warn",
                    )

            try:
                price = await fetch_price_usd(self.config.token_address)
            except Exception as exc:
                await logs.emit(f"Error leyendo precio: {exc}", kind="price", level="error")
                return

            if price is None:
                await logs.emit("Precio USD no disponible", kind="price", level="warn")
                return

            self.runtime.last_price_usd = price
            if self.runtime.token:
                self.runtime.token["price_usd"] = price

            bal_label = (
                f" · demo USDC ≈ {self.runtime.usdc_balance:.4f}"
                if self.runtime.demo and self.runtime.usdc_balance is not None
                else (
                    f" · wallet USDC ≈ {self.runtime.usdc_balance:.4f}"
                    if self.runtime.usdc_balance is not None
                    else ""
                )
            )
            await logs.emit(
                f"{'[DEMO] ' if self.runtime.demo else ''}"
                f"{self.runtime.token.get('symbol', 'TOKEN')} = ${price:.8f} USD"
                f"{bal_label}",
                kind="price",
                level="info",
                data={"price_usd": price, "phase": self.runtime.phase.value, "demo": self.runtime.demo},
            )

            try:
                if self.runtime.phase == Phase.WAITING_BUY:
                    if price <= self.config.buy_price_usd:
                        await logs.emit(
                            f"Señal COMPRA · ${price:.8f} ≤ ${self.config.buy_price_usd}",
                            kind="signal",
                            level="success",
                        )
                        await self._execute_buy()
                    else:
                        await logs.emit(
                            f"Esperando compra · actual ${price:.8f} > target ${self.config.buy_price_usd}",
                            kind="signal",
                            level="info",
                        )
                elif self.runtime.phase in (Phase.HOLDING, Phase.WAITING_SELL):
                    self.runtime.phase = Phase.WAITING_SELL
                    if price >= self.config.sell_price_usd:
                        await logs.emit(
                            f"Señal VENTA · ${price:.8f} ≥ ${self.config.sell_price_usd}",
                            kind="signal",
                            level="success",
                        )
                        await self._execute_sell()
                    else:
                        await logs.emit(
                            f"Esperando venta · actual ${price:.8f} < target ${self.config.sell_price_usd}",
                            kind="signal",
                            level="info",
                        )
                else:
                    await logs.emit(
                        f"Fase {self.runtime.phase.value}: sin acción",
                        kind="cycle",
                        level="info",
                    )
            except (ZeroXError, WalletError, ValueError) as exc:
                self.runtime.error = str(exc)
                await logs.emit(str(exc), kind="error", level="error")
            finally:
                self._save_state()

    async def _execute_buy(self) -> None:
        if self.runtime.demo:
            await self._execute_buy_demo()
            return

        wallet, _ = require_credentials()
        sell_amount = int(self.config.usdc_amount * (10**USDC_DECIMALS))
        bal = get_balance(USDC_ADDRESS, wallet)
        if bal < sell_amount:
            raise WalletError(
                f"USDC insuficiente: tienes {bal / 10**USDC_DECIMALS:.4f}, necesitas {self.config.usdc_amount}"
            )

        token_before = get_balance(self.config.token_address, wallet)
        quote = await get_quote(
            sell_token=USDC_ADDRESS,
            buy_token=self.config.token_address,
            sell_amount=str(sell_amount),
            taker=wallet,
        )
        tx = await approve_and_swap(quote)
        token_after = get_balance(self.config.token_address, wallet)
        bought = max(0, token_after - token_before)
        # Fallback to quote buyAmount if balance delta fails
        if bought == 0:
            bought = int(quote.get("buyAmount") or 0)

        self.runtime.bought_token_amount_raw = bought
        self.runtime.buy_tx = tx
        self.runtime.phase = Phase.WAITING_SELL
        await logs.emit(
            f"COMPRA hecha · recibidos ~{bought} units raw · ahora esperamos venta ≥ ${self.config.sell_price_usd}",
            kind="trade",
            level="success",
            data={"tx": tx, "bought_raw": bought},
        )

    async def _execute_buy_demo(self) -> None:
        price = self.runtime.last_price_usd or 0.0
        if price <= 0:
            raise ValueError("Precio inválido para simular compra")
        if self.runtime.demo_usdc_balance < self.config.usdc_amount:
            raise WalletError(
                f"[DEMO] USDC insuficiente: {self.runtime.demo_usdc_balance:.4f} < {self.config.usdc_amount}"
            )

        # Simula ~18 decimals recibidos
        tokens = self.config.usdc_amount / price
        bought = int(tokens * (10**18))
        self.runtime.demo_usdc_balance -= self.config.usdc_amount
        self.runtime.usdc_balance = self.runtime.demo_usdc_balance
        self.runtime.bought_token_amount_raw = bought
        tx = f"demo-buy-{int(datetime.now(timezone.utc).timestamp())}"
        self.runtime.buy_tx = tx
        self.runtime.phase = Phase.WAITING_SELL
        await logs.emit(
            f"[DEMO] COMPRA simulada · {tokens:.6f} tokens por {self.config.usdc_amount} USDC "
            f"@ ${price:.8f} · ahora esperamos venta ≥ ${self.config.sell_price_usd}",
            kind="demo",
            level="success",
            data={"tx": tx, "bought_raw": bought, "tokens": tokens},
        )

    async def _execute_sell(self) -> None:
        if self.runtime.demo:
            await self._execute_sell_demo()
            return

        wallet, _ = require_credentials()
        bal = get_balance(self.config.token_address, wallet)
        sell_amount = self.runtime.bought_token_amount_raw or bal
        sell_amount = min(sell_amount, bal)
        if sell_amount <= 0:
            raise WalletError("No hay tokens para vender (¿aún no se compró?)")

        quote = await get_quote(
            sell_token=self.config.token_address,
            buy_token=USDC_ADDRESS,
            sell_amount=str(sell_amount),
            taker=wallet,
        )
        tx = await approve_and_swap(quote)
        self.runtime.sell_tx = tx
        self.runtime.phase = Phase.IDLE
        self.runtime.bought_token_amount_raw = 0
        self.runtime.running = False
        await logs.emit(
            f"VENTA hecha · ciclo completo · USDC de vuelta · tx {tx}",
            kind="trade",
            level="success",
            data={"tx": tx},
        )

    async def _execute_sell_demo(self) -> None:
        price = self.runtime.last_price_usd or 0.0
        if price <= 0:
            raise ValueError("Precio inválido para simular venta")
        if self.runtime.bought_token_amount_raw <= 0:
            raise WalletError("[DEMO] No hay tokens simulados para vender")

        tokens = self.runtime.bought_token_amount_raw / (10**18)
        proceeds = tokens * price
        self.runtime.demo_usdc_balance += proceeds
        self.runtime.usdc_balance = self.runtime.demo_usdc_balance
        tx = f"demo-sell-{int(datetime.now(timezone.utc).timestamp())}"
        self.runtime.sell_tx = tx
        self.runtime.phase = Phase.IDLE
        self.runtime.bought_token_amount_raw = 0
        self.runtime.running = False
        await logs.emit(
            f"[DEMO] VENTA simulada · {tokens:.6f} tokens → {proceeds:.4f} USDC @ ${price:.8f} · ciclo completo",
            kind="demo",
            level="success",
            data={"tx": tx, "proceeds_usdc": proceeds},
        )


bot = TradingBot()