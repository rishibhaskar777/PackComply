import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import en from "./locales/en.json";
import hi from "./locales/hi.json";
import { I18nProvider, LANGUAGE_STORAGE_KEY, UI_LANGUAGES } from "./i18n";
import { API, calledPaths, emptyRiskPage, mockApi, response } from "./test/mockApi";

function renderApp() { return render(<I18nProvider><App /></I18nProvider>); }
const file = () => new File(["image"], "label.png", { type: "image/png" });
const result = { id: "inspection-1", inspection_number: "INSP-001", status: "COMPLETED", product_name: "Glucose Biscuits", maker_name: "ABC Foods", review: { status: "UNREVIEWED" }, corrections: [], image: { id: "image-1", original_filename: "label.png" }, ocr: { raw_text: "MRP Rs 100", languages: ["en", "hi"], results: [] }, extraction: { fields: { manufacturer: { value: "ABC Foods", confidence: .94, needs_review: false }, mrp: { value: "Rs 100", confidence: .97, needs_review: false } } }, compliance: { overall_status: "REVIEW", summary: { passed: 1, failed: 0, review: 1 }, findings: [{ rule_id: "MRP_FORMAT", rule_name: "MRP format", status: "PASS", severity: "WARNING", message: "Valid MRP", field: "mrp", value: "Rs 100" }] }, quantity_verification: { declared_quantity: "0.5 KG", normalized_quantity: "500 g", unit: "g", status: "PASS", reason: "Valid declared quantity.", rule_code: "NET_QUANTITY_FORMAT", severity: "WARNING", evidence: "Net Qty: 0.5 KG", requirement: null } };
const unauthenticated = response({ detail: "Not authenticated" }, false, 401);
const done = { inspection_id: "inspection-1", state: "completed", stage: "COMPLETED", percent: 100, completed_steps: 3, total_steps: 3, started_at: "2026-10-17T09:00:00+00:00", elapsed_ms: 1500, steps: [], error: null, version: 4 };
const workflow = (process = response(done, true, 202)) => ({ "POST /inspections": response({ id: "inspection-1", inspection_number: "INSP-001" }), "POST /inspections/inspection-1/image": response({ id: "image-1", original_filename: "label.png" }), "POST /inspections/inspection-1/process": process, "GET /inspections/inspection-1": response(result), "GET /inspections/inspection-1/progress": response(done), "GET /inspections/inspection-1/audit": response([]), "GET /inspections/inspection-1/image": response({}), "GET /inspections/inspection-1/images/image-1": response({}) });

// The authenticated workspace is ready once its primary navigation renders.
const appReady = () => screen.findByRole("navigation", { name: en.primaryNavigation });
const sidebar = () => within(screen.getByRole("navigation", { name: en.primaryNavigation }));
const openUserMenu = async () => fireEvent.click(await screen.findByRole("button", { name: new RegExp(en.accountMenu) }));
const go = (hash: string) => window.history.replaceState(null, "", hash);

async function fillRegistration(confirm = "register-password") {
  fireEvent.click(await screen.findByRole("button", { name: en.createManufacturerAccount }));
  fireEvent.change(screen.getByRole("textbox", { name: en.username }), { target: { value: "new-user" } });
  fireEvent.change(screen.getByRole("textbox", { name: en.email }), { target: { value: "new@example.invalid" } });
  fireEvent.change(screen.getByLabelText(en.password), { target: { value: "register-password" } });
  fireEvent.change(screen.getByLabelText(en.confirmPassword), { target: { value: confirm } });
  fireEvent.click(screen.getByRole("button", { name: en.registerAccount }));
}
async function signIn(password = "a-password") {
  fireEvent.change(await screen.findByRole("textbox", { name: en.username }), { target: { value: "inspector" } });
  fireEvent.change(screen.getByLabelText(en.password), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: en.signIn }));
}

describe("inspection workflow UI", () => {
  beforeEach(() => { localStorage.clear(); go("#/overview"); mockApi({ "GET /auth/me": response({ username: "inspector" }) }); });
  afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); });
  it("renders English and all language choices", async () => { renderApp(); await appReady(); expect(sidebar().getByText(en.overview)).toBeTruthy(); await openUserMenu(); expect(screen.getAllByRole("option")).toHaveLength(23); });
  it("shows the official risk-queue navigation and loads its empty state", async () => {
    const fetchMock = mockApi({ "GET /auth/me": response({ username: "official", role: "OFFICIAL" }), "GET /risk-targeting": response(emptyRiskPage) });
    renderApp(); await appReady(); fireEvent.click(sidebar().getByRole("link", { name: en.riskQueue })); expect(await screen.findByText(en.riskTargetingNoResults)).toBeTruthy();
    expect(fetchMock).toHaveBeenLastCalledWith(`${API}/risk-targeting?page=1&page_size=20`, expect.objectContaining({ credentials: "include" }));
  });
  it("does not show risk-queue navigation to manufacturers", async () => {
    mockApi({ "GET /auth/me": response({ username: "manufacturer", role: "MANUFACTURER" }) }); renderApp(); await appReady(); expect(sidebar().queryByRole("link", { name: en.riskQueue })).toBeNull();
  });
  it("persists a language choice", async () => { renderApp(); await appReady(); await openUserMenu(); fireEvent.change(screen.getByRole("combobox"), { target: { value: "hi" } }); expect(within(screen.getByRole("navigation", { name: hi.primaryNavigation })).getByText(hi.overview)).toBeTruthy(); expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("hi"); });
  it("has complete locale key sets", async () => { for (const language of UI_LANGUAGES) { const locale = await import(`./locales/${language.code}.json`); expect(Object.keys(locale.default).sort()).toEqual(Object.keys(en).sort()); } });
  it("shows the unauthenticated state after a 401 startup check", async () => {
    const fetchMock = mockApi({ "GET /auth/me": unauthenticated });
    renderApp();
    expect(await screen.findByText(en.sessionExpired)).toBeTruthy();
    expect(screen.getByRole("textbox", { name: en.username })).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith(`${API}/auth/me`, expect.objectContaining({ credentials: "include" }));
  });
  it("opens registration from the sign-in page and detects a password mismatch", async () => {
    mockApi({ "GET /auth/me": unauthenticated }); renderApp();
    await fillRegistration("different-password");
    expect((await screen.findByRole("alert")).textContent).toBe(en.passwordsDoNotMatch);
  });
  it("shows the rate-limit message when login is locked out", async () => {
    mockApi({ "GET /auth/me": unauthenticated, "POST /auth/login": response({ detail: "Too many failed attempts. Try again later." }, false, 429) }); renderApp();
    await signIn("wrong-password");
    expect((await screen.findByRole("alert")).textContent).toBe(en.tooManyAttempts);
  });
  async function attemptLogin(loginResult: () => Promise<ReturnType<typeof response>>) {
    mockApi({ "GET /auth/me": unauthenticated, "POST /auth/login": loginResult }); renderApp();
    await signIn();
  }
  it("shows invalid credentials only for a 401 login", async () => {
    await attemptLogin(async () => response({ detail: "Invalid username or password." }, false, 401));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(en.invalidCredentials));
  });
  it("explains an unavailable database instead of invalid credentials", async () => {
    await attemptLogin(async () => response({ detail: "Database unavailable. Please try again shortly." }, false, 503));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(en.serverUnavailable));
  });
  it("explains an unreachable backend on login", async () => {
    await attemptLogin(async () => { throw new TypeError("Failed to fetch"); });
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(en.serverUnavailable));
  });
  it("explains an unavailable server during the startup session check", async () => {
    mockApi({ "GET /auth/me": async () => { throw new TypeError("Failed to fetch"); } }); renderApp();
    expect((await screen.findByRole("alert")).textContent).toBe(en.serverUnavailable);
    cleanup(); mockApi({ "GET /auth/me": response({ detail: "Database unavailable." }, false, 503) }); renderApp();
    expect((await screen.findByRole("alert")).textContent).toBe(en.serverUnavailable);
  });
  it("registers explicitly as a manufacturer and explains why", async () => {
    const fetchMock = mockApi({ "GET /auth/me": [unauthenticated, response({ username: "new-user" })], "POST /auth/register": response({ username: "new-user" }) }); renderApp();
    fireEvent.click(await screen.findByRole("button", { name: en.createManufacturerAccount })); expect(screen.getByText(en.manufacturerRegistrationNote)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: en.backToSignIn })); expect(screen.getByRole("heading", { level: 1, name: en.welcomeBack })).toBeTruthy();
    await fillRegistration();
    await appReady();
    const registerCall = fetchMock.mock.calls.find(call => String(call[0]).endsWith("/auth/register"))!;
    expect(JSON.parse((registerCall[1] as RequestInit).body as string).role).toBe("MANUFACTURER");
  });
  it("verifies the cookie-backed session after login before allowing protected work", async () => {
    const fetchMock = mockApi({ "GET /auth/me": [unauthenticated, response({ username: "inspector" })], "POST /auth/login": response({ username: "inspector" }) }); renderApp();
    await signIn("correct-password");
    expect(await appReady()).toBeTruthy(); expect(fetchMock).toHaveBeenCalledWith(`${API}/auth/login`, expect.objectContaining({ method: "POST", credentials: "include" }));
    const paths = calledPaths(fetchMock); expect(paths.lastIndexOf("/auth/me")).toBeGreaterThan(paths.indexOf("/auth/login"));
  });
  it("registers a user and enters the authenticated application", async () => {
    const fetchMock = mockApi({ "GET /auth/me": [unauthenticated, response({ username: "new-user" })], "POST /auth/register": response({ username: "new-user" }) }); renderApp();
    await fillRegistration();
    expect(await appReady()).toBeTruthy(); expect(fetchMock).toHaveBeenCalledWith(`${API}/auth/register`, expect.objectContaining({ method: "POST", credentials: "include" }));
    const paths = calledPaths(fetchMock); expect(paths.lastIndexOf("/auth/me")).toBeGreaterThan(paths.indexOf("/auth/register"));
  });
  it("shows a localized registration error", async () => {
    mockApi({ "GET /auth/me": unauthenticated, "POST /auth/register": response({ detail: "duplicate" }, false, 409) }); renderApp(); await fillRegistration(); expect((await screen.findByRole("alert")).textContent).toBe(en.invalidRegistration);
  });

  describe("on the new-inspection route", () => {
    beforeEach(() => { go("#/new"); URL.createObjectURL = vi.fn(() => "blob:label"); URL.revokeObjectURL = vi.fn(); });
    const choose = async (candidate = file()) => { renderApp(); await appReady(); fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [candidate] } }); };
    const upload = async () => { await choose(); fireEvent.click(await screen.findByRole("button", { name: en.newStart })); };
    it("uploads, processes and opens the finished record", async () => {
      mockApi({ "GET /auth/me": response({ username: "inspector", role: "OFFICIAL" }), ...workflow() });
      await upload();
      expect(await screen.findByRole("heading", { level: 1, name: "Glucose Biscuits" })).toBeTruthy();
      expect(window.location.hash).toBe("#/inspections/inspection-1");
      expect(screen.getByText("MRP format")).toBeTruthy();
    });
    it("downloads an authorized PDF report through the shared credentialed API helper", async () => {
      const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
      const fetchMock = mockApi({ "GET /auth/me": response({ username: "inspector", role: "OFFICIAL" }), ...workflow(), "GET /inspections/inspection-1/report": response({}) });
      await upload(); fireEvent.click(await screen.findByRole("button", { name: en.recExportPdf }));
      await waitFor(() => expect(click).toHaveBeenCalled()); expect(fetchMock).toHaveBeenCalledWith(`${API}/inspections/inspection-1/report`, expect.objectContaining({ credentials: "include" })); click.mockRestore();
    });
    it("shows a recoverable failure and resumes without creating a second inspection", async () => {
      const fetchMock = mockApi({ "GET /auth/me": response({ username: "inspector", role: "OFFICIAL" }), ...workflow(), "POST /inspections/inspection-1/process": [response({ detail: "Database unavailable." }, false, 500), response(done, true, 202)] });
      await upload(); expect((await screen.findByRole("alert")).textContent).toContain("500: Database unavailable.");
      fireEvent.click(screen.getByRole("button", { name: en.tryAgain }));
      expect(await screen.findByRole("heading", { level: 1, name: "Glucose Biscuits" })).toBeTruthy();
      expect(fetchMock.mock.calls.filter(call => (call[1] as RequestInit | undefined)?.method === "POST" && String(call[0]).endsWith("/inspections")).length).toBe(1);
    });
    it("rejects unsupported files before uploading", async () => {
      const fetchMock = mockApi({ "GET /auth/me": response({ username: "inspector", role: "OFFICIAL" }) });
      await choose(new File(["x"], "notes.txt", { type: "text/plain" }));
      expect((await screen.findByRole("alert")).textContent).toBe(en.newBadType);
      expect((screen.getByRole("button", { name: en.newStart }) as HTMLButtonElement).disabled).toBe(true);
      expect(fetchMock.mock.calls.some(call => (call[1] as RequestInit | undefined)?.method === "POST")).toBe(false);
    });
    it("states the backend size limit and rejects larger photos", async () => {
      mockApi({ "GET /auth/me": response({ username: "inspector", role: "OFFICIAL" }), "GET /inspections/config": response({ max_upload_bytes: 1024 * 1024, allowed_types: ["image/png", "image/jpeg"] }) });
      await choose();
      expect(await screen.findByText("PNG or JPEG, up to 1 MB.")).toBeTruthy();
      const big = new File([new Uint8Array(2 * 1024 * 1024)], "big.png", { type: "image/png" });
      fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [big] } });
      expect((await screen.findByRole("alert")).textContent).toBe("This photo is larger than the 1 MB limit.");
    });
    it("accepts a dropped photo and shows its preview", async () => {
      mockApi({ "GET /auth/me": response({ username: "inspector", role: "OFFICIAL" }) });
      renderApp(); await appReady();
      fireEvent.drop(document.querySelector(".dropzone")!, { dataTransfer: { files: [file()] } });
      expect(await screen.findByAltText(`Preview of the ${en.panelBack} photo`)).toBeTruthy();
      expect((screen.getByRole("button", { name: en.newStart }) as HTMLButtonElement).disabled).toBe(false);
    });
  });
});
