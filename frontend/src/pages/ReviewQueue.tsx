import { ArrowRight, Inbox, Timer } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ago, api, fmtMoney, type RunSummary } from "../api";
import { Card, Empty, StatusPill } from "../components/ui";
import { SlaBadge } from "./RunView";

const ORDER = ["Treasury & Security", "Procurement", "Receiving", "AP Specialist", "Finance Manager"];
const BLURB: Record<string, string> = {
  "Treasury & Security": "Payment-fraud signals: bank details changed, routing mismatch, look-alike sender domain. Verify out-of-band before anything moves.",
  Procurement: "Commercial issues: PO overruns, unplanned charges, closed POs, unknown vendors.",
  Receiving: "Billed before delivered: confirm goods receipts or back-orders.",
  "AP Specialist": "Data issues: low-confidence extraction, arithmetic mismatches, ambiguous PO.",
  "Finance Manager": "Clean invoices above the auto-approval limit.",
};

export default function ReviewQueue() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [showDone, setShowDone] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    const load = () => api.runs().then(setRuns).catch(() => {});
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, []);

  const groups = useMemo(() => {
    const items = runs.filter((r) => r.decision === "NEEDS_REVIEW" && (showDone || r.review_status === "pending"));
    const g: Record<string, RunSummary[]> = {};
    for (const r of items) (g[r.route_to || "AP Specialist"] ||= []).push(r);
    return ORDER.filter((k) => g[k]).map((k) => [k, g[k]] as const);
  }, [runs, showDone]);

  return (
    <div className="space-y-6 animate-fadein">
      <div className="flex flex-wrap items-center justify-end gap-3">
        <div className="flex items-center gap-3">
          <Link to="/inbox" className="text-xs font-medium text-brand-600 hover:underline">
            Team notifications <ArrowRight className="inline h-3.5 w-3.5" />
          </Link>
          <button
            className="btn-outline"
            title="Escalate anything past its SLA now (also runs automatically every 20 s)"
            onClick={() => api.checkSla().then((r) => setNote(r.escalated ? `${r.escalated} escalated` : "Nothing overdue"))}
          >
            <Timer className="h-3.5 w-3.5" /> Check SLAs
          </button>
          {note && <span className="text-xs text-slate-500">{note}</span>}
        </div>
        <label className="flex items-center gap-2 text-[13px] text-slate-600">
          <input type="checkbox" checked={showDone} onChange={(e) => setShowDone(e.target.checked)} className="rounded border-slate-300" /> Show resolved
        </label>
      </div>
      {groups.length === 0 && (
        <div className="card">
          <Empty icon={Inbox} title="No exceptions waiting">Invoices that need a human appear here, grouped by the team that owns the fix.</Empty>
        </div>
      )}
      {groups.map(([team, items]) => (
        <Card key={team} title={<span>{team} <span className="ml-1 rounded-full bg-amber-100 px-2 text-xs text-amber-800">{items.length}</span></span>} pad={false}>
          <p className="border-b border-slate-100 px-4 py-2 text-xs text-slate-500">{BLURB[team]}</p>
          <ul className="divide-y divide-slate-100">
            {items.map((r) => (
              <li key={r.id}>
                <Link to={`/runs/${r.id}`} className="flex flex-wrap items-center gap-4 px-4 py-3 hover:bg-slate-50">
                  <div className="min-w-[200px] flex-1">
                    <div className="font-medium text-slate-800">{r.vendor_name}</div>
                    <div className="num text-xs text-slate-500">
                      {r.invoice_number} · {r.po_number || "no PO"}
                    </div>
                  </div>
                  <div className="text-right font-semibold num">{fmtMoney(r.total, r.currency || "USD")}</div>
                  {r.review_status === "pending" && r.sla_due_at && <SlaBadge due={r.sla_due_at} />}
                  {r.escalated_at && <span className="rounded-md bg-rose-50 px-1.5 py-0.5 text-[11px] font-medium text-rose-700">Escalated</span>}
                  <StatusPill status={r.review_status || "pending"} />
                  <div className="w-16 text-right text-xs text-slate-400">{ago(r.created_at)}</div>
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      ))}
    </div>
  );
}
