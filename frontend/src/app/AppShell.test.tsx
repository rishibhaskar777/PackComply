import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import en from "../locales/en.json";
import { I18nProvider } from "../i18n";
import { API, mockApi, response } from "../test/mockApi";
import { parseHash } from "./router";

const inspection = { id: "abc-123", inspection_number: "PC-20261016-ABC", status: "COMPLETED", product_name: "Parle-G Gluco", review: { status: "UNREVIEWED" }, corrections: [], compliance: { overall_status: "FAIL", verdict: "NON_COMPLIANT", summary: { passed: 0, failed: 1, review: 0 }, findings: [] } };
const renderApp = () => render(<I18nProvider><App /></I18nProvider>);
const nav = async () => within(await screen.findByRole("navigation", { name: en.primaryNavigation }));
const go = (hash: string) => act(() => { window.location.hash = hash; window.dispatchEvent(new HashChangeEvent("hashchange")); });

describe("hash routing and app shell", () => {
  beforeEach(() => { localStorage.clear(); window.history.replaceState(null, "", "#/overview"); });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("parses every route and rejects unknown ones", () => {
    expect(parseHash("#/overview")).toEqual({ name: "overview" });
    expect(parseHash("#/inspections/abc-123")).toEqual({ name: "inspection", id: "abc-123" });
    expect(parseHash("#/rulesets/")).toEqual({ name: "rulesets" });
    expect(parseHash("#/nowhere")).toBeNull();
    expect(parseHash("")).toBeNull();
  });

  it("opens an inspection straight from its URL", async () => {
    window.history.replaceState(null, "", "#/inspections/abc-123");
    const fetchMock = mockApi({ "GET /auth/me": response({ username: "inspector", role: "OFFICIAL" }), "GET /inspections/abc-123": response(inspection), "GET /inspections/abc-123/audit": response([]) });
    renderApp();
    expect(await screen.findByRole("heading", { level: 1, name: "Parle-G Gluco" })).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith(`${API}/inspections/abc-123`, expect.objectContaining({ credentials: "include" }));
    expect((await nav()).getByRole("link", { name: en.inspections }).getAttribute("aria-current")).toBe("page");
    expect(await within(screen.getByRole("navigation", { name: en.breadcrumb })).findByText("PC-20261016-ABC")).toBeTruthy();
  });

  it("defaults to the overview and follows navigation, including back", async () => {
    window.history.replaceState(null, "", "#");
    mockApi({ "GET /auth/me": response({ username: "rishi.bhaskar", role: "OFFICIAL" }), "GET /compliance/rules": response({ ruleset_id: "lm", version: "2", label: "LM(PC) v2", title: "LM(PC) Rules", disclaimer: "", rules: [] }) });
    renderApp();
    const sidebar = await nav();
    await waitFor(() => expect(window.location.hash).toBe("#/overview"));
    expect(sidebar.getByRole("link", { name: en.overview }).getAttribute("aria-current")).toBe("page");
    expect(sidebar.getByRole("link", { name: en.rulesets }).getAttribute("aria-current")).toBeNull();
    go("/rulesets");
    expect(await screen.findByRole("heading", { level: 1, name: en.rulesets })).toBeTruthy();
    expect(sidebar.getByRole("link", { name: en.rulesets }).getAttribute("aria-current")).toBe("page");
    expect(sidebar.getByRole("link", { name: en.overview }).getAttribute("aria-current")).toBeNull();
    go("/overview");
    expect(await screen.findByRole("heading", { level: 1, name: en.overview })).toBeTruthy();
    // The user button shows initials and the role label.
    const account = screen.getByRole("button", { name: new RegExp(en.accountMenu) });
    expect(account.textContent).toContain("RB");
    expect(account.textContent).toContain(en.roleInspector);
  });

  it("sends the top-bar search to the inspections list", async () => {
    const fetchMock = mockApi({ "GET /auth/me": response({ username: "maker", role: "MANUFACTURER" }), "GET /inspections": response({ items: [], page: 1, page_size: 20, total: 0, total_pages: 0 }) });
    renderApp();
    await nav();
    const search = screen.getAllByRole("searchbox", { name: en.searchInspections })[0];
    fireEvent.change(search, { target: { value: "parle" } });
    await waitFor(() => expect(window.location.hash).toBe("#/inspections"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(`${API}/inspections?page=1&page_size=20&search=parle`, expect.anything()));
    expect(await screen.findByText(en.noMatchingInspections)).toBeTruthy();
  });
});
