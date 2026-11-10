import clsx from "clsx";
import { ArrowDown, ArrowUp, Ban, GripVertical, LayoutGrid, Plus, RotateCcw, Settings2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { api, type RunSummary } from "../api";
import { Tabs } from "../components/ui";
import { ChartSwitch, type ChartType } from "../components/dashboard/charts";
import { DEFAULT_LAYOUT, PRESETS, WIDGETS, WIDGET_BY_ID, topFirst, type Ctx, type Filter, type Slot, type Width } from "../components/dashboard/widgets";

const LAYOUT_KEY = "zamp.dashboard.layout.v2";
const OLD_LAYOUT_KEY = "zamp.dashboard.layout.v1"; // v1 layouts are migrated once: analytics widgets moved to the top
const DAYS_KEY = "zamp.dashboard.days.v1";
type Range = "7" | "30" | "90" | "all";
const RANGES: { id: Range; label: string }[] = [
  { id: "7", label: "7 days" },
  { id: "30", label: "30 days" },
  { id: "90", label: "90 days" },
  { id: "all", label: "All time" },
];
const SPAN: Record<Width, string> = { 4: "lg:col-span-4", 6: "lg:col-span-6", 8: "lg:col-span-8", 12: "lg:col-span-12" };
const WIDTHS: { w: Width; label: string; name: string }[] = [
  { w: 4, label: "⅓", name: "one third" },
  { w: 6, label: "½", name: "half" },
  { w: 8, label: "⅔", name: "two thirds" },
  { w: 12, label: "Full", name: "full" },
];

function readLayout(): Slot[] {
  try {
    const v2 = localStorage.getItem(LAYOUT_KEY);
    const raw = JSON.parse(v2 || localStorage.getItem(OLD_LAYOUT_KEY) || "null");
    if (Array.isArray(raw)) {
      const seen = new Set<string>();
      const ok: Slot[] = raw
        .filter((s: any) => WIDGET_BY_ID[s?.id] && [4, 6, 8, 12].includes(s?.w) && !seen.has(s.id) && seen.add(s.id))
        .map((s: any) => ({ id: s.id, w: s.w, ...(WIDGET_BY_ID[s.id].charts?.includes(s.chart) ? { chart: s.chart } : {}) }));
      if (ok.length) return v2 ? ok : topFirst(ok);
    }
  } catch {
    /* private window or bad JSON: fall through to defaults */
  }
  return DEFAULT_LAYOUT;
}
function readRange(): Range {
  try {
    const v = localStorage.getItem(DAYS_KEY) as Range | null;
    if (v && RANGES.some((r) => r.id === v)) return v;
  } catch {
    /* ignore */
  }
  return "all";
}
function save(key: string, v: string) {
  try {
    localStorage.setItem(key, v);
  } catch {
    /* storage blocked: layout just won't persist */
  }
}

export default function Dashboard() {
  const [range, setRange] = useState<Range>(readRange);
  const [layout, setLayout] = useState<Slot[]>(readLayout);
  const [editing, setEditing] = useState(false);
  const [adding, setAdding] = useState(false);
  const [filter, setFilter] = useState<Filter>("ALL");
  const [stats, setStats] = useState<any>(null);
  const [a, setA] = useState<any>(null);
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);
  const focusAfterMove = useRef<string | null>(null);

  const days = range === "all" ? undefined : Number(range);

  useEffect(() => save(LAYOUT_KEY, JSON.stringify(layout)), [layout]);
  useEffect(() => save(DAYS_KEY, range), [range]);

  // one poll for every widget; reset to skeletons when the range changes
  useEffect(() => {
    let live = true;
    setStats(null);
    setA(null);
    const load = () => {
      api.stats(days).then((s) => live && setStats(s)).catch(() => {});
      api.analytics(days).then((x) => live && setA(x)).catch(() => {});
      api.runs().then((r) => live && setRuns(r)).catch(() => {});
    };
    load();
    const t = setInterval(load, 3000);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, [days]);

  const ranged = useMemo(() => {
    if (!runs || !days) return runs;
    const cut = Date.now() - days * 86400000;
    return runs.filter((r) => new Date(r.created_at).getTime() >= cut);
  }, [runs, days]);

  const ctx: Ctx = { stats, a, runs: ranged, filter, setFilter };

  // keep keyboard focus on the moved widget's handle
  useEffect(() => {
    const id = focusAfterMove.current;
    if (id) {
      document.querySelector<HTMLButtonElement>(`[data-handle="${id}"]`)?.focus();
      focusAfterMove.current = null;
    }
  }, [layout]);

  const move = (id: string, delta: number) =>
    setLayout((l) => {
      const i = l.findIndex((s) => s.id === id);
      const j = i + delta;
      if (i < 0 || j < 0 || j >= l.length) return l;
      const n = [...l];
      [n[i], n[j]] = [n[j], n[i]];
      focusAfterMove.current = id;
      return n;
    });
  const moveTo = (id: string, targetId: string) =>
    setLayout((l) => {
      if (id === targetId) return l;
      const from = l.findIndex((s) => s.id === id);
      const to = l.findIndex((s) => s.id === targetId);
      if (from < 0 || to < 0) return l;
      const n = [...l];
      const [item] = n.splice(from, 1);
      n.splice(to, 0, item);
      return n;
    });
  const setWidth = (id: string, w: Width) => setLayout((l) => l.map((s) => (s.id === id ? { ...s, w } : s)));
  const setChart = (id: string, chart: ChartType) => setLayout((l) => l.map((s) => (s.id === id ? { ...s, chart } : s)));
  const remove = (id: string) => setLayout((l) => l.filter((s) => s.id !== id));
  const add = (id: string) => setLayout((l) => [...l, { id, w: WIDGET_BY_ID[id].w }]);

  const hidden = WIDGETS.filter((w) => !layout.some((s) => s.id === w.id));
  const activePreset = PRESETS.find((p) => JSON.stringify(p.layout) === JSON.stringify(layout))?.id;

  return (
    <div className="space-y-4 animate-fadein">
      {/* toolbar */}
      <div className="flex min-h-[36px] flex-wrap items-center justify-between gap-2">
        {editing ? (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-medium text-slate-500">Start from</span>
              <div role="group" aria-label="Layout presets" className="flex items-center gap-1 rounded-lg border border-slate-200 bg-white p-0.5">
                {PRESETS.map((p) => (
                  <button
                    key={p.id}
                    onClick={() => setLayout(p.layout)}
                    aria-pressed={activePreset === p.id}
                    className={clsx(
                      "h-7 rounded-md px-2.5 text-[13px] font-medium transition-colors",
                      activePreset === p.id ? "bg-slate-100 text-slate-900" : "text-slate-500 hover:text-slate-800",
                    )}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button className="btn-outline" onClick={() => setAdding(true)} disabled={!hidden.length}>
                <Plus className="h-4 w-4" /> Add widget{hidden.length ? <span className="num text-xs text-slate-400">{hidden.length}</span> : null}
              </button>
              <button className="btn-ghost" onClick={() => setLayout(DEFAULT_LAYOUT)}>
                <RotateCcw className="h-4 w-4" /> Reset
              </button>
              <button className="btn-primary" onClick={() => setEditing(false)}>Done</button>
            </div>
          </>
        ) : (
          <>
            <Tabs value={range} onChange={setRange} items={RANGES} />
            <button className="btn-ghost" onClick={() => setEditing(true)}>
              <Settings2 className="h-4 w-4" /> Customize
            </button>
          </>
        )}
      </div>

      {editing && (
        <p className="text-xs text-slate-500">
          Drag a widget by its handle, or focus the handle and use the arrow keys. Changes save automatically on this browser.
        </p>
      )}

      {layout.length === 0 ? (
        <div className="card flex flex-col items-center gap-3 px-6 py-12 text-center">
          <LayoutGrid className="h-6 w-6 text-slate-400" />
          <p className="text-sm text-slate-600">Your dashboard is empty.</p>
          <div className="flex gap-2">
            <button className="btn-outline" onClick={() => { setEditing(true); setAdding(true); }}>
              <Plus className="h-4 w-4" /> Add widget
            </button>
            <button className="btn-ghost" onClick={() => setLayout(DEFAULT_LAYOUT)}>Restore default</button>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
          {layout.map((slot, i) => {
            const def = WIDGET_BY_ID[slot.id];
            const chart: ChartType = slot.chart ?? def.charts?.[0] ?? "bar";
            const switcher = def.charts && def.charts.length > 1 ? <ChartSwitch value={chart} options={def.charts} title={def.title} onChange={(t) => setChart(slot.id, t)} /> : null;
            return (
              <Frame
                key={slot.id}
                span={SPAN[slot.w]}
                title={def.title}
                action={<>{def.action?.(ctx, chart)}{switcher}</>}
                strip={def.strip}
                pad={def.pad !== false}
                editing={editing}
                dragging={dragId === slot.id}
                onDragOver={() => dragId && moveTo(dragId, slot.id)}
                controls={
                  <EditControls
                    id={slot.id}
                    switcher={switcher}
                    title={def.title}
                    w={slot.w}
                    first={i === 0}
                    last={i === layout.length - 1}
                    onMove={(d) => move(slot.id, d)}
                    onWidth={(w) => setWidth(slot.id, w)}
                    onRemove={() => remove(slot.id)}
                    onDragStart={() => setDragId(slot.id)}
                    onDragEnd={() => setDragId(null)}
                  />
                }
              >
                {def.render(ctx, chart)}
              </Frame>
            );
          })}
        </div>
      )}

      {stats?.failed > 0 && (
        <p className="flex items-center gap-2 text-xs text-rose-600">
          <Ban className="h-3.5 w-3.5" /> {stats.failed} run(s) failed to process. Open them to see the error.
        </p>
      )}

      {adding && <AddWidget hidden={hidden} onAdd={add} onClose={() => setAdding(false)} />}
    </div>
  );
}

function Frame({
  span, title, action, strip, pad, editing, dragging, onDragOver, controls, children,
}: {
  span: string; title: string; action?: ReactNode; strip?: boolean; pad: boolean; editing: boolean; dragging: boolean;
  onDragOver: () => void; controls: ReactNode; children: ReactNode;
}) {
  const showHeader = editing || !strip;
  return (
    <section
      className={clsx(
        "card min-w-0",
        !pad && "overflow-hidden", // tables and lists clip to the rounded corners; charts keep room for tooltips
        span,
        editing && "outline-dashed outline-2 outline-offset-2 outline-brand-500/40",
        dragging && "opacity-50",
      )}
      onDragOver={(e) => {
        if (!editing) return;
        e.preventDefault();
        onDragOver();
      }}
    >
      {showHeader && (
        <header className="flex min-h-[44px] flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b border-slate-100 px-4 py-2">
          {editing ? controls : (
            <>
              <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
              <div className="flex items-center gap-3">{action}</div>
            </>
          )}
        </header>
      )}
      <div className={clsx(pad && "p-4", editing && "pointer-events-none select-none")} aria-hidden={editing || undefined}>
        {children}
      </div>
    </section>
  );
}

function EditControls({
  id, switcher, title, w, first, last, onMove, onWidth, onRemove, onDragStart, onDragEnd,
}: {
  id: string; switcher: ReactNode; title: string; w: Width; first: boolean; last: boolean;
  onMove: (d: number) => void; onWidth: (w: Width) => void; onRemove: () => void; onDragStart: () => void; onDragEnd: () => void;
}) {
  return (
    <>
      <div className="flex min-w-0 items-center gap-1.5">
        <button
          data-handle={id}
          draggable
          onDragStart={(e) => {
            e.dataTransfer.effectAllowed = "move";
            e.dataTransfer.setData("text/plain", id);
            onDragStart();
          }}
          onDragEnd={onDragEnd}
          onKeyDown={(e) => {
            if (e.key === "ArrowUp" || e.key === "ArrowLeft") { e.preventDefault(); onMove(-1); }
            if (e.key === "ArrowDown" || e.key === "ArrowRight") { e.preventDefault(); onMove(1); }
          }}
          aria-label={`Move ${title}. Use arrow keys to reorder.`}
          className="-ml-1.5 grid h-7 w-6 cursor-grab place-items-center rounded text-slate-400 hover:bg-slate-100 hover:text-slate-700 active:cursor-grabbing"
        >
          <GripVertical className="h-4 w-4" />
        </button>
        <h3 className="truncate text-sm font-semibold text-slate-900">{title}</h3>
      </div>
      <div className="flex items-center gap-1">
        {switcher && <div className="mr-1">{switcher}</div>}
        <button className="grid h-7 w-7 place-items-center rounded text-slate-400 hover:bg-slate-100 hover:text-slate-700 disabled:opacity-30" disabled={first} onClick={() => onMove(-1)} aria-label={`Move ${title} up`}>
          <ArrowUp className="h-3.5 w-3.5" />
        </button>
        <button className="grid h-7 w-7 place-items-center rounded text-slate-400 hover:bg-slate-100 hover:text-slate-700 disabled:opacity-30" disabled={last} onClick={() => onMove(1)} aria-label={`Move ${title} down`}>
          <ArrowDown className="h-3.5 w-3.5" />
        </button>
        <div role="group" aria-label={`${title} width`} className="ml-1 hidden items-center rounded-md border border-slate-200 p-0.5 lg:flex">
          {WIDTHS.map((o) => (
            <button
              key={o.w}
              onClick={() => onWidth(o.w)}
              aria-pressed={w === o.w}
              aria-label={`Width: ${o.name}`}
              className={clsx("h-6 min-w-[28px] rounded px-1.5 text-xs font-medium transition-colors", w === o.w ? "bg-slate-100 text-slate-900" : "text-slate-500 hover:text-slate-800")}
            >
              {o.label}
            </button>
          ))}
        </div>
        <button className="ml-1 grid h-7 w-7 place-items-center rounded text-slate-400 hover:bg-rose-50 hover:text-rose-600" onClick={onRemove} aria-label={`Remove ${title}`}>
          <X className="h-4 w-4" />
        </button>
      </div>
    </>
  );
}

function AddWidget({ hidden, onAdd, onClose }: { hidden: typeof WIDGETS; onAdd: (id: string) => void; onClose: () => void }) {
  const box = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    box.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && closeRef.current();
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      prev?.focus?.();
    };
  }, []);

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center scrim p-4 backdrop-blur-sm" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={box} tabIndex={-1} role="dialog" aria-modal="true" aria-label="Add widget" className="card flex max-h-[80dvh] w-full max-w-lg flex-col outline-none animate-fadein">
        <div className="flex h-12 items-center justify-between border-b border-slate-100 px-4">
          <h2 className="text-sm font-semibold text-slate-900">Add widget</h2>
          <button className="grid h-7 w-7 place-items-center rounded text-slate-400 hover:bg-slate-100 hover:text-slate-700" onClick={onClose} aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>
        {hidden.length === 0 ? (
          <p className="px-4 py-10 text-center text-sm text-slate-500">Every widget is already on your dashboard.</p>
        ) : (
          <ul className="divide-y divide-slate-100 overflow-auto">
            {hidden.map((w) => (
              <li key={w.id} className="flex items-center gap-3 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] font-medium text-slate-900">{w.title}</div>
                  <div className="text-xs text-slate-500">{w.desc}</div>
                </div>
                <button className="btn-outline shrink-0" onClick={() => onAdd(w.id)}>
                  <Plus className="h-4 w-4" /> Add
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>,
    document.body,
  );
}
