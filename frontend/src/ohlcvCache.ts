/** Cache local de velas OHLCV · TTL 10 minutos */
const TTL_MS = 10 * 60 * 1000;

export type CachedOhlcv = {
  candles: Array<{
    time: number;
    open: number;
    high: number;
    low: number;
    close: number;
    volume?: number;
  }>;
  savedAt: number;
  pair: string;
  token: string;
  timeframe: string;
  source?: string;
};

function key(pair: string, token: string, tf: string) {
  // v3: invalida caches locales de ~24h que se guardaron como "max"
  return `ohlcv:v3:${pair.toLowerCase()}:${token.toLowerCase()}:${tf}`;
}

/** Serie usable como historial máximo (no ~24h). */
export function isMaxHistoryUsable(
  candles: CachedOhlcv["candles"] | null | undefined
): boolean {
  if (!candles || candles.length < 40) return false;
  const first = candles[0]?.time;
  const last = candles[candles.length - 1]?.time;
  if (!first || !last || last <= first) return false;
  return (last - first) / 86400 >= 7;
}

export function readOhlcvCache(
  pair: string,
  token: string,
  timeframe: string
): CachedOhlcv | null {
  try {
    const raw = localStorage.getItem(key(pair, token, timeframe));
    if (!raw) return null;
    const data = JSON.parse(raw) as CachedOhlcv;
    if (!data?.candles?.length || !data.savedAt) return null;
    return data;
  } catch {
    return null;
  }
}

export function isCacheFresh(cache: CachedOhlcv | null): boolean {
  if (!cache) return false;
  return Date.now() - cache.savedAt < TTL_MS;
}

export function writeOhlcvCache(
  pair: string,
  token: string,
  timeframe: string,
  candles: CachedOhlcv["candles"],
  source?: string
) {
  const payload: CachedOhlcv = {
    candles,
    savedAt: Date.now(),
    pair,
    token,
    timeframe,
    source,
  };
  try {
    localStorage.setItem(key(pair, token, timeframe), JSON.stringify(payload));
  } catch {
    /* quota */
  }
}

export function cacheAgeLabel(cache: CachedOhlcv | null): string {
  if (!cache) return "";
  const age = Math.round((Date.now() - cache.savedAt) / 1000);
  if (age < 60) return `${age}s`;
  return `${Math.round(age / 60)} min`;
}
