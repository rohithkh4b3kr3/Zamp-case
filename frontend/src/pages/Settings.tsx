import clsx from "clsx";
import RuleGenerator from "../components/RuleGenerator";
import { Loader2, Pencil, Plus, RotateCcw, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api";

/* ---------- policy settings ---------- */

type Field = { key: string; label: string; help: string; unit: string; step?: number };

const SECTIONS: { id: string; title: string; fields: Field[] }[] = [
  {
    id: "matching",
    title: "Matching tolerances",
    fields: [
      { key: "price_tolerance_pct", label: "Unit-price tolerance", help: "Allowed drift from the PO unit price", unit: "%", step: 0.5 },
      { key: "amount_tolerance_abs", label: "Amount slack", help: "Rounding allowed on totals and PO balance", unit: "$" },
      { key: "po_overrun_tolerance_pct", label: "PO overrun tolerance", help: "Billing allowed above the PO value", unit: "%", step: 0.5 },
      { key: "duplicate_window_days", label: "Duplicate window", help: "Same vendor and amount within this period", unit: "days" },
    ],
  },
  {
    id: "approval",
    title: "Approval",
    fields: [
      { key: "approval_limit", label: "Auto-approval limit", help: "Clean invoices above this go to a Finance Manager", unit: "$", step: 1000 },
      { key: "max_invoice_age_days", label: "Maximum invoice age", help: "Older invoices need confirmation before payment", unit: "days" },
      { key: "min_field_confidence", label: "Extraction confidence", help: "Below this, a person verifies the fields (0–1)", unit: "", step: 0.05 },
      { key: "po_inference_min_confidence", label: "PO match confidence", help: "Inferred POs below this go to AP (0–1)", unit: "", step: 0.05 },
    ],
  },
  {
    id: "special",
    title: "Deposits & non-PO bills",
    fields: [
      { key: "max_prepayment_pct", label: "Maximum deposit", help: "Share of a PO a deposit invoice may bill (unless the PO sets its own)", unit: "%", step: 5 },
      { key: "non_po_limit", label: "Non-PO limit", help: "Utility / rent / subscription bills above this go to a Finance Manager", unit: "$", step: 500 },
      { key: "non_po_variance_pct", label: "Non-PO variance", help: "Recurring bills outside this band of the usual amount are reviewed", unit: "%", step: 5 },
    ],
  },
  {
    id: "sla",
    title: "Review SLAs",
    fields: [
      { key: "sla_treasury_hours", label: "Treasury & Security", help: "Fraud signals", unit: "hours", step: 0.5 },
      { key: "sla_procurement_hours", label: "Procurement", help: "PO overruns, unplanned charges, unknown vendors", unit: "hours" },
      { key: "sla_default_hours", label: "All other teams", help: "Receiving, AP Specialist, Finance Manager", unit: "hours" },
    ],
  },
];

function Section({ id, title, action, children }: { id: string; title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section id={id} className="card overflow-hidden">
      <header className="flex min-h-[44px] items-center justify-between gap-3 border-b border-slate-100 px-4 py-2">
        <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
        {action}
      </header>
      {children}
    </section>
  );
}

function Row({ label, help, children }: { label: string; help?: string; children: ReactNode }) {
  return (
    <div className="grid items-center gap-2 px-4 py-2.5 sm:grid-cols-[1fr_168px] sm:gap-4">
      <div className="min-w-0">
        <div className="text-[13px] font-medium text-slate-900">{label}</div>
        {help && <div className="mt-0.5 text-xs text-slate-500">{help}</div>}
      </div>
      {children}
    </div>
  );
}

function NumberInput({ value, unit, step, onChange, label }: { value: number; unit: string; step?: number; onChange: (v: number) => void; label: string }) {
  return (
    <div className="relative">
      <input type="number" min={0} step={step ?? 1} aria-label={label} className={clsx("input num text-right", unit && "pr-14")} value={value} onChange={(e) => onChange(Number(e.target.value))} />
      {unit && <span className="pointer-events-none absolute inset-y-0 right-2.5 flex items-center text-xs text-slate-400">{unit}</span>}
    </div>
  );
}

function Toggle({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={() => onChange(!on)}
      className={clsx("relative h-5 w-9 shrink-0 rounded-full transition-colors", on ? "bg-brand-600" : "bg-slate-300")}
    >
      <span className={clsx("absolute top-0.5 h-4 w-4 rounded-full bg-[#fff] shadow-sm transition-all", on ? "left-[18px]" : "left-0.5")} />
    </button>
  );
}

/* ---------- custom rules ---------- */

type Cond = { field: string; op: string; value: string | number | null };
type Rule = {
  id?: number | string;
  name: string;
  enabled: boolean;
  field: string;
  op: string;
  value: string | number | null;
  conditions?: Cond[];
  outcome: "review" | "reject";
  route_to: string | null;
  message: string;
  hits?: number;
};
type Schema = { fields: { id: string; label: string; type: string }[]; ops: { id: string; label: string; types: string[] }[]; teams: string[] };

const rulesApi = {
  async req<T>(url: string, init?: RequestInit): Promise<T> {
    const r = await fetch(url, { headers: { "Content-Type": "application/json" }, ...init });
    if (!r.ok) {
      let msg = `${r.status} ${r.statusText}`;
      try {
        const j = await r.json();
        msg = j.detail ? (typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail)) : msg;
      } catch {}
      throw new Error(msg);
    }
    return r.json();
  },
  list: () => rulesApi.req<Rule[]>("/api/rules"),
  schema: () => rulesApi.req<Schema>("/api/rules/schema"),
  body: (r: Partial<Rule>) => {
    const { name, enabled, field, op, value, conditions, outcome, route_to, message } = r;
    return JSON.stringify(Object.fromEntries(Object.entries({ name, enabled, field, op, value, conditions, outcome, route_to, message }).filter(([, v]) => v !== undefined)));
  },
  save: (r: Rule) =>
    r.id != null
      ? rulesApi.req<Rule>(`/api/rules/${r.id}`, { method: "PUT", body: rulesApi.body(r) })
      : rulesApi.req<Rule>("/api/rules", { method: "POST", body: rulesApi.body(r) }),
  patch: (id: Rule["id"], p: Partial<Rule>) => rulesApi.req<Rule>(`/api/rules/${id}`, { method: "PUT", body: rulesApi.body(p) }),
  remove: (id: Rule["id"]) => rulesApi.req<any>(`/api/rules/${id}`, { method: "DELETE" }),
  test: (r: Partial<Rule>) => rulesApi.req<{ matched: boolean; detail: string }>("/api/rules/test", { method: "POST", body: JSON.stringify({ rule: JSON.parse(rulesApi.body(r)) }) }),
};

const reviewTeams = (schema: Schema) => schema.teams.filter((t) => t !== "Vendor");

const blankRule = (schema: Schema): Rule => ({
  name: "",
  enabled: true,
  field: schema.fields[0]?.id ?? "total",
  op: schema.ops[0]?.id ?? "gt",
  value: "",
  outcome: "review",
  route_to: reviewTeams(schema)[0] ?? null,
  message: "",
});

function CondText({ c, schema }: { c: Cond; schema: Schema }) {
  const f = schema.fields.find((x) => x.id === c.field)?.label ?? c.field;
  const o = schema.ops.find((x) => x.id === c.op)?.label ?? c.op;
  return (
    <>
      <b className="font-medium text-slate-700">{f}</b> {o.toLowerCase()} {c.op !== "missing" && <b className="num font-medium text-slate-700">{String(c.value)}</b>}
    </>
  );
}

function RuleSummary({ r, schema }: { r: Rule; schema: Schema }) {
  return (
    <span className="text-xs text-slate-500">
      If <CondText c={r} schema={schema} />
      {(r.conditions || []).map((c, i) => (
        <span key={i}>
          {" "}
          and <CondText c={c} schema={schema} />
        </span>
      ))}
      {" · "}
      {r.outcome === "reject" ? <span className="text-rose-700">Reject</span> : <span className="text-amber-700">Review by {r.route_to || "AP Specialist"}</span>}
      {r.hits ? <span className="text-slate-400"> · fired {r.hits}×</span> : null}
    </span>
  );
}

function RuleEditor({ rule, schema, onCancel, onSaved }: { rule: Rule; schema: Schema; onCancel: () => void; onSaved: () => void }) {
  const [r, setR] = useState<Rule>(rule);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const field = schema.fields.find((x) => x.id === r.field);
  const ops = schema.ops.filter((o) => !field || o.types.includes(field.type));
  const needsValue = r.op !== "missing";
  const extra = r.conditions || [];
  const extraValid = extra.every((c) => c.op === "missing" || String(c.value ?? "").trim() !== "");
  const valid = r.name.trim() && (!needsValue || String(r.value ?? "").trim() !== "") && extraValid;
  const setCond = (i: number, patch: Partial<Cond>) => setR((x) => ({ ...x, conditions: (x.conditions || []).map((c, j) => (j === i ? { ...c, ...patch } : c)) }));

  const set = (patch: Partial<Rule>) => setR((x) => ({ ...x, ...patch }));
  const [preview, setPreview] = useState<string | null>(null);
  useEffect(() => {
    if (needsValue && String(r.value ?? "").trim() === "") return setPreview(null);
    const t = setTimeout(() => {
      rulesApi
        .test({ ...r, name: r.name || "Preview", value: needsValue ? r.value : null, conditions: extra.map((c) => ({ ...c, value: c.op === "missing" ? null : c.value })) })
        .then((x) => setPreview(x.detail))
        .catch((e) => setPreview(e.message));
    }, 350);
    return () => clearTimeout(t);
  }, [r.field, r.op, r.value, JSON.stringify(extra)]);
  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const value = needsValue ? r.value : null;
      const conditions = extra.map((c) => ({ ...c, value: c.op === "missing" ? null : c.value }));
      await rulesApi.save({ ...r, value, conditions, route_to: r.outcome === "review" ? r.route_to : "Vendor" });
      onSaved();
    } catch (e: any) {
      setErr(`Rule not saved: ${e.message}`);
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4 bg-slate-50/60 px-4 py-4">
      <label className="block">
        <span className="label">Rule name</span>
        <input className="input mt-1" placeholder="e.g. Large invoices from new vendors" value={r.name} onChange={(e) => set({ name: e.target.value })} autoFocus />
      </label>

      <div>
        <span className="label">Condition</span>
        <div className="mt-1 grid gap-2 sm:grid-cols-[1fr_1fr_1fr]">
          <select
            className="input"
            aria-label="Field"
            value={r.field}
            onChange={(e) => {
              const nf = schema.fields.find((x) => x.id === e.target.value);
              const ok = schema.ops.filter((o) => !nf || o.types.includes(nf.type));
              set({ field: e.target.value, op: ok.some((o) => o.id === r.op) ? r.op : ok[0]?.id ?? r.op });
            }}
          >
            {schema.fields.map((f) => (
              <option key={f.id} value={f.id}>
                {f.label}
              </option>
            ))}
          </select>
          <select className="input" aria-label="Operator" value={r.op} onChange={(e) => set({ op: e.target.value })}>
            {ops.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>
          {needsValue ? (
            <input
              className="input num"
              aria-label="Value"
              inputMode={field?.type === "number" ? "decimal" : "text"}
              placeholder="Value"
              value={r.value ?? ""}
              onChange={(e) => set({ value: e.target.value })}
            />
          ) : (
            <div />
          )}
        </div>
        {extra.map((c, i) => {
          const cf = schema.fields.find((x) => x.id === c.field);
          const cops = schema.ops.filter((o) => !cf || o.types.includes(cf.type));
          return (
            <div key={i} className="mt-2 grid items-center gap-2 sm:grid-cols-[2.5rem_1fr_1fr_1fr_2rem]">
              <span className="text-xs font-medium text-slate-500">and</span>
              <select
                className="input"
                aria-label={`Field ${i + 2}`}
                value={c.field}
                onChange={(e) => {
                  const nf = schema.fields.find((x) => x.id === e.target.value);
                  const ok = schema.ops.filter((o) => !nf || o.types.includes(nf.type));
                  setCond(i, { field: e.target.value, op: ok.some((o) => o.id === c.op) ? c.op : ok[0]?.id ?? c.op });
                }}
              >
                {schema.fields.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.label}
                  </option>
                ))}
              </select>
              <select className="input" aria-label={`Operator ${i + 2}`} value={c.op} onChange={(e) => setCond(i, { op: e.target.value })}>
                {cops.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.label}
                  </option>
                ))}
              </select>
              {c.op !== "missing" ? (
                <input className="input num" aria-label={`Value ${i + 2}`} placeholder="Value" value={c.value ?? ""} onChange={(e) => setCond(i, { value: e.target.value })} />
              ) : (
                <div />
              )}
              <button className="btn-ghost w-8 px-0" aria-label="Remove condition" onClick={() => set({ conditions: extra.filter((_, j) => j !== i) })}>
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          );
        })}
        {extra.length < 4 && (
          <button
            className="btn-ghost mt-1"
            onClick={() => set({ conditions: [...extra, { field: "vendor_name", op: "contains", value: "" }] })}
          >
            <Plus className="h-3.5 w-3.5" /> And condition
          </button>
        )}
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <span className="label">Then</span>
          <div className="mt-1 grid grid-cols-2 gap-1 rounded-lg bg-slate-100 p-1">
            {(["review", "reject"] as const).map((o) => (
              <button
                key={o}
                onClick={() => set({ outcome: o })}
                className={clsx("h-6 rounded-md text-xs font-medium capitalize transition-colors", r.outcome === o ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-800")}
              >
                {o === "review" ? "Send to review" : "Reject"}
              </button>
            ))}
          </div>
        </div>
        {r.outcome === "review" && (
          <label className="block">
            <span className="label">Route to</span>
            <select className="input mt-1" value={r.route_to ?? ""} onChange={(e) => set({ route_to: e.target.value })}>
              {reviewTeams(schema).map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      <label className="block">
        <span className="label">Reason shown to the reviewer</span>
        <input className="input mt-1" placeholder="e.g. Invoices over $50,000 need CFO sign-off" value={r.message} onChange={(e) => set({ message: e.target.value })} />
      </label>

      {err && <div className="text-[13px] text-rose-600">{err}</div>}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-slate-500">{preview}</span>
        <div className="flex gap-2">
        <button className="btn-ghost" onClick={onCancel}>
          Cancel
        </button>
        <button className="btn-primary" disabled={!valid || busy} onClick={submit}>
          {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />} {rule.id != null ? "Save rule" : "Add rule"}
        </button>
        </div>
      </div>
    </div>
  );
}

function CustomRules() {
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [schema, setSchema] = useState<Schema | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [editing, setEditing] = useState<Rule | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = () =>
    Promise.all([rulesApi.list(), rulesApi.schema()])
      .then(([l, s]) => {
        setRules(l);
        setSchema(s);
        setUnavailable(false);
      })
      .catch(() => setUnavailable(true));
  useEffect(() => {
    load();
  }, []);

  const patch = async (r: Rule, p: Partial<Rule>) => {
    setErr(null);
    try {
      await rulesApi.patch(r.id, p);
      load();
    } catch (e: any) {
      setErr(`Rule not updated: ${e.message}`);
    }
  };

  return (
    <Section
      id="rules"
      title="Custom rules"
      action={
        schema && !editing && (
          <button className="btn-outline" onClick={() => setEditing(blankRule(schema))}>
            <Plus className="h-3.5 w-3.5" /> Add rule
          </button>
        )
      }
    >
      {unavailable ? (
        <div className="flex items-center justify-between gap-3 px-4 py-6 text-[13px] text-slate-500">
          Custom rules can't be loaded. The rules service is not running on the server.
          <button className="btn-outline" onClick={load}>
            Retry
          </button>
        </div>
      ) : !rules || !schema ? (
        <div className="space-y-2 p-5">{[0, 1].map((i) => <div key={i} className="skeleton h-10" />)}</div>
      ) : (
        <div className="divide-y divide-slate-100">
          <RuleGenerator
            summary={(d) => <RuleSummary r={{ ...d, enabled: true } as Rule} schema={schema} />}
            onEdit={(d) => setEditing({ ...d, enabled: true } as Rule)}
            onAdd={async (d) => {
              await rulesApi.save({ ...d, enabled: true } as Rule);
              load();
            }}
          />
          {editing && editing.id == null && <RuleEditor rule={editing} schema={schema} onCancel={() => setEditing(null)} onSaved={() => { setEditing(null); load(); }} />}
          {rules.length === 0 && !editing && (
            <div className="px-4 py-8 text-center">
              <div className="text-[13px] font-medium text-slate-800">No custom rules yet</div>
              <div className="mt-1 text-xs text-slate-500">Add a rule to send invoices to review or reject them on your own conditions.</div>
              <button className="btn-primary mt-3" onClick={() => setEditing(blankRule(schema))}>
                <Plus className="h-3.5 w-3.5" /> Add rule
              </button>
            </div>
          )}
          {rules.map((r) =>
            editing?.id === r.id ? (
              <RuleEditor key={String(r.id)} rule={r} schema={schema} onCancel={() => setEditing(null)} onSaved={() => { setEditing(null); load(); }} />
            ) : (
              <div key={String(r.id)} className="group flex items-center gap-4 px-4 py-3">
                <Toggle on={r.enabled} onChange={(v) => patch(r, { enabled: v })} label={`${r.enabled ? "Disable" : "Enable"} ${r.name}`} />
                <div className={clsx("min-w-0 flex-1", !r.enabled && "opacity-50")}>
                  <div className="truncate text-[13px] font-medium text-slate-900">{r.name}</div>
                  <RuleSummary r={r} schema={schema} />
                </div>
                <div className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
                  <button className="btn-ghost w-8 px-0" aria-label={`Edit ${r.name}`} onClick={() => setEditing(r)}>
                    <Pencil className="h-3.5 w-3.5" />
                  </button>
                  <button
                    className="btn-ghost w-8 px-0 hover:text-rose-600"
                    aria-label={`Delete ${r.name}`}
                    onClick={async () => {
                      if (!confirm(`Delete the rule "${r.name}"?`)) return;
                      try {
                        await rulesApi.remove(r.id);
                        load();
                      } catch (e: any) {
                        setErr(`Rule not deleted: ${e.message}`);
                      }
                    }}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            ),
          )}
          {err && <div className="px-4 py-2.5 text-[13px] text-rose-600">{err}</div>}
        </div>
      )}
    </Section>
  );
}

/* ---------- page ---------- */

export default function Settings() {
  const [vals, setVals] = useState<Record<string, number> | null>(null);
  const [orig, setOrig] = useState<Record<string, number> | null>(null);
  const [meta, setMeta] = useState<any>(null);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [resetDone, setResetDone] = useState(false);

  useEffect(() => {
    api.settings().then((v) => {
      setVals(v);
      setOrig(v);
    });
    api.meta().then(setMeta).catch(() => {});
  }, []);

  const dirty = useMemo(() => !!vals && !!orig && Object.keys(vals).some((k) => vals[k] !== orig[k]), [vals, orig]);

  const save = async () => {
    setErr(null);
    setSaving(true);
    try {
      const v = await api.saveSettings(vals!);
      setVals(v);
      setOrig(v);
    } catch (e: any) {
      setErr(`Settings not saved: ${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  const engine = meta
    ? meta.llm_provider === "openai"
      ? `OpenAI · ${meta.llm_model}`
      : meta.llm_provider === "anthropic" || meta.llm_available
        ? `Claude · ${meta.llm_model}`
        : `Text layer + ${meta.ocr_engine === "google-vision" ? "Google Vision OCR" : "local OCR"}`
    : null;

  return (
    <div className="grid items-start gap-4 pb-20 animate-fadein lg:grid-cols-2">
        {SECTIONS.map((sec) => (
          <Section key={sec.id} id={sec.id} title={sec.title}>
            <div className="divide-y divide-slate-100">
              {!vals
                ? sec.fields.map((f) => (
                    <Row key={f.key} label={f.label} help={f.help}>
                      <div className="skeleton h-8" />
                    </Row>
                  ))
                : sec.fields.map((f) => (
                    <Row key={f.key} label={f.label} help={f.help}>
                      <NumberInput label={f.label} value={vals[f.key]} unit={f.unit} step={f.step} onChange={(v) => setVals({ ...vals, [f.key]: v })} />
                    </Row>
                  ))}
            </div>
          </Section>
        ))}
        <Section id="system" title="System">
          <div className="divide-y divide-slate-100">
            <Row label="Extraction engine" help={meta ? `Mode: ${meta.extraction_mode}` : undefined}>
              {engine ? <span className="pill justify-self-start sm:justify-self-end">{engine}</span> : <div className="skeleton h-6 w-32 sm:justify-self-end" />}
            </Row>
            <Row label="Demo pacing" help="Minimum time each stage stays visible in the live view">
              {vals ? <NumberInput label="Demo pacing" value={vals.stage_min_ms} unit="ms" step={50} onChange={(v) => setVals({ ...vals, stage_min_ms: v })} /> : <div className="skeleton h-8" />}
            </Row>
            <Row label="Reset demo data" help="Deletes all runs and restores the seeded invoice ledger">
              <button
                className="btn-outline justify-self-start text-rose-600 hover:text-rose-700 sm:justify-self-end"
                onClick={async () => {
                  if (!confirm("Delete all runs and reset the ledger? This cannot be undone.")) return;
                  await api.reset();
                  setResetDone(true);
                  setTimeout(() => setResetDone(false), 2000);
                }}
              >
                <RotateCcw className="h-3.5 w-3.5" /> {resetDone ? "Reset complete" : "Reset demo"}
              </button>
            </Row>
          </div>
        </Section>
      <div className="lg:col-span-2">
        <CustomRules />
      </div>

      {/* unsaved-changes bar */}
      {(dirty || err) && (
        <div className="fixed inset-x-0 bottom-4 z-30 flex justify-center px-4 animate-fadein">
          <div className="glass flex items-center gap-3 rounded-xl py-2 pl-4 pr-2">
            <span className={clsx("text-[13px]", err ? "text-rose-600" : "text-slate-700")}>{err || "You have unsaved changes"}</span>
            <button
              className="btn-ghost"
              onClick={() => {
                setVals(orig);
                setErr(null);
              }}
            >
              Discard
            </button>
            <button className="btn-primary" onClick={save} disabled={saving || !dirty}>
              {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />} Save changes
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
