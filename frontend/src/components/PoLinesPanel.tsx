import clsx from "clsx";
import { Check, PackagePlus, Pencil, X } from "lucide-react";
import { useState } from "react";
import { api, fmtMoney, type PoLine } from "../api";

/** Expanded PO row: line items with inline edit (hover pencil) and an "Add goods receipt" form. */
export default function PoLinesPanel({ po, onChanged }: { po: { po_number: string; currency?: string; lines: PoLine[] }; onChanged: (msg: string) => void }) {
  const [edit, setEdit] = useState<number | null>(null);
  const [draft, setDraft] = useState<PoLine | null>(null);
  const [receiving, setReceiving] = useState(false);
  const [rec, setRec] = useState<Record<number, string>>({});
  const [grn, setGrn] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const cur = po.currency || "USD";

  const saveLine = async () => {
    if (!draft) return;
    setBusy(true);
    setErr(null);
    try {
      const lines = po.lines.map((l) =>
        l.line === draft.line ? { line: l.line, sku: draft.sku, description: draft.description, qty: Number(draft.qty), unit_price: Number(draft.unit_price) } : { line: l.line, sku: l.sku, description: l.description, qty: l.qty, unit_price: l.unit_price },
      );
      await api.refUpdatePo(po.po_number, { lines });
      setEdit(null);
      onChanged(`Updated ${po.po_number} line ${draft.line}`);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const saveReceipt = async () => {
    setBusy(true);
    setErr(null);
    try {
      const lines = Object.entries(rec)
        .filter(([, v]) => v.trim() !== "" && Number(v) > 0)
        .map(([k, v]) => ({ line: Number(k), qty_received: Number(v) }));
      await api.refAddReceipt(po.po_number, { lines, grn: grn.trim() || undefined });
      setReceiving(false);
      setRec({});
      setGrn("");
      onChanged(`Recorded goods receipt on ${po.po_number} (${lines.length} line${lines.length === 1 ? "" : "s"})`);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="text-left text-xs text-slate-500">
            <th className="w-8 pb-1.5 font-medium">#</th>
            <th className="pb-1.5 font-medium">Item</th>
            <th className="pb-1.5 font-medium">SKU</th>
            <th className="pb-1.5 text-right font-medium">Ordered</th>
            <th className="pb-1.5 text-right font-medium">Received</th>
            <th className="pb-1.5 text-right font-medium">Unit price</th>
            <th className="pb-1.5 text-right font-medium">Line total</th>
            <th className="w-20 pb-1.5" />
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200/70">
          {po.lines.map((l) => {
            const on = edit === l.line && draft;
            const received = l.received ?? 0;
            return (
              <tr key={l.line} className="group">
                <td className="num py-1.5 text-slate-400">{l.line}</td>
                {on ? (
                  <>
                    <td className="py-1 pr-2">
                      <input className="input h-7" value={draft!.description} onChange={(e) => setDraft({ ...draft!, description: e.target.value })} aria-label="Description" />
                    </td>
                    <td className="py-1 pr-2">
                      <input className="input num h-7 w-28" value={draft!.sku || ""} onChange={(e) => setDraft({ ...draft!, sku: e.target.value })} aria-label="SKU" />
                    </td>
                    <td className="py-1 pr-2 text-right">
                      <input className="input num h-7 w-20 text-right" type="number" min={0} step="any" value={draft!.qty} onChange={(e) => setDraft({ ...draft!, qty: e.target.value as any })} aria-label="Ordered quantity" />
                    </td>
                    <td className="num py-1.5 text-right text-slate-500">{received}</td>
                    <td className="py-1 pr-2 text-right">
                      <input className="input num h-7 w-24 text-right" type="number" min={0} step="any" value={draft!.unit_price} onChange={(e) => setDraft({ ...draft!, unit_price: e.target.value as any })} aria-label="Unit price" />
                    </td>
                    <td className="num py-1.5 text-right text-slate-900">{fmtMoney(Number(draft!.qty) * Number(draft!.unit_price), cur)}</td>
                    <td className="py-1 text-right">
                      <div className="flex justify-end gap-0.5">
                        <button className="btn-ghost h-7 w-7 px-0 text-emerald-700" onClick={saveLine} disabled={busy} aria-label="Save line">
                          <Check className="h-4 w-4" />
                        </button>
                        <button className="btn-ghost h-7 w-7 px-0" onClick={() => setEdit(null)} aria-label="Cancel">
                          <X className="h-4 w-4" />
                        </button>
                      </div>
                    </td>
                  </>
                ) : (
                  <>
                    <td className="py-1.5 text-slate-800">{l.description}</td>
                    <td className="num py-1.5 text-xs text-slate-500">{l.sku}</td>
                    <td className="num py-1.5 text-right">{l.qty}</td>
                    <td className={clsx("num py-1.5 text-right", received < l.qty && "font-medium text-amber-700")}>{received}</td>
                    <td className="num py-1.5 text-right">{fmtMoney(l.unit_price, cur)}</td>
                    <td className="num py-1.5 text-right text-slate-900">{fmtMoney(l.unit_price * l.qty, cur)}</td>
                    <td className="py-1 text-right">
                      <button
                        className="btn-ghost h-7 w-7 px-0 opacity-0 transition group-hover:opacity-100 focus:opacity-100"
                        onClick={() => {
                          setEdit(l.line);
                          setDraft({ ...l });
                          setErr(null);
                        }}
                        aria-label={`Edit line ${l.line}`}
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </button>
                    </td>
                  </>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>

      {err && <div className="rounded-md bg-rose-50 px-2.5 py-1.5 text-xs text-rose-700">{err}</div>}

      {receiving ? (
        <div className="flex flex-wrap items-end gap-2 rounded-lg bg-white p-2.5 ring-1 ring-slate-200">
          <label className="text-xs text-slate-500">
            Receipt no.
            <input className="input num mt-1 h-7 w-32" placeholder="auto" value={grn} onChange={(e) => setGrn(e.target.value)} />
          </label>
          {po.lines.map((l) => (
            <label key={l.line} className="text-xs text-slate-500">
              Line {l.line} received
              <input
                className="input num mt-1 h-7 w-24 text-right"
                type="number"
                min={0}
                step="any"
                placeholder={String(Math.max(0, l.qty - (l.received ?? 0)))}
                value={rec[l.line] ?? ""}
                onChange={(e) => setRec({ ...rec, [l.line]: e.target.value })}
              />
            </label>
          ))}
          <div className="ml-auto flex gap-1.5">
            <button className="btn-ghost" onClick={() => setReceiving(false)}>
              Cancel
            </button>
            <button className="btn-primary" onClick={saveReceipt} disabled={busy || !Object.values(rec).some((v) => Number(v) > 0)}>
              Save receipt
            </button>
          </div>
        </div>
      ) : (
        <button className="btn-ghost" onClick={() => setReceiving(true)}>
          <PackagePlus className="h-4 w-4" /> Add goods receipt
        </button>
      )}
    </div>
  );
}
