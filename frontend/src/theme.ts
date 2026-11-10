import { useEffect, useState } from "react";

export type Accent = "green" | "blue" | "teal" | "violet" | "rose" | "orange";
export type Mode = "light" | "dark" | "system";

// swatch = brand-500 of each accent, used for the picker and favicon
export const ACCENTS: { id: Accent; label: string; swatch: string }[] = [
  { id: "green", label: "Green", swatch: "#10b981" },
  { id: "blue", label: "Blue", swatch: "#3b82f6" },
  { id: "teal", label: "Teal", swatch: "#14b8a6" },
  { id: "violet", label: "Violet", swatch: "#8b5cf6" },
  { id: "rose", label: "Red", swatch: "#f43f5e" },
  { id: "orange", label: "Orange", swatch: "#f97316" },
];

const KEY = "ap-theme";
const media = () => window.matchMedia("(prefers-color-scheme: dark)");

function read(): { accent: Accent; mode: Mode } {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || "{}");
    return { accent: ACCENTS.some((a) => a.id === v.accent) ? v.accent : "green", mode: ["light", "dark", "system"].includes(v.mode) ? v.mode : "light" };
  } catch {
    return { accent: "green", mode: "light" };
  }
}

function apply(accent: Accent, mode: Mode) {
  const root = document.documentElement;
  root.dataset.accent = accent;
  root.classList.toggle("dark", mode === "dark" || (mode === "system" && media().matches));
  const swatch = ACCENTS.find((a) => a.id === accent)!.swatch.replace("#", "%23");
  const icon = document.querySelector<HTMLLinkElement>("link[rel=icon]");
  if (icon)
    icon.href = `data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='9' fill='${swatch}'/><path d='M9 11h14l-14 10h14' stroke='white' stroke-width='3' fill='none' stroke-linecap='round' stroke-linejoin='round'/></svg>`;
}

export function useTheme() {
  const [state, setState] = useState(read);

  useEffect(() => {
    apply(state.accent, state.mode);
    try {
      localStorage.setItem(KEY, JSON.stringify(state));
    } catch {}
    if (state.mode !== "system") return;
    const m = media();
    const onChange = () => apply(state.accent, state.mode);
    m.addEventListener("change", onChange);
    return () => m.removeEventListener("change", onChange);
  }, [state]);

  return {
    ...state,
    setAccent: (accent: Accent) => setState((s) => ({ ...s, accent })),
    setMode: (mode: Mode) => setState((s) => ({ ...s, mode })),
  };
}
