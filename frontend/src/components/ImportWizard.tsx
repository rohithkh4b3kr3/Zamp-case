import clsx from "clsx";
import { Check, Download, FileSpreadsheet, Loader2, UploadCloud, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, type ImportPreview, type ImportUpload, type RefKind } from "../api";

const KINDS: { id: RefKind; label: string; noun: [string, string] }[] = [
  { id: "po", label: "Purchase orders", noun: ["PO", "POs"] },
  { id: "grn", label: "Goods receipts", noun: ["receipt", "receipts"] },
  { id: "vendor", label: "Vendors", noun: ["vendor", "vendors"] },
];
const STEPS = ["Upload", "Map columns", "Review"];

/** Import the spreadsheets AP already keeps: upload, confirm the column mapping, review what changes. */
export default function ImportWizard({ initialKind = "po", onClose, onDone }: { initialKind?: RefKind; onClose: () => void; onDone: (msg: string) => void }) {
  const [kind, setKind] = useState<RefKind>(initialKind);
  const [step, setStep] = useState(0);
  const [up, setUp] = useState<ImportUpload | null>(null);
  const [mapping, setMapping] = useState<Record<string, string | null>>({});
  const [pv, setPv] = useState<ImportPreview | null>(null);
  const [mode, setMode] = useState<"merge" | "replace">("merge");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const noun = KINDS.find((k) => k.id === kind)!.noun;
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  // Esc closes; focus stays inside the dialog
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    box.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeRef.current();
      if (e.key === "Tab" && box.current) {
        const f = box.current.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input, select, [tabindex]:not([tabindex="-1"])');
        if (!f.length) return;
        const first = f[0];
        const last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      prev?.focus();
    };
  }, []);

  const run = async <T,>(p: Promise<T>): Promise<T | null> => {
    setBusy(true);
    setErr(null);
    try {
      return await p;
    } catch (e: any) {
      setErr(e.message);
      return null;
    } finally {
      setBusy(false);
    }
  };

  const onFile = async (f: File | undefined) => {
    if (!f) return;
    const r = await run(api.refUpload(kind, f));
    if (r) {
      setUp(r);
      setMapping(r.mapping);
      setStep(1);
    }
  };

  const toReview = async () => {
    if (!up) return;
    const r = await run(api.refPreview(up.token, mapping));
    if (r) {
      setPv(r);
      setStep(2);
    }
  };

  const commit = async () => {
    if (!up) return;
    const r = await run(api.refCommit(up.token, mapping, mode));
    if (r) {
      const n = r.new + r.updated + r.unchanged;
      onDone(
        `Imported ${n} ${n === 1 ? noun[0] : noun[1]}: ${r.new} new, ${r.updated} updated` +
          (r.removed ? `, ${r.removed} removed` : "") +
          (r.skipped_rows ? `, ${r.skipped_rows} row(s) skipped` : ""),
      );
      onClose();
    }
  };

  const missingRequired = up ? up.fields.filter((f) => f.required && !mapping[f.id]).map((f) => f.label) : [];

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center scrim p-4 backdrop-blur-sm" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={box} tabIndex={-1} role="dialog" aria-modal="true" aria-label="Import spreadsheet" className="card flex max-h-[85dvh] w-full max-w-3xl flex-col outline-none animate-fadein">
        {/* header: steps + close */}
        <div className="flex items-center gap-3 border-b border-slate-100 px-4 py-3">
          <ol className="flex flex-1 items-center gap-2 text-[13px]">
            {STEPS.map((s, i) => (
              <li key={s} className="flex items-center gap-2">
                {i > 0 && <span className="h-px w-6 bg-slate-200" />}
                <span
                  className={clsx("grid h-5 w-5 place-items-center rounded-full text-[11px] font-semibold",
                    i < step ? "bg-brand-600 text-white" : i === step ? "bg-brand-50 text-brand-700 ring-1 ring-brand-200" : "bg-slate-100 text-slate-400",
                  )}
                >
                  {i < step ? <Check className="h-3 w-3" /> : i + 1}
                </span>
                <span className={clsx(i === step ? "font-medium text-slate-900" : "text-slate-500")}>{s}</span>
              </li>
            ))}
          </ol>
          <button className="btn-ghost w-8 px-0" onClick={onClose} aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {err && <div className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[13px] text-rose-700">{err}</div>}

          {step === 0 && (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div role="radiogroup" aria-label="What are you importing" className="inline-flex rounded-lg bg-slate-100 p-0.5">
                  {KINDS.map((k) => (
                    <button
                      key={k.id}
                      role="radio"
                      aria-checked={kind === k.id}
                      onClick={() => setKind(k.id)}
                      className={clsx("h-7 rounded-md px-3 text-[13px] font-medium transition", kind === k.id ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-800")}
                    >
                      {k.label}
                    </button>
                  ))}
                </div>
                <a className="btn-ghost" href={api.refTemplateUrl(kind, "xlsx")} download>
                  <Download className="h-4 w-4" /> Template
                </a>
              </div>
              <div
                onDragOver={(e) => {
                  e.preventDefault();
                  setDrag(true);
                }}
                onDragLeave={() => setDrag(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDrag(false);
                  onFile(e.dataTransfer.files?.[0]);
                }}
                onClick={() => fileInput.current?.click()}
                className={clsx("flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-12 text-center transition",
                  drag ? "border-brand-500 bg-brand-50" : "border-slate-200 hover:border-slate-300 hover:bg-slate-50",
                )}
              >
                <input ref={fileInput} type="file" className="hidden" accept=".csv,.xlsx,.xlsm,text/csv" onChange={(e) => onFile(e.target.files?.[0])} />
                {busy ? <Loader2 className="h-7 w-7 animate-spin text-slate-400" /> : <UploadCloud className="h-7 w-7 text-slate-400" />}
                <div className="mt-2 text-[13px] font-medium text-slate-800">Drop a CSV or Excel file, or click to choose</div>
                <div className="mt-0.5 text-xs text-slate-500">First row = column names · one {kind === "po" ? "PO line" : noun[0]} per row</div>
              </div>
            </div>
          )}

          {step === 1 && up && (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2 text-[13px]">
                <span className="pill">
                  <FileSpreadsheet className="h-3 w-3" /> {up.filename}
                </span>
                <span className="text-slate-500">
                  <span className="num">{up.row_count}</span> rows · {up.headers.length} columns
                </span>
              </div>
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="text-left text-xs text-slate-500">
                    <th className="pb-2 font-medium">Field</th>
                    <th className="pb-2 font-medium">Column in your file</th>
                    <th className="pb-2 font-medium">First value</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {up.fields.map((f) => {
                    const col = mapping[f.id];
                    return (
                      <tr key={f.id}>
                        <td className="py-1.5 pr-3 text-slate-800">
                          {f.label}
                          {f.required && <span className="ml-0.5 text-rose-500">*</span>}
                        </td>
                        <td className="w-64 py-1.5 pr-3">
                          <select
                            className={clsx("input h-7", f.required && !col && "border-rose-300")}
                            value={col || ""}
                            onChange={(e) => setMapping({ ...mapping, [f.id]: e.target.value || null })}
                            aria-label={`Column for ${f.label}`}
                          >
                            <option value="">— not in file —</option>
                            {up.headers.map((h) => (
                              <option key={h} value={h}>
                                {h}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td className="max-w-[220px] truncate py-1.5 text-xs text-slate-500">{col ? up.sample[0]?.[col] || "—" : ""}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {step === 2 && pv && (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="chip bg-emerald-50 text-emerald-700">
                  <span className="num">{pv.new.length}</span> new
                </span>
                <span className="chip bg-brand-50 text-brand-700">
                  <span className="num">{pv.updated.length}</span> updated
                </span>
                <span className="chip bg-slate-100 text-slate-600">
                  <span className="num">{pv.unchanged.length}</span> unchanged
                </span>
                <span className={clsx("chip", pv.error_count ? "bg-rose-50 text-rose-700" : "bg-slate-100 text-slate-500")}>
                  <span className="num">{pv.error_count}</span> {pv.error_count === 1 ? "error" : "errors"}
                </span>
                {mode === "replace" && pv.removed_if_replace.length > 0 && (
                  <span className="chip bg-amber-50 text-amber-800">
                    <span className="num">{pv.removed_if_replace.length}</span> removed
                  </span>
                )}
              </div>

              <div className="flex flex-wrap items-center gap-3">
                <div role="radiogroup" aria-label="Import mode" className="inline-flex rounded-lg bg-slate-100 p-0.5">
                  {(
                    [
                      ["merge", "Merge"],
                      ["replace", "Replace all"],
                    ] as const
                  ).map(([m, l]) => (
                    <button
                      key={m}
                      role="radio"
                      aria-checked={mode === m}
                      onClick={() => setMode(m)}
                      className={clsx("h-7 rounded-md px-3 text-[13px] font-medium transition", mode === m ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-800")}
                    >
                      {l}
                    </button>
                  ))}
                </div>
                <span className="text-xs text-slate-500">
                  {mode === "merge" ? `Adds new ${noun[1]} and updates matching ones; others stay.` : `Existing ${noun[1]} not in this file are removed.`}
                </span>
              </div>

              {(pv.new.length > 0 || pv.updated.length > 0) && (
                <div className="flex flex-wrap gap-1">
                  {pv.new.slice(0, 30).map((k) => (
                    <span key={k} className="pill num" title="New">
                      + {k}
                    </span>
                  ))}
                  {pv.updated.slice(0, 30).map((k) => (
                    <span key={k} className="pill num" title="Updated">
                      ~ {k}
                    </span>
                  ))}
                </div>
              )}

              {pv.errors.length > 0 && (
                <div className="overflow-hidden rounded-lg ring-1 ring-slate-200">
                  <table className="tbl w-full">
                    <thead>
                      <tr>
                        <th className="w-16">Row</th>
                        <th className="w-32">Field</th>
                        <th>Problem (row is skipped)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pv.errors.map((e, i) => (
                        <tr key={i}>
                          <td className="num text-slate-500">{e.row}</td>
                          <td className="text-slate-600">{e.field}</td>
                          <td className="text-rose-700">{e.message}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>

        {/* footer actions */}
        <div className="flex items-center justify-between gap-2 border-t border-slate-100 px-4 py-3">
          <button className="btn-ghost" onClick={() => (step === 0 ? onClose() : setStep(step - 1))} disabled={busy}>
            {step === 0 ? "Cancel" : "Back"}
          </button>
          {step === 1 && (
            <div className="flex items-center gap-3">
              {missingRequired.length > 0 && <span className="text-xs text-rose-600">Map: {missingRequired.join(", ")}</span>}
              <button className="btn-primary" onClick={toReview} disabled={busy || missingRequired.length > 0}>
                {busy && <Loader2 className="h-4 w-4 animate-spin" />} Review changes
              </button>
            </div>
          )}
          {step === 2 && pv && (
            <button className="btn-primary" onClick={commit} disabled={busy || pv.objects === 0}>
              {busy && <Loader2 className="h-4 w-4 animate-spin" />}
              {pv.objects === 0 ? "Nothing to import" : `Import ${pv.objects} ${pv.objects === 1 ? noun[0] : noun[1]}`}
            </button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
