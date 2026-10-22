import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { STATUS_POLL_MS, useSystemStatus } from "../app/useSystemStatus";
import en from "../locales/en.json";
import { I18nProvider } from "../i18n";
import { calledPaths, mockApi, response } from "../test/mockApi";
import { OtpInput } from "./auth/OtpInput";
import { passwordScore } from "./auth/PasswordStrength";

const unauthenticated = response({ detail: "Not authenticated" }, false, 401);
const ready = { status: "ok", database: true, ocr: "ready", ruleset_version: "2", rule_count: 14, languages: ["en", "hi"] };
const renderApp = () => render(<I18nProvider><App /></I18nProvider>);
const pill = () => document.querySelector(".status-pill") as HTMLElement;
const lastBody = (fetchMock: ReturnType<typeof mockApi>, path: string) => JSON.parse(fetchMock.mock.calls.filter(call => String(call[0]).endsWith(path)).at(-1)![1]!.body as string);

/** matchMedia for the colour scheme and reduced motion (jsdom has none, which the app treats as reduced motion). */
function mockMatchMedia({ dark = false, reduce = true } = {}) {
  vi.stubGlobal("matchMedia", vi.fn((query: string) => ({
    matches: query.includes("prefers-color-scheme: dark") ? dark : query.includes("prefers-reduced-motion") ? reduce : false,
    media: query, addEventListener: () => undefined, removeEventListener: () => undefined,
  })));
}

describe("sign-in page", () => {
  beforeEach(() => { localStorage.clear(); window.history.replaceState(null, "", "#/overview"); });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); delete document.documentElement.dataset.theme; });

  it.each([["light", false], ["dark", true]] as const)("renders the redesigned page in the %s theme", async (theme, dark) => {
    mockMatchMedia({ dark });
    mockApi({ "GET /auth/me": unauthenticated, "GET /system/status": response(ready) });
    renderApp();
    expect(await screen.findByRole("heading", { level: 1, name: en.loginAsInspector })).toBeTruthy();
    expect(document.documentElement.dataset.theme).toBe(theme);
    expect(screen.getByText(en.storyHeadline)).toBeTruthy();
    expect(screen.getByRole("img", { name: en.sampleLabelCaption })).toBeTruthy();
    expect(screen.getByText(en.portalPill)).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: en.keepSignedIn })).toBeTruthy();
    expect(screen.getByRole("link", { name: en.forgotPassword }).getAttribute("href")).toBe("#/forgot-password");
    expect(screen.getByRole("button", { name: en.createManufacturerAccount })).toBeTruthy();
    // The old glass card and rotating ring are gone.
    expect(document.querySelector(".auth-card, .auth-ring, .aurora")).toBeNull();
  });

  it("shows a ready system with values from the status endpoint", async () => {
    mockApi({ "GET /auth/me": unauthenticated, "GET /system/status": response({ ...ready, ocr: "warming_up", rule_count: 15 }) });
    renderApp();
    await waitFor(() => expect(pill().textContent).toContain(en.statusReady));
    expect(pill().getAttribute("role")).toBe("status");
    expect(pill().getAttribute("aria-live")).toBe("polite");
    const strip = within(document.querySelector(".status-strip") as HTMLElement);
    expect(strip.getByText(en.ocrWarmingUp)).toBeTruthy();
    expect(strip.getByText("LM(PC) v2")).toBeTruthy();
    expect(strip.getByText("EN + HI")).toBeTruthy();
    expect(screen.getByText("15 rules")).toBeTruthy();
  });

  it.each([
    ["database unavailable", response({ ...ready, status: "down", database: false }), en.statusDatabaseUnavailable, "review"],
    ["OCR unavailable", response({ ...ready, status: "degraded", ocr: "unavailable" }), en.statusOcrUnavailable, "review"],
    ["backend offline", async () => { throw new TypeError("Failed to fetch"); }, en.statusBackendOffline, "violation"],
  ] as const)("reports %s", async (_, reply, label, tone) => {
    mockApi({ "GET /auth/me": unauthenticated, "GET /system/status": reply });
    renderApp();
    await waitFor(() => expect(pill().textContent).toContain(label));
    expect(pill().className).toContain(`status-pill--${tone}`);
  });

  it("falls back to 14 rules and shows skeletons until the status arrives", async () => {
    let answer: (value: ReturnType<typeof response>) => void = () => undefined;
    let asked = false;
    mockApi({ "GET /auth/me": unauthenticated, "GET /system/status": () => new Promise(resolve => { asked = true; answer = resolve; }) });
    renderApp();
    await screen.findByRole("heading", { level: 1, name: en.loginAsInspector });
    // The status request is made by a mount effect, which can run just after the heading is visible.
    await waitFor(() => expect(asked).toBe(true));
    expect(pill().textContent).toContain(en.statusChecking);
    expect(document.querySelector(".status-strip")?.getAttribute("aria-busy")).toBe("true");
    expect(document.querySelectorAll(".status-strip .skeleton")).toHaveLength(3);
    expect(screen.getByText("14 rules")).toBeTruthy();
    await act(async () => answer(response(ready)));
    await waitFor(() => expect(pill().textContent).toContain(en.statusReady));
  });

  it("polls every 30 seconds and pauses while the tab is hidden", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const fetchMock = mockApi({ "GET /system/status": response(ready) });
    const polls = () => calledPaths(fetchMock).filter(path => path === "/system/status").length;
    function Probe() { const state = useSystemStatus(); return <span>{state.phase}</span>; }
    render(<Probe />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(polls()).toBe(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(STATUS_POLL_MS); });
    expect(polls()).toBe(2);
    Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
    act(() => { document.dispatchEvent(new Event("visibilitychange")); });
    await act(async () => { await vi.advanceTimersByTimeAsync(STATUS_POLL_MS * 3); });
    expect(polls()).toBe(2);
    Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); await vi.advanceTimersByTimeAsync(0); });
    expect(polls()).toBe(3);
    delete (document as { hidden?: boolean }).hidden;
  });

  it("sends 'keep me signed in' with the login", async () => {
    const fetchMock = mockApi({ "GET /auth/me": [unauthenticated, response({ username: "inspector" })], "POST /auth/login": response({ username: "inspector" }), "GET /system/status": response(ready) });
    renderApp();
    fireEvent.change(await screen.findByRole("textbox", { name: en.username }), { target: { value: "inspector" } });
    fireEvent.change(screen.getByLabelText(en.password), { target: { value: "a-password" } });
    fireEvent.click(screen.getByRole("checkbox", { name: en.keepSignedIn }));
    fireEvent.click(screen.getByRole("button", { name: en.signIn }));
    await screen.findByRole("navigation", { name: en.primaryNavigation });
    expect(lastBody(fetchMock, "/auth/login")).toEqual({ username: "inspector", password: "a-password", remember: true });
  });

  it("hides the scan line and keeps the chips still under reduced motion", async () => {
    mockMatchMedia({ reduce: true });
    mockApi({ "GET /auth/me": unauthenticated, "GET /system/status": response(ready) });
    renderApp();
    await screen.findByRole("heading", { level: 1, name: en.loginAsInspector });
    expect(screen.queryByTestId("scan-line")).toBeNull();
    expect(document.querySelector(".label-card.is-floating, .rule-chip.is-animated")).toBeNull();
    cleanup();
    mockMatchMedia({ reduce: false });
    renderApp();
    await screen.findByRole("heading", { level: 1, name: en.loginAsInspector });
    expect(screen.getByTestId("scan-line")).toBeTruthy();
    expect(document.querySelectorAll(".rule-chip.is-animated").length).toBeGreaterThan(0);
  });
});

describe("forgot password", () => {
  beforeEach(() => { localStorage.clear(); window.history.replaceState(null, "", "#/forgot-password"); });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  const accepted = response({ detail: "If that account exists, a reset code has been issued." }, true, 202);

  async function requestCode(fetchMock = mockApi({ "GET /auth/me": unauthenticated, "GET /system/status": response(ready), "POST /auth/password-reset/request": accepted, "POST /auth/password-reset/confirm": response({ detail: "Password updated." }) })) {
    renderApp();
    expect(await screen.findByRole("heading", { level: 1, name: en.resetTitle })).toBeTruthy();
    expect(screen.getByText(en.resetOfflineNote)).toBeTruthy();
    fireEvent.change(screen.getByRole("textbox", { name: en.username }), { target: { value: "inspector" } });
    fireEvent.click(screen.getByRole("button", { name: en.requestResetCode }));
    const heading = await screen.findByRole("heading", { level: 1, name: en.enterCodeTitle });
    return { fetchMock, heading };
  }
  const digits = () => screen.getAllByRole("textbox").filter(box => box.classList.contains("otp__box")) as HTMLInputElement[];

  it("walks through request, code and success, then returns to sign in", async () => {
    const { fetchMock, heading } = await requestCode();
    expect(lastBody(fetchMock, "/auth/password-reset/request")).toEqual({ username: "inspector" });
    // Focus moves to each new step's heading.
    await waitFor(() => expect(document.activeElement).toBe(heading));
    expect(screen.getByText("Code for inspector · valid for 15 minutes")).toBeTruthy();
    const group = screen.getByRole("group", { name: en.resetCodeLabel });
    expect(within(group).getAllByRole("textbox")).toHaveLength(6);
    expect(digits()[0].getAttribute("inputmode")).toBe("numeric");

    fireEvent.paste(digits()[2], { clipboardData: { getData: () => "48 29 13" } });
    expect(digits().map(box => box.value).join("")).toBe("482913");
    fireEvent.change(screen.getByLabelText(en.newPassword), { target: { value: "Fresh-Passphrase-42" } });
    expect(screen.getByText("Strength: Strong")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: en.setNewPassword }));

    const done = await screen.findByRole("heading", { level: 1, name: en.passwordUpdated });
    await waitFor(() => expect(document.activeElement).toBe(done));
    expect(screen.getByText(en.signedOutEverywhere)).toBeTruthy();
    expect(lastBody(fetchMock, "/auth/password-reset/confirm")).toEqual({ username: "inspector", code: "482913", new_password: "Fresh-Passphrase-42" });

    fireEvent.click(screen.getByRole("button", { name: en.backToSignIn }));
    expect(await screen.findByRole("heading", { level: 1, name: en.loginAsInspector })).toBeTruthy();
    expect(window.location.hash).toBe("#/overview");
  });

  it("shows the generic error for a wrong code and clears the boxes", async () => {
    await requestCode(mockApi({ "GET /auth/me": unauthenticated, "POST /auth/password-reset/request": accepted, "POST /auth/password-reset/confirm": response({ detail: "Invalid or expired code" }, false, 400) }));
    fireEvent.paste(digits()[0], { clipboardData: { getData: () => "111111" } });
    fireEvent.change(screen.getByLabelText(en.newPassword), { target: { value: "Fresh-Passphrase-42" } });
    fireEvent.click(screen.getByRole("button", { name: en.setNewPassword }));
    expect((await screen.findByRole("alert")).textContent).toBe(en.invalidResetCode);
    expect(digits().map(box => box.value).join("")).toBe("");
  });

  it("explains a common password rejected by the server", async () => {
    await requestCode(mockApi({ "GET /auth/me": unauthenticated, "POST /auth/password-reset/request": accepted, "POST /auth/password-reset/confirm": response({ detail: "This password is too common. Choose another." }, false, 422) }));
    fireEvent.paste(digits()[0], { clipboardData: { getData: () => "482913" } });
    fireEvent.change(screen.getByLabelText(en.newPassword), { target: { value: "password1" } });
    fireEvent.click(screen.getByRole("button", { name: en.setNewPassword }));
    expect((await screen.findByRole("alert")).textContent).toBe(en.passwordTooCommon);
  });

  it("checks the code and password before calling the server", async () => {
    const { fetchMock } = await requestCode();
    fireEvent.click(screen.getByRole("button", { name: en.setNewPassword }));
    expect(screen.getByRole("alert").textContent).toBe(en.codeIncomplete);
    fireEvent.paste(digits()[0], { clipboardData: { getData: () => "482913" } });
    fireEvent.change(screen.getByLabelText(en.newPassword), { target: { value: "inspector" } });
    fireEvent.click(screen.getByRole("button", { name: en.setNewPassword }));
    expect(screen.getByRole("alert").textContent).toBe(en.passwordSameAsUsername);
    expect(calledPaths(fetchMock)).not.toContain("/auth/password-reset/confirm");
  });

  it("says when reset requests are rate-limited", async () => {
    mockApi({ "GET /auth/me": unauthenticated, "POST /auth/password-reset/request": response({ detail: "Too many reset requests." }, false, 429) });
    renderApp();
    fireEvent.change(await screen.findByRole("textbox", { name: en.username }), { target: { value: "inspector" } });
    fireEvent.click(screen.getByRole("button", { name: en.requestResetCode }));
    expect((await screen.findByRole("alert")).textContent).toBe(en.tooManyAttempts);
  });

  it("is reachable from the sign-in form and its back link returns there", async () => {
    window.history.replaceState(null, "", "#/overview");
    mockApi({ "GET /auth/me": unauthenticated });
    renderApp();
    fireEvent.click(await screen.findByRole("link", { name: en.forgotPassword }));
    act(() => { window.dispatchEvent(new HashChangeEvent("hashchange")); });
    const heading = await screen.findByRole("heading", { level: 1, name: en.resetTitle });
    await waitFor(() => expect(document.activeElement).toBe(heading));
    fireEvent.click(screen.getByRole("button", { name: en.backToSignIn }));
    expect(await screen.findByRole("heading", { level: 1, name: en.loginAsInspector })).toBeTruthy();
    expect(window.location.hash).toBe("#/overview");
  });
});

describe("one-time code input", () => {
  afterEach(cleanup);
  function Harness({ initial = ["", "", "", "", "", ""] }: { initial?: string[] }) {
    const [value, setValue] = useState(initial);
    return <I18nProvider><OtpInput value={value} onChange={setValue} /></I18nProvider>;
  }
  const boxes = () => screen.getAllByRole("textbox") as HTMLInputElement[];

  it("advances on each digit, ignores letters, and labels every box", () => {
    render(<Harness />);
    fireEvent.change(boxes()[0], { target: { value: "4" } });
    expect(document.activeElement).toBe(boxes()[1]);
    fireEvent.change(boxes()[1], { target: { value: "x" } });
    expect(boxes()[1].value).toBe("");
    expect(boxes().map(box => box.getAttribute("aria-label"))).toEqual([1, 2, 3, 4, 5, 6].map(index => `Digit ${index} of 6`));
  });

  it("fills every box from a pasted code, wherever it is pasted", () => {
    render(<Harness />);
    fireEvent.paste(boxes()[4], { clipboardData: { getData: () => "Code: 905-117" } });
    expect(boxes().map(box => box.value)).toEqual(["9", "0", "5", "1", "1", "7"]);
  });

  it("moves back and clears the previous box on Backspace in an empty box", () => {
    render(<Harness initial={["1", "2", "3", "", "", ""]} />);
    boxes()[3].focus();
    fireEvent.keyDown(boxes()[3], { key: "Backspace" });
    expect(document.activeElement).toBe(boxes()[2]);
    expect(boxes()[2].value).toBe("");
    fireEvent.keyDown(boxes()[1], { key: "Backspace" });
    expect(boxes()[1].value).toBe("");
    expect(boxes().map(box => box.value).join("")).toBe("1");
  });
});

describe("password strength", () => {
  it("scores length and variety, and caps short or username-like passwords", () => {
    expect(passwordScore("")).toBe(0);
    expect(passwordScore("abc")).toBeLessThanOrEqual(1);
    expect(passwordScore("inspector", "inspector")).toBeLessThanOrEqual(1);
    expect(passwordScore("abcdefgh")).toBe(1);
    expect(passwordScore("Abcdefghijkl")).toBe(3);
    expect(passwordScore("Fresh-Passphrase-42")).toBe(4);
  });
});

describe("sign-in role tabs", () => {
  const inspectorUser = { username: "demo_inspector", full_name: "Demo Inspector", role: "OFFICIAL" };
  const makerUser = { username: "demo_maker", full_name: "Demo Manufacturer", role: "MANUFACTURER" };
  const productsEmpty = { total: 0, ready: 0, fix: 0, check: 0, readiness_percent: 0, open_fixes: 0, labels_to_fix: 0, products: [] };
  const tab = (name: string) => screen.getByRole("tab", { name });
  const portal = () => document.documentElement.dataset.portal;
  const title = () => screen.getByRole("heading", { level: 1 }).textContent;

  /** Signed out; the server answers a login with `login` and the following /auth/me with `me`. */
  function server(login: ReturnType<typeof response>, me?: unknown) {
    return mockApi({
      "GET /auth/me": [unauthenticated, ...(me ? [response(me)] : [])], "POST /auth/login": login, "POST /auth/logout": response({}, true, 204),
      "GET /system/status": response(ready), "GET /products": response(productsEmpty), "GET /fixes": response({ total_items: 0, groups: [] }),
    });
  }
  function submitAs(username: string, password = "a-password") {
    fireEvent.change(screen.getByRole("textbox", { name: en.username }), { target: { value: username } });
    fireEvent.change(screen.getByLabelText(en.password), { target: { value: password } });
    fireEvent.click(screen.getByRole("button", { name: en.signIn }));
  }

  beforeEach(() => { localStorage.clear(); window.history.replaceState(null, "", "#/overview"); mockMatchMedia(); });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); delete document.documentElement.dataset.theme; delete document.documentElement.dataset.portal; });

  it("shows both tabs above the heading and username field, Inspector selected by default", async () => {
    server(response({}));
    renderApp();
    expect(await screen.findByRole("tablist", { name: en.loginRoleTabs })).toBeTruthy();
    expect(tab(en.roleInspector).getAttribute("aria-selected")).toBe("true");
    expect(tab(en.roleManufacturer).getAttribute("aria-selected")).toBe("false");
    expect(title()).toBe(en.loginAsInspector);
    expect(screen.getByText(en.loginInspectorHint)).toBeTruthy();
    expect(portal()).toBe("inspector");
    const tablist = screen.getByRole("tablist");
    expect(tablist.compareDocumentPosition(screen.getByRole("textbox", { name: en.username })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(tablist.compareDocumentPosition(screen.getByRole("heading", { level: 1 })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("switching tabs changes the heading, hint and portal theme, and keeps the forgot-password link", async () => {
    server(response({}));
    renderApp();
    await screen.findByRole("tablist");
    fireEvent.click(tab(en.roleManufacturer));
    expect(tab(en.roleManufacturer).getAttribute("aria-selected")).toBe("true");
    expect(title()).toBe(en.loginAsManufacturer);
    expect(screen.getByText(en.loginManufacturerHint)).toBeTruthy();
    expect(portal()).toBe("manufacturer");
    expect(screen.getByRole("link", { name: en.forgotPassword }).getAttribute("href")).toBe("#/forgot-password");
    fireEvent.click(tab(en.roleInspector));
    expect(title()).toBe(en.loginAsInspector);
    expect(portal()).toBe("inspector");
  });

  it("opens on the tab last used on this browser", async () => {
    server(response({}));
    renderApp();
    await screen.findByRole("tablist");
    fireEvent.click(tab(en.roleManufacturer));
    cleanup();
    renderApp();
    await screen.findByRole("tablist");
    expect(tab(en.roleManufacturer).getAttribute("aria-selected")).toBe("true");
    expect(title()).toBe(en.loginAsManufacturer);
    expect(portal()).toBe("manufacturer");
  });

  it("works with storage blocked", async () => {
    const blocked = () => { throw new DOMException("blocked", "SecurityError"); };
    vi.stubGlobal("localStorage", { getItem: blocked, setItem: blocked, removeItem: blocked, clear: blocked });
    server(response({}));
    renderApp();
    await screen.findByRole("tablist");
    expect(title()).toBe(en.loginAsInspector);
    fireEvent.click(tab(en.roleManufacturer));
    expect(title()).toBe(en.loginAsManufacturer);
    expect(portal()).toBe("manufacturer");
  });

  it("moves between tabs with the arrow, Home and End keys", async () => {
    server(response({}));
    renderApp();
    await screen.findByRole("tablist");
    tab(en.roleInspector).focus();
    fireEvent.keyDown(tab(en.roleInspector), { key: "ArrowRight" });
    expect(tab(en.roleManufacturer).getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(tab(en.roleManufacturer));
    expect(title()).toBe(en.loginAsManufacturer);
    fireEvent.keyDown(tab(en.roleManufacturer), { key: "ArrowRight" });
    expect(tab(en.roleInspector).getAttribute("aria-selected")).toBe("true");
    fireEvent.keyDown(tab(en.roleInspector), { key: "End" });
    expect(tab(en.roleManufacturer).getAttribute("aria-selected")).toBe("true");
    fireEvent.keyDown(tab(en.roleManufacturer), { key: "Home" });
    expect(tab(en.roleInspector).getAttribute("aria-selected")).toBe("true");
    // Only the selected tab is in the tab order.
    expect(tab(en.roleInspector).tabIndex).toBe(0);
    expect(tab(en.roleManufacturer).tabIndex).toBe(-1);
  });

  it("never sends the selected tab to the server", async () => {
    const fetchMock = server(response(makerUser), makerUser);
    renderApp();
    await screen.findByRole("tablist");
    fireEvent.click(tab(en.roleManufacturer));
    submitAs("demo_maker");
    await waitFor(() => expect(calledPaths(fetchMock)).toContain("/auth/login"));
    expect(lastBody(fetchMock, "/auth/login")).toEqual({ username: "demo_maker", password: "a-password", remember: false });
  });

  it("continues to the inspector portal when an inspector signs in on the Inspector tab", async () => {
    server(response(inspectorUser), inspectorUser);
    renderApp();
    await screen.findByRole("tablist");
    submitAs("demo_inspector");
    await screen.findByRole("navigation", { name: en.primaryNavigation });
    expect(portal()).toBe("inspector");
    expect(document.querySelector("[data-portal-pill]")?.textContent).toBe(en.portalInspector);
  });

  it("continues to the manufacturer portal when a manufacturer signs in on the Manufacturer tab", async () => {
    server(response(makerUser), makerUser);
    renderApp();
    await screen.findByRole("tablist");
    fireEvent.click(tab(en.roleManufacturer));
    submitAs("demo_maker");
    await screen.findByRole("navigation", { name: en.primaryNavigation });
    expect(portal()).toBe("manufacturer");
    expect(document.querySelector("[data-portal-pill]")?.textContent).toBe(en.portalManufacturer);
  });

  it("ends the session and switches tab when a manufacturer signs in on the Inspector tab", async () => {
    const fetchMock = server(response(makerUser), makerUser);
    renderApp();
    await screen.findByRole("tablist");
    submitAs("demo_maker");
    expect((await screen.findByRole("alert")).textContent).toContain(en.loginWrongManufacturer);
    expect(en.loginWrongManufacturer).toBe("This is a manufacturer account. Please use the Manufacturer tab.");
    // The session was cleared again and the user was never let in.
    expect(calledPaths(fetchMock)).toContain("/auth/logout");
    expect(calledPaths(fetchMock).filter(path => path === "/auth/me")).toHaveLength(1);
    expect(screen.queryByRole("navigation", { name: en.primaryNavigation })).toBeNull();
    // The matching tab is now selected so they can sign in again, and the portal theme follows it.
    expect(tab(en.roleManufacturer).getAttribute("aria-selected")).toBe("true");
    expect(title()).toBe(en.loginAsManufacturer);
    expect(portal()).toBe("manufacturer");
    // Choosing a tab by hand dismisses the notice.
    fireEvent.click(tab(en.roleInspector));
    expect(screen.queryByText(en.loginWrongManufacturer)).toBeNull();
  });

  it("ends the session and switches tab when an inspector signs in on the Manufacturer tab", async () => {
    const fetchMock = server(response(inspectorUser), inspectorUser);
    renderApp();
    await screen.findByRole("tablist");
    fireEvent.click(tab(en.roleManufacturer));
    submitAs("demo_inspector");
    expect((await screen.findByRole("alert")).textContent).toContain(en.loginWrongInspector);
    expect(calledPaths(fetchMock)).toContain("/auth/logout");
    expect(tab(en.roleInspector).getAttribute("aria-selected")).toBe("true");
    expect(title()).toBe(en.loginAsInspector);
    expect(localStorage.getItem("packcomply-login-portal")).toBe("inspector");
  });

  it.each([["manufacturer", 401], ["inspector", 401], ["inspector", 422]] as const)("shows only the generic message, no role, for a failed sign-in on the %s tab (%i)", async (selected, status) => {
    const fetchMock = server(response({ detail: "Invalid username or password." }, false, status));
    renderApp();
    await screen.findByRole("tablist");
    const label = selected === "manufacturer" ? en.roleManufacturer : en.roleInspector;
    fireEvent.click(tab(label));
    submitAs("unknown", "wrong-password");
    expect((await screen.findByRole("alert")).textContent).toContain(en.invalidCredentials);
    expect(screen.queryByText(en.loginWrongInspector)).toBeNull();
    expect(screen.queryByText(en.loginWrongManufacturer)).toBeNull();
    expect(tab(label).getAttribute("aria-selected")).toBe("true");
    expect(calledPaths(fetchMock)).not.toContain("/auth/logout");
  });

  it("shows the lockout message and note on the Manufacturer tab", async () => {
    server(response({ detail: "Too many failed attempts." }, false, 429));
    renderApp();
    await screen.findByRole("tablist");
    fireEvent.click(tab(en.roleManufacturer));
    submitAs("locked", "wrong-password");
    expect((await screen.findByRole("alert")).textContent).toContain(en.tooManyAttempts);
    expect(screen.getByText(en.lockoutNote.replace("{count}", "5"))).toBeTruthy();
  });

  it("falls back to English text for a key a locale lacks, never the raw key", async () => {
    const { loadLocale } = await import("../i18n");
    const hi = await loadLocale("hi") as Record<string, string>;
    const original = hi.loginAsInspector;
    delete hi.loginAsInspector;
    try {
      localStorage.setItem("packcomply-language", "hi");
      server(response({}));
      renderApp();
      expect((await screen.findByRole("heading", { level: 1 })).textContent).toBe(en.loginAsInspector);
    } finally { hi.loginAsInspector = original; }
  });
});
