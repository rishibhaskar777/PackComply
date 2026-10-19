import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import en from "../locales/en.json";
import { I18nProvider } from "../i18n";
import { emptySummary, mockApi, response } from "../test/mockApi";
import { homePath, parseHash, redirectFor } from "./router";

const renderApp = () => render(<I18nProvider><App /></I18nProvider>);
const nav = async () => within(await screen.findByRole("navigation", { name: en.primaryNavigation }));
const go = (hash: string) => act(() => { window.location.hash = hash; window.dispatchEvent(new HashChangeEvent("hashchange")); });
const productsEmpty = { total: 0, ready: 0, fix: 0, check: 0, readiness_percent: 0, open_fixes: 0, labels_to_fix: 0, products: [] };
const maker = () => mockApi({
  "GET /auth/me": response({ username: "demo_maker", full_name: "Demo Manufacturer", role: "MANUFACTURER" }),
  "GET /products": response(productsEmpty), "GET /fixes": response({ total_items: 3, groups: [] }),
  "GET /dashboard/summary": response(emptySummary),
});
const inspector = () => mockApi({ "GET /auth/me": response({ username: "demo_inspector", full_name: "Demo Inspector", role: "OFFICIAL" }) });

describe("role routing", () => {
  it("sends a manufacturer from every inspector route to its equivalent, and an inspector away from manufacturer routes", () => {
    expect(redirectFor({ name: "overview" }, "MANUFACTURER")).toBe("/products");
    expect(redirectFor({ name: "risk" }, "MANUFACTURER")).toBe("/products");
    expect(redirectFor({ name: "inspections" }, "MANUFACTURER")).toBe("/products");
    expect(redirectFor({ name: "inspection", id: "a 1" }, "MANUFACTURER")).toBe("/products/a%201");
    expect(redirectFor({ name: "new" }, "MANUFACTURER")).toBe("/check");
    expect(redirectFor({ name: "patterns" }, "MANUFACTURER")).toBe("/fixes");
    expect(redirectFor({ name: "rulesets" }, "MANUFACTURER")).toBe("/rules-guide");
    for (const route of [{ name: "products" }, { name: "product", id: "x" }, { name: "check" }, { name: "fixes" }, { name: "guide" }] as const) {
      expect(redirectFor(route, "OFFICIAL")).toBe("/overview");
      expect(redirectFor(route, "MANUFACTURER")).toBeNull();
    }
    // Shared pages stay put for both roles.
    for (const route of [{ name: "reports" }, { name: "about" }, { name: "capture" }] as const) {
      expect(redirectFor(route, "OFFICIAL")).toBeNull();
      expect(redirectFor(route, "MANUFACTURER")).toBeNull();
    }
    expect(homePath("OFFICIAL")).toBe("/overview");
    expect(homePath("MANUFACTURER")).toBe("/products");
  });

  it("parses the manufacturer routes", () => {
    expect(parseHash("#/products")).toEqual({ name: "products" });
    expect(parseHash("#/products/abc")).toEqual({ name: "product", id: "abc" });
    expect(parseHash("#/check?previous=abc")).toEqual({ name: "check", previous: "abc" });
    expect(parseHash("#/check")).toEqual({ name: "check", previous: undefined });
    expect(parseHash("#/fixes")).toEqual({ name: "fixes" });
    expect(parseHash("#/rules-guide")).toEqual({ name: "guide" });
  });
});

describe("the two portals", () => {
  beforeEach(() => { localStorage.clear(); window.history.replaceState(null, "", "#/overview"); });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); delete document.documentElement.dataset.portal; });

  it("manufacturer: teal portal, own header, nav, footer card, pill and landing page", async () => {
    maker();
    renderApp();
    const sidebar = await nav();
    await waitFor(() => expect(window.location.hash).toBe("#/products"));
    expect(document.documentElement.dataset.portal).toBe("manufacturer");
    expect(screen.getByText(en.portalManufacturerCaption)).toBeTruthy();
    expect(document.querySelector("[data-portal-pill]")?.textContent).toBe(en.portalManufacturer);
    const links = sidebar.getAllByRole("link").map(link => link.textContent?.replace(/\d+$/, ""));
    expect(links).toEqual([en.myProducts, en.checkALabel, en.fixList, en.selfCheckReports, en.rulesGuide, en.aboutAccuracy]);
    expect(sidebar.getByRole("link", { name: en.myProducts }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByText(en.checkBeforePrint)).toBeTruthy();
    expect(screen.queryByText(en.runsOffline)).toBeNull();
    // The open-fix count rides on the Fix list item.
    expect(await within(sidebar.getByRole("link", { name: new RegExp(en.fixList) })).findByText("3")).toBeTruthy();
    expect(screen.queryByRole("link", { name: en.riskQueue })).toBeNull();
    expect(screen.queryByRole("link", { name: en.repeatIssues })).toBeNull();
    expect(screen.queryByRole("searchbox")).toBeNull();
    expect(screen.getAllByRole("button", { name: en.checkALabel }).length).toBeGreaterThan(0);
  });

  it("inspector: indigo portal, inspector header and the existing offline card", async () => {
    inspector();
    renderApp();
    const sidebar = await nav();
    await waitFor(() => expect(document.documentElement.dataset.portal).toBe("inspector"));
    expect(screen.getByText(en.portalInspectorCaption)).toBeTruthy();
    expect(document.querySelector("[data-portal-pill]")?.textContent).toBe(en.portalInspector);
    expect(screen.getByText(en.runsOffline)).toBeTruthy();
    expect(screen.queryByText(en.checkBeforePrint)).toBeNull();
    expect(sidebar.getAllByRole("link").map(link => link.textContent?.replace(/\d+$/, ""))).toEqual([en.overview, en.inspections, en.riskQueue, en.repeatIssues, en.rulesets, en.reports, en.aboutAccuracy]);
  });

  it("a manufacturer who opens an inspector route is redirected, and the deep link to a record becomes the product result", async () => {
    maker();
    window.history.replaceState(null, "", "#/inspections/abc-123");
    renderApp();
    await nav();
    await waitFor(() => expect(window.location.hash).toBe("#/products/abc-123"));
    go("/risk");
    await waitFor(() => expect(window.location.hash).toBe("#/products"));
    go("/patterns");
    await waitFor(() => expect(window.location.hash).toBe("#/fixes"));
    go("/new");
    await waitFor(() => expect(window.location.hash).toBe("#/check"));
  });

  it("an inspector who opens a manufacturer route lands on the overview and never requests manufacturer data", async () => {
    const fetchMock = inspector();
    window.history.replaceState(null, "", "#/products");
    renderApp();
    await nav();
    await waitFor(() => expect(window.location.hash).toBe("#/overview"));
    expect(fetchMock.mock.calls.some(call => String(call[0]).includes("/products") || String(call[0]).includes("/fixes"))).toBe(false);
  });

  it("signing out clears the portal theme", async () => {
    mockApi({ "GET /auth/me": response({ username: "demo_maker", role: "MANUFACTURER" }), "GET /products": response(productsEmpty), "GET /fixes": response({ total_items: 0, groups: [] }), "POST /auth/logout": response({}, true, 204) });
    renderApp();
    await nav();
    expect(document.documentElement.dataset.portal).toBe("manufacturer");
    fireEvent.click(screen.getByRole("button", { name: new RegExp(en.accountMenu) }));
    fireEvent.click(await screen.findByRole("button", { name: en.logout }));
    await waitFor(() => expect(document.documentElement.dataset.portal).toBeUndefined());
  });
});
