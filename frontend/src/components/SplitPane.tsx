import clsx from "clsx";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

/** Resizable two-pane layout (like ai24c's ResizablePanelGroup). Width is remembered per browser. */
export default function SplitPane({
  left,
  right,
  storageKey = "split",
  initial = 46,
  min = 28,
  max = 70,
  className,
}: {
  left: ReactNode;
  right: ReactNode;
  storageKey?: string;
  initial?: number;
  min?: number;
  max?: number;
  className?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [pct, setPct] = useState(() => {
    try {
      const v = Number(localStorage.getItem(`pane:${storageKey}`));
      return v >= min && v <= max ? v : initial;
    } catch {
      return initial;
    }
  });
  const [drag, setDrag] = useState(false);

  useEffect(() => {
    try {
      localStorage.setItem(`pane:${storageKey}`, String(Math.round(pct)));
    } catch {
      /* storage unavailable */
    }
  }, [pct, storageKey]);

  const move = useCallback(
    (clientX: number) => {
      const r = box.current?.getBoundingClientRect();
      if (!r) return;
      setPct(Math.min(max, Math.max(min, ((clientX - r.left) / r.width) * 100)));
    },
    [min, max],
  );

  useEffect(() => {
    if (!drag) return;
    const mm = (e: PointerEvent) => move(e.clientX);
    const up = () => setDrag(false);
    window.addEventListener("pointermove", mm);
    window.addEventListener("pointerup", up);
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    return () => {
      window.removeEventListener("pointermove", mm);
      window.removeEventListener("pointerup", up);
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
    };
  }, [drag, move]);

  return (
    <div ref={box} className={clsx("flex min-h-0 flex-col gap-4 lg:flex-row lg:gap-0", className)}>
      <div className="h-[70vh] min-h-0 shrink-0 lg:h-auto" style={{ flexBasis: `${pct}%` }}>
        {left}
      </div>
      <div
        role="separator"
        aria-orientation="vertical"
        aria-valuenow={Math.round(pct)}
        tabIndex={0}
        onPointerDown={() => setDrag(true)}
        onDoubleClick={() => setPct(initial)}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft") setPct((p) => Math.max(min, p - 2));
          if (e.key === "ArrowRight") setPct((p) => Math.min(max, p + 2));
        }}
        title="Drag to resize · double-click to reset"
        className="group hidden w-4 shrink-0 cursor-col-resize items-center justify-center lg:flex"
      >
        <div className={clsx("h-12 w-1 rounded-full transition", drag ? "bg-brand-500" : "bg-slate-300 group-hover:bg-brand-400")} />
      </div>
      <div className="min-h-0 min-w-0 flex-1">{right}</div>
    </div>
  );
}
