import clsx from "clsx";
import { Download, Eye, EyeOff, Maximize2, ZoomIn, ZoomOut } from "lucide-react";
import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Box } from "../api";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

export interface Region {
  key: string;
  label: string;
  box: Box;
  tone?: "brand" | "amber" | "rose";
}

interface Props {
  url: string;
  regions: Region[];
  active: string | null;
  onRegionClick?: (key: string) => void;
  downloadName?: string;
}

const GAP = 16; // px between pages
const PAD = 16; // px around pages

/**
 * PDF on the left, ai24c-style: every page rendered with pdf.js, field regions drawn on top,
 * and the selected field scrolled into view and highlighted.
 */
export default function PdfViewer({ url, regions, active, onRegionClick, downloadName }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const [doc, setDoc] = useState<pdfjs.PDFDocumentProxy | null>(null);
  const [sizes, setSizes] = useState<{ w: number; h: number }[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [fitWidth, setFitWidth] = useState(600);
  const [zoom, setZoom] = useState(1); // multiplier on top of fit-to-width
  const [page, setPage] = useState(1);
  const [showAll, setShowAll] = useState(true);

  // load document
  useEffect(() => {
    let cancelled = false;
    setDoc(null);
    setErr(null);
    const task = pdfjs.getDocument({ url });
    task.promise
      .then(async (d) => {
        const s = [];
        for (let i = 1; i <= d.numPages; i++) {
          const vp = (await d.getPage(i)).getViewport({ scale: 1 });
          s.push({ w: vp.width, h: vp.height });
        }
        if (!cancelled) {
          setSizes(s);
          setDoc(d);
        }
      })
      .catch((e) => !cancelled && setErr(String(e?.message || e)));
    return () => {
      cancelled = true;
      task.destroy();
    };
  }, [url]);

  // fit to container width
  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setFitWidth(Math.max(240, el.clientWidth - PAD * 2)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const maxW = Math.max(1, ...sizes.map((s) => s.w));
  const scale = (fitWidth / maxW) * zoom;
  const offsets = sizes.reduce<number[]>((acc, s, i) => [...acc, i === 0 ? PAD : acc[i - 1] + sizes[i - 1].h * scale + GAP], []);

  // current page from scroll position
  const onScroll = useCallback(() => {
    const el = scroller.current;
    if (!el || !offsets.length) return;
    const mid = el.scrollTop + el.clientHeight / 3;
    let p = 1;
    offsets.forEach((o, i) => {
      if (mid >= o) p = i + 1;
    });
    setPage(p);
  }, [offsets]);

  // sync scroll: bring the active region into view
  const activeRegion = regions.find((r) => r.key === active);
  useEffect(() => {
    const el = scroller.current;
    if (!el || !activeRegion || !sizes.length) return;
    const i = activeRegion.box.page - 1;
    if (!sizes[i]) return;
    const top = offsets[i] + activeRegion.box.y0 * sizes[i].h * scale;
    const bottom = offsets[i] + activeRegion.box.y1 * sizes[i].h * scale;
    if (top < el.scrollTop + 24 || bottom > el.scrollTop + el.clientHeight - 24) {
      el.scrollTo({ top: Math.max(0, top - el.clientHeight * 0.3), behavior: "smooth" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, activeRegion?.box.page, scale, sizes.length]);

  const goTo = (p: number) => {
    const el = scroller.current;
    if (el && offsets[p - 1] != null) el.scrollTo({ top: offsets[p - 1] - PAD, behavior: "smooth" });
  };

  return (
    <div className="relative flex h-full min-h-0 flex-col overflow-hidden rounded-xl border border-slate-200 bg-slate-100">
      {/* toolbar */}
      <div className="glass absolute left-1/2 top-3 z-20 flex -translate-x-1/2 items-center gap-1 rounded-full px-2 py-1 text-xs text-slate-600">
        <button className="rounded-full px-2 py-1 hover:bg-slate-100 disabled:opacity-40" disabled={page <= 1} onClick={() => goTo(page - 1)} aria-label="Previous page">
          ‹
        </button>
        <span className="min-w-[4.5rem] text-center font-medium num">
          {sizes.length ? `${page} / ${sizes.length}` : "–"}
        </span>
        <button className="rounded-full px-2 py-1 hover:bg-slate-100 disabled:opacity-40" disabled={page >= sizes.length} onClick={() => goTo(page + 1)} aria-label="Next page">
          ›
        </button>
        <span className="mx-1 h-4 w-px bg-slate-300" />
        <button className="rounded-full p-1.5 hover:bg-slate-100" onClick={() => setZoom((z) => Math.max(0.5, +(z - 0.15).toFixed(2)))} aria-label="Zoom out">
          <ZoomOut className="h-3.5 w-3.5" />
        </button>
        <span className="w-10 text-center num">{Math.round(zoom * 100)}%</span>
        <button className="rounded-full p-1.5 hover:bg-slate-100" onClick={() => setZoom((z) => Math.min(3, +(z + 0.15).toFixed(2)))} aria-label="Zoom in">
          <ZoomIn className="h-3.5 w-3.5" />
        </button>
        <button className="rounded-full p-1.5 hover:bg-slate-100" onClick={() => setZoom(1)} title="Fit width" aria-label="Fit width">
          <Maximize2 className="h-3.5 w-3.5" />
        </button>
        <span className="mx-1 h-4 w-px bg-slate-300" />
        <button
          className={clsx("flex items-center gap-1 rounded-full px-2 py-1 hover:bg-slate-100", showAll && "text-brand-700")}
          onClick={() => setShowAll((v) => !v)}
          title="Show every extracted field on the page"
        >
          {showAll ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
          <span className="hidden sm:inline">Fields</span>
        </button>
        <a className="rounded-full p-1.5 hover:bg-slate-100" href={url} download={downloadName} title="Download PDF" aria-label="Download PDF">
          <Download className="h-3.5 w-3.5" />
        </a>
      </div>

      <div ref={scroller} onScroll={onScroll} className="relative min-h-0 flex-1 overflow-auto">
        {err && <div className="m-6 rounded-xl bg-rose-50 p-4 text-[13px] text-rose-700">Could not render PDF: {err}</div>}
        {!doc && !err && (
          <div className="flex h-full items-start justify-center p-4">
            <div className="skeleton aspect-[1/1.414] w-full max-w-[640px] bg-white" />
          </div>
        )}
        {doc && (
          <div className="relative mx-auto" style={{ width: maxW * scale + PAD * 2, height: (offsets.at(-1) ?? 0) + (sizes.at(-1)?.h ?? 0) * scale + PAD }}>
            {sizes.map((s, i) => (
              <div
                key={i}
                className="absolute overflow-hidden rounded-sm shadow-[0_1px_3px_rgba(0,0,0,.12),0_4px_16px_rgba(0,0,0,.06)]"
                style={{ top: offsets[i], left: PAD + (maxW - s.w) * scale * 0.5, width: s.w * scale, height: s.h * scale }}
              >
                <PageCanvas doc={doc} pageNumber={i + 1} scale={scale} />
                {regions
                  .filter((r) => r.box.page === i + 1 && (showAll || r.key === active))
                  .map((r) => {
                    const on = r.key === active;
                    const tone = r.tone || "brand";
                    return (
                      <button
                        key={r.key}
                        onClick={() => onRegionClick?.(r.key)}
                        title={r.label}
                        className={clsx("group absolute rounded-[3px] border transition-all duration-200",
                          on
                            ? tone === "rose"
                              ? "z-10 border-2 border-rose-500 bg-rose-500/20 ring-4 ring-rose-500/20"
                              : tone === "amber"
                                ? "z-10 border-2 border-amber-500 bg-amber-500/20 ring-4 ring-amber-500/20"
                                : "z-10 border-2 border-brand-500 bg-brand-500/20 ring-4 ring-brand-500/20"
                            : tone === "rose"
                              ? "border-rose-400/60 bg-rose-400/5 hover:bg-rose-400/15"
                              : tone === "amber"
                                ? "border-amber-400/60 bg-amber-400/5 hover:bg-amber-400/15"
                                : "border-brand-400/50 bg-brand-400/5 hover:bg-brand-400/15",
                        )}
                        style={{
                          left: `calc(${r.box.x0 * 100}% - 3px)`,
                          top: `calc(${r.box.y0 * 100}% - 2px)`,
                          width: `calc(${(r.box.x1 - r.box.x0) * 100}% + 6px)`,
                          height: `calc(${(r.box.y1 - r.box.y0) * 100}% + 4px)`,
                        }}
                      >
                        {on && (
                          <span
                            className={clsx("absolute -top-6 left-0 whitespace-nowrap rounded-md px-1.5 py-0.5 text-[10px] font-semibold text-white shadow",
                              tone === "rose" ? "bg-rose-600" : tone === "amber" ? "bg-amber-600" : "bg-brand-600",
                            )}
                          >
                            {r.label}
                          </span>
                        )}
                      </button>
                    );
                  })}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

const PageCanvas = memo(function PageCanvas({ doc, pageNumber, scale }: { doc: pdfjs.PDFDocumentProxy; pageNumber: number; scale: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    let task: pdfjs.RenderTask | null = null;
    let cancelled = false;
    const t = setTimeout(async () => {
      const page = await doc.getPage(pageNumber);
      if (cancelled || !ref.current) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const vp = page.getViewport({ scale: scale * dpr });
      const c = ref.current;
      c.width = Math.floor(vp.width);
      c.height = Math.floor(vp.height);
      task = page.render({ canvasContext: c.getContext("2d")!, viewport: vp });
      task.promise.catch(() => {});
    }, 60); // debounce while resizing / zooming
    return () => {
      cancelled = true;
      clearTimeout(t);
      task?.cancel();
    };
  }, [doc, pageNumber, scale]);
  return <canvas ref={ref} className="block h-full w-full bg-[#fff]" />;
});
