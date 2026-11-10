import clsx from "clsx";
import { AlertTriangle, Ban, CheckCircle2, Circle, CircleDashed, Info, Loader2, MinusCircle, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import type { CheckStatus, Decision, StageStatus } from "../api";

export const decisionMeta: Record<Decision, { label: string; cls: string; dot: string; icon: any }> = {
  APPROVED: { label: "Approved", cls: "bg-emerald-50 text-emerald-700 ring-emerald-600/15", dot: "bg-emerald-500", icon: CheckCircle2 },
  NEEDS_REVIEW: { label: "Needs review", cls: "bg-amber-50 text-amber-800 ring-amber-600/20", dot: "bg-amber-500", icon: AlertTriangle },
  REJECTED: { label: "Rejected", cls: "bg-rose-50 text-rose-700 ring-rose-600/15", dot: "bg-rose-500", icon: Ban },
};

export function DecisionBadge({ decision, size = "sm" }: { decision: Decision | null; size?: "sm" | "lg" }) {
  if (!decision) return <span className="text-slate-400 text-xs">—</span>;
  const m = decisionMeta[decision];
  const Icon = m.icon;
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-md font-medium ring-1 ring-inset",
        m.cls,
        size === "lg" ? "px-2.5 py-1 text-sm" : "px-1.5 py-0.5 text-xs",
      )}
    >
      {size === "lg" ? <Icon className="h-4 w-4" /> : <span className={clsx("h-1.5 w-1.5 rounded-full", m.dot)} />}
      {m.label}
    </span>
  );
}

export function StatusPill({ status }: { status: string }) {
  const map: Record<string, [string, string]> = {
    queued: ["bg-slate-100 text-slate-600", "bg-slate-400"],
    running: ["bg-brand-50 text-brand-700", ""],
    completed: ["bg-slate-100 text-slate-700", "bg-slate-400"],
    failed: ["bg-rose-50 text-rose-700", "bg-rose-500"],
    pending: ["bg-amber-50 text-amber-800", "bg-amber-500"],
    approved: ["bg-emerald-50 text-emerald-700", "bg-emerald-500"],
    rejected: ["bg-rose-50 text-rose-700", "bg-rose-500"],
  };
  const [cls, dot] = map[status] || ["bg-slate-100 text-slate-600", "bg-slate-400"];
  return (
    <span className={clsx("chip capitalize", cls)}>
      {status === "running" ? <Loader2 className="h-3 w-3 animate-spin" /> : <span className={clsx("h-1.5 w-1.5 rounded-full", dot)} />}
      {status}
    </span>
  );
}

export function StageIcon({ status, className = "h-5 w-5" }: { status: StageStatus; className?: string }) {
  switch (status) {
    case "running":
      return <Loader2 className={clsx(className, "animate-spin text-brand-600")} />;
    case "passed":
      return <CheckCircle2 className={clsx(className, "text-emerald-500")} />;
    case "warning":
      return <AlertTriangle className={clsx(className, "text-amber-500")} />;
    case "failed":
      return <XCircle className={clsx(className, "text-rose-500")} />;
    case "skipped":
      return <MinusCircle className={clsx(className, "text-slate-300")} />;
    default:
      return <CircleDashed className={clsx(className, "text-slate-300")} />;
  }
}

export function CheckIcon({ status }: { status: CheckStatus }) {
  const c = "h-4 w-4 shrink-0 mt-0.5";
  if (status === "pass") return <CheckCircle2 className={clsx(c, "text-emerald-500")} />;
  if (status === "warn") return <AlertTriangle className={clsx(c, "text-amber-500")} />;
  if (status === "fail") return <XCircle className={clsx(c, "text-rose-500")} />;
  if (status === "info") return <Info className={clsx(c, "text-sky-500")} />;
  return <Circle className={c} />;
}

export function Card({ title, action, children, className, pad = true }: { title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string; pad?: boolean }) {
  return (
    <section className={clsx("card", className)}>
      {title && (
        <header className="flex min-h-[44px] flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b border-slate-100 px-4 py-2">
          <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
          {action}
        </header>
      )}
      <div className={pad ? "p-4" : ""}>{children}</div>
    </section>
  );
}

export function Kpi({ label, value, sub, tone = "slate", icon: Icon }: { label: string; value: ReactNode; sub?: ReactNode; tone?: string; icon?: any }) {
  const tones: Record<string, string> = {
    slate: "text-slate-400",
    green: "text-emerald-500",
    amber: "text-amber-500",
    rose: "text-rose-500",
    brand: "text-brand-500",
  };
  return (
    <div className="card p-4">
      <Stat label={label} value={value} sub={sub} icon={Icon} iconCls={tones[tone]} />
    </div>
  );
}

export function ConfidenceBar({ value }: { value: number | undefined }) {
  if (value == null) return <span className="text-[11px] text-slate-400">—</span>;
  const pct = Math.round(value * 100);
  const color = value >= 0.9 ? "bg-emerald-500" : value >= 0.8 ? "bg-amber-400" : "bg-rose-500";
  return (
    <div className="flex items-center gap-2" title={`Extraction confidence ${pct}%`}>
      <div className="h-1.5 w-14 overflow-hidden rounded-full bg-slate-100">
        <div className={clsx("h-full rounded-full", color)} style={{ width: `${pct}%` }} />
      </div>
      <span className="num text-[11px] text-slate-500">{pct}%</span>
    </div>
  );
}

export function Empty({ icon: Icon, title, children }: { icon: any; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-10 text-center">
      <div className="grid h-10 w-10 place-items-center rounded-lg border border-slate-200 bg-slate-50">
        <Icon className="h-5 w-5 text-slate-400" />
      </div>
      <div className="mt-3 text-sm font-medium text-slate-800">{title}</div>
      {children && <div className="mt-1 max-w-sm text-sm text-slate-500">{children}</div>}
    </div>
  );
}

export function MethodTag({ method }: { method: string | null }) {
  if (!method) return null;
  const m: Record<string, [string, string]> = {
    text: ["Text layer", "bg-slate-100 text-slate-600"],
    ocr: ["OCR", "bg-violet-50 text-violet-700"],
    llm: ["AI model", "bg-orange-50 text-orange-700"],
  };
  const [label, cls] = m[method] || [method, "bg-slate-100 text-slate-600"];
  return <span className={clsx("chip", cls)}>{label}</span>;
}

/** A single figure: small label, large tabular value, optional one-line context. */
export function Stat({ label, value, sub, icon: Icon, iconCls = "text-slate-400", loading }: { label: ReactNode; value: ReactNode; sub?: ReactNode; icon?: any; iconCls?: string; loading?: boolean }) {
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-1.5 text-xs font-medium text-slate-500">
        {Icon && <Icon className={clsx("h-3.5 w-3.5", iconCls)} />}
        <span className="truncate">{label}</span>
      </div>
      {loading ? <div className="skeleton mt-2 h-7 w-20" /> : <div className="num mt-1.5 truncate text-2xl font-semibold text-slate-900">{value}</div>}
      {sub && <div className="mt-1 truncate text-xs text-slate-500">{sub}</div>}
    </div>
  );
}

/** Segmented filter with counts (Stripe-style status tabs). */
export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { id: T; label: string; count?: number; dot?: string }[] }) {
  return (
    <div role="tablist" className="flex items-center gap-1 overflow-x-auto [scrollbar-width:none]">
      {items.map((t) => {
        const active = t.id === value;
        return (
          <button
            key={t.id}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(t.id)}
            className={clsx(
              "flex h-8 shrink-0 items-center gap-1.5 rounded-lg px-2.5 text-[13px] font-medium transition-colors",
              active ? "bg-slate-100 text-slate-900" : "text-slate-500 hover:bg-slate-50 hover:text-slate-800",
            )}
          >
            {t.dot && <span className={clsx("h-1.5 w-1.5 rounded-full", t.dot)} />}
            {t.label}
            {t.count != null && <span className={clsx("num text-xs", active ? "text-slate-600" : "text-slate-400")}>{t.count}</span>}
          </button>
        );
      })}
    </div>
  );
}

export function SkeletonRows({ rows = 5, cols = 6 }: { rows?: number; cols?: number }) {
  return (
    <tbody>
      {Array.from({ length: rows }).map((_, i) => (
        <tr key={i}>
          {Array.from({ length: cols }).map((__, j) => (
            <td key={j}>
              <div className="skeleton h-4" style={{ width: `${40 + ((i * 7 + j * 13) % 50)}%` }} />
            </td>
          ))}
        </tr>
      ))}
    </tbody>
  );
}
