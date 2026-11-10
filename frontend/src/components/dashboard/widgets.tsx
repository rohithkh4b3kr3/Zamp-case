import clsx from "clsx";
import { ArrowRight, FileStack, Search } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ago, fmtMoney, type Decision, type RunSummary } from "../../api";
import { DecisionBadge, Empty, SkeletonRows, Stat, StatusPill, Tabs, decisionMeta } from "../ui";
import { BRAND, BarList, DEC_HEX, Donut, LineChart, catColor, type ChartType } from "./charts";

export type Filter = "ALL" | Decision;
export type Width = 4 | 6 | 8 | 12;

/** Everything a widget can read. Loaded once by the dashboard page, shared by all widgets. */
export type Ctx = {
  stats: any | null;
  a: any | null; // /api/analytics
  runs: RunSummary[] | null; // already limited to the selected date range
  filter: Filter;
  setFilter: (f: Filter) => void;
};

export type WidgetDef = {
  id: string;
  title: string;
  desc: string;
  w: Width; // default width
  /** strip widgets (a divided row of figures) have no header outside customize mode */
  strip?: boolean;
  pad?: boolean;
  action?: (c: Ctx, chart: ChartType) => ReactNode;
  /** chart types the viewer can switch between; the first is the default */
  charts?: ChartType[];
  render: (c: Ctx, chart: ChartType) => ReactNode;
};

const DECISIONS: Decision[] = ["APPROVED", "NEEDS_REVIEW", "REJECTED"];
const DEC_BAR: Record<Decision, string> = { APPROVED: "bg-emerald-500", NEEDS_REVIEW: "bg-amber-500", REJECTED: "bg-rose-500" };
const fmtShort = (v: number) => (v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `$${(v / 1e3).toFixed(1)}k` : fmtMoney(v));
const day = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" });

/* ---------- small shared pieces ---------- */

function Skel({ h = "h-40" }: { h?: string }) {
  return <div className={clsx("skeleton w-full rounded-lg", h)} />;
}

function NoData({ children = "No invoices in this period." }: { children?: ReactNode }) {
  return <p className="py-6 text-center text-sm text-slate-500">{children}</p>;
}

/** Common guard for analytics widgets: skeleton while loading, quiet note when the period is empty. */
function withA(c: Ctx, body: (a: any) => ReactNode, h?: string) {
  if (!c.a) return <Skel h={h} />;
  if (!c.a.totals.invoices) return <NoData />;
  return body(c.a);
}

function Mini({ label, value, warn }: { label: string; value: number; warn?: boolean }) {
  return (
    <div className="rounded-lg border border-slate-200 px-3 py-2">
      <div className="text-xs text-slate-500">{label}</div>
      <div className={clsx("num mt-0.5 text-lg font-semibold", warn ? "text-rose-600" : "text-slate-900")}>{value}</div>
    </div>
  );
}

function Legend() {
  return (
    <div className="flex items-center gap-3 text-xs text-slate-500">
      {DECISIONS.map((d) => (
        <span key={d} className="flex items-center gap-1.5">
          <span className={clsx("h-1.5 w-1.5 rounded-full", DEC_BAR[d])} />
          {decisionMeta[d].label}
        </span>
      ))}
    </div>
  );
}

function Owner({ r }: { r: RunSummary }) {
  if (r.review_status)
    return (
      <div className="flex items-center gap-2 whitespace-nowrap">
        <span className="text-slate-700">{r.route_to}</span>
        <StatusPill status={r.review_status} />
      </div>
    );
  if (r.decision === "REJECTED") return <span className="text-slate-600">{r.route_to === "Vendor" ? "Returned to vendor" : r.route_to}</span>;
  if (r.ledger_posted) return <span className="text-slate-600">Posted to ledger</span>;
  return <span className="text-slate-400">—</span>;
}

const linkCls = "inline-flex items-center gap-1 text-xs font-medium text-slate-500 hover:text-slate-900";

/* ---------- widget bodies ---------- */

function Metrics({ c }: { c: Ctx }) {
  const s = c.stats;
  const items = [
    { label: "Invoices processed", value: s?.completed ?? 0, sub: `${fmtMoney(s?.value_processed)} total` },
    { label: "Touchless rate", value: s?.touchless_rate != null ? `${Math.round(s.touchless_rate * 100)}%` : "—", sub: "approved without review" },
    { label: "Awaiting review", value: s?.pending_review ?? 0, sub: "with an owning team" },
    { label: "Value blocked", value: fmtMoney(s?.value_blocked), sub: "rejected before payment" },
    { label: "Avg. time to decision", value: s?.avg_duration_ms ? `${(s.avg_duration_ms / 1000).toFixed(1)}s` : "—" },
  ];
  return (
    <div className="grid grid-cols-2 divide-slate-100 md:grid-cols-5 md:divide-x">
      {items.map((m, i) => (
        <div key={m.label} className={clsx("p-4", i === 4 && "col-span-2 md:col-span-1")}>
          <Stat label={m.label} value={m.value} sub={m.sub} loading={!s} />
        </div>
      ))}
    </div>
  );
}

function Protection({ c }: { c: Ctx }) {
  const a = c.a;
  const items = [
    { label: "Duplicates blocked", value: a && fmtMoney(a.protected.duplicates_usd), sub: a && `${a.protected.duplicates} not paid twice` },
    { label: "Fraud signals held", value: a && fmtMoney(a.protected.fraud_usd), sub: a && `${a.protected.fraud_signals} bank change or spoofed sender` },
    { label: "Value on hold", value: a && fmtMoney(a.protected.on_hold_usd), sub: "waiting on an owning team" },
    { label: "Approved value", value: a && fmtShort(c.stats?.value_approved ?? 0), sub: a && `${a.totals.invoices} invoices · ${fmtShort(a.totals.value_usd)} seen` },
  ];
  return (
    <div className="grid grid-cols-2 divide-slate-100 lg:grid-cols-4 lg:divide-x">
      {items.map((m) => (
        <div key={m.label} className="p-4">
          <Stat label={m.label} value={m.value} sub={m.sub} loading={!a} />
        </div>
      ))}
    </div>
  );
}

function Decisions({ c, chart }: { c: Ctx; chart: ChartType }) {
  const s = c.stats;
  if (!s)
    return (
      <div className="space-y-4">
        <div className="skeleton h-2.5 w-full" />
        <div className="grid grid-cols-3 gap-3">{[0, 1, 2].map((i) => <div key={i} className="skeleton h-16" />)}</div>
      </div>
    );
  const d = s.decisions || { APPROVED: 0, NEEDS_REVIEW: 0, REJECTED: 0 };
  const completed = s.completed || 0;
  if (!completed)
    return (
      <Empty icon={FileStack} title="No decisions in this period">
        <Link className="font-medium text-brand-600 hover:underline" to="/new">Process an invoice</Link> to see the split here.
      </Empty>
    );
  if (chart === "pie")
    return (
      <Donut
        items={DECISIONS.map((k) => ({
          label: decisionMeta[k].label,
          value: d[k],
          color: DEC_HEX[k],
          active: c.filter === k,
          onClick: () => c.setFilter(c.filter === k ? "ALL" : k),
        }))}
        center={<><span className="num text-lg font-semibold text-slate-900">{completed}</span><span className="text-[11px] text-slate-500">decided</span></>}
      />
    );
  return (
    <>
      <div className="flex h-2.5 gap-0.5 overflow-hidden rounded-full">
        {DECISIONS.map((k) => (d[k] ? <div key={k} className={clsx(decisionMeta[k].dot, "transition-all")} style={{ width: `${(d[k] / completed) * 100}%` }} /> : null))}
      </div>
      <div className="mt-4 grid grid-cols-3 gap-3">
        {DECISIONS.map((k) => (
          <button
            key={k}
            onClick={() => c.setFilter(c.filter === k ? "ALL" : k)}
            aria-pressed={c.filter === k}
            className={clsx(
              "rounded-lg border p-3 text-left transition-colors",
              c.filter === k ? "border-brand-500 ring-[3px] ring-brand-500/15" : "border-slate-200 hover:bg-slate-50",
            )}
          >
            <div className="flex items-center gap-1.5 text-xs font-medium text-slate-500">
              <span className={clsx("h-1.5 w-1.5 rounded-full", decisionMeta[k].dot)} />
              <span className="truncate">{decisionMeta[k].label}</span>
            </div>
            <div className="mt-1 flex items-baseline gap-2">
              <span className="num text-xl font-semibold text-slate-900">{d[k]}</span>
              <span className="num text-xs text-slate-400">{Math.round((d[k] / completed) * 100)}%</span>
            </div>
          </button>
        ))}
      </div>
    </>
  );
}

function Attention({ c }: { c: Ctx }) {
  if (!c.runs) return <div className="space-y-3 p-4">{[0, 1, 2].map((i) => <div key={i} className="skeleton h-9" />)}</div>;
  const list = c.runs.filter((r) => r.review_status === "pending").slice(0, 6);
  if (!list.length) return <p className="px-4 py-10 text-center text-sm text-slate-500">No invoices waiting for review.</p>;
  return (
    <ul className="divide-y divide-slate-100">
      {list.map((r) => (
        <li key={r.id}>
          <Link to={`/runs/${r.id}`} className="flex items-center gap-3 px-4 py-2.5 transition-colors hover:bg-slate-50">
            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" />
            <div className="min-w-0 flex-1">
              <div className="truncate text-[13px] font-medium text-slate-800">{r.vendor_name || r.filename}</div>
              <div className="truncate text-xs text-slate-500">{r.route_to} · {ago(r.created_at)}</div>
            </div>
            <span className="num shrink-0 text-[13px] text-slate-700">{fmtMoney(r.total, r.currency || "USD")}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

function Invoices({ c }: { c: Ctx }) {
  const [q, setQ] = useState("");
  const nav = useNavigate();
  const all = c.runs ?? [];
  const counts = useMemo(() => {
    const n: Record<Filter, number> = { ALL: all.length, APPROVED: 0, NEEDS_REVIEW: 0, REJECTED: 0 };
    all.forEach((r) => r.decision && n[r.decision]++);
    return n;
  }, [all]);
  const shown = useMemo(() => {
    const s = q.trim().toLowerCase();
    return all.filter((r) => {
      if (c.filter !== "ALL" && r.decision !== c.filter) return false;
      return !s || [r.vendor_name, r.invoice_number, r.po_number, r.filename].some((x) => x?.toLowerCase().includes(s));
    });
  }, [all, c.filter, q]);

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-3 py-2">
        <Tabs
          value={c.filter}
          onChange={c.setFilter}
          items={[
            { id: "ALL" as Filter, label: "All invoices", count: counts.ALL },
            ...DECISIONS.map((k) => ({ id: k as Filter, label: decisionMeta[k].label, count: counts[k], dot: decisionMeta[k].dot })),
          ]}
        />
        <div className="relative w-full sm:w-64">
          <Search className="pointer-events-none absolute left-2.5 top-2 h-4 w-4 text-slate-400" />
          <input className="input pl-8" placeholder="Search vendor, invoice or PO" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search invoices" />
        </div>
      </div>
      {c.runs && shown.length === 0 ? (
        <Empty icon={FileStack} title={all.length ? "No invoices match" : "No invoices in this period"}>
          {all.length ? (
            <button className="font-medium text-brand-600 hover:underline" onClick={() => { setQ(""); c.setFilter("ALL"); }}>Clear filters</button>
          ) : (
            <Link className="font-medium text-brand-600 hover:underline" to="/new">Process an invoice</Link>
          )}
        </Empty>
      ) : (
        <div className="max-h-[640px] overflow-auto">
          <table className="tbl w-full">
            <thead>
              <tr>
                <th>Vendor</th>
                <th>Invoice</th>
                <th>PO</th>
                <th className="text-right">Amount</th>
                <th>Decision</th>
                <th>Owner</th>
                <th className="text-right">Received</th>
              </tr>
            </thead>
            {!c.runs ? (
              <SkeletonRows cols={7} />
            ) : (
              <tbody>
                {shown.map((r) => (
                  <tr
                    key={r.id}
                    tabIndex={0}
                    onClick={() => nav(`/runs/${r.id}`)}
                    onKeyDown={(e) => e.key === "Enter" && nav(`/runs/${r.id}`)}
                    className="cursor-pointer focus:bg-slate-50 focus:outline-none"
                  >
                    <td className="max-w-[260px]">
                      <div className="truncate font-medium text-slate-900">{r.vendor_name || <span className="text-slate-400">Reading…</span>}</div>
                      <div className="truncate text-xs text-slate-400">{r.filename}</div>
                    </td>
                    <td className="num whitespace-nowrap text-xs">{r.invoice_number || <span className="font-sans text-rose-600">Missing</span>}</td>
                    <td className="num whitespace-nowrap text-xs text-slate-600">{r.po_number || "—"}</td>
                    <td className="num whitespace-nowrap text-right font-medium text-slate-900">{fmtMoney(r.total, r.currency || "USD")}</td>
                    <td>{r.status === "completed" ? <DecisionBadge decision={r.decision} /> : <StatusPill status={r.status} />}</td>
                    <td className="text-[13px]"><Owner r={r} /></td>
                    <td className="whitespace-nowrap text-right text-xs text-slate-500">{ago(r.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            )}
          </table>
        </div>
      )}
    </>
  );
}

function Reasons({ c, chart }: { c: Ctx; chart: ChartType }) {
  return withA(c, (a) => {
    if (!a.exception_reasons.length) return <NoData>No exceptions in this period.</NoData>;
    const rows = a.exception_reasons.slice(0, 8);
    if (chart === "pie") return <Donut items={a.exception_reasons.map((r: any, i: number) => ({ label: r.reason, value: r.count, color: catColor(i) }))} />;
    return <BarList items={rows.map((r: any) => ({ label: r.reason, value: r.count }))} />;
  });
}

function Trend({ c, chart }: { c: Ctx; chart: ChartType }) {
  return withA(c, (a) => {
    const trend = a.trend.slice(-30);
    if (chart === "line")
      return (
        <LineChart
          xs={trend.map((t: any) => t.date)}
          xLabel={day}
          legend={false}
          series={DECISIONS.map((d) => ({ key: d, label: decisionMeta[d].label, color: DEC_HEX[d], values: trend.map((t: any) => t[d]) }))}
        />
      );
    if (chart === "pie") {
      const sum = (k: Decision) => trend.reduce((n: number, t: any) => n + t[k], 0);
      return <Donut items={DECISIONS.map((d) => ({ label: decisionMeta[d].label, value: sum(d), color: DEC_HEX[d] }))} />;
    }
    const max = Math.max(1, ...trend.map((t: any) => t.APPROVED + t.NEEDS_REVIEW + t.REJECTED));
    return (
      <div className="flex h-44 items-end gap-1.5">
        {trend.map((t: any) => {
          const tot = t.APPROVED + t.NEEDS_REVIEW + t.REJECTED;
          return (
            <div key={t.date} className="group flex h-full min-w-0 flex-1 flex-col items-center justify-end gap-1.5">
              <div className="relative flex w-full max-w-[44px] flex-col-reverse gap-[2px]" style={{ height: `${(tot / max) * 100}%` }}>
                {DECISIONS.map((d) => (t[d] ? <div key={d} className={clsx("w-full first:rounded-b-[4px] last:rounded-t-[4px]", DEC_BAR[d])} style={{ flex: t[d] }} /> : null))}
                <div className="pointer-events-none absolute -top-16 left-1/2 z-10 hidden -translate-x-1/2 whitespace-nowrap rounded-lg bg-slate-900 px-2 py-1.5 text-[11px] text-white shadow-lg group-hover:block">
                  <div className="font-semibold">{day(t.date)}</div>
                  {DECISIONS.map((d) => (
                    <div key={d}>{decisionMeta[d].label}: {t[d]}</div>
                  ))}
                </div>
              </div>
              <span className="truncate text-[10px] text-slate-500">{day(t.date)}</span>
            </div>
          );
        })}
      </div>
    );
  }, "h-44");
}

function Calendar({ c, chart }: { c: Ctx; chart: ChartType }) {
  return withA(c, (a) => {
    if (!a.payment_calendar.length) return <NoData>No approved invoices scheduled.</NoData>;
    if (chart === "line")
      return (
        <LineChart
          xs={a.payment_calendar.map((p: any) => p.date)}
          xLabel={day}
          fmt={fmtShort}
          series={[{ key: "due", label: "Due (USD)", color: BRAND, values: a.payment_calendar.map((p: any) => p.amount_usd) }]}
        />
      );
    const max = Math.max(1, ...a.payment_calendar.map((p: any) => p.amount_usd));
    return (
      <div className="space-y-2">
        {a.payment_calendar.map((p: any) => (
          <div key={p.date} className="group grid grid-cols-[5.5rem_1fr_6rem] items-center gap-3">
            <span className="text-[13px] font-medium text-slate-700">{day(p.date)}</span>
            <div className="relative h-7 rounded-md bg-slate-100">
              <div className="h-7 rounded-md bg-brand-500/25 transition group-hover:bg-brand-500/35" style={{ width: `${Math.max(3, (p.amount_usd / max) * 100)}%` }} />
              <div className="absolute inset-0 flex items-center gap-1.5 overflow-hidden px-2 text-[11px] text-slate-800">
                {p.invoices.map((i: any) => (
                  <Link key={i.id} to={`/runs/${i.id}`} className="truncate rounded bg-white px-1.5 py-0.5 shadow-sm hover:text-brand-700" title={`${i.vendor} ${i.invoice} · ${fmtMoney(i.usd)}`}>
                    {i.vendor?.split(" ")[0]} {i.invoice}
                  </Link>
                ))}
              </div>
            </div>
            <span className="num text-right text-[13px] font-medium text-slate-800">{fmtMoney(p.amount_usd)}</span>
          </div>
        ))}
      </div>
    );
  });
}

function Sla({ c }: { c: Ctx }) {
  if (!c.a) return <Skel />;
  const a = c.a;
  return (
    <>
      <div className="grid grid-cols-2 gap-3">
        <Mini label="Pending" value={a.sla.pending} />
        <Mini label="Overdue" value={a.sla.overdue} warn={a.sla.overdue > 0} />
        <Mini label="Escalated" value={a.sla.escalated} warn={a.sla.escalated > 0} />
        <Mini label="Resolved" value={a.sla.resolved} />
      </div>
      {a.pending_ages.length > 0 && (
        <ul className="mt-4 space-y-1.5">
          {a.pending_ages.slice(0, 5).map((p: any) => (
            <li key={p.id}>
              <Link to={`/runs/${p.id}`} className="flex items-center justify-between gap-2 rounded-md px-2 py-1.5 text-[13px] hover:bg-slate-50">
                <span className="truncate text-slate-700">{p.vendor}</span>
                <span className="shrink-0 text-xs text-slate-500">{p.team} · {p.age_hours}h</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function Vendors({ c, chart }: { c: Ctx; chart: ChartType }) {
  if (chart === "bar")
    return withA(c, (a) => (
      <div className="p-4">
        <BarList
          labelW="12rem"
          items={a.vendors.slice(0, 10).map((v: any) => ({ label: v.vendor, value: v.exception_rate * 100, display: `${Math.round(v.exception_rate * 100)}%`, title: `${v.vendor}: ${Math.round(v.exception_rate * 100)}% of ${v.runs} invoices stopped` }))}
        />
      </div>
    ), "h-48");
  return withA(c, (a) => (
    <div className="overflow-x-auto">
      <table className="tbl w-full">
        <thead>
          <tr>
            <th>Vendor</th>
            <th className="text-right">Invoices</th>
            <th>Outcome mix</th>
            <th className="text-right">Exception rate</th>
            <th className="text-right">Value (USD)</th>
          </tr>
        </thead>
        <tbody>
          {a.vendors.map((v: any) => (
            <tr key={v.vendor}>
              <td className="font-medium text-slate-800">{v.vendor}</td>
              <td className="num text-right">{v.runs}</td>
              <td>
                <div className="flex h-2 w-32 gap-[2px] overflow-hidden rounded-full" title={`${v.approved} approved · ${v.review} review · ${v.rejected} rejected`}>
                  {v.approved > 0 && <div className="bg-emerald-500" style={{ flex: v.approved }} />}
                  {v.review > 0 && <div className="bg-amber-500" style={{ flex: v.review }} />}
                  {v.rejected > 0 && <div className="bg-rose-500" style={{ flex: v.rejected }} />}
                </div>
              </td>
              <td className="num text-right">{Math.round(v.exception_rate * 100)}%</td>
              <td className="num text-right">{fmtMoney(v.value_usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ), "h-48");
}

function Currencies({ c, chart }: { c: Ctx; chart: ChartType }) {
  return withA(c, (a) => chart === "pie" ? (
    <Donut items={a.currencies.map((x: any, i: number) => ({ label: x.currency, value: x.usd, color: catColor(i), display: fmtShort(x.usd) }))} fmt={fmtShort} />
  ) : chart === "bar" ? (
    <BarList labelW="4rem" valueW="5rem" items={a.currencies.map((x: any) => ({ label: x.currency, value: x.usd, display: fmtShort(x.usd), title: `${x.currency}: ${x.invoices} invoices, ${fmtMoney(x.usd)}` }))} />
  ) : (
    <>
      <ul className="space-y-2 text-[13px]">
        {a.currencies.map((x: any) => (
          <li key={x.currency} className="flex items-center justify-between gap-2">
            <span className="font-medium text-slate-700">
              {x.currency} <span className="font-normal text-slate-400">· {x.invoices} inv.</span>
            </span>
            <span className="num text-slate-800">
              {x.currency !== "USD" && <span className="mr-2 text-xs text-slate-500">{fmtMoney(x.amount, x.currency)}</span>}
              {fmtMoney(x.usd)}
            </span>
          </li>
        ))}
      </ul>
      {a.fx && (
        <p className="mt-3 text-[11px] text-slate-500">
          Rates: {Object.entries(a.fx.to_usd as Record<string, number>).filter(([k]) => k !== "USD").map(([k, v]) => `${k} ${v}`).join(" · ")} ({a.fx.source}, {a.fx.as_of})
        </p>
      )}
    </>
  ), "h-28");
}

function Learning({ c }: { c: Ctx }) {
  if (!c.a) return <Skel h="h-28" />;
  const l = c.a.learning;
  return (
    <>
      <div className="grid grid-cols-2 gap-3">
        <Mini label="Rules learned" value={l.rules} />
        <Mini label="Times applied" value={l.rule_hits} />
      </div>
      {l.corrections_by_field.length > 0 && (
        <div className="mt-3 text-xs text-slate-500">
          Most corrected: {l.corrections_by_field.slice(0, 4).map((x: any) => `${x.field.replace(/_/g, " ")} (${x.count})`).join(", ")}
        </div>
      )}
    </>
  );
}

const METHOD_LABEL: Record<string, string> = { text: "Text layer", ocr: "OCR (scans)", llm: "LLM" };

function Processing({ c }: { c: Ctx }) {
  return withA(c, (a) => {
    const methods = Object.entries(a.processing_ms as Record<string, number>);
    if (!methods.length) return <NoData />;
    const max = Math.max(1, ...methods.map(([, v]) => v));
    return (
      <ul className="space-y-2">
        {methods.map(([k, v]) => (
          <li key={k} className="grid grid-cols-[6.5rem_1fr_3rem] items-center gap-2 text-[13px]" title={`${METHOD_LABEL[k] || k}: ${(v / 1000).toFixed(1)}s`}>
            <span className="text-slate-700">{METHOD_LABEL[k] || k}</span>
            <div className="h-2 rounded-full bg-slate-100">
              <div className="h-2 rounded-full bg-brand-500" style={{ width: `${(v / max) * 100}%` }} />
            </div>
            <span className="num text-right text-slate-800">{(v / 1000).toFixed(1)}s</span>
          </li>
        ))}
      </ul>
    );
  }, "h-24");
}

/* ---------- registry ---------- */

export const WIDGETS: WidgetDef[] = [
  { id: "metrics", title: "Key metrics", desc: "Processed, touchless rate, awaiting review, value blocked, time to decision.", w: 12, strip: true, pad: false, render: (c) => <Metrics c={c} /> },
  { id: "decisions", title: "Decisions", desc: "Approved / review / rejected split. Click one to filter the invoice list.", w: 8, charts: ["bar", "pie"], render: (c, t) => <Decisions c={c} chart={t} /> },
  {
    id: "attention", title: "Needs attention", desc: "Invoices waiting with a review team.", w: 4, pad: false,
    action: () => <Link className={linkCls} to="/review">Review queue <ArrowRight className="h-3.5 w-3.5" /></Link>,
    render: (c) => <Attention c={c} />,
  },
  { id: "invoices", title: "Invoices", desc: "Every invoice with its decision and owner, searchable.", w: 12, pad: false, render: (c) => <Invoices c={c} /> },
  { id: "protection", title: "Money protected", desc: "Duplicates blocked, fraud signals held, value on hold, approved value.", w: 12, strip: true, pad: false, render: (c) => <Protection c={c} /> },
  { id: "reasons", title: "Why invoices were stopped", desc: "Most common exception reasons.", w: 6, charts: ["bar", "pie"], render: (c, t) => <Reasons c={c} chart={t} /> },
  { id: "trend", title: "Decisions per day", desc: "Daily volume by outcome, as bars, lines or a pie.", w: 6, action: (_c, t) => (t === "bar" ? <Legend /> : null), charts: ["bar", "line", "pie"], render: (c, t) => <Trend c={c} chart={t} /> },
  { id: "calendar", title: "Payment calendar", desc: "Approved invoices grouped by due date.", w: 8, charts: ["bar", "line"], render: (c, t) => <Calendar c={c} chart={t} /> },
  { id: "sla", title: "Review SLA", desc: "Pending, overdue and escalated reviews, oldest first.", w: 4, render: (c) => <Sla c={c} /> },
  { id: "vendors", title: "Vendors by exception rate", desc: "Which suppliers cause the most exceptions.", w: 8, pad: false, charts: ["table", "bar"], render: (c, t) => <Vendors c={c} chart={t} /> },
  { id: "currencies", title: "Currencies", desc: "Invoice value per currency, converted to USD.", w: 4, charts: ["table", "bar", "pie"], render: (c, t) => <Currencies c={c} chart={t} /> },
  {
    id: "learning", title: "Learning from reviewers", desc: "Rules learned from corrections and how often they fired.", w: 6,
    action: () => <Link className={linkCls} to="/reference">Learned rules <ArrowRight className="h-3.5 w-3.5" /></Link>,
    render: (c) => <Learning c={c} />,
  },
  { id: "processing", title: "Avg. processing time", desc: "Time per invoice by reading method.", w: 6, render: (c) => <Processing c={c} /> },
];

export const WIDGET_BY_ID = Object.fromEntries(WIDGETS.map((w) => [w.id, w])) as Record<string, WidgetDef>;

export type Slot = { id: string; w: Width; chart?: ChartType };
const slots = (...pairs: [string, Width?][]): Slot[] => pairs.map(([id, w]) => ({ id, w: w ?? WIDGET_BY_ID[id].w }));

/** analytics block shown first on the dashboard (user request), then everything else in registry order */
export const TOP_WIDGETS = ["reasons", "trend", "calendar", "sla", "vendors", "currencies"];
export const topFirst = (layout: Slot[]): Slot[] => [
  ...TOP_WIDGETS.map((id) => layout.find((s) => s.id === id)).filter((s): s is Slot => !!s),
  ...layout.filter((s) => !TOP_WIDGETS.includes(s.id)),
];
export const DEFAULT_LAYOUT: Slot[] = topFirst(WIDGETS.map((w) => ({ id: w.id, w: w.w })));

export const PRESETS: { id: string; label: string; layout: Slot[] }[] = [
  {
    id: "clerk",
    label: "AP clerk",
    layout: slots(["metrics"], ["attention", 4], ["decisions", 8], ["invoices"], ["reasons", 6], ["sla", 6]),
  },
  {
    id: "manager",
    label: "Finance manager",
    layout: slots(["metrics"], ["protection"], ["trend", 6], ["reasons", 6], ["vendors", 8], ["sla", 4], ["learning", 6], ["processing", 6]),
  },
  {
    id: "treasury",
    label: "Treasury",
    layout: slots(["protection"], ["calendar", 8], ["currencies", 4], ["sla", 4], ["vendors", 8]),
  },
];
