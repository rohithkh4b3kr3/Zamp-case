import { X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ago, api } from "../api";

const LABEL: Record<string, string> = {
  ref_import_po: "Purchase orders imported",
  ref_import_grn: "Goods receipts imported",
  ref_import_vendor: "Vendors imported",
  ref_po_updated: "PO edited",
  ref_grn_added: "Goods receipt added",
  ref_source_added: "Live source added",
  ref_source_removed: "Live source removed",
};

/** Slide-over change log for procurement data (imports, syncs, edits). */
export default function RefHistory({ onClose }: { onClose: () => void }) {
  const [items, setItems] = useState<Awaited<ReturnType<typeof api.refHistory>> | null>(null);
  const panel = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    api.refHistory().then(setItems).catch(() => setItems([]));
    panel.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && closeRef.current();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  return createPortal(
    <div className="fixed inset-0 z-50 scrim backdrop-blur-[2px]" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside ref={panel} tabIndex={-1} role="dialog" aria-label="Change history" className="absolute inset-y-0 right-0 flex w-full max-w-md flex-col bg-white shadow-xl outline-none animate-fadein">
        <div className="flex h-12 items-center justify-between border-b border-slate-100 px-4">
          <h2 className="text-sm font-semibold text-slate-900">History</h2>
          <button className="btn-ghost w-8 px-0" onClick={onClose} aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {!items ? (
            <div className="space-y-3">
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="skeleton h-12" />
              ))}
            </div>
          ) : items.length === 0 ? (
            <p className="text-[13px] text-slate-500">No imports or edits yet.</p>
          ) : (
            <ol className="space-y-3">
              {items.map((h) => (
                <li key={h.id} className="rounded-lg px-3 py-2 ring-1 ring-slate-200">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[13px] font-medium text-slate-800">{LABEL[h.action] || h.action.replace(/_/g, " ")}</span>
                    <span className="shrink-0 text-xs text-slate-400" title={h.ts}>
                      {ago(h.ts)} · {h.actor}
                    </span>
                  </div>
                  <p className="mt-0.5 break-words text-xs leading-relaxed text-slate-600">{h.detail}</p>
                </li>
              ))}
            </ol>
          )}
        </div>
      </aside>
    </div>,
    document.body,
  );
}
