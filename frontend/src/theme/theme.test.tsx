import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import en from "../locales/en.json";
import { I18nProvider } from "../i18n";
import { ThemeSwitcher, ThemeToggle } from "../app/ThemeControls";
import { THEME_STORAGE_KEY, ThemeProvider } from "./theme";

type Listener = () => void;

/** matchMedia stub: the OS colour scheme and reduced-motion flags can be flipped live. */
function mockMatchMedia({ dark = false, reduce = false } = {}) {
  const state = { dark, reduce };
  const listeners = new Set<Listener>();
  const matchMedia = vi.fn((query: string) => ({
    get matches() { return query.includes("prefers-color-scheme: dark") ? state.dark : query.includes("prefers-reduced-motion") ? state.reduce : false; },
    media: query,
    addEventListener: (_: string, listener: Listener) => { if (query.includes("color-scheme")) listeners.add(listener); },
    removeEventListener: (_: string, listener: Listener) => listeners.delete(listener),
  }));
  vi.stubGlobal("matchMedia", matchMedia);
  return { setDark(value: boolean) { state.dark = value; listeners.forEach(listener => listener()); } };
}

const renderToggle = () => render(<I18nProvider><ThemeProvider><ThemeToggle /><ThemeSwitcher /></ThemeProvider></I18nProvider>);
const toggle = () => screen.getByRole("button", { name: new RegExp(en.themeToggle) });
const theme = () => document.documentElement.dataset.theme;

describe("theme", () => {
  beforeEach(() => { localStorage.clear(); delete document.documentElement.dataset.theme; document.documentElement.classList.remove("theme-fade"); });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); delete (document as { startViewTransition?: unknown }).startViewTransition; });

  it("defaults to System, then cycles Light → Dark → System and persists each choice", () => {
    mockMatchMedia({ dark: true });
    renderToggle();
    expect(toggle().getAttribute("aria-label")).toBe(`${en.themeToggle}: ${en.themeSystem}`);
    expect(theme()).toBe("dark");

    fireEvent.click(toggle());
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    expect(theme()).toBe("light");
    expect(toggle().getAttribute("aria-label")).toBe(`${en.themeToggle}: ${en.themeLight}`);

    fireEvent.click(toggle());
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(theme()).toBe("dark");
    expect(document.documentElement.style.colorScheme).toBe("dark");

    fireEvent.click(toggle());
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("system");
    expect(screen.getByRole("radio", { name: en.themeSystem }).getAttribute("aria-checked")).toBe("true");
  });

  it("restores a saved choice and offers it in the user-menu switcher", () => {
    mockMatchMedia({ dark: true });
    localStorage.setItem(THEME_STORAGE_KEY, "light");
    renderToggle();
    expect(theme()).toBe("light");
    fireEvent.click(screen.getByRole("radio", { name: en.themeDark }));
    expect(theme()).toBe("dark");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
  });

  it("follows live OS changes only while on System", () => {
    const media = mockMatchMedia({ dark: false });
    renderToggle();
    expect(theme()).toBe("light");
    act(() => media.setDark(true));
    expect(theme()).toBe("dark");
    act(() => media.setDark(false));
    expect(theme()).toBe("light");

    fireEvent.click(screen.getByRole("radio", { name: en.themeLight }));
    act(() => media.setDark(true));
    expect(theme()).toBe("light");
  });

  it("reveals the new theme in a circle from the toggle when View Transitions are available", async () => {
    mockMatchMedia();
    const animate = vi.fn();
    document.documentElement.animate = animate;
    const startViewTransition = vi.fn((update: () => void) => { update(); return { ready: Promise.resolve() }; });
    Object.assign(document, { startViewTransition });
    renderToggle();
    await act(async () => { fireEvent.click(toggle()); });
    expect(startViewTransition).toHaveBeenCalledTimes(1);
    expect(theme()).toBe("light");
    const [keyframes, options] = animate.mock.calls[0];
    expect(keyframes.clipPath[0]).toMatch(/^circle\(0px at /);
    expect(options).toMatchObject({ duration: 500, pseudoElement: "::view-transition-new(root)" });
  });

  it("switches instantly, without a transition, under reduced motion", () => {
    mockMatchMedia({ reduce: true });
    const startViewTransition = vi.fn();
    Object.assign(document, { startViewTransition });
    renderToggle();
    fireEvent.click(toggle());
    expect(startViewTransition).not.toHaveBeenCalled();
    expect(document.documentElement.classList.contains("theme-fade")).toBe(false);
    expect(theme()).toBe("light");
  });

  it("cross-fades for 200 ms when View Transitions are unavailable", () => {
    vi.useFakeTimers();
    mockMatchMedia();
    renderToggle();
    fireEvent.click(toggle());
    expect(document.documentElement.classList.contains("theme-fade")).toBe(true);
    act(() => { vi.advanceTimersByTime(200); });
    expect(document.documentElement.classList.contains("theme-fade")).toBe(false);
  });
});

describe("no-flash theme script in index.html", () => {
  const html = readFileSync(join(__dirname, "../../index.html"), "utf8");
  const script = /<script id="theme-init">([\s\S]*?)<\/script>/.exec(html)?.[1] ?? "";
  const run = () => new Function(script)();

  beforeEach(() => { localStorage.clear(); delete document.documentElement.dataset.theme; });
  afterEach(() => vi.unstubAllGlobals());

  it("runs before the app bundle", () => {
    expect(script).toContain("data-theme");
    expect(html.indexOf('id="theme-init"')).toBeLessThan(html.indexOf("/src/main.tsx"));
  });

  it("applies a saved theme", () => {
    mockMatchMedia({ dark: false });
    localStorage.setItem(THEME_STORAGE_KEY, "dark");
    run();
    expect(theme()).toBe("dark");
    localStorage.setItem(THEME_STORAGE_KEY, "light");
    mockMatchMedia({ dark: true });
    run();
    expect(theme()).toBe("light");
  });

  it("follows the OS when nothing is saved or System is saved", () => {
    mockMatchMedia({ dark: true });
    run();
    expect(theme()).toBe("dark");
    localStorage.setItem(THEME_STORAGE_KEY, "system");
    mockMatchMedia({ dark: false });
    run();
    expect(theme()).toBe("light");
  });

  it("falls back to light when storage and matchMedia are unavailable", () => {
    vi.stubGlobal("matchMedia", undefined);
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    run();
    expect(theme()).toBe("light");
    vi.restoreAllMocks();
  });
});
