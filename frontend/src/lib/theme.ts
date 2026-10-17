/** Theme = CSS custom properties on <html>; the chart reads them so both renderers agree. */

export type Theme = "light" | "dark";

const KEY = "dd-theme";

export function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    /* storage unavailable */
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyTheme(t: Theme): void {
  document.documentElement.dataset.theme = t;
  try {
    localStorage.setItem(KEY, t);
  } catch {
    /* ignore */
  }
}

/** Resolve a `--token` to its computed value (hex/rgba string). */
export function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export interface ChartColors {
  surface: string;
  ink: string;
  muted: string;
  grid: string;
  axis: string;
  price: string;
  volume: string;
  contract: string;
  up: string;
  down: string;
  upWash: string;
  downWash: string;
}

export function chartColors(): ChartColors {
  return {
    surface: token("--surface"),
    ink: token("--ink"),
    muted: token("--muted"),
    grid: token("--grid"),
    axis: token("--axis"),
    price: token("--ink"),
    volume: token("--axis"),
    contract: token("--contract"),
    up: token("--up"),
    down: token("--down"),
    upWash: token("--wash-up"),
    downWash: token("--wash-down"),
  };
}
