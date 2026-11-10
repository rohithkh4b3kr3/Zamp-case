import clsx from "clsx";
import { Loader2, Pencil, Plus, Sparkles, X } from "lucide-react";
import { useState, type ReactNode } from "react";

export type DraftRule = {
  name: string;
  enabled?: boolean;
  field: string;
  op: string;
  value: any;
  outcome: "review" | "reject";
  route_to: string | null;
  message: string;
};

type Draft = { rule: DraftRule; describe: string | null; valid: boolean; error: string | null; preview: { matched: number; of: number; examples: { vendor_name: string; invoice_number: string }[] } | null };
type GenResult = { mode: "llm" | "rules"; model: string | null; notes: string; drafts: Draft[] };

const EXAMPLES = [
  "Send invoices over $10,000 to the Finance Manager",
  "Reject anything from Orion Facilities",
  "Invoices with no PO go to Procurement",
  "Hold invoices not in USD for AP",
];

async function generate(text: string): Promise<GenResult> {
  const r = await fetch("/api/rules/generate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      msg = (await r.json()).detail || msg;
    } catch {
      /* not json */
    }
    throw new Error(msg);
  }
  return r.json();
}

/** Describe a policy in plain English; get draft rules with a live "would have caught N invoices" preview. */
export default function RuleGenerator({
  onAdd,
  onEdit,
  summary,
}: {
  onAdd: (r: DraftRule) => Promise<void>;
  onEdit: (r: DraftRule) => void;
  summary: (r: DraftRule) => ReactNode;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<GenResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [adding, setAdding] = useState<number | null>(null);
  const [added, setAdded] = useState<Set<number>>(new Set());

  const run = async (t = text) => {
    if (!t.trim()) return;
    setBusy(true);
    setErr(null);
    setRes(null);
    setAdded(new Set());
    try {
      setRes(await generate(t));
    } catch (e: any) {
      setErr(`Rule not generated: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3 px-4 py-3">
      <form
        className="flex items-start gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          run();
        }}
      >
        <div className="relative flex-1">
          <Sparkles className="pointer-events-none absolute left-2.5 top-2 h-4 w-4 text-brand-600" />
          <input
            className="input pl-8"
            value={text}
            maxLength={1500}
            onChange={(e) => setText(e.target.value)}
            placeholder="Describe a rule, e.g. “Send invoices over $10,000 to the Finance Manager”"
            aria-label="Describe a rule in plain English"
          />
        </div>
        <button className="btn-primary" disabled={busy || text.trim().length < 6}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />} Generate
        </button>
      </form>

      {!res && !busy && !err && (
        <div className="flex flex-wrap gap-1.5">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              className="pill hover:bg-slate-100"
              onClick={() => {
                setText(ex);
                run(ex);
              }}
            >
              {ex}
            </button>
          ))}
        </div>
      )}

      {err && <div className="text-[13px] text-rose-600">{err}</div>}

      {res && (
        <div className="space-y-2 animate-fadein">
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <span className={clsx("chip", res.mode === "llm" ? "bg-brand-50 text-brand-700" : "bg-slate-100 text-slate-600")}>
              {res.mode === "llm" ? `AI · ${res.model}` : "Phrase parser"}
            </span>
            {res.notes && <span className="min-w-0 flex-1">{res.notes}</span>}
            <button className="btn-ghost ml-auto h-7 w-7 px-0" onClick={() => setRes(null)} aria-label="Dismiss drafts">
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          {res.drafts.map((d, i) => (
            <div key={i} className={clsx("flex flex-wrap items-center gap-3 rounded-lg px-3 py-2.5 ring-1", d.valid ? "ring-brand-200 bg-brand-50/30" : "ring-rose-200 bg-rose-50/40")}>
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] font-medium text-slate-900">{d.rule.name || "Untitled rule"}</div>
                {d.valid ? summary(d.rule) : <div className="text-xs text-rose-700">{d.error}</div>}
                {d.preview && (
                  <div className="mt-0.5 text-xs text-slate-500" title={d.preview.examples.map((x) => `${x.vendor_name} ${x.invoice_number}`).join("\n")}>
                    Would have caught <span className="num font-medium text-slate-700">{d.preview.matched}</span> of <span className="num">{d.preview.of}</span> processed invoices
                  </div>
                )}
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <button className="btn-ghost" onClick={() => onEdit(d.rule)}>
                  <Pencil className="h-3.5 w-3.5" /> Edit
                </button>
                {added.has(i) ? (
                  <span className="chip bg-emerald-50 text-emerald-700">Added</span>
                ) : (
                  <button
                    className="btn-primary"
                    disabled={!d.valid || adding === i}
                    onClick={async () => {
                      setAdding(i);
                      setErr(null);
                      try {
                        await onAdd(d.rule);
                        setAdded((s) => new Set(s).add(i));
                      } catch (e: any) {
                        setErr(`Rule not added: ${e.message}`);
                      } finally {
                        setAdding(null);
                      }
                    }}
                  >
                    {adding === i ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-3.5 w-3.5" />} Add rule
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
