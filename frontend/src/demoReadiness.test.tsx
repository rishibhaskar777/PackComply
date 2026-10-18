import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { ErrorBoundary } from "./app/ErrorBoundary";
import PhoneDialog from "./app/PhoneDialog";
import { UNAUTHORIZED_EVENT } from "./api/client";
import { ProgressPanel } from "./components/ProgressPanel";
import { getLoadedLocale, I18nProvider, LANGUAGE_STORAGE_KEY, loadLocale, UI_LANGUAGES, useI18n } from "./i18n";
import en from "./locales/en.json";
import hi from "./locales/hi.json";
import { AboutPage } from "./pages/AboutPage";
import { mockApi, response } from "./test/mockApi";
import { sampleReport } from "./test/sampleReport";
import type { Progress } from "./types";

const ruleset = { ruleset_id: "LM_PC_2011", version: "2", label: "LM(PC) Rules 2011 v2", title: "LM(PC) Rules 2011", disclaimer: "", rules: [
  { code: "MRP_FORMAT", title: "MRP format", description: "d", legal_reference: "Rule 6(1)(e)", severity: "HIGH" },
  { code: "NET_QUANTITY_FORMAT", title: "Net quantity", description: "d", legal_reference: "Rule 6(1)(d)", severity: "HIGH" },
] };
const official = () => response({ username: "inspector", role: "OFFICIAL" });

describe("lazy locales", () => {
  beforeEach(() => { localStorage.clear(); window.history.replaceState(null, "", "#/overview"); });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("ships English eagerly and fetches another language only when asked, then caches it", async () => {
    expect(getLoadedLocale("en")).toBeTruthy();
    expect(getLoadedLocale("ta")).toBeUndefined();
    const first = await loadLocale("ta");
    expect(Object.keys(first).sort()).toEqual(Object.keys(en).sort());
    expect(getLoadedLocale("ta")).toBe(first);
    expect(await loadLocale("ta")).toBe(first); // the same object: not fetched or validated twice
    await expect(loadLocale("xx" as never)).rejects.toThrow("Unknown locale");
  });

  it("loads every UI language file with the full key set", async () => {
    for (const { code } of UI_LANGUAGES) expect(Object.keys(await loadLocale(code)).sort()).toEqual(Object.keys(en).sort());
  });

  function Probe() {
    const { t, locale, setLocale } = useI18n();
    return <div><span data-testid="locale">{locale}</span><span data-testid="word">{t("overview")}</span><button onClick={() => setLocale("hi")}>to-hi</button><button onClick={() => setLocale("en")}>to-en</button></div>;
  }

  it("switches language without a reload and keeps English until the file arrives", async () => {
    render(<I18nProvider><Probe /></I18nProvider>);
    expect(screen.getByTestId("word").textContent).toBe(en.overview);
    fireEvent.click(screen.getByText("to-hi"));
    expect(screen.getByTestId("locale").textContent).toBe("hi");
    await waitFor(() => expect(screen.getByTestId("word").textContent).toBe(hi.overview));
    expect(document.documentElement.lang).toBe("hi");
    expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("hi");
    fireEvent.click(screen.getByText("to-en"));
    await waitFor(() => expect(screen.getByTestId("word").textContent).toBe(en.overview));
  });

  it("starts in a saved language without flashing English", async () => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, "hi");
    const seen: string[] = [];
    function Spy() { const { t } = useI18n(); seen.push(t("overview")); return <span>{t("overview")}</span>; }
    render(<I18nProvider><Spy /></I18nProvider>);
    expect(await screen.findByText(hi.overview)).toBeTruthy();
    expect(seen.every(word => word === hi.overview)).toBe(true);
  });
});

describe("lazy routes", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("shows the page once a route's code has loaded", async () => {
    window.history.replaceState(null, "", "#/rulesets");
    mockApi({ "GET /auth/me": official(), "GET /compliance/rules": response(ruleset) });
    render(<I18nProvider><App /></I18nProvider>);
    expect(await screen.findByRole("heading", { level: 1, name: en.rulesets })).toBeTruthy();
    expect(await screen.findByText("MRP format")).toBeTruthy();
  });

  it("opens About & accuracy from the sidebar", async () => {
    window.history.replaceState(null, "", "#/overview");
    mockApi({ "GET /auth/me": official(), "GET /compliance/rules": response(ruleset), "GET /system/evaluation": response(sampleReport) });
    render(<I18nProvider><App /></I18nProvider>);
    const sidebar = within(await screen.findByRole("navigation", { name: en.primaryNavigation }));
    fireEvent.click(sidebar.getByRole("link", { name: en.aboutAccuracy }));
    expect(await screen.findByRole("heading", { level: 1, name: en.aboutAccuracy })).toBeTruthy();
    expect(sidebar.getByRole("link", { name: en.aboutAccuracy }).getAttribute("aria-current")).toBe("page");
  });
});

describe("About & accuracy", () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("lists the rules with references and shows the report with its dataset, date and honest labels", async () => {
    mockApi({ "GET /compliance/rules": response(ruleset), "GET /system/evaluation": response(sampleReport) });
    render(<I18nProvider><AboutPage onExpired={() => undefined} /></I18nProvider>);
    expect(await screen.findByText("Rule 6(1)(e)")).toBeTruthy();
    expect(screen.getByText("MRP format")).toBeTruthy();
    expect(screen.getByText(en.aboutHowTitle)).toBeTruthy();
    // The newest run is shown first, labelled as an unseen score with its name and date.
    expect(await screen.findByText(en.aboutKindUnseen)).toBeTruthy();
    expect(screen.getByText("Run: unseen-2026-10-29")).toBeTruthy();
    expect(screen.getByText(/Measured .*2026/)).toBeTruthy();
    expect(screen.getByText("21.5 s")).toBeTruthy(); // median time
    expect(screen.getAllByText("90.0%").length).toBeGreaterThanOrEqual(1); // precision, exactly as in the file
    expect(screen.getByText(en.aboutRuleAgreement).nextElementSibling?.textContent).toBe("83.3%"); // rule agreement 140/168
    expect(screen.getByText("8/12")).toBeTruthy();
    // Verdict confusion matrix cells come straight from the file.
    const matrix = screen.getByRole("table", { name: en.aboutConfusion });
    const rows = within(matrix).getAllByRole("row").map(row => [...row.querySelectorAll("th,td")].map(cell => cell.textContent));
    expect(rows[1]).toEqual([en.verdictCompliant, "1", "3", "0"]);
    expect(rows[3]).toEqual([en.verdictNonCompliant, "0", "1", "2"]);
    expect(within(screen.getByRole("table", { name: en.aboutPerField })).getByText("9 / 1 / 2")).toBeTruthy();
  });

  it("labels a development run as such and lets the viewer switch runs", async () => {
    mockApi({ "GET /compliance/rules": response(ruleset), "GET /system/evaluation": response(sampleReport) });
    render(<I18nProvider><AboutPage onExpired={() => undefined} /></I18nProvider>);
    await screen.findByText(en.aboutKindUnseen);
    fireEvent.change(screen.getByLabelText(en.aboutEvalRuns), { target: { value: "1" } });
    expect(await screen.findByText(en.aboutKindDevelopment)).toBeTruthy();
    expect(screen.getByText(en.aboutKindDevelopmentNote)).toBeTruthy();
    expect(screen.getByText("96.8%")).toBeTruthy();
  });

  it("says plainly when no report exists instead of inventing numbers", async () => {
    mockApi({ "GET /compliance/rules": response(ruleset), "GET /system/evaluation": response({ schema: 1, runs: [] }) });
    render(<I18nProvider><AboutPage onExpired={() => undefined} /></I18nProvider>);
    expect(await screen.findByText(en.aboutEvalNone)).toBeTruthy();
    expect(screen.queryByText(/%/)).toBeNull();
  });
});

describe("global error boundary", () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it("replaces a crashed screen with Something went wrong and a Reload button", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const reload = vi.fn();
    vi.stubGlobal("location", { ...window.location, reload });
    function Broken(): never { throw new Error("boom"); }
    render(<I18nProvider><ErrorBoundary><Broken /></ErrorBoundary></I18nProvider>);
    expect(screen.getByRole("alert").textContent).toContain(en.errorTitle);
    fireEvent.click(screen.getByRole("button", { name: en.errorReload }));
    expect(reload).toHaveBeenCalled();
  });

  it("renders its children when nothing fails", () => {
    render(<I18nProvider><ErrorBoundary><p>fine</p></ErrorBoundary></I18nProvider>);
    expect(screen.getByText("fine")).toBeTruthy();
  });
});

describe("expired session", () => {
  beforeEach(() => { localStorage.clear(); window.history.replaceState(null, "", "#/rulesets"); });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("returns to sign-in with a notice when any request answers 401", async () => {
    mockApi({ "GET /auth/me": official(), "GET /compliance/rules": response({ detail: "Not authenticated" }, false, 401) });
    render(<I18nProvider><App /></I18nProvider>);
    expect(await screen.findByRole("textbox", { name: en.username })).toBeTruthy();
    await waitFor(() => expect(screen.getAllByText(en.sessionExpired).length).toBeGreaterThanOrEqual(1));
  });

  it("does not announce an expired session when nobody is signed in", async () => {
    mockApi({ "GET /auth/me": response({ detail: "Not authenticated" }, false, 401) });
    render(<I18nProvider><App /></I18nProvider>);
    await screen.findByRole("textbox", { name: en.username });
    const before = screen.queryAllByText(en.sessionExpired).length;
    act(() => { window.dispatchEvent(new Event(UNAUTHORIZED_EVENT)); });
    expect(screen.queryAllByText(en.sessionExpired).length).toBe(before); // no extra toast
  });
});

describe("Open on phone", () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("shows a locally drawn QR code and the phone URL", async () => {
    mockApi({ "GET /system/network": response({ lan_ip: "192.168.1.20", phone_url: "https://192.168.1.20:5173/#/capture" }) });
    render(<I18nProvider><PhoneDialog open onClose={() => undefined} onExpired={() => undefined} /></I18nProvider>);
    expect(await screen.findByText("https://192.168.1.20:5173/#/capture")).toBeTruthy();
    const qr = screen.getByRole("img", { name: en.phoneQrLabel });
    expect(qr.querySelector("path")?.getAttribute("d")?.length).toBeGreaterThan(100);
  });

  it("explains when the laptop has no network address", async () => {
    mockApi({ "GET /system/network": response({ lan_ip: null, phone_url: null }) });
    render(<I18nProvider><PhoneDialog open onClose={() => undefined} onExpired={() => undefined} /></I18nProvider>);
    expect((await screen.findByRole("alert")).textContent).toBe(en.phoneNoNetwork);
  });

  it("is offered in the top bar of the desktop layout", async () => {
    window.history.replaceState(null, "", "#/overview");
    mockApi({ "GET /auth/me": official(), "GET /system/network": response({ lan_ip: "10.0.0.5", phone_url: "https://10.0.0.5:5173/#/capture" }) });
    render(<I18nProvider><App /></I18nProvider>);
    fireEvent.click(await screen.findByRole("button", { name: en.phoneOpenButton }));
    expect(await screen.findByText("https://10.0.0.5:5173/#/capture")).toBeTruthy();
  });
});

describe("unreadable photo in the progress panel", () => {
  afterEach(() => { cleanup(); });
  const progress = { inspection_id: "1", state: "completed", stage: "COMPLETED", percent: 100, completed_steps: 3, total_steps: 5, started_at: "2026-10-18T10:00:00+00:00", elapsed_ms: 4000, image_id: null, version: 3, error: null,
    steps: [
      { id: "a:read", kind: "read", label: "Reading text", status: "done", panel: "BACK", languages: ["en", "hi"], image_id: "a", duration_ms: 1000 },
      { id: "b:read", kind: "read", label: "Reading text", status: "failed", panel: "DATE_STAMP", languages: ["en"], image_id: "b", message: "The text on this photo could not be read." },
    ] } as unknown as Progress;

  it("offers Retry photo beside the failed step while the run still completes", () => {
    const retry = vi.fn();
    render(<I18nProvider><ProgressPanel progress={progress} photos={[]} onRetryPhoto={retry} /></I18nProvider>);
    const step = document.querySelector('[data-step="b:read"]') as HTMLElement;
    fireEvent.click(within(step).getByRole("button", { name: en.progRetryPhoto }));
    expect(retry).toHaveBeenCalledTimes(1);
    expect(document.querySelector('[data-step="a:read"] button')).toBeNull();
  });
});

describe("routing", () => {
  beforeEach(() => { localStorage.clear(); window.history.replaceState(null, "", "#/overview"); });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("ignores a duplicate hashchange for the page already shown, so an open sheet stays open", async () => {
    mockApi({ "GET /auth/me": response({ username: "inspector", full_name: "Rishi Bhaskar", role: "OFFICIAL" }) });
    render(<I18nProvider><App /></I18nProvider>);
    const bar = within(await screen.findByRole("navigation", { name: en.mobileNavigation }));
    fireEvent.click(bar.getByRole("button", { name: en.account }));
    await screen.findByRole("dialog", { name: en.account });
    act(() => { window.dispatchEvent(new HashChangeEvent("hashchange")); });
    expect(screen.getByRole("dialog", { name: en.account })).toBeTruthy();
  });
});
