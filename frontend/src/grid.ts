/** Arithmetic grid helpers (AutonomousTrade / grinds). */

export type GridLevelPreview = {
  index: number;
  buy_price: number;
  sell_price: number;
  usdc_alloc: number;
  alloc_pct: number;
  side: "buy";
};

export function gridLinePrices(lower: number, upper: number, gridCount: number): number[] {
  if (!(upper > lower) || gridCount < 2) return [];
  const n = Math.max(2, Math.floor(gridCount));
  const step = (upper - lower) / n;
  return Array.from({ length: n + 1 }, (_, i) => lower + i * step);
}

export function previewBuyLevels(
  lower: number,
  upper: number,
  gridCount: number,
  spot: number | null | undefined,
  usdcAmount: number
): GridLevelPreview[] {
  if (!(upper > lower) || !(usdcAmount > 0) || gridCount < 2) return [];
  const n = Math.max(2, Math.floor(gridCount));
  const step = (upper - lower) / n;
  const ref = spot && spot > 0 ? spot : (lower + upper) / 2;
  const candidates: number[] = [];
  for (let i = 0; i < n; i++) {
    const p = lower + i * step;
    if (p < upper) candidates.push(p);
  }
  let buys = candidates.filter((p) => p < ref);
  if (!buys.length && candidates.length) buys = [candidates[0]];
  const alloc = usdcAmount / buys.length;
  const pct = 100 / buys.length;
  return buys.map((price, index) => ({
    index,
    buy_price: price,
    sell_price: Math.min(upper, price + step),
    usdc_alloc: alloc,
    alloc_pct: pct,
    side: "buy" as const,
  }));
}

export function estimateGrid(
  usdc: number,
  lower: number,
  upper: number,
  gridCount: number,
  cycleMinutes = 10
): {
  profit_usd: number;
  roi_pct: number;
  step: number;
  levels: number;
  alloc: number;
  profit_per_fill: number;
  profit_24h: number;
  profit_7d: number;
  profit_31d: number;
  roi_24h_pct: number;
  cycles_per_day: number;
} | null {
  if (!(usdc > 0 && upper > lower && gridCount >= 2)) return null;
  const n = Math.max(2, Math.floor(gridCount));
  const step = (upper - lower) / n;
  const buyN = Math.max(1, Math.floor(n / 2));
  const alloc = usdc / buyN;
  const mid = (lower + upper) / 2;
  const profit_per_fill = alloc * (step / mid);
  const profit_sweep = profit_per_fill * buyN;
  const mins = Math.max(1, Number(cycleMinutes) || 10);
  const cycles_per_day = (24 * 60) / mins;
  const fill_rate = 0.35;
  const profit_24h = profit_per_fill * cycles_per_day * fill_rate;
  const profit_7d = profit_24h * 7;
  const profit_31d = profit_24h * 31;
  return {
    profit_usd: profit_sweep,
    roi_pct: (profit_sweep / usdc) * 100,
    step,
    levels: buyN,
    alloc,
    profit_per_fill,
    profit_24h,
    profit_7d,
    profit_31d,
    roi_24h_pct: (profit_24h / usdc) * 100,
    cycles_per_day,
  };
}
