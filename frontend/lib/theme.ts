/**
 * theme.ts
 * ========
 * Light / dark theming for the signed-in app.
 *
 * HOW IT WORKS
 * ------------
 * Every colour in globals.css is a CSS variable. The dark palette lives on
 * `:root`; the light palette overrides it under `html[data-theme="light"]`.
 * Switching theme is therefore a single attribute flip on <html> -- no React
 * re-render of the tree, no class soup on every component.
 *
 * THREE CHOICES, TWO RESULTS
 * --------------------------
 * The user picks "light", "dark" or "system". "system" follows the OS
 * `prefers-color-scheme` and keeps following it if the OS changes (macOS
 * auto-switching at sunset, for example). Whatever is picked RESOLVES to
 * light or dark, which is what lands in `data-theme`.
 *
 * NO FLASH ON LOAD
 * ----------------
 * React hydrates after the first paint, so setting the theme from an effect
 * would show one frame of the wrong palette -- the infamous white flash.
 * `THEME_INIT_SCRIPT` is a tiny inline script the root layout places in
 * <head>; it runs before the body paints and sets `data-theme` synchronously.
 *
 * STORAGE CAN THROW
 * -----------------
 * localStorage throws in Safari private mode and when site data is blocked,
 * so every read and write is wrapped. A failure just means "not remembered".
 */

export type ThemeChoice = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "docminds-theme";

/** The stored choice, or "system" when nothing (valid) is stored. */
export function readThemeChoice(): ThemeChoice {
  try {
    const value = localStorage.getItem(THEME_STORAGE_KEY);
    if (value === "light" || value === "dark" || value === "system") return value;
  } catch {
    /* storage unavailable: fall through to the default */
  }
  return "system";
}

export function writeThemeChoice(choice: ThemeChoice): void {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, choice);
  } catch {
    /* not remembered, but the switch still applies for this visit */
  }
}

/** What the OS prefers. Dark when it cannot be asked (dark is the brand default). */
export function systemTheme(): ResolvedTheme {
  if (typeof window === "undefined" || !window.matchMedia) return "dark";
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function resolveTheme(choice: ThemeChoice): ResolvedTheme {
  return choice === "system" ? systemTheme() : choice;
}

/** Put the resolved theme on <html>, where the CSS variables key off it. */
export function applyTheme(choice: ThemeChoice): ResolvedTheme {
  const resolved = resolveTheme(choice);
  document.documentElement.dataset.theme = resolved;
  // Tells the browser to draw native controls (scrollbars, date pickers) to match.
  document.documentElement.style.colorScheme = resolved;
  return resolved;
}

/**
 * The no-flash bootstrap, run inline in <head> before first paint.
 *
 * Deliberately a hand-written string rather than a serialised function: it
 * must be self-contained ES5 with no imports, and keeping the logic visible
 * here makes it easy to check it agrees with `resolveTheme` above.
 */
export const THEME_INIT_SCRIPT = `(function(){try{var c=localStorage.getItem("${THEME_STORAGE_KEY}");var d=c==="light"||c==="dark"?c:(window.matchMedia&&window.matchMedia("(prefers-color-scheme: light)").matches?"light":"dark");document.documentElement.dataset.theme=d;document.documentElement.style.colorScheme=d;}catch(e){document.documentElement.dataset.theme="dark";}})();`;
