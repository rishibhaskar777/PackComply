import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import en from "./locales/en.json";
import hi from "./locales/hi.json";
import { I18nProvider, LANGUAGE_STORAGE_KEY, UI_LANGUAGES } from "./i18n";

function renderApp() { return render(<I18nProvider><App /></I18nProvider>); }
const file = () => new File(["image"], "label.png", { type: "image/png" });
const result = { id: "inspection-1", inspection_number: "INSP-001", status: "COMPLETED", image: { id: "image-1", original_filename: "label.png" }, ocr: { raw_text: "MRP Rs 100", languages: ["en", "hi"], results: [] }, extraction: { fields: { manufacturer: { value: "ABC Foods", confidence: .94, needs_review: false }, mrp: { value: "Rs 100", confidence: .97, needs_review: false } } }, compliance: { overall_status: "REVIEW", summary: { passed: 1, failed: 0, review: 1 }, findings: [{ rule_id: "MRP_FORMAT", rule_name: "MRP format", status: "PASS", severity: "WARNING", message: "Valid MRP", field: "mrp", value: "Rs 100" }] }, quantity_verification: { declared_quantity: "0.5 KG", normalized_quantity: "500 g", unit: "g", status: "PASS", reason: "Valid declared quantity.", rule_code: "NET_QUANTITY_FORMAT", severity: "WARNING", evidence: "Net Qty: 0.5 KG", requirement: null } };
const authenticatedUser = { username: "inspector" };
const response = (body: unknown, ok = true, status = 200) => ({ ok, status, json: async () => body, blob: async () => new Blob(["%PDF-test"], { type: "application/pdf" }) });

function mockAuthenticatedFetch(...responses: ReturnType<typeof response>[]) {
  const fetchMock = vi.fn().mockResolvedValueOnce(response(authenticatedUser));
  for (const nextResponse of responses) fetchMock.mockResolvedValueOnce(nextResponse);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function mockUnauthenticatedFetch() {
  const fetchMock = vi.fn().mockResolvedValueOnce(response({ detail: "Not authenticated" }, false, 401));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("inspection workflow UI", () => {
  beforeEach(() => { localStorage.clear(); mockAuthenticatedFetch(); });
  afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); });
  it("renders English and all language choices", async () => { renderApp(); expect(await screen.findByText(en.dashboard)).toBeTruthy(); expect(screen.getAllByRole("option")).toHaveLength(23); });
  it("shows the official risk-targeting navigation and loads its empty state", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(response({ username: "official", role: "OFFICIAL" })).mockResolvedValueOnce(response({ items: [], total: 0, page: 1, page_size: 20, total_pages: 0, summary: { CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0 } })); vi.stubGlobal("fetch", fetchMock);
    renderApp(); fireEvent.click(await screen.findByRole("button", { name: en.riskTargeting })); expect(await screen.findByText(en.riskTargetingNoResults)).toBeTruthy(); expect(fetchMock).toHaveBeenLastCalledWith("http://localhost:8000/api/risk-targeting?page=1&page_size=20", expect.objectContaining({ credentials: "include" }));
  });
  it("does not show risk-targeting navigation to manufacturers", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response({ username: "manufacturer", role: "MANUFACTURER" }))); renderApp(); await screen.findByText(en.dashboard); expect(screen.queryByRole("button", { name: en.riskTargeting })).toBeNull();
  });
  it("persists a language choice", async () => { renderApp(); fireEvent.change(await screen.findByRole("combobox"), { target: { value: "hi" } }); expect(screen.getByText(hi.dashboard)).toBeTruthy(); expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("hi"); });
  it("has complete locale key sets", async () => { for (const language of UI_LANGUAGES) { const locale = await import(`./locales/${language.code}.json`); expect(Object.keys(locale.default).sort()).toEqual(Object.keys(en).sort()); } });
  it("shows the unauthenticated state after a 401 startup check", async () => {
    const fetchMock = mockUnauthenticatedFetch();
    renderApp();
    expect(await screen.findByText(en.sessionExpired)).toBeTruthy();
    expect(screen.getByRole("textbox", { name: en.username })).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/auth/me", expect.objectContaining({ credentials: "include" }));
  });
  it("renders the registration tab and detects a password mismatch", async () => {
    mockUnauthenticatedFetch(); renderApp();
    fireEvent.click(await screen.findByRole("tab", { name: en.register }));
    fireEvent.change(screen.getByRole("textbox", { name: en.username }), { target: { value: "new-user" } });
    fireEvent.change(screen.getByRole("textbox", { name: en.email }), { target: { value: "new@example.invalid" } });
    fireEvent.change(screen.getByLabelText(en.password), { target: { value: "register-password" } });
    fireEvent.change(screen.getByLabelText(en.confirmPassword), { target: { value: "different-password" } });
    fireEvent.click(screen.getByRole("button", { name: en.registerAccount }));
    expect((await screen.findByRole("alert")).textContent).toBe(en.passwordsDoNotMatch);
  });
  it("shows the rate-limit message when login is locked out", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(response({ detail: "Not authenticated" }, false, 401)).mockResolvedValueOnce(response({ detail: "Too many failed attempts. Try again later." }, false, 429)); vi.stubGlobal("fetch", fetchMock); renderApp();
    fireEvent.change(await screen.findByRole("textbox", { name: en.username }), { target: { value: "inspector" } }); fireEvent.change(screen.getByLabelText(en.password), { target: { value: "wrong-password" } }); fireEvent.click(screen.getByRole("button", { name: en.signIn }));
    expect((await screen.findByRole("alert")).textContent).toBe(en.tooManyAttempts);
  });
  it("registers explicitly as a manufacturer and explains why", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(response({ detail: "Not authenticated" }, false, 401)).mockResolvedValueOnce(response({ username: "new-user" })).mockResolvedValueOnce(response({ username: "new-user" })); vi.stubGlobal("fetch", fetchMock); renderApp();
    fireEvent.click(await screen.findByRole("tab", { name: en.register })); expect(screen.getByText(en.manufacturerRegistrationNote)).toBeTruthy();
    fireEvent.change(screen.getByRole("textbox", { name: en.username }), { target: { value: "new-user" } }); fireEvent.change(screen.getByRole("textbox", { name: en.email }), { target: { value: "new@example.invalid" } }); fireEvent.change(screen.getByLabelText(en.password), { target: { value: "register-password" } }); fireEvent.change(screen.getByLabelText(en.confirmPassword), { target: { value: "register-password" } }); fireEvent.click(screen.getByRole("button", { name: en.registerAccount }));
    await screen.findByText(en.dashboard);
    const registerCall = fetchMock.mock.calls.find(call => String(call[0]).endsWith("/auth/register"))!;
    expect(JSON.parse((registerCall[1] as RequestInit).body as string).role).toBe("MANUFACTURER");
  });
  it("verifies the cookie-backed session after login before allowing protected work", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(response({ detail: "Not authenticated" }, false, 401)).mockResolvedValueOnce(response({ username: "inspector" })).mockResolvedValueOnce(response({ username: "inspector" })); vi.stubGlobal("fetch", fetchMock); renderApp();
    fireEvent.change(await screen.findByRole("textbox", { name: en.username }), { target: { value: "inspector" } }); fireEvent.change(screen.getByLabelText(en.password), { target: { value: "correct-password" } }); fireEvent.click(screen.getByRole("button", { name: en.signIn }));
    expect(await screen.findByText(en.dashboard)).toBeTruthy(); expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/auth/login", expect.objectContaining({ method: "POST", credentials: "include" })); expect(fetchMock).toHaveBeenLastCalledWith("http://localhost:8000/api/auth/me", expect.objectContaining({ credentials: "include" }));
  });
  it("registers a user and enters the authenticated application", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(response({ detail: "Not authenticated" }, false, 401)).mockResolvedValueOnce(response({ username: "new-user" })).mockResolvedValueOnce(response({ username: "new-user" })); vi.stubGlobal("fetch", fetchMock); renderApp();
    fireEvent.click(await screen.findByRole("tab", { name: en.register }));
    fireEvent.change(screen.getByRole("textbox", { name: en.username }), { target: { value: "new-user" } }); fireEvent.change(screen.getByRole("textbox", { name: en.email }), { target: { value: "new@example.invalid" } }); fireEvent.change(screen.getByLabelText(en.password), { target: { value: "register-password" } }); fireEvent.change(screen.getByLabelText(en.confirmPassword), { target: { value: "register-password" } }); fireEvent.click(screen.getByRole("button", { name: en.registerAccount }));
    expect(await screen.findByText(en.dashboard)).toBeTruthy(); expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/auth/register", expect.objectContaining({ method: "POST", credentials: "include" })); expect(fetchMock).toHaveBeenLastCalledWith("http://localhost:8000/api/auth/me", expect.objectContaining({ credentials: "include" }));
  });
  it("shows a localized registration error", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(response({ detail: "Not authenticated" }, false, 401)).mockResolvedValueOnce(response({ detail: "duplicate" }, false, 409)); vi.stubGlobal("fetch", fetchMock); renderApp(); fireEvent.click(await screen.findByRole("tab", { name: en.register })); fireEvent.change(screen.getByRole("textbox", { name: en.username }), { target: { value: "new-user" } }); fireEvent.change(screen.getByRole("textbox", { name: en.email }), { target: { value: "new@example.invalid" } }); fireEvent.change(screen.getByLabelText(en.password), { target: { value: "register-password" } }); fireEvent.change(screen.getByLabelText(en.confirmPassword), { target: { value: "register-password" } }); fireEvent.click(screen.getByRole("button", { name: en.registerAccount })); expect((await screen.findByRole("alert")).textContent).toBe(en.invalidRegistration);
  });
  it("uploads and automatically displays the complete persisted workflow", async () => {
    vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:label"), revokeObjectURL: vi.fn() });
    mockAuthenticatedFetch(response({ id: "inspection-1", inspection_number: "INSP-001" }), response({ id: "image-1", original_filename: "label.png" }), response(result));
    renderApp(); await screen.findByText(en.dashboard); fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [file()] } });
    await screen.findByText("MRP Rs 100"); expect(screen.getByText("ABC Foods")).toBeTruthy(); expect(screen.getByText("MRP format — PASS (WARNING)")).toBeTruthy(); expect(screen.getByText("Inspection complete")).toBeTruthy();
  });
  it("downloads an authorized PDF report through the shared credentialed API helper", async () => {
    vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:report"), revokeObjectURL: vi.fn() }); const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const fetchMock = mockAuthenticatedFetch(response({ id: "inspection-1", inspection_number: "INSP-001" }), response({ id: "image-1", original_filename: "label.png" }), response(result), response({}));
    renderApp(); await screen.findByText(en.dashboard); fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [file()] } }); await screen.findByText(en.downloadPdfReport); fireEvent.click(screen.getByRole("button", { name: en.downloadPdfReport }));
    await waitFor(() => expect(click).toHaveBeenCalled()); expect(fetchMock).toHaveBeenLastCalledWith("http://localhost:8000/api/inspections/inspection-1/report", expect.objectContaining({ credentials: "include" })); click.mockRestore();
  });
  it("renders declared quantity verification with its normalized value and PASS state", async () => {
    vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:label"), revokeObjectURL: vi.fn() });
    mockAuthenticatedFetch(response({ id: "inspection-1", inspection_number: "INSP-001" }), response({ id: "image-1", original_filename: "label.png" }), response(result));
    renderApp(); await screen.findByText(en.dashboard); fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [file()] } });
    expect(await screen.findByText("0.5 KG")).toBeTruthy(); expect(screen.getByText("500 g")).toBeTruthy(); expect(screen.getByText("Pass")).toBeTruthy();
  });
  it("shows a recoverable failure", async () => {
    vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:label"), revokeObjectURL: vi.fn() }); mockAuthenticatedFetch(response({ detail: "Database unavailable." }, false, 500));
    renderApp(); await screen.findByText(en.dashboard); fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [file()] } }); expect((await screen.findByRole("alert")).textContent).toContain("500: Database unavailable.");
  });
  it("starts a new inspection after completion", async () => {
    vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:label"), revokeObjectURL: vi.fn() }); mockAuthenticatedFetch(response({ id: "inspection-1", inspection_number: "INSP-001" }), response({ id: "image-1" }), response(result));
    renderApp(); await screen.findByText(en.dashboard); fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [file()] } }); await screen.findByText("Start New Inspection"); fireEvent.click(screen.getByText("Start New Inspection")); await waitFor(() => expect(screen.queryByText("MRP Rs 100")).toBeNull());
  });
});
