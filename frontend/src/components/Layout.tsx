import clsx from "clsx";
import { BookOpen, Check, Inbox, LayoutDashboard, Mail, Monitor, Moon, Palette, SlidersHorizontal, Sun, UploadCloud } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { api } from "../api";
import { ACCENTS, useTheme, type Mode } from "../theme";

type BadgeKey = "review" | "inbox";

const nav: { to: string; label: string; icon: any; end?: boolean; badge?: BadgeKey }[] = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/intake", label: "Intake", icon: Mail, badge: "inbox" },
  { to: "/new", label: "Process invoice", icon: UploadCloud },
  { to: "/review", label: "Review queue", icon: Inbox, badge: "review" },
  { to: "/reference", label: "Procurement data", icon: BookOpen },
  { to: "/settings", label: "Rules & settings", icon: SlidersHorizontal },
];

const modes: { id: Mode; label: string; icon: any }[] = [
  { id: "light", label: "Light", icon: Sun },
  { id: "dark", label: "Dark", icon: Moon },
  { id: "system", label: "System", icon: Monitor },
];

function LogoMark() {
  return (
    <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-brand-600 text-white shadow-sm">
      <svg viewBox="0 0 32 32" className="h-5 w-5">
        <path d="M9 11h14l-14 10h14" stroke="white" strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </div>
  );
}

function ThemeMenu() {
  const theme = useTheme();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const swatch = ACCENTS.find((a) => a.id === theme.accent)!.swatch;

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-label="Theme settings"
        aria-expanded={open}
        className="glass relative grid h-10 w-10 place-items-center rounded-full text-slate-600 transition hover:text-slate-900"
      >
        <Palette className="h-[18px] w-[18px]" />
        <span className="absolute bottom-2 right-2 h-2 w-2 rounded-full ring-2 ring-white" style={{ background: swatch }} />
      </button>

      {open && (
        <div className="glass absolute right-0 top-12 z-40 w-[18.5rem] origin-top-right animate-popin rounded-2xl p-3">
          <div className="label px-1">Appearance</div>
          <div className="mt-2 grid grid-cols-3 gap-1 rounded-xl bg-slate-100/80 p-1">
            {modes.map((m) => (
              <button
                key={m.id}
                onClick={() => theme.setMode(m.id)}
                className={clsx(
                  "flex items-center justify-center gap-1.5 rounded-lg py-1.5 text-xs font-medium transition",
                  theme.mode === m.id ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-800",
                )}
              >
                <m.icon className="h-3.5 w-3.5" />
                {m.label}
              </button>
            ))}
          </div>

          <div className="label mt-4 px-1">Accent colour</div>
          <div className="mt-2 grid grid-cols-3 gap-1.5">
            {ACCENTS.map((a) => {
              const active = theme.accent === a.id;
              return (
                <button
                  key={a.id}
                  onClick={() => theme.setAccent(a.id)}
                  className={clsx(
                    "flex items-center gap-2 rounded-xl border px-2.5 py-2 text-xs font-medium transition",
                    active ? "border-brand-500 bg-brand-50 text-brand-700 ring-2 ring-brand-500/15" : "border-slate-200 bg-white/60 text-slate-600 hover:border-slate-300",
                  )}
                >
                  <span className="grid h-4 w-4 shrink-0 place-items-center rounded-full" style={{ background: a.swatch }}>
                    {active && <Check className="h-3 w-3 text-white" strokeWidth={3} />}
                  </span>
                  {a.label}
                </button>
              );
            })}
          </div>

        </div>
      )}
    </div>
  );
}

export default function Layout() {
  const [counts, setCounts] = useState<Record<BadgeKey, number>>({ review: 0, inbox: 0 });
  // run pages fill the viewport exactly (no page scroll)
  const fullBleed = useLocation().pathname.startsWith("/runs/");

  useEffect(() => {
    const load = () => {
      api.stats().then((s) => setCounts((c) => ({ ...c, review: s.pending_review ?? 0 }))).catch(() => {});
      fetch("/api/inbox/stats")
        .then((r) => (r.ok ? r.json() : null))
        .then((s) => s && setCounts((c) => ({ ...c, inbox: s.unprocessed ?? 0 })))
        .catch(() => {});
    };
    load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-30 pb-2 pt-3">
        {/* frosted backdrop so scrolled content never shows through the logo/side gaps; fades out at the bottom */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 -z-10 backdrop-blur-xl [mask-image:linear-gradient(to_bottom,black_75%,transparent)]"
          style={{ background: "rgb(var(--page) / .85)" }}
        />
        <div className="mx-auto grid max-w-[1600px] grid-cols-[1fr_auto_1fr] items-center gap-3 px-4 md:px-6">
          <Link to="/" className="flex items-center gap-2.5 justify-self-start">
            <LogoMark />
            <span className="hidden text-[15px] font-semibold tracking-tight text-slate-900 sm:block">AMP Autopilot</span>
          </Link>

          <nav className="glass flex max-w-[calc(100vw-8rem)] items-center gap-0.5 overflow-x-auto rounded-full p-1 [scrollbar-width:none]">
            {nav.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.end}
                title={n.label}
                className={({ isActive }) =>
                  clsx(
                    "relative flex h-9 shrink-0 items-center gap-2 rounded-full px-3 text-[13px] font-medium transition-colors duration-150",
                    isActive ? "bg-brand-600 text-white shadow-sm" : "text-slate-600 hover:bg-slate-100/80 hover:text-slate-900",
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    <n.icon className="h-4 w-4" />
                    <span className={clsx("hidden 2xl:inline", isActive && "md:inline")}>{n.label}</span>
                    {n.badge && counts[n.badge] > 0 && (
                      <span
                        className={clsx(
                          "min-w-[1.25rem] rounded-full px-1.5 text-center text-[11px] font-semibold leading-5",
                          isActive ? "bg-white/25 text-white" : "bg-amber-100 text-amber-800",
                          !isActive && "max-2xl:absolute max-2xl:-right-0.5 max-2xl:-top-0.5 max-2xl:min-w-[1rem] max-2xl:px-1 max-2xl:text-[10px] max-2xl:leading-4",
                        )}
                      >
                        {counts[n.badge]}
                      </span>
                    )}
                  </>
                )}
              </NavLink>
            ))}
          </nav>

          <div className="flex items-center gap-2 justify-self-end">
            <ThemeMenu />
          </div>
        </div>
      </header>

      <main className={clsx("mx-auto max-w-[1600px] px-4 pt-2 md:px-6", fullBleed ? "pb-0" : "pb-8")}>
        <Outlet />
      </main>
    </div>
  );
}
