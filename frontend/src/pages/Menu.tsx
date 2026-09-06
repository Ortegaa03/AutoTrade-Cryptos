import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

export default function Menu() {
  const [running, setRunning] = useState(0);
  const [total, setTotal] = useState(0);

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const data = await api.listOps();
        if (!alive) return;
        const ops = data.operations || [];
        setTotal(ops.length);
        setRunning(ops.filter((o) => o.status === "running").length);
      } catch {
        /* ignore */
      }
    }
    void load();
    const id = window.setInterval(() => void load(), 8000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  return (
    <div className="menu-page">
      <div className="menu-hero">
        <p className="menu-kicker">World Chain</p>
        <h1 className="brand">
          AutoTrade <span>Cryptos</span>
        </h1>
        <p className="menu-sub">
          {running > 0
            ? `${running} en curso · ${total} en historial`
            : total > 0
              ? `${total} operaciones en historial`
              : "Elige el modo de ejecución"}
        </p>
      </div>

      <div className="menu-grid menu-grid-3">
        <Link to="/trade" className="menu-card">
          <span className="menu-n">01</span>
          <h2>AutoTrade</h2>
          <p>Compra y venta por niveles de precio. Una operación clásica.</p>
        </Link>
        <Link to="/trade?mode=grid" className="menu-card">
          <span className="menu-n">02</span>
          <h2>AutonomousTrade</h2>
          <p>Grid en el rango buy–sell. Reparte la inversión por niveles.</p>
        </Link>
        <Link to="/operations" className="menu-card">
          <span className="menu-n">03</span>
          <h2>Historial</h2>
          <p>
            {running > 0
              ? `${running} activas. Pausar, ciclo o eliminar.`
              : "Operaciones en curso, en pausa y completadas."}
          </p>
        </Link>
      </div>
    </div>
  );
}
