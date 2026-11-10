import clsx from "clsx";
import { AlertTriangle, CheckCircle2, Copy, FolderInput, Inbox as InboxIcon, Loader2, MailPlus, Paperclip, RefreshCw, Send, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ago, api, fmtMoney, type InboxEmail, type Notification } from "../api";
import { ConnectorPanel, ReportSettings, Triggers } from "../components/Intake";
import { Card, DecisionBadge, Empty, StatusPill, Tabs } from "../components/ui";

type Tab = "email" | "cloud" | "sftp" | "triggers" | "reports" | "outbox";

const STATUS: Record<string, { label: string; cls: string }> = {
  processed: { label: "Processed", cls: "bg-emerald-50 text-emerald-700" },
  processing: { label: "Processing", cls: "bg-brand-50 text-brand-700" },
  no_pdf: { label: "No PDF attached", cls: "bg-amber-50 text-amber-800" },
  duplicate_email: { label: "Duplicate email", cls: "bg-slate-100 text-slate-600" },
  error: { label: "Error", cls: "bg-rose-50 text-rose-700" },
};

export default function Inbox() {
  const [emails, setEmails] = useState<InboxEmail[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [samples, setSamples] = useState<Awaited<ReturnType<typeof api.inboxSamples>>>([]);
  const [outbox, setOutbox] = useState<Notification[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>(() => {
    const h = window.location.hash.slice(1) as Tab;
    return ["email", "cloud", "sftp", "triggers", "reports", "outbox"].includes(h) ? h : "email";
  });
  const pick = (t: Tab) => {
    setTab(t);
    history.replaceState(null, "", `#${t}`);
  };
  const file = useRef<HTMLInputElement>(null);

  const load = () => {
    api.inbox().then(setEmails).catch(() => {});
    api.inboxStats().then(setStats).catch(() => {});
    api.notifications().then(setOutbox).catch(() => {});
  };
  useEffect(() => {
    load();
    api.inboxSamples().then(setSamples).catch(() => {});
    const t = setInterval(load, 2500);
    return () => clearInterval(t);
  }, []);

  const act = async (key: string, p: Promise<any>) => {
    setBusy(key);
    setErr(null);
    try {
      const e = await p;
      if (e?.id) setOpen(e.id);
      load();
    } catch (x: any) {
      setErr(x.message);
    } finally {
      setBusy(null);
    }
  };

  const drafts = outbox.filter((n) => n.kind === "vendor_draft");
  const reports = outbox.filter((n) => ["run_report", "event_action"].includes(n.kind as string));
  const teamMsgs = outbox.filter((n) => !["vendor_draft", "run_report", "event_action"].includes(n.kind as string));

  return (
    <div className="space-y-4 animate-fadein">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Tabs<Tab>
          value={tab}
          onChange={pick}
          items={[
            { id: "email", label: "Email", count: emails.length },
            { id: "cloud", label: "Cloud storage" },
            { id: "sftp", label: "SFTP" },
            { id: "triggers", label: "Triggers" },
            { id: "reports", label: "Reports" },
            { id: "outbox", label: "Outbox", count: outbox.length },
          ]}
        />
        <div className={clsx("flex flex-wrap items-center gap-2", tab !== "email" && "hidden")}>
          <input ref={file} type="file" accept=".eml,message/rfc822" className="hidden" onChange={(e) => e.target.files?.[0] && act("upload", api.inboxUpload(e.target.files[0]))} />
          <button className="btn-outline" onClick={() => act("poll", api.inboxPoll())} disabled={!!busy} title="Check the drop folder and mailbox now">
            {busy === "poll" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />} Check mail
          </button>
          <button className="btn-primary" onClick={() => file.current?.click()} disabled={!!busy}>
            {busy === "upload" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />} Upload .eml
          </button>
        </div>
      </div>
      {err && <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">{err}</div>}

      {tab === "email" && (
        <div className="grid items-start gap-4 lg:grid-cols-3">
          <section className="card overflow-hidden lg:col-span-2">
            {emails.length === 0 ? (
              <Empty icon={InboxIcon} title="No email received yet">
                Receive a sample email or upload an .eml file.
              </Empty>
            ) : (
              <ul className="divide-y divide-slate-100">
                {emails.map((e) => {
                  const st = STATUS[e.status] || STATUS.error;
                  const isOpen = open === e.id;
                  return (
                    <li key={e.id}>
                      <button
                        onClick={() => setOpen(isOpen ? null : e.id)}
                        aria-expanded={isOpen}
                        className={clsx("flex w-full items-start gap-3 px-4 py-3.5 text-left transition-colors hover:bg-slate-50", isOpen && "bg-slate-50")}
                      >
                        <div className="grid h-9 w-9 shrink-0 place-items-center self-start rounded-full bg-slate-100 text-[13px] font-semibold text-slate-600">
                          {(e.from_name || e.from_addr || "?").slice(0, 1).toUpperCase()}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center justify-between gap-3">
                            <span className="truncate text-[13px] font-semibold text-slate-900">{e.from_name || e.from_addr}</span>
                            <span className="shrink-0 text-xs text-slate-400">{ago(e.received_at)}</span>
                          </div>
                          <div className="mt-0.5 flex items-center justify-between gap-3">
                            <span className="truncate text-[13px] text-slate-600">{e.subject}</span>
                            <span className="flex shrink-0 items-center gap-1.5">
                              {(e.runs || []).length > 0
                                ? (e.runs || []).map((r) =>
                                    r.status === "completed" ? <DecisionBadge key={r.id} decision={r.decision} /> : <StatusPill key={r.id} status={r.status} />,
                                  )
                                : <span className={clsx("chip", st.cls)}>{st.label}</span>}
                            </span>
                          </div>
                          <div className="mt-2 flex min-w-0 flex-wrap items-center gap-1.5">
                            <span className="pill">{e.from_addr}</span>
                            {e.attachments.map((a, i) => (
                              <span key={i} className={clsx("pill", !a.is_pdf && "text-slate-400")}>
                                <Paperclip className="h-3 w-3 shrink-0" />
                                <span className="truncate">{a.filename}</span>
                              </span>
                            ))}
                          </div>
                        </div>
                      </button>
                      {isOpen && (
                        <div className="grid gap-5 border-t border-slate-100 bg-slate-50/50 py-4 pl-16 pr-4 md:grid-cols-2 animate-fadein">
                          <div className="min-w-0">
                            <div className="label mb-1.5">Message</div>
                            <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-slate-700">{e.body_excerpt || "No message body."}</p>
                            <div className="mt-2 truncate text-xs text-slate-400">
                              {st.label} · via {e.source}
                            </div>
                            {e.note && <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">{e.note}</div>}
                          </div>
                          <div className="min-w-0 space-y-2">
                            <div className="label">Invoices created</div>
                            {(e.runs || []).length === 0 && <p className="text-[13px] text-slate-500">No PDF invoice in this email.</p>}
                            {(e.runs || []).map((r) => (
                              <Link key={r.id} to={`/runs/${r.id}`} className="flex items-center justify-between gap-2 rounded-lg border border-slate-200 px-3 py-2 text-[13px] transition-colors hover:bg-slate-50">
                                <span className="min-w-0 truncate">
                                  {r.vendor_name || r.filename} <span className="num text-xs text-slate-400">{r.invoice_number}</span>
                                </span>
                                <span className="flex shrink-0 items-center gap-2">
                                  <span className="num">{fmtMoney(r.total, r.currency || "USD")}</span>
                                  {r.status === "completed" ? <DecisionBadge decision={r.decision} /> : <StatusPill status={r.status} />}
                                </span>
                              </Link>
                            ))}
                          </div>
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          <div className="space-y-4">
          <ConnectorPanel kinds={["imap"]} title="Linked mailboxes" empty="Link a mailbox to import invoice PDFs from new emails automatically." />
          <Card title="Sample emails" pad={false}>
            <ul className="divide-y divide-slate-100">
              {samples.map((s) => (
                <li key={s.file} className="flex items-center gap-3 px-4 py-2.5">
                  <div className="min-w-0 flex-1" title={s.note}>
                    <div className="truncate text-[13px] font-medium text-slate-800">{s.subject}</div>
                  </div>
                  <span className="pill hidden shrink-0 sm:inline-flex">
                    <Paperclip className="h-3 w-3" /> {s.attachments.length}
                  </span>
                  <button className="btn-ghost h-7 shrink-0 px-2" disabled={!!busy} onClick={() => act(s.file, api.inboxSample(s.file))} aria-label={`Receive ${s.subject}`}>
                    {busy === s.file ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <MailPlus className="h-3.5 w-3.5" />} Receive
                  </button>
                </li>
              ))}
            </ul>
            <div className="flex items-center justify-between gap-3 border-t border-slate-100 px-4 py-2.5 text-xs text-slate-500">
              <span className="flex items-center gap-1.5">
                <span className={clsx("h-1.5 w-1.5 rounded-full", stats?.imap_enabled ? "bg-emerald-500" : "bg-slate-300")} />
                {stats?.imap_enabled ? `Mailbox connected${stats.imap_last_poll ? ` · ${ago(stats.imap_last_poll)}` : ""}` : "Mailbox not connected"}
              </span>
              <span className="flex items-center gap-1" title={stats?.drop_folder || "storage/inbox_drop"}>
                <FolderInput className="h-3.5 w-3.5" /> Drop folder on
              </span>
            </div>
            {stats?.imap_error && <div className="px-4 pb-2.5 text-xs text-rose-600">{stats.imap_error}</div>}
          </Card>
          </div>
        </div>
      )}

      {tab === "cloud" && (
        <ConnectorPanel kinds={["gdrive", "s3"]} title="Cloud storage" empty="Connect a Google Drive folder or an S3 bucket. New PDFs are imported on the schedule you set." />
      )}
      {tab === "sftp" && <ConnectorPanel kinds={["sftp"]} title="SFTP servers" empty="Connect an SFTP server, such as a vendor portal or bank drop. New PDFs are imported on the schedule you set." />}
      {tab === "triggers" && <Triggers />}
      {tab === "reports" && <ReportSettings />}

      {tab === "outbox" && (
        <div className="grid items-start gap-4 lg:grid-cols-3">
          <Card title={<span>Team notifications <span className="num ml-1 font-normal text-slate-400">{teamMsgs.length}</span></span>} pad={false}>
            <MessageList items={teamMsgs} empty="Routed exceptions and SLA escalations appear here." />
          </Card>
          <Card title={<span>Vendor email drafts <span className="num ml-1 font-normal text-slate-400">{drafts.length}</span></span>} pad={false}>
            <MessageList items={drafts} empty="Replies to vendors appear here as drafts. Nothing is sent automatically." />
          </Card>
          <Card title={<span>Reports & actions <span className="num ml-1 font-normal text-slate-400">{reports.length}</span></span>} pad={false}>
            <MessageList items={reports} empty="Run reports and event actions appear here after each run." />
          </Card>
        </div>
      )}
    </div>
  );
}

export function MessageList({ items, empty }: { items: Notification[]; empty: string }) {
  const [open, setOpen] = useState<number | null>(null);
  const [copied, setCopied] = useState<number | null>(null);
  if (!items.length) return <p className="px-4 py-10 text-center text-sm text-slate-500">{empty}</p>;
  return (
    <ul className="divide-y divide-slate-100">
      {items.map((n) => (
        <li key={n.id}>
          <button onClick={() => setOpen(open === n.id ? null : n.id)} aria-expanded={open === n.id} className="flex w-full items-start gap-2.5 px-4 py-3 text-left transition-colors hover:bg-slate-50">
            {n.kind === "escalation" ? (
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-500" />
            ) : n.kind === "vendor_draft" ? (
              <Send className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
            ) : (
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
            )}
            <div className="min-w-0 flex-1">
              <div className="truncate text-[13px] font-medium text-slate-900">{n.subject}</div>
              <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-500">
                <span className="truncate">
                  {n.channel} · {n.recipient || n.team}
                </span>
                <span
                  className={clsx(
                    "chip capitalize",
                    n.status === "sent" ? "bg-emerald-50 text-emerald-700" : n.status === "failed" ? "bg-rose-50 text-rose-700" : "bg-slate-100 text-slate-600",
                  )}
                  title={n.status === "simulated" ? "No Slack or SMTP configured, so the message is shown here instead of sent" : undefined}
                >
                  {n.status}
                </span>
                <span>{ago(n.ts)}</span>
              </div>
            </div>
          </button>
          {open === n.id && (
            <div className="px-4 pb-3 pl-11">
              <pre className="whitespace-pre-wrap rounded-lg border border-slate-200 bg-slate-50 p-3 font-sans text-[13px] leading-relaxed text-slate-700">{n.body}</pre>
              {n.error && <div className="mt-1 text-xs text-rose-600">{n.error}</div>}
              <div className="mt-2 flex gap-2">
                {n.run_id && (
                  <Link to={`/runs/${n.run_id}`} className="btn-outline">
                    Open invoice
                  </Link>
                )}
                <button
                  className="btn-ghost"
                  onClick={() => {
                    navigator.clipboard.writeText(`Subject: ${n.subject}\n\n${n.body}`);
                    setCopied(n.id);
                    setTimeout(() => setCopied(null), 1200);
                  }}
                >
                  <Copy className="h-3.5 w-3.5" /> {copied === n.id ? "Copied" : "Copy"}
                </button>
              </div>
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}
