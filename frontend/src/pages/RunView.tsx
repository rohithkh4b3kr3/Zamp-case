import clsx from "clsx";
import {
  ArrowLeft,
  ArrowRight,
  ClipboardList,
  BellRing,
  Clock,
  Mail as MailIcon,
  MessageSquare,
  Check,
  ClipboardCopy,
  FileJson,
  FileSpreadsheet,
  FileText,
  GitBranch,
  History,
  Layers,
  ListChecks,
  Mail,
  Scale,
  ShieldAlert,
  UserCheck,
  X,
} from "lucide-react";
import React, { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, fmtMoney, fmtTime, type Run, type Stage } from "../api";
import AskPanel from "../components/AskPanel";
import PoMatch from "../components/PoMatch";
import ExtractedFields, { FIELD_LABELS } from "../components/ExtractedFields";
import type { Region } from "../components/PdfViewer";

// pdf.js is large: load it only when a run is opened
const PdfViewer = lazy(() => import("../components/PdfViewer"));
import SplitPane from "../components/SplitPane";
import { CheckIcon, DecisionBadge, MethodTag, StageIcon, StatusPill } from "../components/ui";

type Tab = "decision" | "data" | "po" | "checks" | "ask" | "audit";

export default function RunView() {
  const { id } = useParams();
  const nav = useNavigate();
  const [run, setRun] = useState<Run | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [pinned, setPinned] = useState(false);
  const [tab, setTab] = useState<Tab>("data");
  const [tabPinned, setTabPinned] = useState(false);
  const [active, setActive] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    setRun(null);
    setSelected(null);
    setPinned(false);
    setActive(null);
    setTab("data");
    setTabPinned(false);
    let es: EventSource | null = null;
    api
      .run(id)
      .then((r) => {
        setRun(r);
        if (r.status === "completed" || r.status === "failed") return;
        es = new EventSource(`/api/runs/${id}/events`);
        es.onmessage = (ev) => setRun((prev) => ({ ...(prev || {}), ...JSON.parse(ev.data) }));
        es.addEventListener("done", () => {
          es?.close();
          api.run(id).then(setRun);
        });
        es.onerror = () => {
          es?.close();
          api.run(id).then(setRun);
        };
      })
      .catch((e) => setErr(e.message));
    return () => es?.close();
  }, [id]);

  // follow the active stage while the run is live, unless the user picked one
  useEffect(() => {
    if (!run || pinned) return;
    const running = run.stages.find((s) => s.status === "running");
    if (running) setSelected(running.key);
    else if (run.status === "completed" || run.status === "failed") {
      const firstBad = run.stages.find((s) => s.status === "failed") || run.stages.find((s) => s.status === "warning");
      setSelected((firstBad || run.stages[run.stages.length - 1]).key);
    }
  }, [run, pinned]);

  // live: show the stage checks; when the decision lands, switch to it (unless the user chose a tab)
  const done = run?.status === "completed";
  useEffect(() => {
    if (tabPinned || !run) return;
    setTab(done && run.decision_detail ? "decision" : run.extracted ? "data" : "checks");
  }, [done, !!run?.extracted, !!run?.decision_detail, tabPinned]); // eslint-disable-line react-hooks/exhaustive-deps

  // regions for the PDF overlay
  const regions = useMemo<Region[]>(() => {
    const x = run?.extracted;
    if (!x) return [];
    const out: Region[] = [];
    const failing = new Set<string>();
    for (const s of run!.stages) for (const c of s.checks) if (c.status === "fail") failing.add(c.id);
    for (const [k, b] of Object.entries(x.field_boxes || {})) {
      if (k !== "bank" && !FIELD_LABELS[k]) continue; // e.g. tax_rate shares the tax row
      const tone = k === "bank" && failing.has("bank_account") ? "rose" : k === "invoice_number" && failing.has("invoice_number_dup") ? "rose" : "brand";
      out.push({ key: k, label: k === "bank" ? "Remit-to bank" : FIELD_LABELS[k] || k.replace(/_/g, " "), box: b, tone });
    }
    x.line_items.forEach((l, i) => l.box && out.push({ key: `line_${i}`, label: `Line ${i + 1} · ${l.description.slice(0, 28)}`, box: l.box }));
    x.cues?.forEach((c, i) => c.box && out.push({ key: `cue_${i}`, label: c.type.replace("_", " "), box: c.box, tone: "amber" }));
    return out;
  }, [run?.extracted, run?.stages]); // eslint-disable-line react-hooks/exhaustive-deps

  if (err) return <div className="card p-6 text-rose-600">{err}</div>;
  if (!run)
    return (
      <div className="space-y-4">
        <div className="skeleton h-6 w-72" />
        <div className="skeleton h-20 w-full" />
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="skeleton h-[60vh]" />
          <div className="skeleton h-[60vh]" />
        </div>
      </div>
    );

  const live = run.status === "running" || run.status === "queued";
  const stage = run.stages.find((s) => s.key === selected) || run.stages[0];
  const doneCount = run.stages.filter((s) => !["pending", "running"].includes(s.status)).length;
  const pickTab = (t: Tab) => {
    setTab(t);
    setTabPinned(true);
  };

  const correct = async (fields: Record<string, any>, note: string) => {
    const r = await api.correct(run.id, fields, "AP Reviewer", note);
    nav(`/runs/${r.id}`);
  };

  const tabs: [Tab, string, any, boolean][] = [
    ["decision", "Decision", Scale, !!run.decision_detail],
    ["data", "Extracted data", FileText, true],
    ["po", "PO match", ClipboardList, run.status === "completed"],
    ["checks", "Checks & log", ListChecks, true],
    ["ask", "Ask", MessageSquare, run.status === "completed"],
    ["audit", "Audit trail", History, true],
  ];

  const meta = [
    `Run ${run.id.slice(0, 8)}`,
    fmtTime(run.created_at),
    run.filename,
    run.extraction_method ? `read by ${run.extraction_method}` : null,
    run.duration_ms != null ? `${(run.duration_ms / 1000).toFixed(1)}s` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="flex flex-col gap-2 animate-fadein lg:h-[calc(100dvh-5.5rem)]">
      {/* toolbar: identity · status · lineage · export */}
      <div className="flex h-10 shrink-0 items-center gap-2">
        <Link to="/" className="btn-ghost w-8 px-0" title="All runs" aria-label="All runs">
          <ArrowLeft className="h-4 w-4" />
        </Link>
        <div className="flex min-w-0 items-baseline gap-2" title={meta}>
          <span className="truncate text-[13px] font-semibold text-slate-900">{run.vendor_name || run.filename}</span>
          {run.invoice_number && <span className="num shrink-0 text-[13px] text-slate-500">#{run.invoice_number}</span>}
        </div>
        {live ? (
          <span className="chip shrink-0 bg-brand-50 text-brand-700">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand-600" /> Running · {Math.min(doneCount + 1, 8)}/8
          </span>
        ) : run.status === "failed" ? (
          <StatusPill status="failed" />
        ) : (
          <DecisionBadge decision={run.decision} />
        )}

        <div className="ml-1 flex min-w-0 items-center gap-1.5 overflow-x-auto [scrollbar-width:none]">
          {run.email && (
            <Link to="/inbox" className="pill shrink-0" title={`From ${run.email.from_addr} · “${run.email.subject}”`}>
              <MailIcon className="h-3 w-3" /> {run.email.from_addr}
            </Link>
          )}
          {run.batch && run.batch.length > 1 && (
            <span className="flex shrink-0 items-center gap-1" title="Invoices split from one PDF">
              <Layers className="h-3.5 w-3.5 text-slate-400" />
              {run.batch.map((b, i) => (
                <Link
                  key={b.id}
                  to={`/runs/${b.id}`}
                  title={b.batch_label}
                  className={clsx("chip", b.id === run.id ? "bg-brand-50 text-brand-700" : "bg-slate-100 text-slate-600 hover:bg-slate-200")}
                >
                  {i + 1}
                  {b.decision && b.id !== run.id && <DecisionDot d={b.decision} />}
                </Link>
              ))}
            </span>
          )}
          {run.parent_run_id && (
            <Link to={`/runs/${run.parent_run_id}`} className="chip shrink-0 bg-brand-50 text-brand-700" title={`Re-run with reviewer corrections: ${Object.keys(run.overrides || {}).join(", ")}`}>
              <GitBranch className="h-3 w-3" /> Corrected re-run
            </Link>
          )}
          {run.superseded_by && (
            <Link to={`/runs/${run.superseded_by}`} className="chip shrink-0 bg-amber-50 text-amber-800" title="A corrected version of this run exists">
              <GitBranch className="h-3 w-3" /> Superseded <ArrowRight className="h-3 w-3" />
            </Link>
          )}
        </div>

        <div className="flex-1" />
        {done && (
          <>
            <a className="btn-ghost w-8 px-0" href={api.exportUrl(run.id, "json")} title="Download extraction and decision (JSON)" aria-label="Download JSON">
              <FileJson className="h-4 w-4" />
            </a>
            <a className="btn-ghost w-8 px-0" href={api.exportUrl(run.id, "csv")} title="Download fields and line items (CSV)" aria-label="Download CSV">
              <FileSpreadsheet className="h-4 w-4" />
            </a>
          </>
        )}
      </div>

      <Stepper
        stages={run.stages}
        selected={stage.key}
        onSelect={(k) => {
          setSelected(k);
          setPinned(true);
          pickTab("checks");
        }}
      />

      {run.status === "failed" && <div className="shrink-0 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[13px] text-rose-700">Run failed: {run.error}</div>}

      {/* ai24c layout: document left, data right */}
      <SplitPane
        storageKey="runview"
        className="min-h-0 flex-1"
        left={
          <Suspense
            fallback={
              <div className="flex h-full items-center justify-center rounded-xl border border-slate-200 bg-slate-100">
                <div className="skeleton h-[85%] w-[80%]" />
              </div>
            }
          >
            <PdfViewer
              url={api.fileUrl(run.id)}
              regions={regions}
              active={active}
              downloadName={run.filename}
              onRegionClick={(k) => {
                setActive(k);
                pickTab("data");
              }}
            />
          </Suspense>
        }
        right={
          <div className="card flex h-full min-h-0 flex-col overflow-hidden">
            <div className="flex shrink-0 gap-1 overflow-x-auto border-b border-slate-100 px-2 [scrollbar-width:none]">
              {tabs
                .filter(([, , , show]) => show)
                .map(([k, label, Icon]) => (
                  <button
                    key={k}
                    onClick={() => pickTab(k)}
                    className={clsx("-mb-px flex h-10 shrink-0 items-center gap-1.5 border-b-2 px-3 text-[13px] font-medium transition",
                      tab === k ? "border-brand-600 text-brand-700" : "border-transparent text-slate-500 hover:text-slate-800",
                    )}
                  >
                    <Icon className="h-4 w-4" /> {label}
                    {k === "decision" && run.decision && <DecisionDot d={run.decision} />}
                  </button>
                ))}
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-4">
              {tab === "decision" && run.decision_detail && <DecisionPanel run={run} onUpdate={setRun} />}
              {tab === "data" && <ExtractedFields run={run} active={active} setActive={setActive} onCorrect={correct} />}
              {tab === "po" && <PoMatch run={run} onRerun={correct} />}
              {tab === "checks" && <StagePanel stage={stage} />}
              {tab === "ask" && <AskPanel run={run} />}
              {tab === "audit" && <AuditPanel run={run} />}
            </div>
          </div>
        }
      />
    </div>
  );
}

function DecisionDot({ d }: { d: string }) {
  return <span className={clsx("h-2 w-2 rounded-full", d === "APPROVED" ? "bg-emerald-500" : d === "REJECTED" ? "bg-rose-500" : "bg-amber-500")} />;
}

/* ------------------------------------------------------------------ stepper (compact, one row) */
function Stepper({ stages, selected, onSelect }: { stages: Stage[]; selected: string; onSelect: (k: string) => void }) {
  return (
    <div className="card flex h-10 shrink-0 items-center overflow-x-auto px-2 [scrollbar-width:none]">
      <ol className="flex w-full min-w-[760px] items-center">
        {stages.map((s, i) => {
          const done = !["pending", "running"].includes(s.status);
          const on = selected === s.key;
          return (
            <li key={s.key} className="flex flex-1 items-center">
              {i > 0 && (
                <div className="mx-1 h-px flex-1 bg-slate-200">
                  <div className={clsx("h-full bg-brand-500 transition-all duration-500", done || s.status === "running" ? "w-full" : "w-0")} />
                </div>
              )}
              <button
                onClick={() => onSelect(s.key)}
                title={`${s.name} — ${s.status === "running" ? "working…" : s.summary || s.status}`}
                className={clsx("flex h-7 shrink-0 items-center gap-1.5 rounded-md px-2 text-xs font-medium transition",
                  on ? "bg-brand-50 text-brand-700" : "text-slate-600 hover:bg-slate-100",
                  s.status === "running" && "animate-pulse",
                  (s.status === "pending" || s.status === "skipped") && !on && "text-slate-400",
                )}
              >
                <StageIcon status={s.status} className="h-4 w-4" />
                <span className="whitespace-nowrap">{s.name}</span>
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/* ------------------------------------------------------------------ decision */
function DecisionPanel({ run, onUpdate }: { run: Run; onUpdate: (r: Run) => void }) {
  const d = run.decision_detail!;
  const tone = {
    APPROVED: "border-emerald-200 bg-gradient-to-br from-emerald-50 to-white",
    NEEDS_REVIEW: "border-amber-200 bg-gradient-to-br from-amber-50 to-white",
    REJECTED: "border-rose-200 bg-gradient-to-br from-rose-50 to-white",
  }[d.decision];
  const [copied, setCopied] = useState(false);
  const [showEmail, setShowEmail] = useState(false);

  return (
    <section className={clsx("rounded-xl border p-5 shadow-sm animate-fadein", tone)}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-3">
          <DecisionBadge decision={d.decision} size="lg" />
          {d.doc_type === "credit_note" && <span className="chip bg-sky-50 text-sky-700">Credit note</span>}
          {d.doc_type === "prepayment" && <span className="chip bg-violet-50 text-violet-700">Deposit invoice</span>}
          {d.route_to && (
            <span className="text-[13px] text-slate-600">
              {d.decision === "REJECTED" && d.route_to === "Vendor" ? "Returned to vendor" : <>Routed to <b>{d.route_to}</b></>}
            </span>
          )}
        </div>
        <div className="text-xs text-slate-500">
          {d.checks_passed}/{d.checks_total} checks passed{d.po && <> · {d.po} ({d.po_source})</>}
        </div>
      </div>
      <p className="mt-3 text-[15px] font-medium leading-relaxed text-slate-900">{d.headline}</p>

      <div className="mt-4 grid grid-cols-3 gap-3">
        <Amount label="Invoice total" value={d.amounts.invoice_total} cur={d.currency} />
        <Amount label={d.decision === "APPROVED" ? "Approved to pay" : "Can be paid now"} value={d.amounts.recommended_payment} tone="green" cur={d.currency} />
        <Amount label="On hold" value={d.amounts.on_hold ?? (d.decision !== "APPROVED" ? d.amounts.invoice_total : null)} tone={d.decision === "APPROVED" ? undefined : "rose"} cur={d.currency} />
      </div>
      {d.amounts.deposit_deducted ? (
        <div className="mt-2 flex items-center justify-between rounded-lg bg-white/70 px-3 py-2 text-[13px] ring-1 ring-slate-200">
          <span className="text-slate-600">Deposit already paid on this PO</span>
          <span className="num font-medium text-slate-900">−{fmtMoney(d.amounts.deposit_deducted, d.currency || "USD")}</span>
        </div>
      ) : null}
      {d.fx && (
        <div className="mt-2 text-xs text-slate-500">
          ≈ <b className="text-slate-700">{fmtMoney(d.fx.base_total)}</b> at {d.currency}→USD {d.fx.rate} (rates as of {d.fx.as_of}); approval limits use the USD amount.
        </div>
      )}
      {d.learned && d.learned.length > 0 && (
        <div className="mt-3 rounded-xl border border-violet-200 bg-violet-50 px-3 py-2 text-[13px] text-violet-900">
          <b>Learned from this correction:</b> {d.learned.join("; ")}. Applied automatically to this vendor's next invoices.
        </div>
      )}
      {(run.notifications || []).filter((n) => n.kind === "routed" || n.kind === "escalation").length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-600">
          <BellRing className="h-3.5 w-3.5 text-brand-600" />
          {(run.notifications || [])
            .filter((n) => n.kind === "routed" || n.kind === "escalation")
            .map((n) => (
              <span key={n.id} className={clsx("rounded-md px-1.5 py-0.5", n.kind === "escalation" ? "bg-rose-50 text-rose-700" : "bg-slate-100")}>
                {n.kind === "escalation" ? "Escalated" : "Notified"} {n.team} via {n.channel} ({n.status})
              </span>
            ))}
          {run.sla_due_at && run.review_status === "pending" && <SlaBadge due={run.sla_due_at} />}
        </div>
      )}

      {d.reasons.length > 0 && (
        <div className="mt-4">
          <div className="label mb-2">Why</div>
          <ul className="space-y-2">
            {d.reasons.map((r, i) => (
              <li key={i} className="flex gap-2 text-[13px]">
                <span
                  className={clsx("mt-1.5 h-2 w-2 shrink-0 rounded-full",
                    r.severity === "critical" ? "bg-rose-600" : r.severity === "high" ? "bg-rose-400" : "bg-amber-400",
                  )}
                />
                <span>
                  <b className="font-semibold text-slate-800">{r.label}.</b>{r.custom && <span className="chip mx-1 bg-violet-50 text-violet-700">Custom rule</span>} <span className="text-slate-700">{r.text}</span>
                  {r.route && <span className="ml-1 text-xs text-slate-400">→ {r.route}</span>}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <div>
          <div className="label mb-2">Next actions</div>
          <ol className="space-y-1.5">
            {d.actions.map((a, i) => (
              <li key={i} className="flex gap-2 text-[13px] text-slate-700">
                <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-white text-[11px] font-semibold text-slate-500 ring-1 ring-slate-200">{i + 1}</span>
                {a}
              </li>
            ))}
          </ol>
        </div>
        <div className="space-y-3">
          {d.payment && (
            <div className="rounded-lg bg-white/70 p-3 text-[13px] ring-1 ring-slate-200">
              <div className="label mb-1">Payment</div>
              {d.payment.discount ? (
                <>
                  Pay <b className="num">{fmtMoney(d.payment.discount.pay, d.currency || "USD")}</b> by <b>{d.payment.discount.by}</b> to save{" "}
                  <b className="num text-emerald-700">{fmtMoney(d.payment.discount.amount, d.currency || "USD")}</b> ({d.payment.discount.pct}% early-payment discount)
                </>
              ) : (
                <>
                  {d.payment.amount != null && (
                    <>
                      <b className="num">{fmtMoney(d.payment.amount, d.currency || "USD")}</b>{" "}
                    </>
                  )}
                  scheduled <b>{d.payment.scheduled_for}</b>
                </>
              )}
              <div className="text-xs text-slate-500">
                {d.payment.terms} · to {d.payment.to_account}
              </div>
            </div>
          )}
          {d.internal_note && (
            <div className="rounded-lg bg-rose-50 p-3 text-[13px] text-rose-800 ring-1 ring-rose-200">
              <div className="label mb-1 flex items-center gap-1.5 !text-rose-700"><ShieldAlert className="h-3.5 w-3.5" /> Internal note</div>
              {d.internal_note}
            </div>
          )}
        </div>
      </div>

      {d.vendor_email && (
        <div className="mt-4 rounded-lg bg-white ring-1 ring-slate-200">
          <button onClick={() => setShowEmail((v) => !v)} className="flex w-full items-center justify-between px-3 py-2.5 text-left">
            <span className="flex items-center gap-2 text-[13px] font-medium text-slate-700">
              <Mail className="h-4 w-4 text-brand-600" /> Drafted vendor email
              <span className="text-xs font-normal text-slate-400">— ready for a human to send</span>
            </span>
            <span className="text-xs text-brand-600">{showEmail ? "Hide" : "Show"}</span>
          </button>
          {showEmail && (
            <div className="border-t border-slate-100 p-3">
              <div className="text-xs text-slate-500">
                To: <span className="num">{d.vendor_email.to}</span>
              </div>
              <div className="mt-0.5 text-xs text-slate-500">
                Subject: <span className="font-medium text-slate-700">{d.vendor_email.subject}</span>
              </div>
              <pre className="mt-2 whitespace-pre-wrap rounded-md bg-slate-50 p-3 font-sans text-[13px] leading-relaxed text-slate-700">{d.vendor_email.body}</pre>
              <button
                className="btn-outline mt-2"
                onClick={() => {
                  navigator.clipboard.writeText(`Subject: ${d.vendor_email!.subject}\n\n${d.vendor_email!.body}`);
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1500);
                }}
              >
                {copied ? <Check className="h-4 w-4 text-emerald-600" /> : <ClipboardCopy className="h-4 w-4" />} {copied ? "Copied" : "Copy email"}
              </button>
            </div>
          )}
        </div>
      )}

      {d.decision === "NEEDS_REVIEW" && <ReviewBox run={run} onUpdate={onUpdate} />}
    </section>
  );
}

export function SlaBadge({ due }: { due: string }) {
  const ms = new Date(due).getTime() - Date.now();
  const h = Math.abs(ms) / 3.6e6;
  const txt = h < 1 ? `${Math.round(h * 60)}m` : h < 48 ? `${h.toFixed(1)}h` : `${Math.round(h / 24)}d`;
  return (
    <span className={clsx("inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium", ms < 0 ? "bg-rose-50 text-rose-700" : h < 4 ? "bg-amber-50 text-amber-800" : "bg-slate-100 text-slate-600")}>
      <Clock className="h-3 w-3" /> {ms < 0 ? `SLA overdue ${txt}` : `SLA ${txt} left`}
    </span>
  );
}

function Amount({ label, value, tone, cur }: { label: string; value: number | null | undefined; tone?: "green" | "rose"; cur?: string }) {
  return (
    <div className="rounded-lg bg-white/80 p-3 ring-1 ring-slate-200">
      <div className="text-[11px] font-medium text-slate-500">{label}</div>
      <div className={clsx("mt-0.5 text-lg font-semibold num", value == null ? "text-slate-300" : tone === "green" ? "text-emerald-700" : tone === "rose" ? "text-rose-700" : "text-slate-900")}>
        {value == null ? "—" : fmtMoney(value, cur || "USD")}
      </div>
    </div>
  );
}

function ReviewBox({ run, onUpdate }: { run: Run; onUpdate: (r: Run) => void }) {
  const [note, setNote] = useState("");
  const [who, setWho] = useState("A. Reviewer");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  if (run.review_status && run.review_status !== "pending") {
    return (
      <div className={clsx("mt-4 flex items-start gap-3 rounded-lg p-3 text-[13px] ring-1", run.review_status === "approved" ? "bg-emerald-50 ring-emerald-200" : "bg-rose-50 ring-rose-200")}>
        <UserCheck className="mt-0.5 h-4 w-4" />
        <div>
          <b className="capitalize">{run.review_status}</b> by {run.reviewer} · {run.reviewed_at && fmtTime(run.reviewed_at)}
          {run.review_note && <div className="mt-0.5 text-slate-600">“{run.review_note}”</div>}
          {run.review_status === "approved" && <div className="text-xs text-slate-500">Posted to ledger as an approved exception.</div>}
        </div>
      </div>
    );
  }
  const act = async (a: "approve" | "reject") => {
    setBusy(true);
    setErr(null);
    try {
      onUpdate(await api.review(run.id, a, who, note));
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mt-4 rounded-lg border border-amber-200 bg-white p-3">
      <div className="label mb-2 flex items-center gap-1.5"><UserCheck className="h-3.5 w-3.5" /> Reviewer decision ({run.route_to})</div>
      <div className="grid gap-2 sm:grid-cols-[160px_1fr]">
        <input className="input" value={who} onChange={(e) => setWho(e.target.value)} placeholder="Your name" />
        <input className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Reason (required to approve an exception)" />
      </div>
      {err && <div className="mt-2 text-xs text-rose-600">{err}</div>}
      <div className="mt-2 flex gap-2">
        <button className="btn bg-emerald-600 text-white hover:bg-emerald-700" disabled={busy} onClick={() => act("approve")}>
          <Check className="h-4 w-4" /> Approve exception
        </button>
        <button className="btn bg-rose-600 text-white hover:bg-rose-700" disabled={busy} onClick={() => act("reject")}>
          <X className="h-4 w-4" /> Reject
        </button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ stage detail */
function StagePanel({ stage }: { stage: Stage }) {
  const logRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [stage.logs.length]);

  return (
    <div>
      <div className="mb-2 flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 text-[13px] font-semibold text-slate-800">
          <StageIcon status={stage.status} className="h-4 w-4" />
          {stage.name}
          <span className="font-normal text-slate-400">— {stage.description}</span>
        </div>
        {stage.duration_ms != null && <span className="shrink-0 num text-xs text-slate-400">{stage.duration_ms} ms</span>}
      </div>
      {stage.status === "pending" && <p className="text-[13px] text-slate-400">Waiting for earlier stages…</p>}
      {stage.status === "skipped" && <p className="text-[13px] text-slate-500">Skipped: {stage.summary}</p>}

      {stage.checks.length > 0 && (
        <ul className="divide-y divide-slate-100">
          {stage.checks.map((c, i) => (
            <li key={i} className="flex gap-3 py-2.5 animate-fadein">
              <CheckIcon status={c.status} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[13px] font-medium text-slate-800">{c.label}</span>
                  {c.id.startsWith("custom_") && <span className="chip bg-violet-50 text-violet-700">Custom rule</span>}
                  {c.outcome && (
                    <span className={clsx("chip capitalize", c.outcome === "reject" ? "bg-rose-50 text-rose-700" : "bg-amber-50 text-amber-800")}>
                      {c.outcome === "reject" ? "blocks" : "needs review"}
                    </span>
                  )}
                  {c.route && c.outcome && <span className="text-[11px] text-slate-400">→ {c.route}</span>}
                </div>
                <div className="text-[13px] text-slate-600">{c.message}</div>
              </div>
            </li>
          ))}
        </ul>
      )}

      {stage.key === "po_match" && stage.data?.candidates && <Candidates rows={stage.data.candidates} />}
      {stage.key === "three_way" && stage.data?.lines && <MatchTable data={stage.data} />}

      {stage.logs.length > 0 && (
        <div className="mt-3">
          <div className="label mb-1.5">Execution log</div>
          <div ref={logRef} className="max-h-44 overflow-y-auto rounded-lg border border-slate-200 bg-slate-50 p-3 num text-[12px] leading-relaxed text-slate-700">
            {stage.logs.map((l, i) => (
              <div key={i}>
                <span className="text-slate-400">+{String(l.t).padStart(4, " ")}ms</span> {l.msg}
              </div>
            ))}
            {stage.status === "running" && <div className="animate-pulse text-brand-600">▍</div>}
          </div>
        </div>
      )}
    </div>
  );
}

function Candidates({ rows }: { rows: any[] }) {
  return (
    <div className="mt-3">
      <div className="label mb-1.5">PO inference — candidate scoring</div>
      <table className="tbl w-full overflow-hidden rounded-lg ring-1 ring-slate-200">
        <thead>
          <tr>
            <th>Open PO</th>
            <th>Lines covered</th>
            <th>Similarity</th>
            <th>Amount fit</th>
            <th>Remaining</th>
            <th className="text-right">Score</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.po_number} className={i === 0 ? "bg-emerald-50/50" : ""}>
              <td className="num text-xs">{r.po_number}</td>
              <td>{Math.round(r.coverage * 100)}%</td>
              <td>{Math.round(r.line_similarity * 100)}%</td>
              <td>{Math.round(r.amount_fit * 100)}%</td>
              <td className="num">{fmtMoney(r.remaining)}</td>
              <td className="text-right font-semibold">{r.score.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-1 text-[11px] text-slate-400">Score = 45% line coverage + 30% description/SKU similarity + 25% fit against remaining PO balance.</p>
    </div>
  );
}

function MatchTable({ data }: { data: any }) {
  const pct = Math.min(100, (data.cumulative / data.po_total) * 100);
  const before = Math.min(100, (data.billed_before / data.po_total) * 100);
  const over = data.cumulative > data.po_total;
  return (
    <div className="mt-3 space-y-3">
      <div>
        <div className="mb-1 flex justify-between text-xs text-slate-500">
          <span>
            PO consumption: billed before {fmtMoney(data.billed_before)} {data.prior_invoices?.length ? `(${data.prior_invoices.join(", ")})` : ""}
          </span>
          <span className={over ? "font-semibold text-rose-600" : ""}>
            {fmtMoney(data.cumulative)} / {fmtMoney(data.po_total)}
          </span>
        </div>
        <div className="relative h-2.5 overflow-hidden rounded-full bg-slate-100">
          <div className={clsx("absolute inset-y-0 left-0", over ? "bg-rose-400" : "bg-brand-400")} style={{ width: `${pct}%` }} />
          <div className="absolute inset-y-0 left-0 bg-slate-400" style={{ width: `${before}%` }} />
        </div>
        <div className="mt-1 text-[11px] text-slate-400">
          Grey = previously billed · colour = this invoice · receipts {data.receipts?.join(", ") || "none"}
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="tbl w-full rounded-lg ring-1 ring-slate-200">
          <thead>
            <tr>
              <th>Invoice line</th>
              <th>PO line</th>
              <th className="text-right">Qty</th>
              <th className="text-right">Price vs PO</th>
              <th className="text-right">Ordered / received / billed before</th>
              <th>Result</th>
            </tr>
          </thead>
          <tbody>
            {data.lines.map((l: any, i: number) => (
              <tr key={i}>
                <td className="max-w-[200px]">
                  <div className="text-slate-800">{l.description}</div>
                  <div className="num text-[11px] text-slate-400">{l.sku || ""}</div>
                </td>
                <td className="max-w-[180px] text-xs text-slate-600">{l.po_line ? `#${l.po_line} ${l.po_description}` : <span className="text-rose-600">— none —</span>}</td>
                <td className="text-right num">{l.qty}</td>
                <td className="text-right num">
                  {fmtMoney(l.unit_price)}
                  {l.po_price != null && (
                    <div className={clsx("text-[11px]", Math.abs(l.price_variance_pct) > 0.001 ? "text-amber-600" : "text-slate-400")}>
                      PO {fmtMoney(l.po_price)} ({l.price_variance_pct > 0 ? "+" : ""}
                      {l.price_variance_pct}%)
                    </div>
                  )}
                </td>
                <td className="text-right text-xs num text-slate-600">{l.po_line ? `${l.po_qty} / ${l.received} / ${l.previously_billed}` : "—"}</td>
                <td>
                  <span
                    className={clsx("rounded px-1.5 py-0.5 text-[11px] font-medium",
                      l.status === "pass" ? "bg-emerald-50 text-emerald-700" : l.status === "warn" ? "bg-amber-50 text-amber-800" : "bg-rose-50 text-rose-700",
                    )}
                  >
                    {l.note}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ side panels */
function AuditPanel({ run }: { run: Run }) {
  const details: [string, React.ReactNode][] = [
    ["Run", <span className="num">{run.id}</span>],
    ["Received", fmtTime(run.created_at)],
    ["File", run.filename],
    ["Read by", <MethodTag method={run.extraction_method} />],
    ["Duration", run.duration_ms != null ? <span className="num">{(run.duration_ms / 1000).toFixed(1)}s</span> : "—"],
    ["SHA-256", <span className="num break-all text-[11px]">{run.file_hash || "—"}</span>],
  ];
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-[6rem_1fr] gap-x-3 gap-y-1.5 rounded-lg bg-slate-50 p-3 text-[13px] ring-1 ring-slate-200">
        {details.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-slate-500">{k}</dt>
            <dd className="min-w-0 truncate text-slate-800">{v}</dd>
          </div>
        ))}
      </dl>
      {!run.audit?.length ? (
        <div className="text-[13px] text-slate-500">Audit entries appear when the run completes.</div>
      ) : (
    <ol className="relative space-y-4 border-l border-slate-200 p-4 pl-6">
      {run.audit.map((a) => (
        <li key={a.id} className="relative">
          <span className="absolute -left-[25px] top-1 h-2.5 w-2.5 rounded-full border-2 border-white bg-brand-500" />
          <div className="text-xs text-slate-400">
            {fmtTime(a.ts)} · <span className="font-medium text-slate-600">{a.actor}</span>
          </div>
          <div className="text-[13px] font-medium text-slate-800">{a.action.replace(/_/g, " ")}</div>
          {a.detail && <div className="break-words text-xs text-slate-500">{a.detail}</div>}
        </li>
      ))}
    </ol>
      )}
    </div>
  );
}
