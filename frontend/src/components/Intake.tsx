import clsx from "clsx";
import { Check, Copy, Loader2, Pencil, Plus, RefreshCw, RotateCw, Trash2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { ago } from "../api";

/* ---------- api ---------- */

export type Connector = {
  id: string;
  kind: Kind;
  kind_label: string;
  name: string;
  enabled: boolean;
  config: Record<string, any>;
  interval_minutes: number;
  last_run: string | null;
  last_status: "ok" | "error" | null;
  last_error: string | null;
  files_ingested: number;
  next_run: string | null;
};
export type Kind = "imap" | "gdrive" | "s3" | "sftp";
type Action = { id: string; name: string; enabled: boolean; event: string; threshold: number | null; channel: string; target: string; hits: number; last_fired: string | null };
type Report = { enabled: boolean; to: string; only_exceptions: boolean; attach_pdf: boolean; attach_json: boolean };

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, { headers: { "Content-Type": "application/json" }, ...init });
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const j = await r.json();
      if (j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {}
    throw new Error(msg);
  }
  return r.json();
}
export const intakeApi = {
  connectors: (kind?: string) => req<Connector[]>(`/api/intake/connectors${kind ? `?kind=${kind}` : ""}`),
  save: (c: Partial<Connector>) =>
    c.id ? req<Connector>(`/api/intake/connectors/${c.id}`, { method: "PUT", body: JSON.stringify(c) }) : req<Connector>("/api/intake/connectors", { method: "POST", body: JSON.stringify(c) }),
  remove: (id: string) => req(`/api/intake/connectors/${id}`, { method: "DELETE" }),
  test: (c: Partial<Connector>) => req<{ ok: boolean; detail: string; found?: number }>("/api/intake/connectors/test", { method: "POST", body: JSON.stringify(c) }),
  sync: (id: string) => req<{ ok: boolean; detail: string }>(`/api/intake/connectors/${id}/sync`, { method: "POST" }),
  webhook: () => req<{ url: string }>("/api/intake/webhook"),
  rotate: () => req<{ url: string }>("/api/intake/webhook/rotate", { method: "POST" }),
  actions: () => req<Action[]>("/api/intake/actions"),
  saveAction: (a: Partial<Action>) =>
    a.id ? req<Action>(`/api/intake/actions/${a.id}`, { method: "PUT", body: JSON.stringify(a) }) : req<Action>("/api/intake/actions", { method: "POST", body: JSON.stringify(a) }),
  removeAction: (id: string) => req(`/api/intake/actions/${id}`, { method: "DELETE" }),
  report: () => req<Report>("/api/intake/report"),
  saveReport: (r: Report) => req<Report>("/api/intake/report", { method: "PUT", body: JSON.stringify(r) }),
};

/* ---------- small pieces ---------- */

export function Toggle({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button role="switch" aria-checked={on} aria-label={label} onClick={() => onChange(!on)} className={clsx("relative h-5 w-9 shrink-0 rounded-full transition-colors", on ? "bg-brand-600" : "bg-slate-300")}>
      <span className={clsx("absolute top-0.5 h-4 w-4 rounded-full bg-[#fff] shadow-sm transition-all", on ? "left-[18px]" : "left-0.5")} />
    </button>
  );
}

function Panel({ title, action, children, className }: { title: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={clsx("card overflow-hidden", className)}>
      <header className="flex min-h-[44px] items-center justify-between gap-3 border-b border-slate-100 px-4 py-2">
        <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
        {action}
      </header>
      {children}
    </section>
  );
}

function Field({ label, hint, children, wide }: { label: string; hint?: string; children: ReactNode; wide?: boolean }) {
  return (
    <label className={clsx("block", wide && "sm:col-span-2")}>
      <span className="label">{label}</span>
      <div className="mt-1">{children}</div>
      {hint && <span className="mt-1 block text-xs text-slate-400">{hint}</span>}
    </label>
  );
}

function StatusDot({ c }: { c: Connector }) {
  const cls = !c.enabled ? "bg-slate-300" : c.last_status === "error" ? "bg-rose-500" : c.last_status === "ok" ? "bg-emerald-500" : "bg-slate-400";
  return <span className={clsx("h-2 w-2 shrink-0 rounded-full", cls)} />;
}

const every = (m: number) => (m < 1 ? `${Math.round(m * 60)} s` : m < 60 ? `${m} min` : `${(m / 60).toFixed(m % 60 ? 1 : 0)} h`);

/* ---------- connector form ---------- */

type FieldSpec = { key: string; label: string; placeholder?: string; type?: "text" | "password" | "textarea" | "number" | "check"; hint?: string; wide?: boolean };

const FIELDS: Record<Kind, FieldSpec[]> = {
  imap: [
    { key: "host", label: "IMAP server", placeholder: "imap.gmail.com" },
    { key: "port", label: "Port", placeholder: "993", type: "number" },
    { key: "username", label: "Email address", placeholder: "ap@company.com" },
    { key: "password", label: "App password", type: "password", hint: "Use an app password, not your normal sign-in password" },
    { key: "folder", label: "Folder", placeholder: "INBOX" },
    { key: "use_ssl", label: "Use SSL", type: "check" },
  ],
  gdrive: [
    { key: "folder_id", label: "Folder ID", placeholder: "1AbC…", hint: "The last part of the folder URL in Google Drive", wide: true },
    { key: "service_account_json", label: "Service account key (JSON)", type: "textarea", hint: "Share the folder with the service account's email address", wide: true },
    { key: "pattern", label: "File filter", placeholder: "*.pdf" },
  ],
  s3: [
    { key: "bucket", label: "Bucket", placeholder: "acme-ap-invoices" },
    { key: "prefix", label: "Prefix", placeholder: "incoming/" },
    { key: "access_key_id", label: "Access key ID" },
    { key: "secret_access_key", label: "Secret access key", type: "password" },
    { key: "region", label: "Region", placeholder: "us-east-1" },
    { key: "endpoint_url", label: "Endpoint URL", placeholder: "Optional, for S3-compatible storage" },
    { key: "pattern", label: "File filter", placeholder: "*.pdf" },
  ],
  sftp: [
    { key: "host", label: "Host", placeholder: "sftp.vendor-portal.com" },
    { key: "port", label: "Port", placeholder: "22", type: "number" },
    { key: "username", label: "Username" },
    { key: "password", label: "Password", type: "password", hint: "Or use a private key below" },
    { key: "remote_path", label: "Remote folder", placeholder: "/outbound/invoices" },
    { key: "pattern", label: "File filter", placeholder: "*.pdf" },
    { key: "private_key", label: "Private key", type: "textarea", hint: "Optional. Unencrypted OpenSSH or PEM key", wide: true },
  ],
};

const summary = (c: Connector) =>
  c.kind === "imap" ? `${c.config.username} · ${c.config.folder || "INBOX"}` : c.kind === "gdrive" ? `Folder ${c.config.folder_id}` : c.kind === "s3" ? `s3://${c.config.bucket}/${c.config.prefix || ""}` : `${c.config.username}@${c.config.host}:${c.config.remote_path || "."}`;

function ConnectorForm({ kind, initial, onDone, onCancel }: { kind: Kind; initial?: Connector; onDone: () => void; onCancel: () => void }) {
  const [name, setName] = useState(initial?.name ?? "");
  const [cfg, setCfg] = useState<Record<string, any>>(initial?.config ?? (kind === "imap" ? { use_ssl: true, folder: "INBOX" } : { pattern: "*.pdf" }));
  const [interval, setInterval_] = useState(initial?.interval_minutes ?? 5);
  const [busy, setBusy] = useState<"test" | "save" | null>(null);
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);

  const body = { id: initial?.id, kind, name, config: cfg, interval_minutes: interval };
  const run = async (what: "test" | "save") => {
    setBusy(what);
    setResult(null);
    try {
      if (what === "test") setResult(await intakeApi.test(body));
      else {
        await intakeApi.save(body);
        onDone();
      }
    } catch (e: any) {
      setResult({ ok: false, detail: e.message });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-3 border-b border-slate-100 bg-slate-50/60 px-4 py-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name">
          <input className="input" value={name} placeholder={kind === "imap" ? "AP mailbox" : kind === "sftp" ? "Vendor portal" : "Invoices folder"} onChange={(e) => setName(e.target.value)} autoFocus />
        </Field>
        <Field label="Check every">
          <div className="relative">
            <input className="input num pr-12" type="number" min={0.5} step={0.5} value={interval} onChange={(e) => setInterval_(Number(e.target.value))} />
            <span className="pointer-events-none absolute inset-y-0 right-2.5 flex items-center text-xs text-slate-400">min</span>
          </div>
        </Field>
        {FIELDS[kind].map((f) => (
          <Field key={f.key} label={f.label} hint={f.hint} wide={f.wide}>
            {f.type === "textarea" ? (
              <textarea className="input num h-24 resize-y text-xs" value={cfg[f.key] ?? ""} onChange={(e) => setCfg({ ...cfg, [f.key]: e.target.value })} spellCheck={false} />
            ) : f.type === "check" ? (
              <span className="flex h-8 items-center">
                <Toggle on={cfg[f.key] !== false} onChange={(v) => setCfg({ ...cfg, [f.key]: v })} label={f.label} />
              </span>
            ) : (
              <input
                className="input"
                type={f.type === "password" ? "password" : f.type === "number" ? "number" : "text"}
                placeholder={f.placeholder}
                value={cfg[f.key] ?? ""}
                autoComplete="off"
                onChange={(e) => setCfg({ ...cfg, [f.key]: f.type === "number" ? Number(e.target.value) || "" : e.target.value })}
              />
            )}
          </Field>
        ))}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className={clsx("text-xs", !result ? "text-slate-400" : result.ok ? "text-emerald-700" : "text-rose-600")}>
          {result ? result.detail : "Test the connection before saving. Testing imports nothing."}
        </span>
        <div className="flex gap-2">
          <button className="btn-ghost" onClick={onCancel}>Cancel</button>
          <button className="btn-outline" disabled={!!busy} onClick={() => run("test")}>
            {busy === "test" && <Loader2 className="h-3.5 w-3.5 animate-spin" />} Test connection
          </button>
          <button className="btn-primary" disabled={!!busy} onClick={() => run("save")}>
            {busy === "save" && <Loader2 className="h-3.5 w-3.5 animate-spin" />} {initial ? "Save" : "Connect"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ---------- connector list ---------- */

export function ConnectorPanel({ kinds, title, empty }: { kinds: Kind[]; title: string; empty: string }) {
  const [items, setItems] = useState<Connector[] | null>(null);
  const [adding, setAdding] = useState<Kind | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);

  const load = () =>
    Promise.all(kinds.map((k) => intakeApi.connectors(k)))
      .then((r) => {
        setItems(r.flat());
        setErr(null);
      })
      .catch((e) => setErr(`Connections can't be loaded: ${e.message}`));
  useEffect(() => {
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [kinds.join()]);

  const label = (k: Kind) => ({ imap: "Email inbox", gdrive: "Google Drive", s3: "Amazon S3", sftp: "SFTP server" })[k];

  return (
    <Panel
      title={title}
      action={
        !adding && (
          <div className="flex gap-1.5">
            {kinds.map((k) => (
              <button key={k} className="btn-outline" onClick={() => setAdding(k)}>
                <Plus className="h-3.5 w-3.5" /> {kinds.length > 1 ? label(k) : "Connect"}
              </button>
            ))}
          </div>
        )
      }
    >
      {adding && <ConnectorForm kind={adding} onCancel={() => setAdding(null)} onDone={() => { setAdding(null); load(); }} />}
      {err ? (
        <p className="px-4 py-6 text-[13px] text-rose-600">{err}</p>
      ) : !items ? (
        <div className="space-y-2 p-4">{[0, 1].map((i) => <div key={i} className="skeleton h-10" />)}</div>
      ) : items.length === 0 && !adding ? (
        <p className="px-4 py-8 text-center text-[13px] text-slate-500">{empty}</p>
      ) : (
        <ul className="divide-y divide-slate-100">
          {items.map((c) =>
            editing === c.id ? (
              <li key={c.id}>
                <ConnectorForm kind={c.kind} initial={c} onCancel={() => setEditing(null)} onDone={() => { setEditing(null); load(); }} />
              </li>
            ) : (
              <li key={c.id} className="group flex items-center gap-3 px-4 py-3">
                <StatusDot c={c} />
                <div className={clsx("min-w-0 flex-1", !c.enabled && "opacity-50")}>
                  <div className="flex items-center gap-2">
                    <span className="truncate text-[13px] font-medium text-slate-900">{c.name}</span>
                    {kinds.length > 1 && <span className="chip bg-slate-100 text-slate-600">{c.kind_label}</span>}
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-slate-500">
                    <span className="pill">{summary(c)}</span>
                    <span>Every {every(c.interval_minutes)}</span>
                    <span>· {c.last_run ? `checked ${ago(c.last_run)}` : "not checked yet"}</span>
                    <span>· <span className="num">{c.files_ingested}</span> imported</span>
                  </div>
                  {(note[c.id] || (c.last_status === "error" && c.last_error)) && (
                    <div className={clsx("mt-1 truncate text-xs", c.last_status === "error" && !note[c.id] ? "text-rose-600" : "text-slate-600")} title={note[c.id] || c.last_error || ""}>
                      {note[c.id] || c.last_error}
                    </div>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    className="btn-ghost h-7 px-2"
                    disabled={busy === c.id}
                    onClick={async () => {
                      setBusy(c.id);
                      try {
                        const r = await intakeApi.sync(c.id);
                        setNote({ ...note, [c.id]: r.detail });
                      } catch (e: any) {
                        setNote({ ...note, [c.id]: `Sync failed: ${e.message}` });
                      }
                      setBusy(null);
                      load();
                    }}
                  >
                    {busy === c.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />} Sync now
                  </button>
                  <Toggle on={c.enabled} label={`${c.enabled ? "Pause" : "Resume"} ${c.name}`} onChange={(v) => intakeApi.save({ id: c.id, enabled: v, config: c.config }).then(load)} />
                  <button className="btn-ghost w-8 px-0 opacity-0 transition-opacity focus:opacity-100 group-hover:opacity-100" aria-label={`Edit ${c.name}`} onClick={() => setEditing(c.id)}>
                    <Pencil className="h-3.5 w-3.5" />
                  </button>
                  <button
                    className="btn-ghost w-8 px-0 opacity-0 transition-opacity hover:text-rose-600 focus:opacity-100 group-hover:opacity-100"
                    aria-label={`Remove ${c.name}`}
                    onClick={() => confirm(`Remove "${c.name}"? Files already imported stay in AP Autopilot.`) && intakeApi.remove(c.id).then(load)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </li>
            ),
          )}
        </ul>
      )}
    </Panel>
  );
}

/* ---------- triggers ---------- */

function Schedules() {
  const [items, setItems] = useState<Connector[] | null>(null);
  const load = () => intakeApi.connectors().then(setItems).catch(() => setItems([]));
  useEffect(() => {
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, []);
  return (
    <Panel title="Scheduled pulls">
      {!items ? (
        <div className="p-4"><div className="skeleton h-10" /></div>
      ) : items.length === 0 ? (
        <p className="px-4 py-8 text-center text-[13px] text-slate-500">Connect a mailbox, cloud folder or SFTP server to schedule pulls.</p>
      ) : (
        <table className="tbl w-full">
          <thead>
            <tr>
              <th>Source</th>
              <th>Every</th>
              <th>Last checked</th>
              <th>Next check</th>
              <th className="text-right">On</th>
            </tr>
          </thead>
          <tbody>
            {items.map((c) => (
              <tr key={c.id}>
                <td>
                  <div className="flex items-center gap-2">
                    <StatusDot c={c} />
                    <span className="font-medium text-slate-900">{c.name}</span>
                    <span className="chip bg-slate-100 text-slate-600">{c.kind_label}</span>
                  </div>
                </td>
                <td className="num">{every(c.interval_minutes)}</td>
                <td className="text-slate-500">{c.last_run ? ago(c.last_run) : "—"}</td>
                <td className="text-slate-500">{c.next_run ? (new Date(c.next_run).getTime() <= Date.now() ? "Now" : new Date(c.next_run).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })) : "Paused"}</td>
                <td className="text-right">
                  <span className="inline-flex"><Toggle on={c.enabled} label={`${c.enabled ? "Pause" : "Resume"} ${c.name}`} onChange={(v) => intakeApi.save({ id: c.id, enabled: v, config: c.config }).then(load)} /></span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

function Webhook() {
  const [url, setUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  useEffect(() => {
    intakeApi.webhook().then((w) => setUrl(w.url)).catch(() => setUrl(""));
  }, []);
  const copy = (k: string, v: string) => {
    navigator.clipboard.writeText(v);
    setCopied(k);
    setTimeout(() => setCopied(null), 1200);
  };
  const curl = url ? `curl -F "file=@invoice.pdf" ${url}` : "";
  return (
    <Panel
      title="Inbound webhook"
      action={
        <button
          className="btn-ghost"
          onClick={() => confirm("Generate a new URL? Systems using the current URL will stop working.") && intakeApi.rotate().then((w) => setUrl(w.url))}
        >
          <RotateCw className="h-3.5 w-3.5" /> New URL
        </button>
      }
    >
      <div className="space-y-3 p-4">
        <p className="text-[13px] text-slate-600">POST a PDF to this URL from any system to start a run.</p>
        {url === null ? (
          <div className="skeleton h-8" />
        ) : (
          <>
            <div className="flex gap-2">
              <input className="input num text-xs" readOnly value={url} aria-label="Webhook URL" onFocus={(e) => e.target.select()} />
              <button className="btn-outline shrink-0" onClick={() => copy("url", url)}>
                {copied === "url" ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />} {copied === "url" ? "Copied" : "Copy"}
              </button>
            </div>
            <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
              <code className="num min-w-0 flex-1 truncate text-xs text-slate-600">{curl}</code>
              <button className="btn-ghost h-6 shrink-0 px-1.5" aria-label="Copy example" onClick={() => copy("curl", curl)}>
                {copied === "curl" ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
              </button>
            </div>
            <p className="text-xs text-slate-400">Also accepts JSON with {"{filename, content_base64}"} or {"{url}"}. Keep the URL secret: anyone with it can submit invoices.</p>
          </>
        )}
      </div>
    </Panel>
  );
}

const EVENTS: Record<string, string> = {
  any: "Any decision",
  approved: "Invoice approved",
  needs_review: "Invoice sent to review",
  rejected: "Invoice rejected",
  amount_over: "Total over amount",
  fraud: "Fraud signal detected",
};
const CHANNELS: Record<string, { label: string; placeholder: string }> = {
  email: { label: "Email", placeholder: "controller@company.com" },
  slack: { label: "Slack", placeholder: "https://hooks.slack.com/services/…" },
  webhook: { label: "Webhook", placeholder: "https://erp.company.com/hooks/ap" },
};

function ActionForm({ initial, onDone, onCancel }: { initial?: Action; onDone: () => void; onCancel: () => void }) {
  const [a, setA] = useState<Partial<Action>>(initial ?? { name: "", event: "rejected", channel: "email", target: "", threshold: null, enabled: true });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (p: Partial<Action>) => setA((x) => ({ ...x, ...p }));
  return (
    <div className="space-y-3 border-b border-slate-100 bg-slate-50/60 px-4 py-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="When">
          <select className="input" value={a.event} onChange={(e) => set({ event: e.target.value })}>
            {Object.entries(EVENTS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </Field>
        {a.event === "amount_over" ? (
          <Field label="Amount (USD)">
            <input className="input num" inputMode="decimal" placeholder="10000" value={a.threshold ?? ""} onChange={(e) => set({ threshold: e.target.value as any })} />
          </Field>
        ) : (
          <Field label="Name">
            <input className="input" placeholder={EVENTS[a.event!]} value={a.name ?? ""} onChange={(e) => set({ name: e.target.value })} />
          </Field>
        )}
        <Field label="Notify via">
          <div className="grid grid-cols-3 gap-1 rounded-lg bg-slate-100 p-1">
            {Object.entries(CHANNELS).map(([k, v]) => (
              <button key={k} onClick={() => set({ channel: k })} className={clsx("h-6 rounded-md text-xs font-medium transition-colors", a.channel === k ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-800")}>
                {v.label}
              </button>
            ))}
          </div>
        </Field>
        <Field label={a.channel === "email" ? "Send to" : "Webhook URL"}>
          <input className="input" placeholder={CHANNELS[a.channel!].placeholder} value={a.target ?? ""} onChange={(e) => set({ target: e.target.value })} />
        </Field>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-rose-600">{err}</span>
        <div className="flex gap-2">
          <button className="btn-ghost" onClick={onCancel}>Cancel</button>
          <button
            className="btn-primary"
            disabled={busy || !a.target}
            onClick={async () => {
              setBusy(true);
              setErr(null);
              try {
                await intakeApi.saveAction(a);
                onDone();
              } catch (e: any) {
                setErr(`Action not saved: ${e.message}`);
                setBusy(false);
              }
            }}
          >
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />} {initial ? "Save" : "Add action"}
          </button>
        </div>
      </div>
    </div>
  );
}

function EventActions() {
  const [items, setItems] = useState<Action[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const load = () => intakeApi.actions().then(setItems).catch(() => setItems([]));
  useEffect(() => {
    load();
  }, []);
  return (
    <Panel title="Event actions" action={!adding && <button className="btn-outline" onClick={() => setAdding(true)}><Plus className="h-3.5 w-3.5" /> Add action</button>}>
      {adding && <ActionForm onCancel={() => setAdding(false)} onDone={() => { setAdding(false); load(); }} />}
      {!items ? (
        <div className="p-4"><div className="skeleton h-10" /></div>
      ) : items.length === 0 && !adding ? (
        <p className="px-4 py-8 text-center text-[13px] text-slate-500">Notify people or systems when an invoice is approved, rejected or flagged.</p>
      ) : (
        <ul className="divide-y divide-slate-100">
          {items.map((a) =>
            editing === a.id ? (
              <li key={a.id}><ActionForm initial={a} onCancel={() => setEditing(null)} onDone={() => { setEditing(null); load(); }} /></li>
            ) : (
              <li key={a.id} className="group flex items-center gap-3 px-4 py-3">
                <Toggle on={a.enabled} label={`${a.enabled ? "Disable" : "Enable"} ${a.name}`} onChange={(v) => intakeApi.saveAction({ id: a.id, enabled: v }).then(load)} />
                <div className={clsx("min-w-0 flex-1", !a.enabled && "opacity-50")}>
                  <div className="truncate text-[13px] font-medium text-slate-900">{a.name}</div>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-slate-500">
                    <span>{EVENTS[a.event]}{a.event === "amount_over" && a.threshold != null ? ` $${a.threshold.toLocaleString()}` : ""}</span>
                    <span>→</span>
                    <span className="chip bg-slate-100 text-slate-600">{CHANNELS[a.channel]?.label}</span>
                    <span className="pill">{a.target}</span>
                    {a.hits > 0 && <span>· fired <span className="num">{a.hits}</span>×</span>}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
                  <button className="btn-ghost w-8 px-0" aria-label={`Edit ${a.name}`} onClick={() => setEditing(a.id)}><Pencil className="h-3.5 w-3.5" /></button>
                  <button className="btn-ghost w-8 px-0 hover:text-rose-600" aria-label={`Delete ${a.name}`} onClick={() => confirm(`Delete "${a.name}"?`) && intakeApi.removeAction(a.id).then(load)}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </li>
            ),
          )}
        </ul>
      )}
    </Panel>
  );
}

export function Triggers() {
  return (
    <div className="grid items-start gap-4 lg:grid-cols-2">
      <div className="space-y-4">
        <Schedules />
        <Webhook />
      </div>
      <EventActions />
    </div>
  );
}

/* ---------- reports ---------- */

function Row({ label, help, children }: { label: string; help: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 px-4 py-3">
      <div className="min-w-0">
        <div className="text-[13px] font-medium text-slate-900">{label}</div>
        <div className="mt-0.5 text-xs text-slate-500">{help}</div>
      </div>
      {children}
    </div>
  );
}

export function ReportSettings() {
  const [r, setR] = useState<Report | null>(null);
  const [orig, setOrig] = useState<Report | null>(null);
  const [state, setState] = useState<"idle" | "saving" | "saved">("idle");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    intakeApi.report().then((x) => { setR(x); setOrig(x); }).catch((e) => setErr(`Report settings can't be loaded: ${e.message}`));
  }, []);
  const dirty = r && orig && JSON.stringify(r) !== JSON.stringify(orig);
  const set = (p: Partial<Report>) => r && setR({ ...r, ...p });

  return (
    <Panel
      className="max-w-3xl"
      title="Run report email"
      action={
        r && (
          <button
            className="btn-primary"
            disabled={!dirty || state === "saving"}
            onClick={async () => {
              setState("saving");
              setErr(null);
              try {
                const x = await intakeApi.saveReport(r);
                setR(x);
                setOrig(x);
                setState("saved");
                setTimeout(() => setState("idle"), 1500);
              } catch (e: any) {
                setErr(`Not saved: ${e.message}`);
                setState("idle");
              }
            }}
          >
            {state === "saving" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : state === "saved" ? <Check className="h-3.5 w-3.5" /> : null}
            {state === "saved" ? "Saved" : "Save changes"}
          </button>
        )
      }
    >
      {!r ? (
        err ? <p className="px-4 py-6 text-[13px] text-rose-600">{err}</p> : <div className="space-y-2 p-4">{[0, 1, 2].map((i) => <div key={i} className="skeleton h-10" />)}</div>
      ) : (
        <div className="divide-y divide-slate-100">
          <Row label="Email a report after each run" help="Decision, reasons and a link to the invoice">
            <Toggle on={r.enabled} onChange={(v) => set({ enabled: v })} label="Email a report after each run" />
          </Row>
          <Row label="Send to" help="The AP team mailbox or a distribution list">
            <input className="input w-64" type="email" value={r.to} disabled={!r.enabled} onChange={(e) => set({ to: e.target.value })} aria-label="Report recipient" />
          </Row>
          <Row label="Only exceptions" help="Skip approved invoices; report reviews and rejections">
            <Toggle on={r.only_exceptions} onChange={(v) => set({ only_exceptions: v })} label="Only exceptions" />
          </Row>
          <Row label="Attach PDF summary" help="One-page report for filing">
            <Toggle on={r.attach_pdf} onChange={(v) => set({ attach_pdf: v })} label="Attach PDF summary" />
          </Row>
          <Row label="Attach JSON" help="Machine-readable decision and extracted fields">
            <Toggle on={r.attach_json} onChange={(v) => set({ attach_json: v })} label="Attach JSON" />
          </Row>
          {err && <p className="px-4 py-2.5 text-[13px] text-rose-600">{err}</p>}
          <p className="px-4 py-2.5 text-xs text-slate-400">Sent reports appear in the Outbox tab. Without an SMTP server configured, they are recorded there but not delivered.</p>
        </div>
      )}
    </Panel>
  );
}
