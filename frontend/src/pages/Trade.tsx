import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, calcEstimate, Operation, TokenInfo } from "../api";
import TradingChart, { Candle } from "../components/TradingChart";
import { estimateGrid, gridLinePrices, needsGridRecenter, suggestGridBounds } from "../grid";
import {
  isCacheFresh,
  isMaxHistoryUsable,
  readOhlcvCache,
  writeOhlcvCache,
} from "../ohlcvCache";
import { readTradeDraft, writeTradeDraft } from "../tradeDraft";

function formatUsd(n?: number | null) {
  if (n == null || Number.isNaN(n)) return "—";
  if (n >= 1) return `$${n.toLocaleString("en-US", { maximumFractionDigits: 4 })}`;
  return `$${n.toFixed(8)}`;
}

function timeLabel(iso: string) {
  try {
    return new Date(iso).toLocaleTimeString("es-ES", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return "--:--:--";
  }
}

function applyOpToForm(op: Operation) {
  return {
    tokenAddress: op.token_address || "",
    buyPrice: String(op.buy_price_usd ?? ""),
    sellPrice: String(op.sell_price_usd ?? ""),
    usdcAmount: String(op.usdc_amount ?? "10"),
    cycleMinutes: String(Math.max(1, Math.round((op.cycle_seconds || 600) / 60))),
    gridCount: String(op.grid_count && op.grid_count >= 2 ? op.grid_count : 10),
    token: op.token || null,
    lastOpId: op.id,
    mode: op.mode || "classic",
  };
}

export default function Trade() {
  const [params] = useSearchParams();
  const isGrid = params.get("mode") === "grid";
  const draft = readTradeDraft();
  const [tokenAddress, setTokenAddress] = useState(draft?.tokenAddress || "");
  const [buyPrice, setBuyPrice] = useState(draft?.buyPrice || "");
  const [sellPrice, setSellPrice] = useState(draft?.sellPrice || "");
  const [usdcAmount, setUsdcAmount] = useState(draft?.usdcAmount || "10");
  const [cycleMinutes, setCycleMinutes] = useState(draft?.cycleMinutes || "10");
  const [gridCount, setGridCount] = useState("10");
  const [walletBal, setWalletBal] = useState<{
    configured: boolean;
    wallet: string | null;
    usdc_balance: number;
    error?: string | null;
  } | null>(null);
  const [token, setToken] = useState<TokenInfo | null>(draft?.token || null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [chartError, setChartError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [logs, setLogs] = useState<
    Array<{ id: string; ts: string; level: string; kind: string; message: string }>
  >([]);
  const [lastOpId, setLastOpId] = useState<string | null>(draft?.lastOpId || null);
  const [activeOps, setActiveOps] = useState<Operation[]>([]);
  const [hydrated, setHydrated] = useState(false);
  const gridCenteredRef = useRef(false);
  const logEndRef = useRef<HTMLDivElement>(null);
  const focusOpIdRef = useRef<string | null>(draft?.lastOpId || null);

  useEffect(() => {
    focusOpIdRef.current = lastOpId;
  }, [lastOpId]);

  const price = token?.price_usd;
  const estimate = useMemo(() => {
    if (isGrid) {
      const g = estimateGrid(
        Number(usdcAmount),
        Number(buyPrice),
        Number(sellPrice),
        Number(gridCount) || 10,
        Number(cycleMinutes) || 10
      );
      if (!g) return null;
      return {
        tokens: 0,
        total_usd: Number(usdcAmount) + g.profit_24h,
        profit_usd: g.profit_24h,
        roi_pct: g.roi_24h_pct,
        grid: g,
      };
    }
    return calcEstimate(Number(usdcAmount), Number(buyPrice), Number(sellPrice));
  }, [isGrid, usdcAmount, buyPrice, sellPrice, gridCount, cycleMinutes]);

  const chartGridPrices = useMemo(() => {
    if (!isGrid) return [];
    const lower = Number(buyPrice);
    const upper = Number(sellPrice);
    const levels = Math.max(2, Math.floor(Number(gridCount) || 10));
    if (!(upper > lower)) return [];
    return gridLinePrices(lower, upper, levels);
  }, [isGrid, buyPrice, sellPrice, gridCount]);

  async function loadCandles(pair: string, tokenAddr: string) {
    setChartError(null);
    const timeframe = "max";
    const cached = readOhlcvCache(pair, tokenAddr, timeframe);
    const cachedOk = cached && isMaxHistoryUsable(cached.candles);

    if (isCacheFresh(cached) && cachedOk && cached) {
      setCandles(cached.candles);
      return;
    }

    if (cachedOk && cached) {
      setCandles(cached.candles);
    }

    try {
      const data = await api.ohlcv(pair, tokenAddr, timeframe);
      if (!isMaxHistoryUsable(data.candles)) {
        throw new Error(
          `Historial incompleto (${data.candles?.length || 0} velas). Reintenta.`
        );
      }
      setCandles(data.candles);
      writeOhlcvCache(pair, tokenAddr, timeframe, data.candles, data.source);
    } catch (e) {
      if (cachedOk && cached) {
        setCandles(cached.candles);
        setChartError(null);
      } else {
        setCandles([]);
        setChartError(e instanceof Error ? e.message : String(e));
      }
    }
  }

  useEffect(() => {
    let cancelled = false;
    async function syncFromOps() {
      try {
        const data = await api.listOps();
        if (cancelled) return;
        const list = data.operations || [];
        const running = list.filter((o) => o.status === "running" || o.status === "paused");
        setActiveOps(running);

        if (lastOpId && !list.some((o) => o.id === lastOpId)) {
          setLastOpId(null);
          focusOpIdRef.current = null;
        }

        const preferred =
          (lastOpId && list.find((o) => o.id === lastOpId)) ||
          running[0] ||
          list[0] ||
          null;

        const hasDraftToken = Boolean(draft?.tokenAddress || draft?.token);
        if (!hasDraftToken && preferred) {
          const form = applyOpToForm(preferred);
          setTokenAddress(form.tokenAddress);
          setBuyPrice(form.buyPrice);
          setSellPrice(form.sellPrice);
          setUsdcAmount(form.usdcAmount);
          setCycleMinutes(form.cycleMinutes);
          setGridCount(form.gridCount);
          setToken(form.token);
          setLastOpId(form.lastOpId);
          gridCenteredRef.current = true;
          if (form.token?.pair_address && form.token.address) {
            void loadCandles(form.token.pair_address, form.token.address);
          }
        } else if (draft?.token?.pair_address && draft.token.address) {
          void loadCandles(draft.token.pair_address, draft.token.address);
        }
      } catch {
        /* ignore */
      } finally {
        if (!cancelled) setHydrated(true);
      }
    }
    void syncFromOps();
    const id = window.setInterval(() => {
      void api.listOps().then((data) => {
        setActiveOps(
          (data.operations || []).filter((o) => o.status === "running" || o.status === "paused")
        );
      });
    }, 8000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    writeTradeDraft({
      tokenAddress,
      buyPrice,
      sellPrice,
      usdcAmount,
      cycleMinutes,
      tf: "max",
      lastOpId,
      token,
    });
  }, [hydrated, tokenAddress, buyPrice, sellPrice, usdcAmount, cycleMinutes, lastOpId, token]);

  useEffect(() => {
    setLogs([]);
    const es = new EventSource("/api/logs/stream?history=0");
    es.addEventListener("log", (ev) => {
      try {
        const entry = JSON.parse((ev as MessageEvent).data) as {
          id: string;
          ts: string;
          level: string;
          kind: string;
          message: string;
          data?: { id?: string };
        };
        const focus = focusOpIdRef.current;
        if (!focus) return;
        const opId = entry.data?.id;
        if (!opId || opId !== focus) return;
        setLogs((prev) => {
          if (prev.some((p) => p.id === entry.id)) return prev;
          return [...prev, entry].slice(-250);
        });
      } catch {
        /* ignore */
      }
    });
    return () => {
      es.close();
      setLogs([]);
    };
  }, []);

  useEffect(() => {
    setLogs([]);
  }, [lastOpId]);

  useEffect(() => {
    gridCenteredRef.current = false;
  }, [isGrid, tokenAddress]);

  // Recentrar BUY/SELL ±5% alrededor del spot si el rango está sesgado
  useEffect(() => {
    if (gridCenteredRef.current) return;
    const spot =
      (token?.price_usd && token.price_usd > 0 ? token.price_usd : null) ??
      (candles.length && candles[candles.length - 1].close > 0
        ? candles[candles.length - 1].close
        : null);
    if (!spot) return;
    const buy = Number(buyPrice);
    const sell = Number(sellPrice);
    if (!needsGridRecenter(buy, sell, spot)) {
      gridCenteredRef.current = true;
      return;
    }
    const band = suggestGridBounds(spot, 0.05);
    if (!band) return;
    setBuyPrice(band.buy);
    setSellPrice(band.sell);
    gridCenteredRef.current = true;
  }, [isGrid, token?.price_usd, candles, buyPrice, sellPrice]);

  async function refreshWallet() {
    try {
      setWalletBal(await api.wallet());
    } catch {
      setWalletBal({ configured: false, wallet: null, usdc_balance: 0 });
    }
  }

  useEffect(() => {
    void refreshWallet();
    const id = window.setInterval(() => void refreshWallet(), 30_000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  function loadOpIntoForm(op: Operation) {
    const form = applyOpToForm(op);
    setTokenAddress(form.tokenAddress);
    setBuyPrice(form.buyPrice);
    setSellPrice(form.sellPrice);
    setUsdcAmount(form.usdcAmount);
    setCycleMinutes(form.cycleMinutes);
    setGridCount(form.gridCount);
    setToken(form.token);
    setLastOpId(form.lastOpId);
    gridCenteredRef.current = true;
    if (form.token?.pair_address && form.token.address) {
      void loadCandles(form.token.pair_address, form.token.address);
    }
  }

  async function onLookup() {
    setError(null);
    setBusy(true);
    try {
      const info = await api.resolveToken(tokenAddress.trim());
      setToken(info);
      // Nuevo token → siempre recentrar BUY/SELL y subniveles al precio actual
      gridCenteredRef.current = false;
      if (info.price_usd != null && info.price_usd > 0) {
        const band = suggestGridBounds(info.price_usd, 0.05);
        if (band) {
          setBuyPrice(band.buy);
          setSellPrice(band.sell);
          gridCenteredRef.current = true;
        }
      }
      if (info.pair_address) await loadCandles(info.pair_address, info.address);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function launch(demo: boolean) {
    setError(null);
    setBusy(true);
    try {
      const grinds = Math.max(2, Math.floor(Number(gridCount) || 10));
      const op = await api.createOp({
        token_address: tokenAddress.trim(),
        buy_price_usd: Number(buyPrice),
        sell_price_usd: Number(sellPrice),
        usdc_amount: Number(usdcAmount),
        demo,
        cycle_seconds: 86400,
        start: true,
        mode: isGrid ? "grid" : "classic",
        grid_count: isGrid ? grinds : 0,
      });
      setLastOpId(op.id);
      if (op.token) setToken(op.token);
      setActiveOps((prev) => {
        const rest = prev.filter((p) => p.id !== op.id);
        return [op, ...rest];
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void launch(true);
  }

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <Link to="/" className="back-link">
            ← Menú
          </Link>
          <h1 className="brand">
            {isGrid ? (
              <>
                Autonomous <span>Trade</span>
              </>
            ) : (
              <>
                AutoTrade <span>Cryptos</span>
              </>
            )}
          </h1>
        </div>
        <div className="status-row">
          {activeOps.length > 0 && (
            <span className="pill ok">{activeOps.length} activas</span>
          )}
          <Link to="/operations" className="btn btn-ghost">
            Historial
          </Link>
        </div>
      </header>

      <div className="layout">
        <aside className="sidebar">
          <form className="stack" onSubmit={onSubmit}>
            <div className="wallet-strip">
              <div>
                <div className="wallet-label">Balance wallet</div>
                <div className="wallet-addr">
                  {walletBal?.configured
                    ? walletBal.wallet || "Conectada"
                    : "—"}
                </div>
              </div>
              <div className="wallet-bal">
                <strong>
                  $
                  {(walletBal?.usdc_balance ?? 0).toLocaleString("en-US", {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 4,
                  })}
                </strong>
                <span>USDC</span>
              </div>
            </div>

            {activeOps.length > 0 && (
              <div className="active-ops">
                <div className="wallet-label">Ops en curso / pausa</div>
                {activeOps.slice(0, 4).map((op) => (
                  <button
                    key={op.id}
                    type="button"
                    className="active-op-chip"
                    onClick={() => loadOpIntoForm(op)}
                  >
                    <span>
                      {op.token?.symbol || "OP"} · {op.status} ·{" "}
                      24h
                    </span>
                    <span className="muted">cargar</span>
                  </button>
                ))}
              </div>
            )}

            <div className="token-strip">
              {token?.image ? (
                <img src={token.image} alt={token.symbol} />
              ) : (
                <div className="token-fallback">—</div>
              )}
              <div>
                <div className="token-name">{token?.name || "Sin token"}</div>
                <div className="token-meta">
                  <span>{token?.symbol || "—"}</span>
                  <strong>{formatUsd(price)}</strong>
                </div>
              </div>
            </div>

            <div className="field">
              <label>Contrato</label>
              <div className="input-row">
                <input
                  value={tokenAddress}
                  onChange={(e) => setTokenAddress(e.target.value)}
                  placeholder="0x…"
                  spellCheck={false}
                  required
                />
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={onLookup}
                  disabled={busy || !tokenAddress}
                >
                  Buscar
                </button>
              </div>
            </div>

            <div className="field">
              <label>USDC a invertir</label>
              <input
                type="number"
                step="any"
                min="0"
                value={usdcAmount}
                onChange={(e) => setUsdcAmount(e.target.value)}
                required
              />
            </div>

            <div className="field">
              <label>Ciclo</label>
              <input type="text" value="24 horas" disabled readOnly />
            </div>

            {isGrid && (
              <div className="field">
                <label>Niveles de grid</label>
                <input
                  type="number"
                  step="1"
                  min="2"
                  max="100"
                  value={gridCount}
                  onChange={(e) => setGridCount(e.target.value)}
                  required
                />
                <button
                  type="button"
                  className="btn btn-ghost"
                  style={{ marginTop: "0.45rem", width: "100%" }}
                  onClick={() => {
                    const spot =
                      (token?.price_usd && token.price_usd > 0
                        ? token.price_usd
                        : null) ??
                      (candles.length && candles[candles.length - 1].close > 0
                        ? candles[candles.length - 1].close
                        : null);
                    if (!spot) return;
                    const band = suggestGridBounds(spot, 0.05);
                    if (!band) return;
                    setBuyPrice(band.buy);
                    setSellPrice(band.sell);
                    gridCenteredRef.current = true;
                  }}
                >
                  Centrar en precio actual
                </button>
              </div>
            )}

            <div className="levels">
              <div className="field">
                <label>Buy ≤</label>
                <input
                  type="number"
                  step="any"
                  value={buyPrice}
                  onChange={(e) => setBuyPrice(e.target.value)}
                  required
                />
              </div>
              <div className="field">
                <label>Sell ≥</label>
                <input
                  type="number"
                  step="any"
                  value={sellPrice}
                  onChange={(e) => setSellPrice(e.target.value)}
                  required
                />
              </div>
            </div>

            {estimate && (
              <div className="estimate">
                <div className="estimate-title">
                  {isGrid ? "Estimado" : "Si la operación sale bien"}
                </div>
                {isGrid && "grid" in estimate && estimate.grid ? (
                  <>
                    <div className="estimate-row">
                      <span>24h</span>
                      <strong>+${estimate.grid.profit_24h.toFixed(4)}</strong>
                    </div>
                    <div className="estimate-row">
                      <span>7d</span>
                      <strong>+${estimate.grid.profit_7d.toFixed(4)}</strong>
                    </div>
                    <div className="estimate-row">
                      <span>31d</span>
                      <strong>+${estimate.grid.profit_31d.toFixed(4)}</strong>
                    </div>
                    <div className="estimate-row">
                      <span>ROI 24h</span>
                      <strong>{estimate.grid.roi_24h_pct.toFixed(2)}%</strong>
                    </div>
                  </>
                ) : (
                  <>
                    <div className="estimate-row">
                      <span>Beneficio</span>
                      <strong>+${estimate.profit_usd.toFixed(4)}</strong>
                    </div>
                    <div className="estimate-row">
                      <span>Total final</span>
                      <strong>${estimate.total_usd.toFixed(4)}</strong>
                    </div>
                    <div className="estimate-row">
                      <span>ROI</span>
                      <strong>{estimate.roi_pct.toFixed(2)}%</strong>
                    </div>
                  </>
                )}
              </div>
            )}

            <div className="action-block">
              <button type="submit" className="btn btn-primary grow" disabled={busy}>
                Lanzar simulación
              </button>
              <button
                type="button"
                className="btn btn-accent grow"
                disabled={busy}
                onClick={() => void launch(false)}
              >
                Lanzar en vivo
              </button>
            </div>

            {lastOpId && activeOps.some((o) => o.id === lastOpId) && (
              <p className="hint-inline">
                <Link to="/operations">Operación · {lastOpId.slice(0, 8)}</Link>
              </p>
            )}
            {error && <div className="err">{error}</div>}
          </form>
        </aside>

        <main className="main">
          <div className="chart-head">
            <h2>Chart</h2>
            <span className="muted" style={{ fontSize: "0.75rem" }}>
              Historial máximo disponible
            </span>
          </div>

          <div className="chart-shell own">
            {candles.length > 0 ? (
              <TradingChart
                candles={candles}
                buyPrice={buyPrice}
                sellPrice={sellPrice}
                spotPrice={price}
                gridPrices={chartGridPrices}
                onBuyChange={setBuyPrice}
                onSellChange={setSellPrice}
              />
            ) : (
              <div className="chart-empty">{chartError || "Busca un token."}</div>
            )}
          </div>

          <section className="logs-panel">
            <div className="chart-head">
              <h2>Logs</h2>
            </div>
            <div className="logs">
              {logs.map((l) => (
                <div key={l.id} className={`log-line ${l.level}`}>
                  <span className="time">{timeLabel(l.ts)}</span>
                  <span className="kind">{l.kind}</span>
                  <span>{l.message}</span>
                </div>
              ))}
              <div ref={logEndRef} />
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}
