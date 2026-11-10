import clsx from "clsx";
import { ArrowLeftRight, Check, Loader2, Search, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, fmtMoney, type PoDetail, type PoSummary, type Run } from "../api";

/**
 * Invoice vs purchase order, side by side: what the AP clerk used to do in Excel.
 * "Change PO" lets a person pick the PO by hand; the decision is re-run with that PO (audited).
 */
export default function PoMatch({ run, onRerun }: { run: Run; onRerun: (fields: Record<string, any>, note: string) => Promise<void> }) {
  const tw = run.stages.find((s) => s.key === "three_way");
  const pm = run.stages.find((s) => s.key === "po_match");
  const poNumber = run.decision_detail?.po || run.po_number || null;
  const source = run.decision_detail?.po_source;
  const [po, setPo] = useState<PoDetail | null>(null);
  const [picking, setPicking] = useState(false);
  const cur = run.currency || "USD";

  useEffect(() => {
    setPo(null);
    if (poNumber) api.refPo(poNumber).then(setPo).catch(() => setPo(null));
  }, [poNumber, run.id]);

  const canChange = run.status === "completed" && !run.ledger_posted && !run.superseded_by;
  const lines: any[] = tw?.data?.lines || [];
  const unbilled = po ? po.lines.filter((l) => !lines.some((x) => x.po_line === l.line)) : [];

  return (
    <div className="space-y-4">
      {/* PO header */}
      <div className="flex flex-wrap items-center gap-2">
        {poNumber ? (
          <>
            <span className="num text-[13px] font-semibold text-slate-900">{poNumber}</span>
            <span className={clsx("chip", source === "inferred" ? "bg-amber-50 text-amber-800" : "bg-slate-100 text-slate-600")}>
              {source === "inferred" ? "Inferred" : source === "explicit" ? "On invoice" : "Matched"}
            </span>
            {po && (
              <span className="text-xs text-slate-500">
                {po.vendor_name} · {po.status} · <span className="num">{fmtMoney(po.remaining, po.currency)}</span> of{" "}
                <span className="num">{fmtMoney(po.total, po.currency)}</span> left before this invoice
              </span>
            )}
          </>
        ) : (
          <span className="text-[13px] text-slate-600">No purchase order matched</span>
        )}
        <div className="flex-1" />
        {canChange && (
          <button className="btn-outline" onClick={() => setPicking(true)}>
            <ArrowLeftRight className="h-4 w-4" /> {poNumber ? "Change PO" : "Choose PO"}
          </button>
        )}
      </div>

      {picking && (
        <PoPicker
          vendorId={run.vendor_id || null}
          current={poNumber}
          onCancel={() => setPicking(false)}
          onPick={async (p) => {
            await onRerun({ po_number: p }, `PO chosen manually: ${p}`);
          }}
        />
      )}

      {/* PO inference scoring, when the vendor left the PO off */}
      {pm?.data?.candidates && (
        <div className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600 ring-1 ring-slate-200">
          Candidates scored:{" "}
          {pm.data.candidates.map((c: any, i: number) => (
            <span key={c.po_number} className="num">
              {i > 0 && " · "}
              {c.po_number} {c.score.toFixed(2)}
            </span>
          ))}
        </div>
      )}

      {/* side by side */}
      {lines.length > 0 ? (
        <div className="overflow-x-auto rounded-lg ring-1 ring-slate-200">
          <table className="tbl w-full">
            <thead>
              <tr>
                <th>Invoice line</th>
                <th>PO line</th>
                <th className="text-right">Qty</th>
                <th className="text-right" title="Ordered / received / billed before">Ord · Rcv · Billed</th>
                <th className="text-right">Price (PO)</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((l, i) => {
                const priceOff = l.price_variance_pct != null && Math.abs(l.price_variance_pct) > 0.001;
                const qtyOff = l.po_line && l.qty + (l.previously_billed || 0) > (l.received ?? 0);
                return (
                  <tr key={i} className={clsx(l.status === "fail" && "bg-rose-50/40")}>
                    <td className="max-w-[180px]">
                      <div className="truncate text-slate-800">{l.description}</div>
                      {l.sku && <div className="num text-[11px] text-slate-400">{l.sku}</div>}
                    </td>
                    <td className="max-w-[180px]">
                      {l.po_line ? (
                        <div className="truncate text-slate-600">
                          <span className="num text-slate-400">#{l.po_line}</span> {l.po_description}
                        </div>
                      ) : (
                        <span className="chip bg-rose-50 text-rose-700">Not on PO</span>
                      )}
                    </td>
                    <td className="num text-right">{l.qty}</td>
                    <td className="num whitespace-nowrap text-right text-slate-600">
                      {l.po_line ? (
                        <>
                          {l.po_qty} · <span className={clsx(qtyOff && "font-medium text-amber-700")}>{l.received}</span> · {l.previously_billed}
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="num whitespace-nowrap text-right">
                      <span className={clsx(priceOff ? "font-medium text-amber-700" : "text-slate-800")}>{fmtMoney(l.unit_price, cur)}</span>
                      {l.po_price != null && (
                        <div className="text-[11px] text-slate-500">
                          PO {fmtMoney(l.po_price, cur)}
                          {priceOff && <span className="text-amber-700"> · {l.price_variance_pct > 0 ? "+" : ""}{l.price_variance_pct}%</span>}
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
              {unbilled.map((l) => (
                <tr key={`po-${l.line}`} className="text-slate-400">
                  <td className="text-xs italic">not on this invoice</td>
                  <td className="max-w-[200px] truncate">
                    <span className="num">#{l.line}</span> {l.description}
                  </td>
                  <td className="text-right">—</td>
                  <td className="num whitespace-nowrap text-right">
                    {l.qty} · {l.received} · {l.billed_qty}
                  </td>
                  <td className="num whitespace-nowrap text-right">PO {fmtMoney(l.unit_price, cur)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        tw && <p className="text-[13px] text-slate-500">{tw.summary || "No line comparison for this run."}</p>
      )}

      {po && po.invoices.length > 0 && (
        <div className="text-xs text-slate-500">
          Already billed on this PO:{" "}
          {po.invoices.map((inv, i) => (
            <span key={inv.invoice_number} className="num">
              {i > 0 && ", "}
              {inv.invoice_number} ({fmtMoney(inv.subtotal, po.currency)}, {inv.status})
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function PoPicker({ vendorId, current, onPick, onCancel }: { vendorId: string | null; current: string | null; onPick: (po: string) => Promise<void>; onCancel: () => void }) {
  const [pos, setPos] = useState<PoSummary[] | null>(null);
  const [q, setQ] = useState("");
  const [all, setAll] = useState(!vendorId);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setPos(null);
    api.refPos(all ? null : vendorId).then(setPos).catch((e) => setErr(e.message));
  }, [all, vendorId]);

  const shown = useMemo(() => {
    const s = q.trim().toLowerCase();
    return (pos || []).filter((p) => !s || [p.po_number, p.vendor_name, ...p.lines.map((l) => l.description)].some((x) => String(x).toLowerCase().includes(s)));
  }, [pos, q]);

  return (
    <div className="rounded-lg ring-1 ring-brand-200">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-3 py-2">
        <div className="relative min-w-[180px] flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-2 h-4 w-4 text-slate-400" />
          <input className="input pl-8" autoFocus placeholder="Search PO number, vendor or item" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        {vendorId && (
          <label className="flex items-center gap-1.5 text-xs text-slate-600">
            <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} className="rounded border-slate-300" /> All vendors
          </label>
        )}
        <button className="btn-ghost w-8 px-0" onClick={onCancel} aria-label="Close">
          <X className="h-4 w-4" />
        </button>
      </div>
      {err && <div className="px-3 py-2 text-xs text-rose-600">{err}</div>}
      <ul className="max-h-64 divide-y divide-slate-100 overflow-y-auto">
        {!pos ? (
          <li className="p-3">
            <div className="skeleton h-8" />
          </li>
        ) : shown.length === 0 ? (
          <li className="px-3 py-3 text-[13px] text-slate-500">No purchase orders match.</li>
        ) : (
          shown.map((p) => (
            <li key={p.po_number} className="flex items-center gap-3 px-3 py-2 text-[13px]">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="num font-medium text-slate-900">{p.po_number}</span>
                  <span className={clsx("chip capitalize", p.status === "open" ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-500")}>{p.status}</span>
                  {all && <span className="truncate text-xs text-slate-500">{p.vendor_name}</span>}
                </div>
                <div className="truncate text-xs text-slate-500">{p.lines.map((l) => l.description).join(" · ")}</div>
              </div>
              <span className="num shrink-0 text-xs text-slate-500">{fmtMoney(p.remaining, p.currency)} left</span>
              {p.po_number === current ? (
                <span className="chip bg-slate-100 text-slate-500">
                  <Check className="h-3 w-3" /> Current
                </span>
              ) : (
                <button
                  className="btn-primary"
                  disabled={!!busy}
                  onClick={async () => {
                    setBusy(p.po_number);
                    setErr(null);
                    try {
                      await onPick(p.po_number);
                    } catch (e: any) {
                      setErr(e.message);
                      setBusy(null);
                    }
                  }}
                >
                  {busy === p.po_number && <Loader2 className="h-4 w-4 animate-spin" />} Use & re-run
                </button>
              )}
            </li>
          ))
        )}
      </ul>
    </div>
  );
}
