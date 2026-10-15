import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import en from "../locales/en.json";
import { I18nProvider } from "../i18n";
import { API, emptySummary, mockApi, response } from "../test/mockApi";
import { OverviewPage } from "./OverviewPage";

const summary = {
  period: "all", total_inspections: 12,
  verdict_counts: { NON_COMPLIANT: 5, NEEDS_REVIEW: 4, COMPLIANT: 2, PENDING: 1 },
  top_risk: [
    { id: "a1", inspection_number: "PC-1", product_name: "Parle-G Gold", maker: "Parle Products", verdict: "NON_COMPLIANT", risk_score: 72, risk_level: "HIGH" },
    { id: "b2", inspection_number: "PC-2", product_name: null, maker: null, verdict: "NEEDS_REVIEW", risk_score: 20, risk_level: "LOW" },
  ],
  repeat_issues: [{ rule_id: "MRP_FORMAT", title: "Retail sale price (MRP)", legal_reference: "LM(PC) Rules 2011, Rule 6(1)(e)", label_count: 7 }],
  median_processing_seconds: 18.4, ruleset_version: "2",
};
const renderOverview = (official = true) => render(<I18nProvider><OverviewPage official={official} onExpired={() => undefined} /></I18nProvider>);

describe("overview dashboard", () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("renders stats, the risk queue, verdict mix and repeat issues", async () => {
    const fetchMock = mockApi({ "GET /dashboard/summary": response(summary) });
    renderOverview();
    expect(await screen.findByText("12 inspections · LM(PC) ruleset 2")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith(`${API}/dashboard/summary?period=all`, expect.objectContaining({ credentials: "include" }));
    expect(screen.getByText("Median processing time 18s")).toBeTruthy();
    const queue = within(screen.getByRole("table", { name: en.riskQueue }));
    const link = queue.getByRole("link", { name: /Parle-G Gold/ });
    expect(link.getAttribute("href")).toBe("#/inspections/a1");
    expect(queue.getByText("Parle Products")).toBeTruthy();
    expect(queue.getByText("72")).toBeTruthy();
    expect(queue.getByText(en.unknown)).toBeTruthy();
    expect(screen.getByText("Retail sale price (MRP)")).toBeTruthy();
    expect(screen.getByText("LM(PC) Rules 2011, Rule 6(1)(e)")).toBeTruthy();
    expect(screen.getByText("7 labels")).toBeTruthy();
    expect(screen.getByText("11 labels with a verdict")).toBeTruthy();
  });

  it("opens an inspection when a queue row is clicked", async () => {
    mockApi({ "GET /dashboard/summary": response(summary) });
    window.history.replaceState(null, "", "#/overview");
    renderOverview();
    fireEvent.click((await screen.findByText("Parle Products")).closest("tr")!);
    expect(window.location.hash).toBe("#/inspections/a1");
  });

  it("hides numerical risk from manufacturers", async () => {
    mockApi({ "GET /dashboard/summary": response({ ...summary, top_risk: summary.top_risk.map(row => ({ ...row, risk_score: null, risk_level: null })) }) });
    renderOverview(false);
    expect(await screen.findByRole("table", { name: en.needsAttention })).toBeTruthy();
    expect(screen.queryByRole("columnheader", { name: en.risk })).toBeNull();
  });

  it("reloads for the selected period", async () => {
    const fetchMock = mockApi({ "GET /dashboard/summary": response(summary) });
    renderOverview();
    fireEvent.click(await screen.findByRole("tab", { name: en.period7Days }));
    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith(`${API}/dashboard/summary?period=7d`, expect.anything()));
    expect(screen.getByRole("tab", { name: en.period7Days }).getAttribute("aria-selected")).toBe("true");
  });

  it("shows an empty state that starts the first inspection", async () => {
    mockApi({ "GET /dashboard/summary": response(emptySummary) });
    renderOverview();
    expect(await screen.findByText(en.noInspections)).toBeTruthy();
    expect(screen.getByRole("link", { name: en.startFirstInspectionAction }).getAttribute("href")).toBe("#/new");
  });

  it("shows the Live pill only while the backend reports ready", async () => {
    const fetchMock = mockApi({ "GET /dashboard/summary": response(summary), "GET /health/ready": response({ status: "ready" }) });
    render(<I18nProvider><OverviewPage official onExpired={() => undefined} showLive /></I18nProvider>);
    expect(await screen.findByText(en.live)).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith(`${API}/health/ready`, expect.objectContaining({ credentials: "include" }));
    cleanup();
    mockApi({ "GET /dashboard/summary": response(summary), "GET /health/ready": response({ detail: "Database unavailable." }, false, 503) });
    render(<I18nProvider><OverviewPage official onExpired={() => undefined} showLive /></I18nProvider>);
    await screen.findByText("12 inspections · LM(PC) ruleset 2");
    expect(screen.queryByText(en.live)).toBeNull();
  });

  it("explains an unavailable API and retries", async () => {
    const fetchMock = mockApi({ "GET /dashboard/summary": [response({ detail: "Database unavailable." }, false, 503), response(summary)] });
    renderOverview();
    expect((await screen.findByRole("alert")).textContent).toBe(en.serverUnavailable);
    fireEvent.click(screen.getByRole("button", { name: en.tryAgain }));
    expect(await screen.findByText("12 inspections · LM(PC) ruleset 2")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
