import type { TokenInfo } from "./api";

const KEY = "atc_trade_draft_v1";

export type TradeDraft = {
  tokenAddress: string;
  buyPrice: string;
  sellPrice: string;
  usdcAmount: string;
  cycleMinutes: string;
  tf: string;
  lastOpId: string | null;
  token: TokenInfo | null;
  updatedAt: number;
};

export function readTradeDraft(): TradeDraft | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const data = JSON.parse(raw) as TradeDraft;
    if (!data || typeof data !== "object") return null;
    return data;
  } catch {
    return null;
  }
}

export function writeTradeDraft(draft: Omit<TradeDraft, "updatedAt">) {
  try {
    const payload: TradeDraft = { ...draft, updatedAt: Date.now() };
    localStorage.setItem(KEY, JSON.stringify(payload));
  } catch {
    /* ignore quota */
  }
}
