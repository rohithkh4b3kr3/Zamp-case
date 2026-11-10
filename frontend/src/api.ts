export type Decision = "APPROVED" | "NEEDS_REVIEW" | "REJECTED";
export type StageStatus = "pending" | "running" | "passed" | "warning" | "failed" | "skipped";
export type CheckStatus = "pass" | "warn" | "fail" | "info";

export interface Check {
  id: string;
  label: string;
  status: CheckStatus;
  message: string;
  outcome: "review" | "reject" | null;
  route: string | null;
  severity: "low" | "medium" | "high" | "critical";
}

export interface Stage {
  key: string;
  name: string;
  description: string;
  status: StageStatus;
  summary: string;
  checks: Check[];
  logs: { t: number; msg: string }[];
  data: Record<string, any>;
  started_at: string | null;
  duration_ms: number | null;
}

/** Normalised region on a PDF page (0..1 of page width/height). */
export interface Box {
  page: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface LineItem {
  sku: string | null;
  description: string;
  qty: number | null;
  unit_price: number | null;
  amount: number;
  confidence?: number;
  box?: Box;
}

export interface Extracted {
  method: "text" | "ocr" | "llm";
  vendor_name: string | null;
  vendor_tax_id: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  due_date: string | null;
  po_number: string | null;
  currency: string | null;
  subtotal: number | null;
  tax_amount: number | null;
  tax_rate: number | null;
  tax_inclusive: boolean;
  total: number | null;
  bank: { bank_name: string | null; account_number: string | null; routing_number: string | null };
  line_items: LineItem[];
  cues: { type: string; evidence: string; box?: Box }[];
  field_confidence: Record<string, number>;
  field_boxes?: Record<string, Box>;
  corrected_fields?: Record<string, { was: any; now: any }>;
  ocr_confidence?: number;
  ocr_engine?: string;
  llm_usage?: { provider: string; model: string; input_tokens?: number; output_tokens?: number };
  cross_check?: { agreement: string; differences: any[] };
}

export interface DecisionDetail {
  decision: Decision;
  headline: string;
  route_to: string | null;
  reasons: { text: string; label: string; severity: string; stage: string; route: string | null; custom?: boolean }[];
  actions: string[];
  vendor_email: { to: string | null; subject: string; body: string } | null;
  internal_note: string;
  amounts: { invoice_total: number; recommended_payment: number | null; on_hold: number | null; deposit_deducted?: number | null; early_discount?: number | null };
  doc_type?: "invoice" | "credit_note" | "prepayment";
  payment: { scheduled_for: string; terms: string; to_account: string; amount?: number; discount?: { pct: number; amount: number; by: string; pay: number } } | null;
  currency?: string;
  fx?: { rate: number; base_total: number; currency: string; as_of: string } | null;
  learned?: string[];
  checks_passed: number;
  checks_total: number;
  extraction_confidence: number;
  po: string | null;
  po_source: string | null;
}

export interface RunSummary {
  id: string;
  created_at: string;
  updated_at: string;
  filename: string;
  source: string;
  status: "queued" | "running" | "completed" | "failed";
  decision: Decision | null;
  route_to: string | null;
  vendor_id: string | null;
  vendor_name: string | null;
  invoice_number: string | null;
  po_number: string | null;
  total: number | null;
  currency: string | null;
  extraction_method: string | null;
  duration_ms: number | null;
  review_status: "pending" | "approved" | "rejected" | null;
  reviewer: string | null;
  ledger_posted: number;
  error: string | null;
  batch_id?: string | null;
  batch_label?: string | null;
  parent_run_id?: string | null;
  superseded_by?: string | null;
  email_id?: string | null;
  base_total?: number | null;
  fx_rate?: number | null;
  sla_due_at?: string | null;
  escalated_at?: string | null;
}

export interface Run extends RunSummary {
  file_hash: string | null;
  stages: Stage[];
  extracted: Extracted | null;
  decision_detail: DecisionDetail | null;
  review_note: string | null;
  reviewed_at: string | null;
  overrides?: Record<string, any> | null;
  batch?: { id: string; batch_label: string; status: string; decision: Decision | null; invoice_number: string | null; total: number | null }[] | null;
  notifications?: Notification[];
  email?: { id: string; from_addr: string; from_name: string | null; subject: string; received_at: string; source: string } | null;
  audit?: { id: number; ts: string; actor: string; action: string; detail: string }[];
}

export interface InboxEmail {
  id: string;
  received_at: string;
  source: string;
  message_id: string | null;
  from_addr: string | null;
  from_name: string | null;
  subject: string | null;
  body_excerpt: string | null;
  attachments: { filename: string; content_type: string; size: number; is_pdf: boolean }[];
  run_ids: string[];
  runs?: { id: string; status: string; decision: Decision | null; invoice_number: string | null; vendor_name: string | null; total: number | null; currency: string | null; filename: string }[];
  status: "processing" | "processed" | "no_pdf" | "duplicate_email" | "error";
  note: string | null;
}

export interface Notification {
  id: number;
  ts: string;
  run_id: string | null;
  email_id: string | null;
  kind: "routed" | "escalation" | "vendor_draft" | "reviewed" | "run_report" | "event_action";
  team: string | null;
  channel: string;
  recipient: string | null;
  subject: string | null;
  body: string | null;
  status: "sent" | "simulated" | "draft" | "failed";
  error: string | null;
}

export interface MemoryRule {
  id: number;
  vendor_id: string;
  vendor_name?: string;
  kind: "alias" | "date_format" | "correction";
  value: string;
  detail: string | null;
  source_run: string | null;
  created_at: string;
  hits: number;
}

export type RefKind = "po" | "grn" | "vendor";

export interface ImportUpload {
  token: string;
  kind: RefKind;
  filename: string;
  headers: string[];
  row_count: number;
  sample: Record<string, string>[];
  mapping: Record<string, string | null>;
  fields: { id: string; label: string; required: boolean }[];
}

export interface ImportPreview {
  kind: RefKind;
  mapping: Record<string, string | null>;
  objects: number;
  rows: number;
  new: string[];
  updated: string[];
  unchanged: string[];
  removed_if_replace: string[];
  errors: { row: number; field: string; message: string }[];
  error_count: number;
  items: any[];
}

export interface RefSource {
  id: string;
  name: string;
  kind: RefKind;
  url: string;
  interval_min: number;
  last_sync: string | null;
  last_status: "ok" | "partial" | "error" | null;
  last_message: string | null;
}

export interface PoLine {
  line: number;
  sku?: string;
  description: string;
  qty: number;
  unit_price: number;
  received?: number;
  billed_qty?: number;
}

export interface PoSummary {
  po_number: string;
  vendor_id: string;
  vendor_name: string;
  date: string;
  status: string;
  currency: string;
  total: number;
  billed: number;
  remaining: number;
  lines: PoLine[];
}

export interface PoDetail extends PoSummary {
  requester: string;
  tax_rate: number;
  receipts: { grn: string; date: string; lines: { line: number; qty_received: number }[] }[];
  invoices: { invoice_number: string; invoice_date: string; subtotal: number; status: string; run_id: string | null }[];
}

export interface StartResult {
  id: string;
  ids: string[] | null;
  batch_id?: string;
}

export interface Sample {
  file: string;
  title: string;
  kind: "happy" | "edge" | "extra" | "learning";
  expected: Decision;
  story: string;
}

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const b = await res.json();
      msg = b.detail || msg;
    } catch {
      /* not json */
    }
    throw new Error(msg);
  }
  return res.json();
}

export const api = {
  runs: () => fetch("/api/runs").then(j<RunSummary[]>),
  run: (id: string) => fetch(`/api/runs/${id}`).then(j<Run>),
  stats: (days?: number) => fetch(`/api/stats${days ? `?days=${days}` : ""}`).then(j<any>),
  meta: () => fetch("/api/meta").then(j<any>),
  samples: () => fetch("/api/samples").then(j<Sample[]>),
  reference: () => fetch("/api/reference").then(j<any>),
  settings: () => fetch("/api/settings").then(j<Record<string, number>>),
  saveSettings: (v: Record<string, number>) =>
    fetch("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(v) }).then(j<Record<string, number>>),
  reset: () => fetch("/api/reset", { method: "POST" }).then(j<any>),
  runSample: (file: string) => fetch(`/api/runs/sample/${encodeURIComponent(file)}`, { method: "POST" }).then(j<StartResult>),
  upload: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return fetch("/api/runs", { method: "POST", body: fd }).then(j<StartResult>);
  },
  correct: (id: string, fields: Record<string, any>, reviewer: string, note = "") =>
    fetch(`/api/runs/${id}/correct`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fields, reviewer, note }),
    }).then(j<{ id: string }>),
  ask: (id: string, question: string, history: { role: string; content: string }[]) =>
    fetch(`/api/runs/${id}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, history }),
    }).then(j<{ answer: string; mode: "llm" | "rules"; model: string | null; note?: string }>),
  inbox: () => fetch("/api/inbox").then(j<InboxEmail[]>),
  inboxStats: () => fetch("/api/inbox/stats").then(j<any>),
  inboxSamples: () => fetch("/api/inbox/samples").then(j<{ file: string; from: string; from_name: string; subject: string; attachments: string[]; note: string }[]>),
  inboxSample: (file: string) => fetch(`/api/inbox/sample/${encodeURIComponent(file)}`, { method: "POST" }).then(j<InboxEmail>),
  inboxUpload: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return fetch("/api/inbox/upload", { method: "POST", body: fd }).then(j<InboxEmail>);
  },
  inboxPoll: () => fetch("/api/inbox/poll", { method: "POST" }).then(j<any>),
  notifications: (runId?: string) => fetch(`/api/notifications${runId ? `?run_id=${runId}` : ""}`).then(j<Notification[]>),
  checkSla: () => fetch("/api/notifications/check-sla", { method: "POST" }).then(j<{ escalated: number }>),
  memory: () => fetch("/api/memory").then(j<MemoryRule[]>),
  forget: (id: number) => fetch(`/api/memory/${id}`, { method: "DELETE" }).then(j<any>),
  analytics: (days?: number) => fetch(`/api/analytics${days ? `?days=${days}` : ""}`).then(j<any>),
  // procurement data: import, sources, edits
  refUpload: (kind: RefKind, file: File) => {
    const fd = new FormData();
    fd.append("kind", kind);
    fd.append("file", file);
    return fetch("/api/reference/import/upload", { method: "POST", body: fd }).then(j<ImportUpload>);
  },
  refPreview: (token: string, mapping: Record<string, string | null>) =>
    fetch("/api/reference/import/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token, mapping }) }).then(j<ImportPreview>),
  refCommit: (token: string, mapping: Record<string, string | null>, mode: "merge" | "replace") =>
    fetch("/api/reference/import/commit", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token, mapping, mode }) }).then(
      j<{ new: number; updated: number; unchanged: number; removed: number; skipped_rows: number; label: string }>,
    ),
  refTemplateUrl: (kind: RefKind, format: "csv" | "xlsx" = "xlsx") => `/api/reference/template/${kind}?format=${format}`,
  refSources: () => fetch("/api/reference/sources").then(j<RefSource[]>),
  refAddSource: (s: { name: string; kind: RefKind; url: string; interval_min: number }) =>
    fetch("/api/reference/sources", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(s) }).then(j<RefSource>),
  refSyncSource: (id: string) => fetch(`/api/reference/sources/${id}/sync`, { method: "POST" }).then(j<RefSource>),
  refRemoveSource: (id: string) => fetch(`/api/reference/sources/${id}`, { method: "DELETE" }).then(j<any>),
  refPos: (vendorId?: string | null) => fetch(`/api/reference/pos${vendorId ? `?vendor_id=${encodeURIComponent(vendorId)}` : ""}`).then(j<PoSummary[]>),
  refPo: (po: string) => fetch(`/api/reference/po/${encodeURIComponent(po)}`).then(j<PoDetail>),
  refUpdatePo: (po: string, patch: Record<string, any>) =>
    fetch(`/api/reference/po/${encodeURIComponent(po)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) }).then(j<PoDetail>),
  refAddReceipt: (po: string, body: { lines: { line: number; qty_received: number }[]; date?: string; grn?: string }) =>
    fetch(`/api/reference/po/${encodeURIComponent(po)}/receipts`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(j<PoDetail>),
  refHistory: () => fetch("/api/reference/history").then(j<{ id: number; ts: string; actor: string; action: string; detail: string }[]>),
  exportUrl: (id: string, format: "json" | "csv") => `/api/runs/${id}/export?format=${format}`,
  fileUrl: (id: string) => `/api/runs/${id}/file`,
  review: (id: string, action: "approve" | "reject", reviewer: string, note: string) =>
    fetch(`/api/runs/${id}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, reviewer, note }),
    }).then(j<Run>),
};

export const fmtMoney = (v: number | null | undefined, cur = "USD") =>
  v == null ? "—" : new Intl.NumberFormat("en-US", { style: "currency", currency: cur || "USD" }).format(v);

export const fmtTime = (iso: string) => {
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
};

export const ago = (iso: string) => {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};
