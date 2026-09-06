import { useEffect, useRef } from "react";
import {
  AutoscaleInfo,
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  IChartApi,
  IPriceLine,
  ISeriesApi,
  createChart,
} from "lightweight-charts";

export type Candle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
};

type Props = {
  candles: Candle[];
  buyPrice: string;
  sellPrice: string;
  spotPrice?: number | null;
  gridPrices?: number[];
  onBuyChange: (v: string) => void;
  onSellChange: (v: string) => void;
};

function parseNum(s: string) {
  const n = Number(s);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function fmt(n: number) {
  if (n >= 1) return n.toFixed(4);
  if (n >= 0.01) return n.toFixed(6);
  if (n >= 0.0001) return n.toFixed(8);
  return n.toFixed(10);
}

function priceFormatFor(values: number[]) {
  const positives = values.filter((v) => Number.isFinite(v) && v > 0);
  const sample = positives.length ? Math.min(...positives) : 1;
  if (sample >= 1) return { type: "price" as const, precision: 4, minMove: 0.0001 };
  if (sample >= 0.01) return { type: "price" as const, precision: 6, minMove: 0.000001 };
  if (sample >= 0.0001) return { type: "price" as const, precision: 8, minMove: 0.00000001 };
  return { type: "price" as const, precision: 10, minMove: 0.0000000001 };
}

function axisLabel(n: number) {
  if (n >= 1) return n.toFixed(4);
  if (n >= 0.01) return n.toFixed(6);
  if (n >= 0.0001) return n.toFixed(8);
  return n.toFixed(10);
}

/** Separación mínima (px) entre etiquetas del eje para no solaparse. */
const GRID_LABEL_MIN_PX = 22;

type GridLineEntry = {
  line: IPriceLine;
  price: number;
  isBuy: boolean;
  labelVisible: boolean;
};

export default function TradingChart({
  candles,
  buyPrice,
  sellPrice,
  spotPrice,
  gridPrices = [],
  onBuyChange,
  onSellChange,
}: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const buyLineRef = useRef<IPriceLine | null>(null);
  const sellLineRef = useRef<IPriceLine | null>(null);
  const spotLineRef = useRef<IPriceLine | null>(null);
  const gridLinesRef = useRef<GridLineEntry[]>([]);
  const dragRef = useRef<"buy" | "sell" | null>(null);
  const propsRef = useRef({ buyPrice, sellPrice, onBuyChange, onSellChange });
  const syncGridLabelsRef = useRef<() => void>(() => {});

  propsRef.current = { buyPrice, sellPrice, onBuyChange, onSellChange };

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;

    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#050807" },
        textColor: "#8aa897",
        fontFamily: "IBM Plex Mono, monospace",
      },
      grid: {
        vertLines: { color: "rgba(140,200,170,0.06)" },
        horzLines: { color: "rgba(140,200,170,0.06)" },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: {
        borderColor: "rgba(140,200,170,0.15)",
        entireTextOnly: true,
        alignLabels: true,
      },
      timeScale: { borderColor: "rgba(140,200,170,0.15)", timeVisible: true },
      localization: {
        priceFormatter: (price: number) => axisLabel(price),
      },
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#3dffa8",
      downColor: "#ff6b6b",
      borderUpColor: "#3dffa8",
      borderDownColor: "#ff6b6b",
      wickUpColor: "#3dffa8",
      wickDownColor: "#ff6b6b",
      // Evita la línea verde automática del último close (conflicto con PRICE/BUY)
      lastValueVisible: false,
      priceLineVisible: false,
      priceFormat: {
        type: "price",
        precision: 8,
        minMove: 0.00000001,
      },
    });

    chartRef.current = chart;
    seriesRef.current = series;

    const onMove = (e: PointerEvent) => {
      const seriesApi = seriesRef.current;
      const wrap = wrapRef.current;
      if (!dragRef.current || !seriesApi || !wrap) return;
      const rect = wrap.getBoundingClientRect();
      const y = e.clientY - rect.top;
      const price = seriesApi.coordinateToPrice(y);
      if (price == null || !Number.isFinite(price) || price <= 0) return;
      const { buyPrice: bp, sellPrice: sp, onBuyChange: setBuy, onSellChange: setSell } =
        propsRef.current;
      if (dragRef.current === "buy") {
        const sellN = parseNum(sp);
        setBuy(fmt(sellN != null ? Math.min(price, sellN * 0.9999) : price));
      } else {
        const buyN = parseNum(bp);
        setSell(fmt(buyN != null ? Math.max(price, buyN * 1.0001) : price));
      }
    };
    const onUp = () => {
      dragRef.current = null;
      if (wrapRef.current) wrapRef.current.style.cursor = "";
      // Tras zoom/pan en el eje de precio, recalcular labels de grinds
      requestAnimationFrame(() => syncGridLabelsRef.current());
    };
    const onDown = (e: PointerEvent) => {
      const seriesApi = seriesRef.current;
      const wrap = wrapRef.current;
      if (!seriesApi || !wrap) return;
      const rect = wrap.getBoundingClientRect();
      const y = e.clientY - rect.top;
      const buy = parseNum(propsRef.current.buyPrice);
      const sell = parseNum(propsRef.current.sellPrice);
      const buyY = buy != null ? seriesApi.priceToCoordinate(buy) : null;
      const sellY = sell != null ? seriesApi.priceToCoordinate(sell) : null;
      const hit = 14; // px — arrastrar desde la línea / label nativo
      const nearBuy = buyY != null && Math.abs(y - buyY) <= hit;
      const nearSell = sellY != null && Math.abs(y - sellY) <= hit;
      if (!nearBuy && !nearSell) return;
      e.preventDefault();
      if (nearBuy && nearSell) {
        dragRef.current = Math.abs(y - (buyY as number)) <= Math.abs(y - (sellY as number))
          ? "buy"
          : "sell";
      } else {
        dragRef.current = nearBuy ? "buy" : "sell";
      }
      wrap.style.cursor = "ns-resize";
    };

    const onWheel = () => {
      requestAnimationFrame(() => syncGridLabelsRef.current());
    };

    // Mientras se arrastra el eje de precio (zoom vertical), ir actualizando
    let scaleDrag = false;
    const onPointerDownScale = (e: PointerEvent) => {
      const rect = el.getBoundingClientRect();
      // Eje derecho ≈ últimos ~64px
      if (e.clientX >= rect.right - 64) scaleDrag = true;
    };
    const onPointerMoveScale = () => {
      if (!scaleDrag) return;
      syncGridLabelsRef.current();
    };
    const onPointerUpScale = () => {
      if (scaleDrag) {
        scaleDrag = false;
        syncGridLabelsRef.current();
      }
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointermove", onPointerMoveScale);
    window.addEventListener("pointerup", onPointerUpScale);
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointerdown", onPointerDownScale);
    el.addEventListener("wheel", onWheel, { passive: true });

    const ro = new ResizeObserver(() => {
      requestAnimationFrame(() => syncGridLabelsRef.current());
    });
    ro.observe(el);

    chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
      requestAnimationFrame(() => syncGridLabelsRef.current());
    });

    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointermove", onPointerMoveScale);
      window.removeEventListener("pointerup", onPointerUpScale);
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointerdown", onPointerDownScale);
      el.removeEventListener("wheel", onWheel);
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      buyLineRef.current = null;
      sellLineRef.current = null;
      spotLineRef.current = null;
      gridLinesRef.current = [];
    };
  }, []);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    if (!candles.length) {
      series.setData([]);
      return;
    }
    const format = priceFormatFor(candles.flatMap((c) => [c.open, c.high, c.low, c.close]));
    series.applyOptions({ priceFormat: format });
    chartRef.current?.applyOptions({
      localization: {
        priceFormatter: (price: number) => axisLabel(price),
      },
    });
    series.setData(
      candles.map((c) => ({
        time: c.time as import("lightweight-charts").UTCTimestamp,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      }))
    );
    chartRef.current?.timeScale().fitContent();
  }, [candles]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;

    const buy = parseNum(buyPrice);
    const sell = parseNum(sellPrice);
    const lastClose =
      candles.length > 0 && Number.isFinite(candles[candles.length - 1].close)
        ? candles[candles.length - 1].close
        : null;
    // Preferir close del chart para PRICE (alineado con velas)
    const spot =
      lastClose && lastClose > 0
        ? lastClose
        : spotPrice && spotPrice > 0
          ? spotPrice
          : null;
    const near = (a: number, b: number) => Math.abs(a - b) / Math.max(a, b, 1e-12) < 1e-6;

    // Incluir bounds/grid en el autoscale para que no se apilen arriba del chart
    const extras = [
      buy,
      sell,
      spot,
      ...gridPrices.filter((p) => Number.isFinite(p) && p > 0),
    ].filter((p): p is number => p != null && p > 0);

    series.applyOptions({
      autoscaleInfoProvider: (original: () => AutoscaleInfo | null) => {
        const base = original();
        if (!extras.length) return base;
        if (base?.priceRange) {
          return {
            ...base,
            priceRange: {
              minValue: Math.min(base.priceRange.minValue, ...extras),
              maxValue: Math.max(base.priceRange.maxValue, ...extras),
            },
          };
        }
        return {
          priceRange: {
            minValue: Math.min(...extras),
            maxValue: Math.max(...extras),
          },
        };
      },
    });

    // Bounds BUY / SELL (siempre sólidas y claras)
    if (buy != null) {
      const buyOpts = {
        price: buy,
        color: "#3dffa8",
        lineWidth: 2 as const,
        lineStyle: 0 as const,
        axisLabelVisible: true,
        title: "BUY",
      };
      if (!buyLineRef.current) {
        buyLineRef.current = series.createPriceLine(buyOpts);
      } else {
        buyLineRef.current.applyOptions(buyOpts);
      }
    }

    if (sell != null) {
      const sellOpts = {
        price: sell,
        color: "#ff7a59",
        lineWidth: 2 as const,
        lineStyle: 0 as const,
        axisLabelVisible: true,
        title: "SELL",
      };
      if (!sellLineRef.current) {
        sellLineRef.current = series.createPriceLine(sellOpts);
      } else {
        sellLineRef.current.applyOptions(sellOpts);
      }
    }

    // Precio actual
    if (spot != null) {
      const spotOpts = {
        price: spot,
        color: "rgba(255,255,255,0.85)",
        lineWidth: 2 as const,
        lineStyle: 2 as const,
        axisLabelVisible: true,
        title: "PRICE",
      };
      if (!spotLineRef.current) {
        spotLineRef.current = series.createPriceLine(spotOpts);
      } else {
        spotLineRef.current.applyOptions(spotOpts);
      }
    }

    // Limpiar grinds anteriores
    for (const entry of gridLinesRef.current) {
      try {
        series.removePriceLine(entry.line);
      } catch {
        /* ignore */
      }
    }
    gridLinesRef.current = [];

    if (buy != null && sell != null && gridPrices.length > 0) {
      const mid = spot ?? (buy + sell) / 2;
      // Subniveles: líneas siempre; labels solo si hay espacio (syncGridLabels)
      for (const gp of gridPrices) {
        if (!Number.isFinite(gp) || gp <= 0) continue;
        if (near(gp, buy) || near(gp, sell)) continue;
        if (spot != null && near(gp, spot)) continue;
        if (gp <= buy || gp >= sell) continue;

        const isBuyGrind = gp < mid;
        const line = series.createPriceLine({
          price: gp,
          color: isBuyGrind ? "rgba(61,255,168,0.55)" : "rgba(255,122,89,0.55)",
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: false,
          title: "",
        });
        gridLinesRef.current.push({ line, price: gp, isBuy: isBuyGrind, labelVisible: false });
      }
    }

    const syncGridLabels = () => {
      const s = seriesRef.current;
      if (!s || !gridLinesRef.current.length) return;

      // Anclas fijas (BUY / SELL / PRICE) — nunca se ocultan
      const anchors: number[] = [];
      for (const p of [parseNum(propsRef.current.buyPrice), parseNum(propsRef.current.sellPrice)]) {
        if (p == null) continue;
        const y = s.priceToCoordinate(p);
        if (y != null && Number.isFinite(y)) anchors.push(y);
      }
      // spot: última vela o prop
      const spotYPrice =
        candles.length > 0 && Number.isFinite(candles[candles.length - 1].close)
          ? candles[candles.length - 1].close
          : spotPrice && spotPrice > 0
            ? spotPrice
            : null;
      if (spotYPrice != null) {
        const y = s.priceToCoordinate(spotYPrice);
        if (y != null && Number.isFinite(y)) anchors.push(y);
      }

      // Candidatos de grind con su coordenada Y
      const candidates: { entry: GridLineEntry; y: number }[] = [];
      for (const entry of gridLinesRef.current) {
        const y = s.priceToCoordinate(entry.price);
        if (y == null || !Number.isFinite(y)) continue;
        candidates.push({ entry, y: Number(y) });
      }
      candidates.sort((a, b) => a.y - b.y);

      const taken = [...anchors].sort((a, b) => a - b);
      const show = new Set<GridLineEntry>();

      const fits = (y: number) =>
        taken.every((t) => Math.abs(t - y) >= GRID_LABEL_MIN_PX);

      for (const { entry, y } of candidates) {
        if (!fits(y)) continue;
        show.add(entry);
        taken.push(y);
        taken.sort((a, b) => a - b);
      }

      for (const entry of gridLinesRef.current) {
        const want = show.has(entry);
        if (want === entry.labelVisible) continue;
        entry.labelVisible = want;
        entry.line.applyOptions({
          axisLabelVisible: want,
          title: want ? (entry.isBuy ? "buy" : "sell") : "",
        });
      }
    };

    syncGridLabelsRef.current = syncGridLabels;
    // Tras layout/autoscale
    requestAnimationFrame(() => {
      syncGridLabels();
      requestAnimationFrame(syncGridLabels);
    });
  }, [buyPrice, sellPrice, spotPrice, gridPrices, candles]);

  return (
    <div className="trading-chart">
      <div
        className="chart-canvas"
        ref={wrapRef}
        title="Arrastra BUY/SELL · zoom en el eje de precio para ver labels de grid"
      />
    </div>
  );
}
