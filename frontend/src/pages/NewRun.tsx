import clsx from "clsx";
import { Eye, FileText, Loader2, Play, ScanLine, UploadCloud } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Sample } from "../api";
import { Card, DecisionBadge } from "../components/ui";

const kindMeta = {
  happy: { label: "Standard", cls: "bg-slate-100 text-slate-600" },
  edge: { label: "Edge case", cls: "bg-amber-50 text-amber-800" },
  extra: { label: "Scenario", cls: "bg-slate-100 text-slate-600" },
  learning: { label: "Learning", cls: "bg-violet-50 text-violet-700" },
} as Record<string, { label: string; cls: string }>;

export default function NewRun() {
  const [samples, setSamples] = useState<Sample[]>([]);
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const nav = useNavigate();

  useEffect(() => {
    api.samples().then(setSamples).catch((e) => setErr(String(e)));
  }, []);

  async function go(p: Promise<{ id: string }>, key: string) {
    setBusy(key);
    setErr(null);
    try {
      const { id } = await p;
      nav(`/runs/${id}`);
    } catch (e: any) {
      setErr(e.message);
      setBusy(null);
    }
  }

  function onFiles(files: FileList | null) {
    const f = files?.[0];
    if (!f) return;
    if (!f.name.toLowerCase().endsWith(".pdf")) {
      setErr("Only PDF files can be processed. Choose a .pdf invoice.");
      return;
    }
    go(api.upload(f), "upload");
  }

  return (
    <div className="mx-auto max-w-5xl space-y-4 animate-fadein">
      <div
        role="button"
        tabIndex={0}
        aria-label="Upload an invoice PDF"
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          onFiles(e.dataTransfer.files);
        }}
        onClick={() => input.current?.click()}
        className={clsx(
          "flex cursor-pointer items-center gap-5 rounded-xl border border-dashed bg-white px-6 py-10 transition-colors",
          drag ? "border-brand-500 bg-brand-50" : "border-slate-300 hover:border-slate-400 hover:bg-slate-50",
        )}
      >
        <input ref={input} type="file" accept="application/pdf" className="hidden" onChange={(e) => onFiles(e.target.files)} />
        <div className="grid h-14 w-14 shrink-0 place-items-center rounded-xl border border-slate-200 bg-slate-50">
          {busy === "upload" ? <Loader2 className="h-6 w-6 animate-spin text-brand-600" /> : <UploadCloud className="h-6 w-6 text-slate-500" />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-base font-medium text-slate-900">{drag ? "Drop to upload" : "Drop an invoice PDF here"}</div>
          <div className="mt-1 text-[13px] text-slate-500">Digital or scanned · up to 15 MB</div>
        </div>
        <span className="btn-outline pointer-events-none hidden sm:inline-flex">Browse files</span>
      </div>
      {err && <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">{err}</div>}

      <Card title={<span>Sample invoices <span className="num ml-1 font-normal text-slate-400">{samples.length || ""}</span></span>} pad={false}>
        {samples.length === 0 && !err ? (
          <div className="space-y-px p-4">{[0, 1, 2, 3].map((i) => <div key={i} className="skeleton mb-2 h-12" />)}</div>
        ) : (
          <ul className="divide-y divide-slate-100">
            {samples.map((s) => (
              <li key={s.file} className="group flex items-center gap-4 px-4 py-3 transition-colors hover:bg-slate-50">
                <div className="grid h-8 w-8 shrink-0 place-items-center rounded-md border border-slate-200 bg-white text-slate-400">
                  {s.file.includes("scanned") ? <ScanLine className="h-4 w-4" /> : <FileText className="h-4 w-4" />}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-[13px] font-medium text-slate-900">{s.title}</span>
                    <span className={clsx("chip hidden md:inline-flex", (kindMeta[s.kind] || kindMeta.extra).cls)}>{(kindMeta[s.kind] || kindMeta.extra).label}</span>
                  </div>
                  <p className="truncate text-xs text-slate-500" title={s.story}>{s.story}</p>
                </div>
                <div className="hidden w-32 shrink-0 sm:block">
                  <DecisionBadge decision={s.expected} />
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  <a className="btn-ghost w-8 px-0" href={`/api/samples/${s.file}`} target="_blank" rel="noreferrer" title="Preview PDF" aria-label={`Preview ${s.title}`}>
                    <Eye className="h-4 w-4" />
                  </a>
                  <button className="btn-outline" disabled={!!busy} onClick={() => go(api.runSample(s.file), s.file)}>
                    {busy === s.file ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />} Run
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
