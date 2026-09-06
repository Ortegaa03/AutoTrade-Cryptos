export type LogLevel = "info" | "success" | "warn" | "error";

export type LogEntry = {
  id: string;
  ts: string;
  level: LogLevel;
  kind: string;
  message: string;
  data?: Record<string, unknown>;
};

export type TokenInfo = {
  address: string;
  name: string;
  symbol: string;
  image?: string | null;
  price_usd?: number | null;
  pair_address?: string;
  chart_embed?: string | null;
  liquidity_usd?: number;
  volume_24h?: number;
  price_change_24h?: number;
  url?: string;
};

export type Estimate = {
  tokens: number;
  total_usd: number;
  profit_usd: number;
  roi_pct: number;
};

export type OpEvent = {
  id: string;
  ts: string;
  kind: string;
  title: string;
  detail?: string;
  data?: Record<string, unknown>;
};

export type Operation = {
  id: string;
  token_address: string;
  buy_price_usd: number;
  sell_price_usd: number;
  usdc_amount: number;
  demo: boolean;
  cycle_seconds: number;
  status: string;
  phase: string;
  token: TokenInfo;
  last_price_usd?: number | null;
  last_cycle_at?: string | null;
  estimated?: Estimate;
  realized_profit_usd?: number | null;
  realized_total_usd?: number | null;
  error?: string | null;
  created_at?: string;
  updated_at?: string;
  completed_at?: string | null;
  running?: boolean;
  task_alive?: boolean;
  buy_tx?: string | null;
  sell_tx?: string | null;
  buy_amount_out?: number | null;
  sell_amount_out?: number | null;
  mode: string;
  grid_count?: number;
  grid_levels?: Array<{
    index: number;
    buy_price: number;
    sell_price: number;
    status: string;
    usdc_alloc: number;
    alloc_pct: number;
    tokens?: number;
    buys?: number;
    sells?: number;
    realized_usd?: number;
  }>;
  events?: OpEvent[];
};

async function parse<T>(res: Response): Promise<T> {
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (data as { detail?: string }).detail || res.statusText;
    throw new Error(detail);
  }
  return data as T;
}

export function calcEstimate(usdc: number, buy: number, sell: number): Estimate | null {
  if (!(usdc > 0 && buy > 0 && sell > 0)) return null;
  const tokens = usdc / buy;
  const total_usd = tokens * sell;
  const profit_usd = total_usd - usdc;
  return {
    tokens,
    total_usd,
    profit_usd,
    roi_pct: (profit_usd / usdc) * 100,
  };
}

export const api = {
  health: () => fetch("/api/health").then((r) => parse(r)),
  wallet: () =>
    fetch("/api/wallet").then((r) =>
      parse<{
        configured: boolean;
        wallet: string | null;
        usdc_balance: number;
        error?: string | null;
      }>(r)
    ),
  resolveToken: (token_address: string) =>
    fetch("/api/token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token_address }),
    }).then((r) => parse<TokenInfo>(r)),
  ohlcv: (pair_address: string, token_address: string, timeframe = "max") =>
    fetch(
      `/api/ohlcv?pair_address=${encodeURIComponent(pair_address)}&token_address=${encodeURIComponent(token_address)}&timeframe=${encodeURIComponent(timeframe)}&limit=1000`
    ).then((r) =>
      parse<{
        candles: Array<{
          time: number;
          open: number;
          high: number;
          low: number;
          close: number;
          volume?: number;
        }>;
        source: string;
        side: string;
        timeframe: string;
      }>(r)
    ),
  listOps: () => fetch("/api/operations").then((r) => parse<{ operations: Operation[] }>(r)),
  createOp: (body: {
    token_address: string;
    buy_price_usd: number;
    sell_price_usd: number;
    usdc_amount: number;
    demo?: boolean;
    cycle_seconds?: number;
    start?: boolean;
    mode?: "classic" | "grid";
    grid_count?: number;
  }) =>
    fetch("/api/operations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => parse<Operation>(r)),
  pauseOp: (id: string) =>
    fetch(`/api/operations/${id}/pause`, { method: "POST" }).then((r) => parse<Operation>(r)),
  resumeOp: (id: string) =>
    fetch(`/api/operations/${id}/resume`, { method: "POST" }).then((r) => parse<Operation>(r)),
  stopOp: (id: string) =>
    fetch(`/api/operations/${id}/stop`, { method: "POST" }).then((r) => parse<Operation>(r)),
  deleteOp: (id: string) =>
    fetch(`/api/operations/${id}`, { method: "DELETE" }).then((r) => parse<{ ok: boolean }>(r)),
  cycleOp: (id: string) =>
    fetch(`/api/operations/${id}/cycle`, { method: "POST" }).then((r) => parse<Operation>(r)),
  setCycleConfig: (id: string, cycle_minutes: number) =>
    fetch(`/api/operations/${id}/cycle-config`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        cycle_minutes,
        cycle_seconds: Math.max(30, Math.round(cycle_minutes * 60)),
      }),
    }).then((r) => parse<Operation>(r)),
};
