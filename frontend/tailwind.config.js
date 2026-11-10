/** @type {import('tailwindcss').Config} */

// Every themed palette resolves to CSS variables defined in src/theme.css, so accent and
// light/dark switch at runtime without touching component classes.
const shades = ["50", "100", "200", "300", "400", "500", "600", "700", "800", "900", "950"];
const themed = (name) => Object.fromEntries(shades.map((s) => [s, `rgb(var(--${name}-${s}) / <alpha-value>)`]));

export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: { sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"], mono: ["JetBrains Mono", "ui-monospace", "monospace"] },
      colors: {
        white: "rgb(var(--surface) / <alpha-value>)",
        page: "rgb(var(--page) / <alpha-value>)",
        brand: themed("brand"),
        slate: themed("slate"),
        emerald: themed("emerald"),
        amber: themed("amber"),
        rose: themed("rose"),
        sky: themed("sky"),
        violet: themed("violet"),
        orange: themed("orange"),
      },
      keyframes: {
        pulsering: { "0%": { boxShadow: "0 0 0 0 rgb(var(--brand-500) / .45)" }, "100%": { boxShadow: "0 0 0 10px rgb(var(--brand-500) / 0)" } },
        fadein: { "0%": { opacity: 0, transform: "translateY(4px)" }, "100%": { opacity: 1, transform: "none" } },
        popin: { "0%": { opacity: 0, transform: "translateY(-4px) scale(.98)" }, "100%": { opacity: 1, transform: "none" } },
      },
      animation: {
        pulsering: "pulsering 1.2s ease-out infinite",
        fadein: "fadein .3s ease-out both",
        popin: "popin .16s ease-out both",
      },
    },
  },
  plugins: [],
};
