import { useEffect, useRef, useState } from "react";
import {
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
  const gridLinesRef = useRef<IPriceLine[]>([]);
  const dragRef = useRef<"buy" | "sell" | null>(null);
  const propsRef = useRef({ buyPrice, sellPrice, onBuyChange, onSellChange });
  const [handles, setHandles] = useState<{ buy: number | null; sell: number | null }>({
    buy: null,
    sell: null,
  });

  propsRef.current = { buyPrice, sellPrice, onBuyChange, onSellChange };

  function syncHandles() {
    const series = seriesRef.current;
    if (!series) return;
    const buy = parseNum(propsRef.current.buyPrice);
    const sell = parseNum(propsRef.current.sellPrice);
    setHandles({
      buy: buy != null ? series.priceToCoordinate(buy) : null,
      sell: sell != null ? series.priceToCoordinate(sell) : null,
    });
  }

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
        entireTextOnly: false,
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
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    chart.timeScale().subscribeVisibleLogicalRangeChange(() => syncHandles());

    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
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
    requestAnimationFrame(syncHandles);
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
    const isGrid = gridPrices.length > 0;

    const near = (a: number, b: number) => Math.abs(a - b) / Math.max(a, b, 1e-12) < 1e-6;

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
    for (const line of gridLinesRef.current) {
      try {
        series.removePriceLine(line);
      } catch {
        /* ignore */
      }
    }
    gridLinesRef.current = [];

    if (isGrid && buy != null && sell != null) {
      const mid = spot ?? (buy + sell) / 2;
      // Niveles interiores: buy grinds bajo PRICE, sell grinds sobre PRICE
      for (const gp of gridPrices) {
        if (!Number.isFinite(gp) || gp <= 0) continue;
        if (near(gp, buy) || near(gp, sell)) continue;
        if (spot != null && near(gp, spot)) continue;

        const isBuyGrind = gp < mid;
        const isSellGrind = gp > mid;
        if (!isBuyGrind && !isSellGrind) continue;

        const line = series.createPriceLine({
          price: gp,
          color: isBuyGrind ? "rgba(61,255,168,0.7)" : "rgba(255,122,89,0.7)",
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          // Sin título BUY/SELL para no confundir con los bounds
          title: isBuyGrind ? "· buy" : "· sell",
        });
        gridLinesRef.current.push(line);
      }
    }

    requestAnimationFrame(syncHandles);
  }, [buyPrice, sellPrice, spotPrice, gridPrices, candles]);

  return (
    <div className="trading-chart">
      <div className="chart-canvas" ref={wrapRef} />
      {handles.buy != null && (
        <button
          type="button"
          className="chart-handle buy"
          style={{ top: handles.buy }}
          onPointerDown={(e) => {
            e.preventDefault();
            dragRef.current = "buy";
          }}
        >
          BUY
        </button>
      )}
      {handles.sell != null && (
        <button
          type="button"
          className="chart-handle sell"
          style={{ top: handles.sell }}
          onPointerDown={(e) => {
            e.preventDefault();
            dragRef.current = "sell";
          }}
        >
          SELL
        </button>
      )}
    </div>
  );
}
