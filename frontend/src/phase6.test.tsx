import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { documentTitle } from "./app/AppShell";
import { routeFor } from "./app/router";
import { ToastProvider } from "./components/ui";
import { I18nProvider } from "./i18n";
import en from "./locales/en.json";
import { NewInspectionPage } from "./pages/NewInspectionPage";
import { AboutPage } from "./pages/AboutPage";
import { AuditTrail } from "./pages/record/AuditTrail";
import { calledPaths, emptySummary, mockApi, response } from "./test/mockApi";
import { sampleReport } from "./test/sampleReport";

const t = (key: keyof typeof en) => en[key];
const renderApp = () => render(<I18nProvider><App /></I18nProvider>);
const productsEmpty = { total: 0, ready: 0, fix: 0, check: 0, readiness_percent: 0, open_fixes: 0, labels_to_fix: 0, products: [] };
const maker = () => mockApi({
  "GET /auth/me": response({ username: "demo_maker", full_name: "Demo Manufacturer", role: "MANUFACTURER" }),
  "GET /products": response(productsEmpty), "GET /fixes": response({ total_items: 0, groups: [] }), "GET /dashboard/summary": response(emptySummary),
});
const inspector = () => mockApi({ "GET /auth/me": response({ username: "demo_inspector", full_name: "Demo Inspector", role: "OFFICIAL" }), "GET /dashboard/summary": response(emptySummary) });
/** The shell is on screen and its effects (the key handlers) have run. */
const shellReady = async () => { await screen.findByRole("navigation", { name: en.primaryNavigation }); await act(async () => undefined); };
const reset = () => { cleanup(); vi.unstubAllGlobals(); delete document.documentElement.dataset.portal; };

describe("document titles", () => {
  it("name the page for the role, with the record for a detail page", () => {
    expect(documentTitle({ name: "overview" }, "OFFICIAL", t)).toBe("Today's inspection priorities · PackComply");
    expect(documentTitle({ name: "products" }, "MANUFACTURER", t)).toBe("My products · PackComply");
    expect(documentTitle({ name: "fixes" }, "MANUFACTURER", t)).toBe("Fix list · PackComply");
    expect(documentTitle({ name: "check" }, "MANUFACTURER", t)).toBe("Check a label · PackComply");
    expect(documentTitle({ name: "reports" }, "MANUFACTURER", t)).toBe("Self-check reports · PackComply");
    expect(documentTitle({ name: "reports" }, "OFFICIAL", t)).toBe("Reports · PackComply");
    expect(documentTitle({ name: "new" }, "OFFICIAL", t)).toBe("New inspection · PackComply");
    expect(documentTitle({ name: "risk" }, "OFFICIAL", t)).toBe("Risk queue · PackComply");
    expect(documentTitle({ name: "about" }, "OFFICIAL", t)).toBe("About & accuracy · PackComply");
    expect(documentTitle({ name: "inspection", id: "a" }, "OFFICIAL", t, "PC-1")).toBe("PC-1 · Inspection details · PackComply");
    expect(documentTitle({ name: "product", id: "a" }, "MANUFACTURER", t, "Gluco Biscuits")).toBe("Gluco Biscuits · Self-check result · PackComply");
    expect(documentTitle({ name: "notfound" }, "OFFICIAL", t)).toBe("Page not found · PackComply");
    expect(documentTitle({ name: "notfound" }, "MANUFACTURER", t)).toBe("Page not found · PackComply");
  });

  it("every route has a title that is more than the product name", () => {
    const routes = ["overview", "inspections", "new", "risk", "patterns", "rulesets", "reports", "about", "products", "fixes", "guide", "check", "capture", "notfound"] as const;
    for (const role of ["OFFICIAL", "MANUFACTURER"]) for (const name of routes) {
      const title = documentTitle({ name } as never, role, t);
      expect(title.endsWith(" · PackComply") && title.length > " · PackComply".length).toBe(true);
    }
  });
});

describe("not found", () => {
  beforeEach(() => { localStorage.clear(); window.history.replaceState(null, "", "#/overview"); });
  afterEach(reset);

  it("treats an empty hash as the default page but an unknown one as not found", () => {
    expect(routeFor("")).toEqual({ name: "overview" });
    expect(routeFor("#/")).toEqual({ name: "overview" });
    expect(routeFor("#/nowhere")).toEqual({ name: "notfound" });
    expect(routeFor("#/inspections/a/b")).toEqual({ name: "notfound" });
    expect(routeFor("#/products")).toEqual({ name: "products" });
  });

  it("shows a friendly page with a link to the inspector home", async () => {
    inspector();
    window.history.replaceState(null, "", "#/nowhere");
    renderApp();
    expect(await screen.findByText(en.notFoundBody)).toBeTruthy();
    expect(window.location.hash).toBe("#/nowhere");
    await waitFor(() => expect(document.title).toBe(`${en.notFoundTitle} · ${en.appName}`));
    const link = screen.getByRole("link", { name: en.notFoundHome.replace("{page}", en.overview) });
    expect(link.getAttribute("href")).toBe("#/overview");
  });

  it("links a manufacturer to My products", async () => {
    maker();
    window.history.replaceState(null, "", "#/definitely/missing");
    renderApp();
    const link = await screen.findByRole("link", { name: en.notFoundHome.replace("{page}", en.myProducts) });
    expect(link.getAttribute("href")).toBe("#/products");
  });
});

describe("page titles in the app", () => {
  beforeEach(() => { localStorage.clear(); window.history.replaceState(null, "", "#/overview"); });
  afterEach(reset);

  it("inspector home carries its own title", async () => {
    inspector();
    renderApp();
    await screen.findByRole("navigation", { name: en.primaryNavigation });
    await waitFor(() => expect(document.title).toBe("Today's inspection priorities · PackComply"));
  });

  it("manufacturer home carries its own title", async () => {
    maker();
    window.history.replaceState(null, "", "#/products");
    renderApp();
    await waitFor(() => expect(document.title).toBe("My products · PackComply"));
  });

  it("the sign-in page has its own title", async () => {
    mockApi({ "GET /auth/me": response({ detail: "no" }, false, 401) });
    renderApp();
    await waitFor(() => expect(document.title).toBe(`${en.signIn} · ${en.appName}`));
  });
});

describe("keyboard shortcuts", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(reset);

  it("? opens the list, Escape closes it, and typing in a field does not trigger anything", async () => {
    mockApi({ "GET /auth/me": response({ username: "i", role: "OFFICIAL" }), "GET /compliance/rules": response({ ruleset_id: "x", version: "2", label: "L", title: "T", disclaimer: "", rules: [] }), "GET /system/evaluation": response({ schema: 1, runs: [] }) });
    window.history.replaceState(null, "", "#/about");
    renderApp();
    await shellReady();
    const search = screen.getByRole("searchbox", { name: en.searchInspections });
    fireEvent.keyDown(search, { key: "?" });
    expect(screen.queryByRole("dialog", { name: en.shortcutsTitle })).toBeNull();
    fireEvent.keyDown(search, { key: "n" });
    expect(window.location.hash).toBe("#/about");

    fireEvent.keyDown(document.body, { key: "?" });
    const dialog = await screen.findByRole("dialog", { name: en.shortcutsTitle });
    expect(within(dialog).getByText("Ctrl K")).toBeTruthy();
    expect(within(dialog).getByText(en.overview)).toBeTruthy();
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: en.shortcutsTitle })).toBeNull());
  });

  it("G then O goes home for an inspector, and N starts a new inspection", async () => {
    inspector();
    window.history.replaceState(null, "", "#/risk");
    renderApp();
    await shellReady();
    fireEvent.keyDown(document.body, { key: "o" }); // without G first: nothing
    expect(window.location.hash).toBe("#/risk");
    fireEvent.keyDown(document.body, { key: "g" });
    fireEvent.keyDown(document.body, { key: "o" });
    await waitFor(() => expect(window.location.hash).toBe("#/overview"));
    fireEvent.keyDown(document.body, { key: "n" });
    await waitFor(() => expect(window.location.hash).toBe("#/new"));
  });

  it("G then P goes home for a manufacturer, and N starts a new check", async () => {
    maker();
    window.history.replaceState(null, "", "#/fixes");
    renderApp();
    await shellReady();
    fireEvent.keyDown(document.body, { key: "g" });
    fireEvent.keyDown(document.body, { key: "p" });
    await waitFor(() => expect(window.location.hash).toBe("#/products"));
    fireEvent.keyDown(document.body, { key: "n" });
    await waitFor(() => expect(window.location.hash).toBe("#/check"));
  });
});

describe("PDF artwork on the manufacturer check page", () => {
  const routes = () => ({
    "GET /inspections/config": response({ max_upload_bytes: 10485760, ocr_max_image_dimension: 1280 }),
    "POST /inspections": response({ id: "n1" }, true, 201),
    "POST /inspections/n1/image": response({ id: "img", panel: "BACK" }, true, 201),
  });
  const mount = (isMaker: boolean) => render(<I18nProvider><ToastProvider><NewInspectionPage maker={isMaker} onExpired={vi.fn()} onCompleted={vi.fn()} /></ToastProvider></I18nProvider>);
  const input = () => screen.getByLabelText(en.newChoosePanel.replace("{panel}", en.panelBack)) as HTMLInputElement;
  const pdf = () => new File(["%PDF-1.4"], "label.pdf", { type: "application/pdf" });
  beforeEach(() => { URL.createObjectURL = vi.fn(() => "blob:photo"); URL.revokeObjectURL = vi.fn(); });
  afterEach(() => { reset(); window.location.hash = ""; });

  it("a manufacturer can choose a PDF, which goes up untouched (not re-encoded as an image)", async () => {
    const fetchMock = mockApi(routes());
    mount(true);
    expect(input().accept).toContain("application/pdf");
    expect(screen.getByText(/PNG, JPEG or PDF \(first page\)/)).toBeTruthy();
    fireEvent.change(input(), { target: { files: [pdf()] } });
    await waitFor(() => expect(calledPaths(fetchMock)).toContain("/inspections/n1/image?process_now=true"));
    const upload = fetchMock.mock.calls.find(call => String(call[0]).includes("/n1/image"))![1] as RequestInit;
    const sent = (upload.body as FormData).get("image") as File;
    expect(sent.name).toBe("label.pdf");
    expect(sent.type).toBe("application/pdf");
    expect(screen.getByText(en.newPdfChosen)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("an inspector cannot choose a PDF", async () => {
    const fetchMock = mockApi(routes());
    mount(false);
    expect(input().accept).not.toContain("pdf");
    fireEvent.change(input(), { target: { files: [pdf()] } });
    expect((await screen.findByRole("alert")).textContent).toBe(en.newBadType);
    expect(calledPaths(fetchMock)).not.toContain("/inspections");
  });

  it("a manufacturer is told which types are accepted when the file is something else", async () => {
    mockApi(routes());
    mount(true);
    fireEvent.change(input(), { target: { files: [new File(["x"], "label.gif", { type: "image/gif" })] } });
    expect((await screen.findByRole("alert")).textContent).toBe(en.newBadTypeMaker);
  });
});

describe("About: development and unseen side by side", () => {
  afterEach(reset);
  const rules = { ruleset_id: "LM_PC_2011", version: "2", label: "L", title: "T", disclaimer: "", rules: [] };

  it("shows a comparison of the latest development run and the latest unseen run when both exist", async () => {
    mockApi({ "GET /compliance/rules": response(rules), "GET /system/evaluation": response(sampleReport) });
    render(<I18nProvider><AboutPage onExpired={() => undefined} /></I18nProvider>);
    expect(await screen.findByText(en.aboutCompareTitle)).toBeTruthy();
    expect(screen.getByText(/development-2026-10 \(/)).toBeTruthy();
    expect(screen.getByText(/unseen-2026-10-29 \(/)).toBeTruthy();
  });

  it("does not show a comparison with only a development run", async () => {
    const dev = { ...sampleReport, runs: sampleReport.runs.filter(run => run.kind === "development") };
    mockApi({ "GET /compliance/rules": response(rules), "GET /system/evaluation": response(dev) });
    render(<I18nProvider><AboutPage onExpired={() => undefined} /></I18nProvider>);
    expect(await screen.findByText(en.aboutKindDevelopment)).toBeTruthy();
    expect(screen.queryByText(en.aboutCompareTitle)).toBeNull();
  });
});

describe("audit trail", () => {
  afterEach(reset);
  it("shows the source PDF and its SHA-256 for an artwork PDF that was rendered", () => {
    const sha = "ab12cd34ef56".repeat(5);
    render(<I18nProvider><AuditTrail loading={false} events={[{ type: "PDF_RENDERED", at: "2026-10-20T09:00:00+00:00", actor: "Maker", details: { source_filename: "label.pdf", source_sha256: sha, pages: 2 } }]} /></I18nProvider>);
    const item = document.querySelector('[data-event="PDF_RENDERED"]') as HTMLElement;
    expect(within(item).getByText(en.recAuditPdf)).toBeTruthy();
    expect(within(item).getByText("label.pdf")).toBeTruthy();
    expect(within(item).getByTitle(sha).textContent).toBe("ab12cd34…");
  });
});
