import clsx from "clsx";
import { BarChart3, LineChart as LineIcon, PieChart as PieIcon, Table2 } from "lucide-react";
import { useLayoutEffect, useRef, useState, type MouseEvent, type ReactNode } from "react";

export type ChartType = "bar" | "line" | "pie" | "table";

export const CHART_META: Record<ChartType, { label: string; icon: any }> = {
  bar: { label: "Bar chart", icon: BarChart3 },
  line: { label: "Line chart", icon: LineIcon },
  pie: { label: "Pie chart", icon: PieIcon },
  table: { label: "Table", icon: Table2 },
};

/* Categorical palette (fixed order, validated for CVD separation); dark column is the same hues stepped for dark surfaces. */
const CAT_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"];
const CAT_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300"];
const OTHER = "#94a3b8";
export const isDark = () => typeof document !== "undefined" && document.documentElement.classList.contains("dark");
export const catColor = (i: number) => (isDark() ? CAT_DARK : CAT_LIGHT)[i % 6];

/* Decision (status) colours: always paired with a text label. */
export const BRAND = "rgb(var(--brand-500))";
export const DEC_HEX = { APPROVED: "#10b981", NEEDS_REVIEW: "#f59e0b", REJECTED: "#f43f5e" } as const;

/** Small icon segmented control that switches a widget's chart type. */
export function ChartSwitch({ value, options, onChange, title }: { value: ChartType; options: ChartType[]; onChange: (t: ChartType) => void; title: string }) {
  return (
    <div role="radiogroup" aria-label={`${title} chart type`} className="flex items-center rounded-md border border-slate-200 p-0.5">
      {options.map((t) => {
        const M = CHART_META[t];
        const on = t === value;
        return (
          <button
            key={t}
            role="radio"
            aria-checked={on}
            aria-label={M.label}
            title={M.label}
            onClick={() => onChange(t)}
            className={clsx("grid h-6 w-6 place-items-center rounded transition-colors", on ? "bg-slate-100 text-slate-900" : "text-slate-400 hover:text-slate-700")}
          >
            <M.icon className="h-3.5 w-3.5" />
          </button>
        );
      })}
    </div>
  );
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    if (!ref.current) return;
    setW(ref.current.getBoundingClientRect().width);
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

function niceMax(v: number) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}

/* ---------------- donut ---------------- */

export type Slice = { label: string; value: number; color: string; display?: string; onClick?: () => void; active?: boolean };

/** Donut with legend. Folds anything past `max` slices into "Other". */
export function Donut({ items, max = 6, center, fmt = (v) => String(v) }: { items: Slice[]; max?: number; center?: ReactNode; fmt?: (v: number) => string }) {
  const [hover, setHover] = useState<number | null>(null);
  let data = items.filter((s) => s.value > 0);
  if (data.length > max) {
    const rest = data.slice(max - 1);
    data = [...data.slice(0, max - 1), { label: `Other (${rest.length})`, value: rest.reduce((a, s) => a + s.value, 0), color: OTHER }];
  }
  const total = data.reduce((a, s) => a + s.value, 0);
  if (!total) return <p className="py-6 text-center text-sm text-slate-500">Nothing to show.</p>;

  const R = 60, r = 40, C = 70;
  let a0 = -Math.PI / 2;
  const arcs = data.map((s) => {
    const a1 = a0 + (s.value / total) * Math.PI * 2;
    const large = a1 - a0 > Math.PI ? 1 : 0;
    const p = (rad: number, ang: number) => `${C + rad * Math.cos(ang)} ${C + rad * Math.sin(ang)}`;
    const full = data.length === 1;
    const d = full
      ? `M ${C} ${C - R} A ${R} ${R} 0 1 1 ${C - 0.01} ${C - R} L ${C - 0.01} ${C - r} A ${r} ${r} 0 1 0 ${C} ${C - r} Z`
      : `M ${p(R, a0)} A ${R} ${R} 0 ${large} 1 ${p(R, a1)} L ${p(r, a1)} A ${r} ${r} 0 ${large} 0 ${p(r, a0)} Z`;
    a0 = a1;
    return d;
  });
  const h = hover != null ? data[hover] : null;

  return (
    <div className="flex flex-col items-center gap-4 sm:flex-row">
      <div className="relative shrink-0">
        <svg viewBox="0 0 140 140" className="h-36 w-36" role="img" aria-label={data.map((s) => `${s.label} ${fmt(s.value)}`).join(", ")}>
          {arcs.map((d, i) => (
            <path
              key={i}
              d={d}
              style={{ fill: data[i].color }}
              className={clsx("stroke-white transition-opacity", hover != null && hover !== i && "opacity-40", data[i].onClick && "cursor-pointer")}
              strokeWidth={2}
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
              onClick={data[i].onClick}
            />
          ))}
        </svg>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center">
          {h ? (
            <>
              <span className="num text-base font-semibold text-slate-900">{h.display ?? fmt(h.value)}</span>
              <span className="num text-[11px] text-slate-500">{Math.round((h.value / total) * 100)}%</span>
            </>
          ) : (
            center
          )}
        </div>
      </div>
      <ul className="min-w-0 flex-1 space-y-1.5 self-stretch sm:self-center">
        {data.map((s, i) => {
          const row = (
            <>
              <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: s.color }} />
              <span className="min-w-0 flex-1 truncate text-left text-slate-700">{s.label}</span>
              <span className="num text-slate-800">{s.display ?? fmt(s.value)}</span>
              <span className="num w-9 text-right text-xs text-slate-400">{Math.round((s.value / total) * 100)}%</span>
            </>
          );
          const cls = clsx("flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-[13px] transition-colors", hover === i && "bg-slate-50", s.active && "ring-1 ring-brand-500/40");
          return (
            <li key={s.label} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              {s.onClick ? (
                <button className={clsx(cls, "hover:bg-slate-50")} onClick={s.onClick} aria-pressed={s.active}>{row}</button>
              ) : (
                <div className={cls}>{row}</div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/* ---------------- line ---------------- */

export type LineSeries = { key: string; label: string; color: string; values: number[] };

/** Multi-series line chart with a crosshair tooltip. One y-axis only. */
export function LineChart({ xs, series, xLabel = (x) => x, fmt = (v) => String(v), height = 176, legend = true }: { xs: string[]; series: LineSeries[]; xLabel?: (x: string) => string; fmt?: (v: number) => string; height?: number; legend?: boolean }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hi, setHi] = useState<number | null>(null);
  if (!xs.length) return <p className="py-6 text-center text-sm text-slate-500">Nothing to show.</p>;

  const padL = 40, padR = 10, padT = 10, padB = 22;
  const W = Math.max(width, 120), H = height;
  const iw = W - padL - padR, ih = H - padT - padB;
  const max = niceMax(Math.max(0, ...series.flatMap((s) => s.values)));
  const x = (i: number) => padL + (xs.length === 1 ? iw / 2 : (i / (xs.length - 1)) * iw);
  const y = (v: number) => padT + ih - (v / max) * ih;
  const ticks = [0, max / 2, max];
  const labelEvery = Math.max(1, Math.ceil(xs.length / Math.max(2, Math.floor(iw / 64))));

  function onMove(e: MouseEvent<SVGRectElement>) {
    const box = (e.currentTarget as SVGRectElement).getBoundingClientRect();
    const px = e.clientX - box.left;
    const i = xs.length === 1 ? 0 : Math.round((px / box.width) * (xs.length - 1));
    setHi(Math.max(0, Math.min(xs.length - 1, i)));
  }

  return (
    <div ref={ref} className="relative w-full">
      {legend && series.length > 1 && (
        <div className="mb-2 flex flex-wrap items-center gap-3 text-xs text-slate-500">
          {series.map((s) => (
            <span key={s.key} className="flex items-center gap-1.5">
              <span className="h-0.5 w-3 rounded-full" style={{ background: s.color }} />
              {s.label}
            </span>
          ))}
        </div>
      )}
      {width > 0 && (
        <svg width={W} height={H} className="block overflow-visible" role="img" aria-label={series.map((s) => s.label).join(", ")}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} className="stroke-slate-100" strokeWidth={1} />
              <text x={padL - 6} y={y(t)} dy="0.32em" textAnchor="end" className="num fill-slate-400 text-[10px]">{fmt(t)}</text>
            </g>
          ))}
          {xs.map((d, i) =>
            i % labelEvery === 0 || i === xs.length - 1 ? (
              <text key={d} x={x(i)} y={H - 6} textAnchor="middle" className="fill-slate-400 text-[10px]">{xLabel(d)}</text>
            ) : null,
          )}
          {hi != null && <line x1={x(hi)} x2={x(hi)} y1={padT} y2={padT + ih} className="stroke-slate-300" strokeDasharray="3 3" />}
          {series.map((s) => (
            <g key={s.key}>
              <polyline fill="none" style={{ stroke: s.color }} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" points={s.values.map((v, i) => `${x(i)},${y(v)}`).join(" ")} />
              {(xs.length === 1 || hi != null) && (
                <circle cx={x(hi ?? 0)} cy={y(s.values[hi ?? 0])} r={4} style={{ fill: s.color }} className="stroke-white" strokeWidth={2} />
              )}
            </g>
          ))}
          <rect x={padL} y={padT} width={iw} height={ih} fill="transparent" onMouseMove={onMove} onMouseLeave={() => setHi(null)} />
        </svg>
      )}
      {hi != null && (
        <div
          className="pointer-events-none absolute top-6 z-10 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-[11px] shadow-lg"
          style={{ left: Math.min(Math.max(x(hi) - 60, 0), W - 130) }}
        >
          <div className="mb-0.5 font-semibold text-slate-900">{xLabel(xs[hi])}</div>
          {series.map((s) => (
            <div key={s.key} className="flex items-center gap-1.5 text-slate-600">
              <span className="h-1.5 w-1.5 rounded-full" style={{ background: s.color }} />
              {s.label}: <span className="num font-medium text-slate-900">{fmt(s.values[hi])}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------------- horizontal bars ---------------- */

export type BarItem = { label: string; value: number; display?: string; title?: string };

export function BarList({ items, fmt = (v) => String(v), labelW = "11rem", valueW = "3rem" }: { items: BarItem[]; fmt?: (v: number) => string; labelW?: string; valueW?: string }) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <ul className="space-y-2.5">
      {items.map((r) => (
        <li key={r.label} className="group grid items-center gap-3" style={{ gridTemplateColumns: `minmax(0,${labelW}) 1fr ${valueW}` }} title={r.title ?? `${r.label}: ${r.display ?? fmt(r.value)}`}>
          <span className="truncate text-[13px] text-slate-700">{r.label}</span>
          <div className="h-2 rounded-full bg-slate-100">
            <div className="h-2 rounded-full bg-brand-500 transition-all group-hover:bg-brand-600" style={{ width: `${(r.value / max) * 100}%` }} />
          </div>
          <span className="num text-right text-[13px] font-medium text-slate-800">{r.display ?? fmt(r.value)}</span>
        </li>
      ))}
    </ul>
  );
}
