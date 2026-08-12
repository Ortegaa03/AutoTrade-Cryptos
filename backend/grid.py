"""Grid / AutonomousTrade helpers (arithmetic grid, neutral)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_grid_levels(
    *,
    lower: float,
    upper: float,
    grid_count: int,
    spot: Optional[float],
    usdc_amount: float,
) -> List[Dict[str, Any]]:
    """
    Arithmetic grid between lower (buy bound) and upper (sell bound).

    - `grid_count` = number of intervals (N grinds)
    - Buy arms: levels strictly below spot (or mid if no spot)
    - Capital split equally across buy arms (% por nivel)
    - After a buy at price P, sell target = P + step
    """
    if upper <= lower:
        raise ValueError("Sell (upper) debe ser > Buy (lower)")
    n = max(2, int(grid_count))
    step = (upper - lower) / n
    ref = spot if spot and spot > 0 else (lower + upper) / 2

    # Interior buy prices: lower, lower+step, ... up to but not including upper
    candidates: List[float] = []
    for i in range(n):
        p = lower + i * step
        if p < upper - step * 1e-9:
            candidates.append(p)

    buy_prices = [p for p in candidates if p < ref]
    if not buy_prices:
        # Spot at/below lower → arm lowest level only
        buy_prices = [candidates[0]] if candidates else [lower]

    alloc = usdc_amount / len(buy_prices)
    pct = 100.0 / len(buy_prices)

    levels: List[Dict[str, Any]] = []
    for idx, price in enumerate(buy_prices):
        sell_p = min(upper, price + step)
        levels.append(
            {
                "index": idx,
                "buy_price": price,
                "sell_price": sell_p,
                "status": "waiting_buy",
                "usdc_alloc": alloc,
                "alloc_pct": pct,
                "tokens": 0.0,
                "buys": 0,
                "sells": 0,
                "realized_usd": 0.0,
                "last_buy_tx": None,
                "last_sell_tx": None,
            }
        )
    return levels


def grid_prices_for_chart(lower: float, upper: float, grid_count: int) -> List[float]:
    n = max(2, int(grid_count))
    step = (upper - lower) / n
    return [lower + i * step for i in range(n + 1)]


def estimate_grid(
    usdc_amount: float,
    lower: float,
    upper: float,
    grid_count: int,
    cycle_seconds: int = 600,
) -> Dict[str, float]:
    """
    Estimados continuos (el grid no para al tocar bounds).
    Modelo: ~0.35 round-trips por ciclo × profit de un nivel.
    """
    if usdc_amount <= 0 or upper <= lower:
        return {
            "tokens": 0,
            "total_usd": 0,
            "profit_usd": 0,
            "roi_pct": 0,
            "levels": 0,
            "profit_24h": 0,
            "profit_7d": 0,
            "profit_31d": 0,
        }
    n = max(2, int(grid_count))
    step = (upper - lower) / n
    buy_n = max(1, n // 2)
    alloc = usdc_amount / buy_n
    mid = (lower + upper) / 2
    per_fill = alloc * (step / mid) if mid > 0 else 0
    # Un barrido completo de todos los niveles (referencia)
    profit_sweep = per_fill * buy_n
    cycle_sec = max(30, int(cycle_seconds))
    cycles_day = (24 * 3600) / cycle_sec
    fill_rate = 0.35  # % de ciclos que cierran al menos 1 buy→sell en rango
    profit_24h = per_fill * cycles_day * fill_rate
    profit_7d = profit_24h * 7
    profit_31d = profit_24h * 31
    return {
        "tokens": usdc_amount / mid if mid > 0 else 0,
        "total_usd": usdc_amount + profit_sweep,
        "profit_usd": profit_sweep,
        "roi_pct": (profit_sweep / usdc_amount) * 100 if usdc_amount else 0,
        "levels": float(buy_n),
        "step": step,
        "alloc_per_level": alloc,
        "profit_per_fill": per_fill,
        "cycles_per_day": cycles_day,
        "profit_24h": profit_24h,
        "profit_7d": profit_7d,
        "profit_31d": profit_31d,
        "roi_24h_pct": (profit_24h / usdc_amount) * 100 if usdc_amount else 0,
    }
