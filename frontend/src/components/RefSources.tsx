import clsx from "clsx";
import { Link2, Loader2, MoreHorizontal, Plus, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { ago, api, type RefKind, type RefSource } from "../api";
import { Empty } from "./ui";

const KIND_LABEL: Record<RefKind, string> = { po: "Purchase orders", grn: "Goods receipts", vendor: "Vendors" };
const DOT: Record<string, string> = { ok: "bg-emerald-500", partial: "bg-amber-500", error: "bg-rose-500" };

/** Live spreadsheet links (Google Sheets, OneDrive, any CSV URL), re-read on a schedule and merged. */
export default function RefSources({ onChanged, onCount }: { onChanged: (msg: string) => void; onCount?: (n: number) => void }) {
  const [items, setItems] = useState<RefSource[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ name: "", kind: "po" as RefKind, url: "", interval_min: 60 });
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [menu, setMenu] = useState<string | null>(null);

  const load = () =>
    api
      .refSources()
      .then((s) => {
        setItems(s);
        onCount?.(s.length);
      })
      .catch(() => setItems([]));
  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const add = async () => {
    setBusy("add");
    setErr(null);
    try {
      const s = await api.refAddSource(form);
      setAdding(false);
      setForm({ name: "", kind: "po", url: "", interval_min: 60 });
      onChanged(s.last_status === "error" ? `Added “${s.name}”, but the first sync failed: ${s.last_message}` : `Added “${s.name}”: ${s.last_message}`);
      load();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(null);
    }
  };

  const sync = async (s: RefSource) => {
    setBusy(s.id);
    try {
      const r = await api.refSyncSource(s.id);
      onChanged(r.last_status === "error" ? `Sync failed for “${s.name}”: ${r.last_message}` : `Synced “${s.name}”: ${r.last_message}`);
      load();
    } finally {
      setBusy(null);
    }
  };

  const remove = async (s: RefSource) => {
    setMenu(null);
    if (!confirm(`Stop syncing “${s.name}”? Data already imported stays.`)) return;
    await api.refRemoveSource(s.id);
    onChanged(`Removed source “${s.name}”`);
    load();
  };

  return (
    <div>
      <div className="flex items-center justify-end border-b border-slate-100 px-3 py-2">
        {!adding && (
          <button className="btn-outline" onClick={() => setAdding(true)}>
            <Plus className="h-4 w-4" /> Add source
          </button>
        )}
      </div>

      {adding && (
        <div className="border-b border-slate-100 bg-slate-50/60 px-4 py-3">
          <div className="flex flex-wrap items-end gap-2">
            <label className="text-xs text-slate-500">
              Name
              <input className="input mt-1 w-44" placeholder="AP PO tracker" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </label>
            <label className="text-xs text-slate-500">
              Contains
              <select className="input mt-1 w-40" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as RefKind })}>
                {Object.entries(KIND_LABEL).map(([k, l]) => (
                  <option key={k} value={k}>
                    {l}
                  </option>
                ))}
              </select>
            </label>
            <label className="min-w-[260px] flex-1 text-xs text-slate-500">
              Sheet link (Google Sheets, OneDrive or CSV URL)
              <input className="input mt-1" placeholder="https://docs.google.com/spreadsheets/d/…" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} />
            </label>
            <label className="text-xs text-slate-500">
              Every
              <select className="input mt-1 w-28" value={form.interval_min} onChange={(e) => setForm({ ...form, interval_min: Number(e.target.value) })}>
                {[15, 30, 60, 240, 1440].map((m) => (
                  <option key={m} value={m}>
                    {m < 60 ? `${m} min` : m === 1440 ? "day" : `${m / 60} h`}
                  </option>
                ))}
              </select>
            </label>
            <div className="flex gap-1.5">
              <button className="btn-ghost" onClick={() => setAdding(false)}>
                Cancel
              </button>
              <button className="btn-primary" onClick={add} disabled={busy === "add" || !form.name.trim() || !form.url.trim()}>
                {busy === "add" && <Loader2 className="h-4 w-4 animate-spin" />} Add and sync
              </button>
            </div>
          </div>
          {err && <div className="mt-2 text-xs text-rose-600">{err}</div>}
        </div>
      )}

      {!items ? (
        <div className="space-y-2 p-4">
          {[0, 1].map((i) => (
            <div key={i} className="skeleton h-10" />
          ))}
        </div>
      ) : items.length === 0 && !adding ? (
        <Empty icon={Link2} title="No live sources">
          Link the spreadsheet AP already maintains; it is re-read on a schedule and merged in.
        </Empty>
      ) : (
        <ul className="divide-y divide-slate-100">
          {items.map((s) => (
            <li key={s.id} className="flex flex-wrap items-center gap-3 px-4 py-2.5 text-[13px]">
              <span className={clsx("h-2 w-2 shrink-0 rounded-full", DOT[s.last_status || ""] || "bg-slate-300")} title={s.last_message || "Not synced yet"} />
              <div className="min-w-[180px]">
                <div className="font-medium text-slate-900">{s.name}</div>
                <div className="text-xs text-slate-500">
                  {KIND_LABEL[s.kind]} · every {s.interval_min < 60 ? `${s.interval_min} min` : `${s.interval_min / 60} h`}
                </div>
              </div>
              <span className="pill max-w-[320px] truncate" title={s.url}>
                {s.url}
              </span>
              <span className={clsx("min-w-0 flex-1 truncate text-xs", s.last_status === "error" ? "text-rose-600" : "text-slate-500")} title={s.last_message || ""}>
                {s.last_message || "Not synced yet"}
              </span>
              <span className="shrink-0 text-xs text-slate-400">{s.last_sync ? ago(s.last_sync) : "—"}</span>
              <button className="btn-ghost" onClick={() => sync(s)} disabled={busy === s.id}>
                <RefreshCw className={clsx("h-3.5 w-3.5", busy === s.id && "animate-spin")} /> Sync now
              </button>
              <div className="relative">
                <button className="btn-ghost w-8 px-0" onClick={() => setMenu(menu === s.id ? null : s.id)} aria-label="More">
                  <MoreHorizontal className="h-4 w-4" />
                </button>
                {menu === s.id && (
                  <div className="card absolute right-0 top-9 z-10 w-36 p-1">
                    <button className="btn-ghost w-full justify-start text-rose-600 hover:text-rose-700" onClick={() => remove(s)}>
                      Remove
                    </button>
                  </div>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
