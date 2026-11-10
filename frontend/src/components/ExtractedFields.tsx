import clsx from "clsx";
import { Crosshair, Loader2, Pencil, RotateCcw, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { fmtMoney, type Run } from "../api";
import { ConfidenceBar, MethodTag } from "./ui";

type Kind = "text" | "date" | "money";
const FIELDS: { key: string; label: string; kind: Kind }[] = [
  { key: "vendor_name", label: "Vendor", kind: "text" },
  { key: "vendor_tax_id", label: "Tax ID", kind: "text" },
  { key: "invoice_number", label: "Invoice #", kind: "text" },
  { key: "invoice_date", label: "Invoice date", kind: "date" },
  { key: "due_date", label: "Due date", kind: "date" },
  { key: "po_number", label: "PO reference", kind: "text" },
  { key: "subtotal", label: "Subtotal", kind: "money" },
  { key: "tax_amount", label: "Tax", kind: "money" },
  { key: "total", label: "Total", kind: "money" },
];

export const FIELD_LABELS: Record<string, string> = Object.fromEntries(FIELDS.map((f) => [f.key, f.label]));

interface Props {
  run: Run;
  active: string | null;
  setActive: (k: string | null) => void;
  onCorrect: (fields: Record<string, any>, note: string) => Promise<void>;
}

/** Extracted data, ai24c-style: each value is linked to its place on the PDF; reviewers can correct and re-run. */
export default function ExtractedFields({ run, active, setActive, onCorrect }: Props) {
  const x = run.extracted;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const initial = useMemo(() => Object.fromEntries(FIELDS.map((f) => [f.key, x?.[f.key as keyof typeof x] == null ? "" : String(x[f.key as keyof typeof x])])), [x]);
  useEffect(() => {
    setDraft(initial);
  }, [initial]);

  if (!x)
    return (
      <div className="space-y-2">
        {Array.from({ length: 9 }).map((_, i) => (
          <div key={i} className="skeleton h-8" />
        ))}
      </div>
    );

  const boxes = x.field_boxes || {};
  const corrected = x.corrected_fields || {};
  const changed = Object.fromEntries(Object.entries(draft).filter(([k, v]) => v.trim() !== (initial[k] ?? "").trim()));
  const canCorrect = run.status === "completed" && !run.ledger_posted && !run.superseded_by;

  const show = (k: string, kind: Kind) => {
    const v = (x as any)[k];
    if (v == null || v === "") return null;
    if (kind === "money") return fmtMoney(v, x.currency || "USD") + (k === "tax_amount" && x.tax_rate ? ` (${(x.tax_rate * 100).toFixed(2)}%)` : "");
    return String(v);
  };

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await onCorrect(changed, note);
    } catch (e: any) {
      setErr(e.message);
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <div className="label">Header fields</div>
          <MethodTag method={x.method} />
          {x.ocr_engine && <span className="text-[11px] text-slate-400">{x.ocr_engine === "google-vision" ? "Google Vision" : "RapidOCR"}</span>}
          {x.llm_usage && <span className="text-[11px] text-slate-400">{x.llm_usage.model}</span>}
        </div>
        {canCorrect &&
          (editing ? (
            <div className="flex gap-1.5">
              <button className="btn-ghost" onClick={() => { setEditing(false); setDraft(initial); setErr(null); }}>
                <X className="h-3.5 w-3.5" /> Cancel
              </button>
              <button className="btn-primary" disabled={!Object.keys(changed).length || busy} onClick={save}>
                {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
                Save & re-run ({Object.keys(changed).length})
              </button>
            </div>
          ) : (
            <button className="btn-outline" onClick={() => setEditing(true)}>
              <Pencil className="h-3.5 w-3.5" /> Correct fields
            </button>
          ))}
      </div>

      {editing && (
        <div className="rounded-xl border border-brand-200 bg-brand-50 px-3 py-2 text-xs text-brand-800">
          Fix a misread value and the whole pipeline re-runs on the same PDF with your value. The original run stays in the audit trail.
        </div>
      )}

      <dl className="divide-y divide-slate-100 overflow-hidden rounded-xl ring-1 ring-slate-200">
        {FIELDS.map((f) => {
          const value = show(f.key, f.kind);
          const located = !!boxes[f.key];
          const isActive = active === f.key;
          const fix = corrected[f.key];
          return (
            <div
              key={f.key}
              onClick={() => !editing && located && setActive(isActive ? null : f.key)}
              onMouseEnter={() => !editing && located && setActive(f.key)}
              className={clsx("flex items-center justify-between gap-3 px-3 py-2 transition",
                !editing && located && "cursor-pointer hover:bg-slate-50",
                isActive && "bg-brand-50 shadow-[inset_3px_0_0_rgb(var(--brand-500))]",
              )}
            >
              <dt className="flex w-28 shrink-0 items-center gap-1.5 text-xs text-slate-500">
                {f.label}
                {located && !editing && <Crosshair className={clsx("h-3 w-3", isActive ? "text-brand-600" : "text-slate-300")} />}
              </dt>
              {editing ? (
                <input
                  className={clsx("input py-1 text-[13px]", changed[f.key] !== undefined && "border-brand-500 ring-2 ring-brand-100")}
                  value={draft[f.key] ?? ""}
                  placeholder={f.kind === "date" ? "YYYY-MM-DD or Nov 5, 2026" : f.kind === "money" ? "0.00" : "not found"}
                  onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })}
                />
              ) : (
                <dd className={clsx("min-w-0 flex-1 truncate text-[13px]", value ? "font-medium text-slate-800" : "italic text-rose-500")}>
                  {value || "not found"}
                  {fix && (
                    <span className="ml-2 chip bg-brand-50 text-brand-700" title={`Was: ${fix.was ?? "empty"}`}>
                      corrected
                    </span>
                  )}
                </dd>
              )}
              {!editing && <ConfidenceBar value={x.field_confidence?.[f.key]} />}
            </div>
          );
        })}
      </dl>

      {editing && (
        <div className="space-y-2">
          <input className="input" placeholder="Note for the audit trail (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
          {err && <div className="text-xs text-rose-600">{err}</div>}
        </div>
      )}

      <div>
        <div className="label mb-1.5">
          Remit-to bank (as printed)
        </div>
        <div
          onClick={() => boxes.bank && setActive(active === "bank" ? null : "bank")}
          onMouseEnter={() => boxes.bank && setActive("bank")}
          className={clsx("rounded-xl px-3 py-2 text-[13px] ring-1 ring-slate-200 transition",
            boxes.bank && "cursor-pointer hover:bg-slate-50",
            active === "bank" && "bg-brand-50 ring-brand-300",
          )}
        >
          {x.bank?.account_number ? (
            <>
              {x.bank.bank_name || "Bank"} · acct <span className="num">••••{x.bank.account_number.slice(-4)}</span>
              {x.bank.routing_number && (
                <>
                  {" "}
                  · routing <span className="num">{x.bank.routing_number}</span>
                </>
              )}
            </>
          ) : (
            <span className="text-slate-400">none on invoice</span>
          )}
        </div>
      </div>

      <div>
        <div className="label mb-1.5">Line items ({x.line_items.length})</div>
        <div className="overflow-x-auto rounded-xl ring-1 ring-slate-200">
          <table className="tbl w-full">
            <thead>
              <tr>
                <th>Description</th>
                <th className="text-right">Qty</th>
                <th className="text-right">Unit</th>
                <th className="text-right">Amount</th>
              </tr>
            </thead>
            <tbody>
              {x.line_items.map((l, i) => {
                const k = `line_${i}`;
                return (
                  <tr
                    key={i}
                    onClick={() => l.box && setActive(active === k ? null : k)}
                    onMouseEnter={() => l.box && setActive(k)}
                    className={clsx(l.box && "cursor-pointer hover:bg-slate-50", active === k && "bg-brand-50")}
                  >
                    <td>
                      <div className="text-slate-800">{l.description}</div>
                      {l.sku && <div className="num text-[11px] text-slate-400">{l.sku}</div>}
                    </td>
                    <td className="text-right num">{l.qty ?? "—"}</td>
                    <td className="text-right num">{fmtMoney(l.unit_price)}</td>
                    <td className="text-right font-medium num">{fmtMoney(l.amount)}</td>
                  </tr>
                );
              })}
              {!x.line_items.length && (
                <tr>
                  <td colSpan={4} className="text-center text-slate-400">
                    Bundled invoice — no itemised lines
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {x.cues?.length > 0 && (
        <div>
          <div className="label mb-1.5">Language cues spotted</div>
          <ul className="space-y-1">
            {x.cues.map((c, i) => {
              const k = `cue_${i}`;
              return (
                <li
                  key={i}
                  onClick={() => c.box && setActive(active === k ? null : k)}
                  onMouseEnter={() => c.box && setActive(k)}
                  className={clsx("rounded-lg bg-amber-50 px-2.5 py-1.5 text-xs text-amber-900", c.box && "cursor-pointer", active === k && "ring-2 ring-amber-400")}
                >
                  <b className="capitalize">{c.type.replace("_", " ")}</b> — “{c.evidence.replace(/\s+/g, " ")}”
                </li>
              );
            })}
          </ul>
        </div>
      )}
      {run.file_hash && <div className="num text-[10px] text-slate-400">sha256 {run.file_hash}</div>}
    </div>
  );
}
