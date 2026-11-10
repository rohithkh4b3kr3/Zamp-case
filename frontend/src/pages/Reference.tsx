import clsx from "clsx";
import { ChevronRight, History, Search, Upload, X } from "lucide-react";
import { Fragment, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useNavigate } from "react-router-dom";
import { api, fmtMoney, type RunSummary } from "../api";
import ImportWizard from "../components/ImportWizard";
import PoLinesPanel from "../components/PoLinesPanel";
import RefHistory from "../components/RefHistory";
import RefSources from "../components/RefSources";
import { Empty, SkeletonRows, Tabs, decisionMeta } from "../components/ui";

type Tab = "pos" | "vendors" | "ledger" | "sources" | "fx";

const has = (q: string, ...xs: any[]) => !q || xs.some((x) => String(x ?? "").toLowerCase().includes(q));

function StatusChip({ value, good }: { value: string; good: boolean }) {
  return (
    <span className={clsx("chip capitalize", good ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-600")}>
      <span className={clsx("h-1.5 w-1.5 rounded-full", good ? "bg-emerald-500" : "bg-slate-400")} />
      {value}
    </span>
  );
}

/** Invoices processed against a PO; each chip opens that run (the extracted-invoice page). */
function PoInvoices({ runs: all }: { runs: RunSummary[] }) {
  // one chip per invoice: re-runs of the same invoice collapse to the latest run
  const latest = new Map<string, RunSummary>();
  for (const r of all) {
    const key = r.invoice_number || r.id;
    const prev = latest.get(key);
    if (!prev || r.created_at > prev.created_at) latest.set(key, r);
  }
  const runs = [...latest.values()].sort((a, b) => b.created_at.localeCompare(a.created_at));
  if (!runs.length) return <span className="text-xs text-slate-400">—</span>;
  const shown = runs.slice(0, 3);
  return (
    <div className="flex flex-wrap items-center gap-1">
      {shown.map((r) => (
        <Link
          key={r.id}
          to={`/runs/${r.id}`}
          onClick={(e) => e.stopPropagation()}
          className="pill num transition-colors hover:border-brand-500 hover:text-brand-700"
          title={`${r.vendor_name || r.filename} · ${fmtMoney(r.total, r.currency || "USD")} · ${r.decision ? decisionMeta[r.decision].label : r.status}`}
        >
          <span className={clsx("h-1.5 w-1.5 shrink-0 rounded-full", r.decision ? decisionMeta[r.decision].dot : "bg-slate-400")} />
          {r.invoice_number || r.filename}
        </Link>
      ))}
      {runs.length > shown.length && <span className="text-xs text-slate-400">+{runs.length - shown.length}</span>}
    </div>
  );
}

export default function Reference() {
  const [ref, setRef] = useState<any>(null);
  const [tab, setTab] = useState<Tab>("pos");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [history, setHistory] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [sourceCount, setSourceCount] = useState<number | undefined>(undefined);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const nav = useNavigate();
  const reload = () => api.reference().then(setRef).catch(() => {});
  const notify = (msg: string) => {
    setToast(msg);
    reload();
    setTimeout(() => setToast((t) => (t === msg ? null : t)), 5000);
  };

  useEffect(() => {
    api.refSources().then((s) => setSourceCount(s.length)).catch(() => {});
    const load = () => {
      api.reference().then(setRef).catch(() => {});
      api.runs().then(setRuns).catch(() => {});
    };
    load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, []);

  const s = q.trim().toLowerCase();
  // newest first; a PO can carry several invoices (split billing, deposits, credit notes)
  const runsByPo = runs.reduce<Record<string, RunSummary[]>>((m, r) => {
    if (r.po_number) (m[r.po_number] ||= []).push(r);
    return m;
  }, {});
  const vendorName = (id: string) => ref?.vendors.find((v: any) => v.vendor_id === id)?.name;
  const pos = (ref?.purchase_orders || []).filter((p: any) => has(s, p.po_number, p.vendor_name, p.requester));
  const vendors = (ref?.vendors || []).filter((v: any) => has(s, v.vendor_id, v.name, v.tax_id, v.email, ...(v.aliases || [])));
  const ledger = [...(ref?.ledger || [])].reverse().filter((e: any) => has(s, e.invoice_number, e.po_number, vendorName(e.vendor_id)));

  return (
    <section className="card overflow-hidden animate-fadein">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-3 py-2">
        <Tabs<Tab>
          value={tab}
          onChange={(t) => {
            setTab(t);
            setOpen(null);
          }}
          items={[
            { id: "pos", label: "Purchase orders", count: ref?.purchase_orders.length },
            { id: "vendors", label: "Vendors", count: ref?.vendors.length },
            { id: "ledger", label: "Invoice ledger", count: ref?.ledger.length },
            { id: "sources", label: "Sources", count: sourceCount },
            { id: "fx", label: "FX & teams" },
          ]}
        />
        <div className="flex w-full items-center gap-2 sm:w-auto">
          {tab !== "fx" && tab !== "sources" && (
            <div className="relative w-full sm:w-64">
              <Search className="pointer-events-none absolute left-2.5 top-2 h-4 w-4 text-slate-400" />
              <input className="input pl-8" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search procurement data" />
            </div>
          )}
          <button className="btn-outline shrink-0" onClick={() => setImporting(true)}>
            <Upload className="h-4 w-4" /> Import
          </button>
          <button className="btn-ghost w-8 shrink-0 px-0" onClick={() => setHistory(true)} title="History of imports and edits" aria-label="History">
            <History className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="overflow-x-auto">
        {tab === "pos" && (
          <table className="tbl w-full">
            <thead>
              <tr>
                <th className="w-8" />
                <th>PO</th>
                <th>Vendor</th>
                <th>Requester</th>
                <th>Date</th>
                <th className="w-56">Billed</th>
                <th className="text-right">Remaining</th>
                <th>Invoices</th>
                <th>Status</th>
              </tr>
            </thead>
            {!ref ? (
              <SkeletonRows cols={9} />
            ) : (
              <tbody>
                {pos.map((p: any) => {
                  const pct = Math.min(100, (p.billed / p.total) * 100);
                  const isOpen = open === p.po_number;
                  const short = p.lines.some((l: any) => l.received < l.qty);
                  return (
                    <Fragment key={p.po_number}>
                      <tr
                        tabIndex={0}
                        aria-expanded={isOpen}
                        onClick={() => setOpen(isOpen ? null : p.po_number)}
                        onKeyDown={(e) => e.key === "Enter" && setOpen(isOpen ? null : p.po_number)}
                        className={clsx("cursor-pointer focus:outline-none focus-visible:bg-slate-50", isOpen && "bg-slate-50")}
                      >
                        <td className="pr-0">
                          <ChevronRight className={clsx("h-4 w-4 text-slate-400 transition-transform", isOpen && "rotate-90")} />
                        </td>
                        <td className="num whitespace-nowrap font-medium text-slate-900">{p.po_number}</td>
                        <td className="max-w-[240px] truncate text-slate-800">{p.vendor_name}</td>
                        <td className="whitespace-nowrap text-slate-500">{p.requester}</td>
                        <td className="num whitespace-nowrap text-slate-500">{p.date}</td>
                        <td>
                          <div className="flex items-center gap-2">
                            <div className="h-1.5 flex-1 rounded-full bg-slate-100">
                              <div className={clsx("h-1.5 rounded-full", pct >= 100 ? "bg-slate-400" : "bg-brand-500")} style={{ width: `${pct}%` }} />
                            </div>
                            <span className="num w-10 text-right text-xs text-slate-500">{Math.round(pct)}%</span>
                          </div>
                        </td>
                        <td className="num whitespace-nowrap text-right">
                          <span className="font-medium text-slate-900">{fmtMoney(p.remaining)}</span>
                          <span className="text-xs text-slate-400"> / {fmtMoney(p.total)}</span>
                        </td>
                        <td>
                          <PoInvoices runs={runsByPo[p.po_number] || []} />
                        </td>
                        <td>
                          <div className="flex items-center gap-1.5">
                            <StatusChip value={p.status} good={p.status === "open"} />
                            {short && <span className="chip bg-amber-50 text-amber-800">Partly received</span>}
                          </div>
                        </td>
                      </tr>
                      {isOpen && (
                        <tr className="hover:bg-transparent">
                          <td colSpan={9} className="bg-slate-50/60 px-4 py-3 pl-12">
                            <PoLinesPanel po={p} onChanged={notify} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            )}
          </table>
        )}

        {tab === "vendors" && (
          <table className="tbl w-full">
            <thead>
              <tr>
                <th>Vendor</th>
                <th>Status</th>
                <th>Terms</th>
                <th>Bank on file</th>
                <th>Contact</th>
                <th>Learned rules</th>
              </tr>
            </thead>
            {!ref ? (
              <SkeletonRows cols={6} />
            ) : (
              <tbody>
                {vendors.map((v: any) => (
                  <tr key={v.vendor_id}>
                    <td className="max-w-[280px]">
                      <div className="truncate font-medium text-slate-900">{v.name}</div>
                      <div className="mt-1 flex flex-wrap gap-1">
                        <span className="pill num">{v.vendor_id}</span>
                        <span className="pill num">{v.tax_id}</span>
                      </div>
                    </td>
                    <td>
                      <StatusChip value={v.status} good={v.status === "approved"} />
                      {v.status_reason && <div className="mt-1 max-w-[200px] text-xs text-slate-500">{v.status_reason}</div>}
                    </td>
                    <td className="whitespace-nowrap text-slate-600">{v.payment_terms}</td>
                    <td className="whitespace-nowrap">
                      <div className="text-slate-700">{v.bank.bank_name}</div>
                      <div className="num text-xs text-slate-500">{v.bank.account_number}</div>
                    </td>
                    <td>
                      <span className="pill">{v.email}</span>
                    </td>
                    <td>
                      {v.learned?.length ? (
                        <div className="flex flex-wrap gap-1">
                          {v.learned.map((m: any) => (
                            <span key={m.id} className="chip bg-violet-50 text-violet-700" title={`${m.detail || ""} · used ${m.hits}×`}>
                              {m.kind === "alias" ? `Alias “${m.value}”` : m.kind === "date_format" ? (m.value === "DMY" ? "Dates DD/MM" : "Dates MM/DD") : m.value}
                              <button
                                className="-mr-0.5 rounded text-violet-400 hover:text-rose-600"
                                aria-label="Forget this learned rule"
                                onClick={() => confirm("Forget this learned rule?") && api.forget(m.id).then(() => api.reference().then(setRef))}
                              >
                                <X className="h-3 w-3" />
                              </button>
                            </span>
                          ))}
                        </div>
                      ) : (
                        <span className="text-slate-400">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            )}
          </table>
        )}

        {tab === "ledger" && (
          <table className="tbl w-full">
            <thead>
              <tr>
                <th>Invoice</th>
                <th>Vendor</th>
                <th>PO</th>
                <th>Date</th>
                <th className="text-right">Subtotal</th>
                <th className="text-right">Total</th>
                <th>Status</th>
                <th>Source</th>
              </tr>
            </thead>
            {!ref ? (
              <SkeletonRows cols={8} />
            ) : (
              <tbody>
                {ledger.map((e: any) => (
                  <tr
                    key={e.id}
                    tabIndex={e.run_id ? 0 : undefined}
                    onClick={() => e.run_id && nav(`/runs/${e.run_id}`)}
                    onKeyDown={(k) => k.key === "Enter" && e.run_id && nav(`/runs/${e.run_id}`)}
                    className={clsx(e.run_id && "cursor-pointer focus:outline-none focus-visible:bg-slate-50")}
                    title={e.run_id ? "Open the extracted invoice" : undefined}
                  >
                    <td className="num whitespace-nowrap font-medium text-slate-900">{e.invoice_number}</td>
                    <td className="max-w-[240px] truncate">{vendorName(e.vendor_id)}</td>
                    <td className="num whitespace-nowrap text-slate-600">{e.po_number || "—"}</td>
                    <td className="num whitespace-nowrap text-slate-500">{e.invoice_date}</td>
                    <td className="num text-right">{fmtMoney(e.subtotal)}</td>
                    <td className="num text-right font-medium text-slate-900">{fmtMoney(e.total)}</td>
                    <td>
                      <StatusChip value={e.status} good={e.status === "paid" || e.status === "approved" || e.status === "posted"} />
                    </td>
                    <td className="whitespace-nowrap text-slate-500">
                      {e.source === "seed" ? "Historical" : <Link className="font-medium text-brand-600 hover:underline" to={`/runs/${e.run_id}`} onClick={(ev) => ev.stopPropagation()}>AP Autopilot</Link>}
                    </td>
                  </tr>
                ))}
              </tbody>
            )}
          </table>
        )}

        {tab === "sources" && <RefSources onChanged={notify} onCount={setSourceCount} />}

        {tab === "fx" && ref && (
          <div className="grid divide-y divide-slate-100 lg:grid-cols-2 lg:divide-x lg:divide-y-0">
            <div>
              <div className="flex items-center justify-between px-4 py-3">
                <h3 className="text-sm font-semibold text-slate-900">FX rates to USD</h3>
                <span className="text-xs text-slate-500">
                  {ref.fx_rates?.source} · {ref.fx_rates?.as_of}
                </span>
              </div>
              <table className="tbl w-full">
                <thead>
                  <tr>
                    <th>Currency</th>
                    <th className="text-right">1 unit in USD</th>
                    <th className="text-right">1,000 units</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries((ref.fx_rates?.to_usd || {}) as Record<string, number>).map(([c, r]) => (
                    <tr key={c}>
                      <td className="font-medium text-slate-900">{c}</td>
                      <td className="num text-right">{r}</td>
                      <td className="num text-right">{fmtMoney(r * 1000)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div>
              <div className="flex items-center justify-between px-4 py-3">
                <h3 className="text-sm font-semibold text-slate-900">Review teams</h3>
                <Link to="/settings" className="text-xs font-medium text-slate-500 hover:text-slate-900">
                  Edit SLAs
                </Link>
              </div>
              <table className="tbl w-full">
                <thead>
                  <tr>
                    <th>Team</th>
                    <th>Channels</th>
                    <th className="text-right">SLA</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries((ref.teams || {}) as Record<string, any>).map(([t, v]) => (
                    <tr key={t}>
                      <td className="font-medium text-slate-900">{t}</td>
                      <td>
                        <div className="flex flex-wrap gap-1">
                          {v.email && <span className="pill">{v.email}</span>}
                          {v.slack && <span className="pill">{v.slack}</span>}
                        </div>
                      </td>
                      <td className="num whitespace-nowrap text-right text-slate-600">{v.sla_setting.replace("sla_", "").replace("_hours", "")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {ref && tab !== "fx" && tab !== "sources" && (tab === "pos" ? pos : tab === "vendors" ? vendors : ledger).length === 0 && (
          <Empty icon={Search} title="Nothing matches your search">
            <button className="font-medium text-brand-600 hover:underline" onClick={() => setQ("")}>
              Clear search
            </button>
          </Empty>
        )}
      </div>
      {importing && (
        <ImportWizard
          initialKind={tab === "vendors" ? "vendor" : "po"}
          onClose={() => setImporting(false)}
          onDone={notify}
        />
      )}
      {history && <RefHistory onClose={() => setHistory(false)} />}
      {toast &&
        createPortal(
          <div role="status" className="fixed bottom-4 right-4 z-50 max-w-sm rounded-lg bg-slate-800 px-3.5 py-2.5 text-[13px] text-slate-50 shadow-lg animate-fadein">
            {toast}
          </div>,
          document.body,
        )}
    </section>
  );
}
