import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { flushSync } from "react-dom";
import { prefersReducedMotion } from "../lib/motion";

export type ThemePreference = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";
export type ThemeOrigin = { x: number; y: number };

// Keep in step with the no-flash script in index.html.
export const THEME_STORAGE_KEY = "packcomply-theme";
export const THEME_ORDER: ThemePreference[] = ["light", "dark", "system"];
const DARK_QUERY = "(prefers-color-scheme: dark)";
const REVEAL_MS = 500;
const FADE_MS = 200;

function readPreference(): ThemePreference {
  try {
    const saved = localStorage.getItem(THEME_STORAGE_KEY);
    return saved === "light" || saved === "dark" || saved === "system" ? saved : "system";
  } catch { return "system"; }
}

function systemTheme(): ResolvedTheme {
  return typeof window.matchMedia === "function" && window.matchMedia(DARK_QUERY).matches ? "dark" : "light";
}

export function resolveTheme(preference: ThemePreference): ResolvedTheme {
  return preference === "system" ? systemTheme() : preference;
}

export function applyTheme(theme: ResolvedTheme) {
  const root = document.documentElement;
  root.dataset.theme = theme;
  root.style.colorScheme = theme;
  // Browser chrome follows the theme's --background token.
  const background = getComputedStyle(root).getPropertyValue("--background").trim();
  if (background) document.querySelector('meta[name="theme-color"]')?.setAttribute("content", background);
}

type ViewTransitionDocument = Document & { startViewTransition?: (update: () => void) => { ready: Promise<void> } };

/**
 * Run `update` (which changes data-theme) with a circular reveal expanding from `origin`, or a
 * 200 ms cross-fade when the View Transitions API is missing, there is no origin, or motion is reduced.
 */
function transitionTheme(update: () => void, origin?: ThemeOrigin) {
  const doc = document as ViewTransitionDocument;
  const reduced = prefersReducedMotion();
  if (reduced) { update(); return; }
  if (!origin || typeof doc.startViewTransition !== "function") {
    const root = document.documentElement;
    root.classList.add("theme-fade");
    update();
    window.setTimeout(() => root.classList.remove("theme-fade"), FADE_MS);
    return;
  }
  const radius = Math.hypot(Math.max(origin.x, window.innerWidth - origin.x), Math.max(origin.y, window.innerHeight - origin.y));
  const transition = doc.startViewTransition(update);
  transition.ready.then(() => {
    document.documentElement.animate(
      { clipPath: [`circle(0px at ${origin.x}px ${origin.y}px)`, `circle(${radius}px at ${origin.x}px ${origin.y}px)`] },
      { duration: REVEAL_MS, easing: "cubic-bezier(0.22, 1, 0.36, 1)", pseudoElement: "::view-transition-new(root)" },
    );
  }).catch(() => undefined);
}

type ThemeValue = {
  preference: ThemePreference;
  resolved: ResolvedTheme;
  setPreference: (preference: ThemePreference, origin?: ThemeOrigin) => void;
  /** Light → Dark → System → Light. */
  cycle: (origin?: ThemeOrigin) => void;
};

const ThemeContext = createContext<ThemeValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>(readPreference);
  const [resolved, setResolved] = useState<ResolvedTheme>(() => resolveTheme(readPreference()));

  // Keep the document in sync (the inline script already set it before React mounted).
  useEffect(() => { applyTheme(resolved); }, [resolved]);

  // While on System, follow live OS changes.
  useEffect(() => {
    if (preference !== "system" || typeof window.matchMedia !== "function") return;
    const query = window.matchMedia(DARK_QUERY);
    const update = () => transitionTheme(() => { const next = query.matches ? "dark" : "light"; applyTheme(next); setResolved(next); });
    query.addEventListener?.("change", update);
    return () => query.removeEventListener?.("change", update);
  }, [preference]);

  // Pause ambient animation while the tab is hidden.
  useEffect(() => {
    const update = () => document.documentElement.classList.toggle("is-hidden", document.hidden);
    update();
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);

  const setPreference = useCallback((next: ThemePreference, origin?: ThemeOrigin) => {
    try { localStorage.setItem(THEME_STORAGE_KEY, next); } catch { /* storage unavailable */ }
    const nextResolved = resolveTheme(next);
    transitionTheme(() => {
      applyTheme(nextResolved);
      flushSync(() => { setPreferenceState(next); setResolved(nextResolved); });
    }, origin);
  }, []);

  const value = useMemo<ThemeValue>(() => ({
    preference, resolved, setPreference,
    cycle: origin => setPreference(THEME_ORDER[(THEME_ORDER.indexOf(preference) + 1) % THEME_ORDER.length], origin),
  }), [preference, resolved, setPreference]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeValue {
  const context = useContext(ThemeContext);
  if (!context) throw new Error("useTheme must be used inside ThemeProvider.");
  return context;
}

/** Centre of an element, used as the origin of the circular reveal. */
export function originOf(element: Element | null): ThemeOrigin | undefined {
  if (!element) return undefined;
  const rect = element.getBoundingClientRect();
  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
}
