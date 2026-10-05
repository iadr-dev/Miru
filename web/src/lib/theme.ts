/** Theme contract shared by the no-flash boot script and the client toggle. */
export const THEME_STORAGE_KEY = "miru-theme";

export const THEMES = ["dark", "light"] as const;
export type Theme = (typeof THEMES)[number];

export function isTheme(value: unknown): value is Theme {
  return value === "dark" || value === "light";
}

/**
 * Runs before paint, inlined in <head>, to set `data-theme` on <html> so the first server-painted
 * frame already matches the user's choice — no flash of the wrong theme. Persisted choice wins;
 * otherwise fall back to the OS `prefers-color-scheme`, defaulting to dark (dark
 * is primary). Kept dependency-free and stringified because it must run before React hydrates.
 */
export const NO_FLASH_THEME_SCRIPT = `(() => {
  try {
    var stored = localStorage.getItem(${JSON.stringify(THEME_STORAGE_KEY)});
    var theme = stored === "light" || stored === "dark"
      ? stored
      : (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
    document.documentElement.setAttribute("data-theme", theme);
  } catch (e) {
    document.documentElement.setAttribute("data-theme", "dark");
  }
})();`;
