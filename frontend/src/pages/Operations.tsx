import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, Operation } from "../api";

const STATUS_LABEL: Record<string, string> = {
  draft: "Preparada",
  running: "En curso",
  paused: "Pausa",
  completed: "Completada",
  stopped: "Detenida",
};

function formatTs(iso?: string) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("es-ES", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}

function amountOutFromEvents(op: Operation, kind: "buy" | "sell"): number | null {
  if (kind === "buy" && op.buy_amount_out != null) return op.buy_amount_out;
  if (kind === "sell" && op.sell_amount_out != null) return op.sell_amount_out;
  for (const ev of [...(op.events || [])].reverse()) {
    if (ev.kind !== kind) continue;
    const d = ev.data || {};
    const raw = d.amount_out ?? (kind === "buy" ? d.tokens : d.proceeds);
    if (typeof raw === "number" && Number.isFinite(raw)) return raw;
    if (typeof raw === "string" && raw.trim() && !Number.isNaN(Number(raw))) return Number(raw);
  }
  return null;
}

export default function Operations() {
  const [ops, setOps] = useState<Operation[]>([]);
  const [filter, setFilter] = useState<"all" | "running" | "paused" | "completed">("all");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);

  async function refresh() {
    const data = await api.listOps();
    setOps(data.operations);
  }

  useEffect(() => {
    void refresh().catch((e) => setError(String(e)));
    const id = window.setInterval(() => {
      void refresh().catch(() => undefined);
    }, 2500);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    if (!flash) return;
    const t = window.setTimeout(() => setFlash(null), 3500);
    return () => window.clearTimeout(t);
  }, [flash]);

  const filtered = useMemo(() => {
    if (filter === "all") return ops;
    return ops.filter((o) => o.status === filter);
  }, [ops, filter]);

  async function act(id: string, label: string, fn: () => Promise<unknown>) {
    setBusy(id);
    setError(null);
    try {
      await fn();
      await refresh();
      setFlash(`${label} · ok`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <Link to="/" className="back-link">
            ← Menú
          </Link>
          <h1 className="brand">
            Historial <span>ops</span>
          </h1>
        </div>
        <div className="status-row">
          <span className="pill muted">{ops.length} total</span>
          <span className="pill ok">{ops.filter((o) => o.status === "running").length} en curso</span>
        </div>
      </header>

      <div className="filters">
        {(
          [
            ["all", "Todas"],
            ["running", "En curso"],
            ["paused", "Pausa"],
            ["completed", "Completadas"],
          ] as const
        ).map(([k, label]) => (
          <button
            key={k}
            type="button"
            className={`btn btn-ghost ${filter === k ? "active" : ""}`}
            onClick={() => setFilter(k)}
          >
            {label}
          </button>
        ))}
      </div>

      {flash && <div className="flash-ok">{flash}</div>}
      {error && <div className="err">{error}</div>}

      <div className="ops-list">
        {filtered.length === 0 && <div className="empty">No hay operaciones en este filtro.</div>}
        {filtered.map((op) => {
          const open = openId === op.id;
          const events = [...(op.events || [])].reverse();
          const zombie = op.status === "running" && op.task_alive === false;
          const buyOut = amountOutFromEvents(op, "buy");
          const sellOut = amountOutFromEvents(op, "sell");
          return (
            <article key={op.id} className={`op-card status-${op.status}`}>
              <div className="op-top">
                <div className="op-token">
                  {op.token?.image ? (
                    <img src={op.token.image} alt="" />
                  ) : (
                    <div className="token-fallback">—</div>
                  )}
                  <div>
                    <strong>{op.token?.name || "Token"}</strong>
                    <div className="muted">
                      {op.token?.symbol || "—"} · {op.demo ? "DEMO" : "LIVE"}
                      {op.mode === "grid" ? " · GRID" : ""} · {op.id.slice(0, 8)}
                      {zombie ? " · loop reiniciando…" : ""}
                    </div>
                  </div>
                </div>
                <span className={`pill status ${op.status}`}>
                  {STATUS_LABEL[op.status] || op.status}
                </span>
              </div>

              <div className="op-grid">
                <div>
                  <span className="lbl">Buy ≤</span>
                  <strong>${op.buy_price_usd}</strong>
                </div>
                <div>
                  <span className="lbl">Sell ≥</span>
                  <strong>${op.sell_price_usd}</strong>
                </div>
                <div>
                  <span className="lbl">Inversión</span>
                  <strong>{op.usdc_amount} USDC</strong>
                </div>
                <div>
                  <span className="lbl">Ciclo</span>
                  <strong>24h · cron diario</strong>
                </div>
                <div>
                  <span className="lbl">Último precio</span>
                  <strong>
                    {op.last_price_usd != null ? `$${Number(op.last_price_usd).toFixed(8)}` : "—"}
                  </strong>
                </div>
                <div>
                  <span className="lbl">Último ciclo</span>
                  <strong>{formatTs(op.last_cycle_at || undefined)}</strong>
                </div>
                <div>
                  <span className="lbl">Est. beneficio</span>
                  <strong className="pos">
                    +${(op.estimated?.profit_usd ?? 0).toFixed(4)} (
                    {(op.estimated?.roi_pct ?? 0).toFixed(2)}%)
                  </strong>
                </div>
                {op.realized_profit_usd != null && (
                  <div>
                    <span className="lbl">Realizado</span>
                    <strong className="pos">
                      +${op.realized_profit_usd.toFixed(4)} · total $
                      {(op.realized_total_usd ?? 0).toFixed(4)}
                    </strong>
                  </div>
                )}
                <div>
                  <span className="lbl">Fase</span>
                  <strong>{op.phase}</strong>
                </div>
                {op.mode === "grid" && (
                  <div>
                    <span className="lbl">Grinds</span>
                    <strong>
                      {op.grid_count || 0} · {(op.grid_levels || []).length} niveles buy
                    </strong>
                  </div>
                )}
                {buyOut != null && (
                  <div>
                    <span className="lbl">Amount out (buy)</span>
                    <strong>
                      {buyOut.toLocaleString("en-US", {
                        maximumFractionDigits: 8,
                      })}{" "}
                      {op.token?.symbol || "TOKEN"}
                    </strong>
                  </div>
                )}
                {sellOut != null && (
                  <div>
                    <span className="lbl">Amount out (sell)</span>
                    <strong>
                      $
                      {sellOut.toLocaleString("en-US", {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 6,
                      })}{" "}
                      USDC
                    </strong>
                  </div>
                )}
              </div>

              <div className="op-actions">
                <button
                  type="button"
                  className={`btn btn-ghost ${open ? "active" : ""}`}
                  onClick={() => setOpenId(open ? null : op.id)}
                >
                  {open ? "Ocultar detalles" : "Mostrar más detalles"}
                </button>
                {(op.status === "paused" || op.status === "draft" || op.status === "stopped") && (
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={busy === op.id}
                    onClick={() => act(op.id, "Reanudada", () => api.resumeOp(op.id))}
                  >
                    {busy === op.id ? "…" : "Reanudar"}
                  </button>
                )}
                {op.status === "running" && (
                  <>
                    <button
                      type="button"
                      className="btn btn-warn"
                      disabled={busy === op.id}
                      onClick={() => act(op.id, "Pausada", () => api.pauseOp(op.id))}
                    >
                      {busy === op.id ? "…" : "Pausar"}
                    </button>
                    <button
                      type="button"
                      className="btn btn-soft"
                      disabled={busy === op.id}
                      onClick={() => act(op.id, "Ciclo forzado", () => api.cycleOp(op.id))}
                    >
                      {busy === op.id ? "…" : "Ciclo"}
                    </button>
                  </>
                )}
                {op.status !== "completed" && (
                  <button
                    type="button"
                    className="btn btn-outline"
                    disabled={busy === op.id}
                    onClick={() => act(op.id, "Stop", () => api.stopOp(op.id))}
                  >
                    {busy === op.id ? "…" : "Stop"}
                  </button>
                )}
                <button
                  type="button"
                  className="btn btn-danger"
                  disabled={busy === op.id}
                  onClick={() => {
                    if (confirm("¿Eliminar esta operación?")) {
                      void act(op.id, "Eliminada", () => api.deleteOp(op.id));
                    }
                  }}
                >
                  Eliminar
                </button>
              </div>

              {open && (
                <section className="op-report">
                  <h3>Informe de la operación</h3>
                  <div className="report-meta">
                    <div>
                      <span className="lbl">ID</span>
                      <code>{op.id}</code>
                    </div>
                    <div>
                      <span className="lbl">Creada</span>
                      <strong>{formatTs(op.created_at)}</strong>
                    </div>
                    <div>
                      <span className="lbl">Actualizada</span>
                      <strong>{formatTs(op.updated_at)}</strong>
                    </div>
                    <div>
                      <span className="lbl">Completada</span>
                      <strong>{formatTs(op.completed_at || undefined)}</strong>
                    </div>
                    <div>
                      <span className="lbl">Buy tx</span>
                      <code>{op.buy_tx || "—"}</code>
                    </div>
                    <div>
                      <span className="lbl">Sell tx</span>
                      <code>{op.sell_tx || "—"}</code>
                    </div>
                    <div>
                      <span className="lbl">Amount out compra</span>
                      <strong>
                        {buyOut != null
                          ? `${buyOut.toLocaleString("en-US", {
                              maximumFractionDigits: 8,
                            })} ${op.token?.symbol || "TOKEN"}`
                          : "—"}
                      </strong>
                    </div>
                    <div>
                      <span className="lbl">Amount out venta</span>
                      <strong>
                        {sellOut != null
                          ? `$${sellOut.toLocaleString("en-US", {
                              minimumFractionDigits: 2,
                              maximumFractionDigits: 6,
                            })} USDC`
                          : "—"}
                      </strong>
                    </div>
                    {op.mode === "grid" && (op.grid_levels || []).length > 0 && (
                      <div className="report-grid-levels">
                        <span className="lbl">Niveles grid</span>
                        <ul className="grid-level-list">
                          {(op.grid_levels || []).map((lv) => (
                            <li key={lv.index}>
                              G{lv.index} · buy ${Number(lv.buy_price).toPrecision(6)} → sell $
                              {Number(lv.sell_price).toPrecision(6)} · {lv.alloc_pct?.toFixed?.(1) ?? lv.alloc_pct}% ·{" "}
                              {lv.status}
                              {lv.realized_usd
                                ? ` · +$${Number(lv.realized_usd).toFixed(4)}`
                                : ""}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {op.error && (
                      <div className="report-error">
                        <span className="lbl">Último error</span>
                        <strong>{op.error}</strong>
                      </div>
                    )}
                  </div>

                  <h4>Timeline</h4>
                  {events.length === 0 ? (
                    <p className="muted">Sin eventos registrados todavía.</p>
                  ) : (
                    <ol className="timeline">
                      {events.map((ev) => (
                        <li key={ev.id} className={`tl-item kind-${ev.kind}`}>
                          <div className="tl-time">{formatTs(ev.ts)}</div>
                          <div className="tl-body">
                            <div className="tl-title">
                              <span className="tl-kind">{ev.kind}</span>
                              {ev.title}
                            </div>
                            {ev.detail && <p>{ev.detail}</p>}
                            {ev.data?.amount_out != null && (
                              <p className="tl-amount">
                                Amount out:{" "}
                                <strong>
                                  {typeof ev.data.amount_out === "number"
                                    ? ev.kind === "sell"
                                      ? `$${Number(ev.data.amount_out).toFixed(6)} USDC`
                                      : Number(ev.data.amount_out).toLocaleString("en-US", {
                                          maximumFractionDigits: 8,
                                        })
                                    : String(ev.data.amount_out)}
                                </strong>
                              </p>
                            )}
                          </div>
                        </li>
                      ))}
                    </ol>
                  )}
                </section>
              )}
            </article>
          );
        })}
      </div>
    </div>
  );
}
