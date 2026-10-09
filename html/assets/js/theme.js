/**
 * Light / dark theme toggle.
 *
 * - Default: LIGHT (LangChain brand palette — see main.css :root).
 * - Theme persists in localStorage under `gbclaw.theme`.
 * - Applied as `data-theme="light" | "dark"` on <html> so CSS variables
 *   can swap palettes without flicker.
 * - The early-restore is an inline <script> in index.html (before CSS
 *   loads) to avoid a light/dark flash on first paint.
 */

export const THEME_STORAGE_KEY = "gbclaw.theme";
export const DEFAULT_THEME = "light";
const VALID_THEMES = ["light", "dark"];

/** Read saved theme, falling back to system preference, then DEFAULT_THEME. */
export function resolveInitialTheme() {
  try {
    const saved = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (saved && VALID_THEMES.includes(saved)) return saved;
  } catch {
    /* localStorage may be blocked — ignore */
  }
  try {
    if (window.matchMedia?.("(prefers-color-scheme: dark)").matches) {
      return "dark";
    }
  } catch {
    /* matchMedia unavailable */
  }
  return DEFAULT_THEME;
}

/** Apply the theme to <html> and persist to localStorage. */
export function applyTheme(theme, { persist = true } = {}) {
  const value = VALID_THEMES.includes(theme) ? theme : DEFAULT_THEME;
  document.documentElement.setAttribute("data-theme", value);
  if (persist) {
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, value);
    } catch {
      /* ignore */
    }
  }
  document.dispatchEvent(
    new CustomEvent("themechange", { detail: { theme: value } }),
  );
  return value;
}

/** Toggle between light and dark, returning the new value. */
export function toggleTheme() {
  const current = document.documentElement.getAttribute("data-theme") || DEFAULT_THEME;
  return applyTheme(current === "dark" ? "light" : "dark");
}

/**
 * Bind the toggle button (label, aria-pressed, click handler).
 * Returns a cleanup function.
 */
export function bindThemeToggle(button, labelEl) {
  const setLabel = (theme) => {
    if (labelEl) labelEl.textContent = theme === "dark" ? "Dark" : "Light";
    button?.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
  };

  const onChange = () => {
    const theme = document.documentElement.getAttribute("data-theme") || DEFAULT_THEME;
    setLabel(theme);
  };

  const onClick = () => {
    toggleTheme();
  };

  button?.addEventListener("click", onClick);
  document.addEventListener("themechange", onChange);
  onChange();

  return () => {
    button?.removeEventListener("click", onClick);
    document.removeEventListener("themechange", onChange);
  };
}