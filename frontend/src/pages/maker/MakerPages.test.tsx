import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../components/ui";
import { I18nProvider } from "../../i18n";
import en from "../../locales/en.json";
import { API, calledPaths, mockApi, response } from "../../test/mockApi";
import type { Finding, ProductCard, ProductsSummary, Summary } from "../../types";
import { FixListPage } from "./FixListPage";
import { ProductResultPage } from "./ProductResultPage";
import { ProductsPage } from "./ProductsPage";
import { RulesGuidePage } from "./RulesGuidePage";
import { SelfCheckReportsPage } from "./SelfCheckReportsPage";

const card = (overrides: Partial<ProductCard>): ProductCard => ({
  product_key: "k", inspection_id: "i", product_name: "Product", net_quantity: "70 g", checked_at: "2026-10-18T10:00:00Z", checks: 1, status: "READY", fixes: 0, review: 0, passed: 11, rules_total: 11,
  has_photo: false, image_id: null, previous_fixes: null, improved: false, history: [], ...overrides,
});
const products: ProductsSummary = {
  total: 6, ready: 3, fix: 2, check: 1, readiness_percent: 50, open_fixes: 5, labels_to_fix: 2,
  products: [
    card({ product_key: "gluco", inspection_id: "g2", product_name: "Gluco Biscuits", checks: 2, previous_fixes: 3, improved: true }),
    card({ product_key: "marie", inspection_id: "m1", product_name: "Marie Gold", status: "FIX", fixes: 3, passed: 8 }),
    card({ product_key: "rusk", inspection_id: "r1", product_name: "Rusk", status: "FIX", fixes: 2, passed: 9 }),
    card({ product_key: "toast", inspection_id: "t1", product_name: "Toast", status: "CHECK", review: 1, passed: 10 }),
    card({ product_key: "cracker", inspection_id: "c1", product_name: "Cream Cracker" }),
    card({ product_key: "wafer", inspection_id: "w1", product_name: "Wafers" }),
  ],
};
const mount = (node: React.ReactNode) => render(<I18nProvider><ToastProvider>{node}</ToastProvider></I18nProvider>);

describe("My products", () => {
  beforeEach(() => { window.history.replaceState(null, "", "#/products"); });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it("shows the readiness ring, the headline, the breakdown, the improvement line and one card per product", async () => {
    mockApi({ "GET /products": response(products) });
    mount(<ProductsPage onExpired={vi.fn()} />);
    expect(await screen.findByRole("heading", { level: 2, name: "3 of 6 products are ready to print" })).toBeTruthy();
    expect(screen.getByRole("img", { name: "50% of products are ready to print" })).toBeTruthy();
    expect(screen.getByText("Fix 5 items on 2 labels to be fully compliant with the Legal Metrology (Packaged Commodities) Rules, 2011.")).toBeTruthy();
    expect(screen.getByText(en.labelReadiness)).toBeTruthy();
    const breakdown = within(screen.getByRole("heading", { level: 2, name: en.readinessBreakdown }).closest("section")!);
    expect(breakdown.getByText(en.readyToPrint)).toBeTruthy();
    expect(breakdown.getByText(en.fixNeeded)).toBeTruthy();
    expect(breakdown.getByText(en.checkOnPack)).toBeTruthy();
    expect(screen.getByText("Gluco Biscuits improved from 3 fixes to 0 after your last upload")).toBeTruthy();
    const grid = within(screen.getByRole("list", { name: en.myProducts }));
    expect(grid.getAllByRole("article")).toHaveLength(6);
    expect(grid.getByText("Fix needed · 3")).toBeTruthy();
    expect(grid.getByText("Fix needed · 2")).toBeTruthy();
    expect(grid.getByText("Check on pack · 1")).toBeTruthy();
    expect(grid.getAllByText(en.readyToPrint)).toHaveLength(3);
    expect(grid.getByText(/Checked .* · 2 checks/)).toBeTruthy();
    // Actions: View fixes (teal link) / Report (download) / Review.
    expect(grid.getByRole("link", { name: `${en.viewFixes}: Marie Gold` }).getAttribute("href")).toBe("#/products/m1");
    expect(grid.getByRole("link", { name: `${en.reviewAction}: Toast` }).getAttribute("href")).toBe("#/products/t1");
    expect(grid.getAllByRole("button", { name: new RegExp(`^${en.viewReport}:`) })).toHaveLength(3);
    expect(screen.getAllByRole("link", { name: en.checkALabel }).every(link => link.getAttribute("href") === "#/check")).toBe(true);
  });

  it("filters All / Fix needed / Ready with counts", async () => {
    mockApi({ "GET /products": response(products) });
    mount(<ProductsPage onExpired={vi.fn()} />);
    await screen.findByRole("list", { name: en.myProducts });
    expect(screen.getByRole("tab", { name: /^All\s*6$/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: /^Fix needed\s*2$/ }));
    expect(within(screen.getByRole("list", { name: en.myProducts })).getAllByRole("article")).toHaveLength(2);
    fireEvent.click(screen.getByRole("tab", { name: /^Ready\s*3$/ }));
    expect(within(screen.getByRole("list", { name: en.myProducts })).getAllByRole("article")).toHaveLength(3);
  });

  it("downloads the self-check report from a ready product", async () => {
    URL.createObjectURL = vi.fn(() => "blob:pdf"); URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const fetchMock = mockApi({ "GET /products": response(products), "GET /inspections/g2/self-check-report": response({}) });
    mount(<ProductsPage onExpired={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: `${en.viewReport}: Gluco Biscuits` }));
    await waitFor(() => expect(calledPaths(fetchMock)).toContain("/inspections/g2/self-check-report"));
  });

  it("welcomes a new manufacturer with an empty state that starts the first check", async () => {
    mockApi({ "GET /products": response({ ...products, total: 0, ready: 0, fix: 0, check: 0, readiness_percent: 0, open_fixes: 0, labels_to_fix: 0, products: [] }) });
    mount(<ProductsPage onExpired={vi.fn()} />);
    expect(await screen.findByText(en.noProductsTitle)).toBeTruthy();
    expect(screen.getByRole("link", { name: en.checkALabel }).getAttribute("href")).toBe("#/check");
    expect(screen.queryByRole("img", { name: /ready to print/ })).toBeNull();
  });

  it("explains an unavailable server and retries", async () => {
    const fetchMock = mockApi({ "GET /products": [response({ detail: "down" }, false, 503), response(products)] });
    mount(<ProductsPage onExpired={vi.fn()} />);
    expect((await screen.findByRole("alert")).textContent).toContain(en.serverUnavailable);
    fireEvent.click(screen.getByRole("button", { name: en.tryAgain }));
    expect(await screen.findByRole("heading", { level: 2, name: /products are ready to print/ })).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("asks the app to sign in again when the session ended", async () => {
    mockApi({ "GET /products": response({ detail: "Not authenticated." }, false, 401) });
    const onExpired = vi.fn();
    mount(<ProductsPage onExpired={onExpired} />);
    await waitFor(() => expect(onExpired).toHaveBeenCalled());
  });
});

const finding = (overrides: Partial<Finding> & { rule_id: string }): Finding => ({
  rule_name: "Rule", status: "PASS", severity: "HIGH", message: "ok", field: null, value: null, source_bboxes: null, image_id: null, fields: [], pass_text: "Passed sentence.", ...overrides,
});
const resultSummary = (extra: Partial<Summary> = {}): Summary => ({
  id: "m1", inspection_number: "PC-1", status: "COMPLETED", product_name: "Marie Gold", completed_at: "2026-10-18T10:00:00Z",
  image: { id: "img1", original_filename: "a.jpg", width: 1000, height: 800, panel: "BACK" },
  images: [{ id: "img1", panel: "BACK", width: 1000, height: 800, uploaded_at: "2026-10-18T09:59:00Z", active: true }],
  ocr: { raw_text: "", languages: ["en"], results: [] },
  extraction: { fields: { consumer_care_phone: { value: null, confidence: 0, needs_review: true, source_bboxes: [[[100, 100], [300, 100], [300, 140], [100, 140]]], image_id: "img1" } } },
  compliance: {
    overall_status: "FAIL", verdict: "NON_COMPLIANT", ruleset_label: "LM(PC) 2011 ruleset v2", summary: { passed: 2, failed: 2, review: 1, not_applicable: 1 },
    findings: [
      finding({ rule_id: "CONSUMER_CARE_FORMAT", rule_name: "Consumer-care contact details", status: "FAIL", severity: "WARNING", message: "Consumer care e-mail not found.", field: "consumer_care_phone", legal_reference: "LM(PC) Rules 2011, Rule 6(2)", rule_chip: "Rule 6(2)", fix_title: "Consumer care contact is incomplete", fix_steps: ["Print a consumer care line."], example: "Consumer Care, Sample Foods Pvt Ltd, Mumbai 400057 · 1800-123-4567 · care@samplefoods.in", source_bboxes: [[[100, 100], [300, 100], [300, 140], [100, 140]]], image_id: "img1" }),
      finding({ rule_id: "MRP_FORMAT", rule_name: "Retail sale price (MRP)", status: "FAIL", severity: "HIGH", message: "MRP not found.", legal_reference: "LM(PC) Rules 2011, Rule 6(1)(e)", rule_chip: "Rule 6(1)(e)", fix_title: "MRP is missing or unclear", fix_steps: ["Print MRP."], example: "MRP ₹49.00 (incl. of all taxes)" }),
      finding({ rule_id: "MANUFACTURE_DATE", rule_name: "Month and year of manufacture", status: "REVIEW", severity: "WARNING", message: "Not visible.", fix_title: "Month and year of manufacture is missing", fix_steps: ["Print Mfd."], example: "Mfd: 10/2026" }),
      finding({ rule_id: "NET_QUANTITY_FORMAT", rule_name: "Net quantity declaration", status: "PASS", pass_text: "Net quantity is declared in a standard unit." }),
      finding({ rule_id: "PRODUCT_IDENTITY", rule_name: "Common name", status: "PASS", pass_text: "The common name of the product is stated." }),
      finding({ rule_id: "COUNTRY_OF_ORIGIN", rule_name: "Country of origin", status: "NOT_APPLICABLE" }),
    ],
  },
  ...extra,
});

describe("Self-check result", () => {
  const realRect = Element.prototype.getBoundingClientRect;
  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => "blob:photo"); URL.revokeObjectURL = vi.fn();
    window.history.replaceState(null, "", "#/products/m1");
    // jsdom has no layout: give the evidence frame a size so the markers can be placed.
    Element.prototype.getBoundingClientRect = function () { return this.classList?.contains("evidence-frame") ? ({ width: 500, height: 400, left: 0, top: 0, right: 500, bottom: 400, x: 0, y: 0, toJSON: () => ({}) } as DOMRect) : realRect.call(this); };
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); Element.prototype.getBoundingClientRect = realRect; });
  const routes = (summary = resultSummary(), extra: Record<string, ReturnType<typeof response>> = {}) => ({
    "GET /inspections/m1": response(summary), "GET /inspections/m1/images/img1": response({}),
    "GET /products": response({ ...products, products: [card({ inspection_id: "m1", product_key: "marie", product_name: "Marie Gold", status: "FIX", fixes: 2, previous_fixes: 5, improved: true, checks: 2, history: [{ inspection_id: "m1", checked_at: "2026-10-18T10:00:00Z", fixes: 2, review: 1, status: "FIX" }, { inspection_id: "m0", checked_at: "2026-10-10T10:00:00Z", fixes: 5, review: 0, status: "FIX" }] })] }),
    ...extra,
  });

  it("states what to fix before printing, with the plain-language fix cards", async () => {
    mockApi(routes());
    mount(<ProductResultPage inspectionId="m1" onExpired={vi.fn()} />);
    expect(await screen.findByRole("heading", { level: 1, name: "Marie Gold" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: "Fix 2 items before printing" })).toBeTruthy();
    expect(screen.getByText(/Self-check · .* · 2 of 5 rules already pass/)).toBeTruthy();
    expect(screen.getByText("Improved from 5 fixes to 2 since your previous upload")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: en.fixListTitle })).toBeTruthy();
    const cards = screen.getAllByRole("listitem").filter(item => item.hasAttribute("data-rule"));
    // Failed rules first (most severe first), then the item to verify on the pack.
    expect(cards.map(item => item.getAttribute("data-rule"))).toEqual(["MRP_FORMAT", "CONSUMER_CARE_FORMAT", "MANUFACTURE_DATE"]);
    const care = within(cards[1]);
    expect(care.getByRole("heading", { level: 3, name: "Consumer care contact is incomplete" })).toBeTruthy();
    expect(care.getByText("Rule 6(2)")).toBeTruthy();
    expect(care.getByText(en.howToFix)).toBeTruthy();
    expect(care.getByText("Print a consumer care line.")).toBeTruthy();
    expect(care.getByText(en.exampleLabel)).toBeTruthy();
    expect(care.getByText("Consumer Care, Sample Foods Pvt Ltd, Mumbai 400057 · 1800-123-4567 · care@samplefoods.in").className).toContain("mono");
    expect(within(cards[2]).getByText(en.checkOnPack)).toBeTruthy();
    expect(screen.getByText(en.selfAssessDisclaimer)).toBeTruthy();
    expect(screen.queryByText(en.recStatViolations)).toBeNull();
    expect(screen.queryByText(en.risk)).toBeNull();
  });

  it("Show on label selects the marker and zooms to the box", async () => {
    mockApi(routes());
    mount(<ProductResultPage inspectionId="m1" onExpired={vi.fn()} />);
    const careCard = (await screen.findAllByRole("listitem")).find(item => item.getAttribute("data-rule") === "CONSUMER_CARE_FORMAT")!;
    // A finding with no box on the photo offers no highlight.
    expect(within(screen.getAllByRole("listitem").find(item => item.getAttribute("data-rule") === "MRP_FORMAT")!).queryByRole("button", { name: new RegExp(en.showOnLabel) })).toBeNull();
    const show = within(careCard).getByRole("button", { name: new RegExp(en.showOnLabel) });
    fireEvent.click(show);
    expect(show.getAttribute("aria-pressed")).toBe("true");
    expect(careCard.className).toContain("is-selected");
    await waitFor(() => expect(document.querySelector(".evidence-marker.is-selected")).toBeTruthy());
    fireEvent.click(show);
    expect(show.getAttribute("aria-pressed")).toBe("false");
  });

  it("lists the declarations already correct in plain language, collapsed until opened", async () => {
    mockApi(routes());
    mount(<ProductResultPage inspectionId="m1" onExpired={vi.fn()} />);
    const toggle = await screen.findByRole("button", { name: "2 declarations already correct" });
    expect(screen.queryByText("Net quantity is declared in a standard unit.")).toBeNull();
    fireEvent.click(toggle);
    expect(screen.getByText("Net quantity is declared in a standard unit.")).toBeTruthy();
    expect(screen.getByText("The common name of the product is stated.")).toBeTruthy();
  });

  it("Upload corrected label continues this product; Download fetches the self-check PDF", async () => {
    const fetchMock = mockApi(routes(resultSummary(), { "GET /inspections/m1/self-check-report": response({}) }));
    URL.createObjectURL = vi.fn(() => "blob:pdf");
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    mount(<ProductResultPage inspectionId="m1" onExpired={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: en.downloadSelfCheck }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(`${API}/inspections/m1/self-check-report`, expect.anything()));
    fireEvent.click(screen.getByRole("button", { name: en.uploadCorrected }));
    expect(window.location.hash).toBe("#/check?previous=m1");
  });

  it("says Ready to print when nothing is left to fix or verify", async () => {
    const clean = resultSummary();
    clean.compliance = { ...clean.compliance!, overall_status: "PASS", verdict: "COMPLIANT", summary: { passed: 5, failed: 0, review: 0 }, findings: clean.compliance!.findings.filter(item => item.status === "PASS") };
    mockApi(routes(clean));
    mount(<ProductResultPage inspectionId="m1" onExpired={vi.fn()} />);
    expect(await screen.findByRole("heading", { level: 2, name: en.bannerReady })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: en.nothingToFix })).toBeTruthy();
    expect(document.querySelectorAll(".fix-card")).toHaveLength(0);
  });

  it("says Verify on the pack when only review items remain", async () => {
    const review = resultSummary();
    review.compliance = { ...review.compliance!, verdict: "NEEDS_REVIEW", summary: { passed: 2, failed: 0, review: 1 }, findings: review.compliance!.findings.filter(item => item.status !== "FAIL") };
    mockApi(routes(review));
    mount(<ProductResultPage inspectionId="m1" onExpired={vi.fn()} />);
    expect(await screen.findByRole("heading", { level: 2, name: "Verify 1 item on the pack" })).toBeTruthy();
  });

  it("shows a not-found state for another account's check", async () => {
    mockApi({ "GET /inspections/m1": response({ detail: "Inspection not found." }, false, 404) });
    mount(<ProductResultPage inspectionId="m1" onExpired={vi.fn()} />);
    expect(await screen.findByText(en.productNotFoundTitle)).toBeTruthy();
  });
});

describe("Fix list, reports and rules guide", () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it("groups open fixes by rule with the products they affect", async () => {
    mockApi({ "GET /fixes": response({ total_items: 5, groups: [
      { rule_id: "CONSUMER_CARE_FORMAT", title: "Consumer care contact incomplete", rule_name: "Consumer care", legal_reference: "LM(PC) Rules 2011, Rule 6(2)", rule_chip: "Rule 6(2)", severity: "WARNING", status: "FAIL", product_count: 4, fix_steps: ["Print the consumer care line."], example: "Consumer Care, Sample Foods", products: [{ inspection_id: "a", product_name: "Rusk", status: "FAIL" }, { inspection_id: "b", product_name: "Toast", status: "FAIL" }] },
      { rule_id: "MANUFACTURE_DATE", title: "Month and year missing", rule_name: "Date", legal_reference: null, rule_chip: null, severity: "WARNING", status: "REVIEW", product_count: 1, fix_steps: [], example: null, products: [{ inspection_id: "c", product_name: "Wafers", status: "REVIEW" }] },
    ] }) });
    mount(<FixListPage onExpired={vi.fn()} />);
    expect(await screen.findByRole("heading", { level: 2, name: "Consumer care contact incomplete · 4 products" })).toBeTruthy();
    expect(screen.getByText("5 items to fix across your latest checks")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Rusk/ }).getAttribute("href")).toBe("#/products/a");
    expect(screen.getByText("Rule 6(2)")).toBeTruthy();
    expect(screen.getByText(en.checkOnPack)).toBeTruthy();
  });

  it("shows a calm empty state when nothing needs fixing", async () => {
    mockApi({ "GET /fixes": response({ total_items: 0, groups: [] }) });
    mount(<FixListPage onExpired={vi.fn()} />);
    expect(await screen.findByText(en.noFixesTitle, { selector: "h2" })).toBeTruthy();
  });

  it("lists self-checks with a readiness status and a PDF download", async () => {
    URL.createObjectURL = vi.fn(() => "blob:pdf"); URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const fetchMock = mockApi({ "GET /inspections": response({ items: [
      { id: "a", inspection_number: "PC-A", status: "COMPLETED", created_at: "2026-10-18T10:00:00Z", updated_at: "2026-10-18T10:00:00Z", product_name: "Rusk", compliance_status: "FAIL", verdict: "NON_COMPLIANT", image_filename: null },
      { id: "b", inspection_number: "PC-B", status: "COMPLETED", created_at: "2026-10-17T10:00:00Z", updated_at: "2026-10-17T10:00:00Z", product_name: "Toast", compliance_status: "PASS", verdict: "COMPLIANT", image_filename: null },
    ], page: 1, page_size: 20, total: 2, total_pages: 1 }), "GET /inspections/a/self-check-report": response({}) });
    mount(<SelfCheckReportsPage onExpired={vi.fn()} />);
    const table = within(await screen.findByRole("table", { name: en.selfCheckReports }));
    expect(table.getByText(en.fixNeeded)).toBeTruthy();
    expect(table.getByText(en.readyToPrint)).toBeTruthy();
    fireEvent.click(table.getByRole("button", { name: `${en.downloadSelfCheck}: Rusk` }));
    await waitFor(() => expect(calledPaths(fetchMock)).toContain("/inspections/a/self-check-report"));
  });

  it("explains every rule in plain language with its reference and an example", async () => {
    mockApi({ "GET /compliance/rules": response({ ruleset_id: "LM_PC_2011", version: "2", label: "v2", title: "t", disclaimer: "", rules: [
      { code: "MRP_FORMAT", title: "Retail sale price (MRP)", description: "The label must declare the MRP.", legal_reference: "LM(PC) Rules 2011, Rule 6(1)(e)", severity: "HIGH", expected: "MRP ₹<amount>", rule_chip: "Rule 6(1)(e)", example: "MRP ₹49.00 (incl. of all taxes)" },
      { code: "FSSAI_LICENSE_PRESENT", title: "FSSAI licence", description: "Food cross-check.", legal_reference: "FSS regulations", severity: "INFO", expected: "14 digits", rule_chip: null, example: "FSSAI Lic. No. 11224999000123" },
    ] }) });
    mount(<RulesGuidePage onExpired={vi.fn()} />);
    expect(await screen.findByRole("heading", { level: 2, name: "Retail sale price (MRP)" })).toBeTruthy();
    expect(screen.getByText("The 2 rules every label is checked against, in plain language.")).toBeTruthy();
    expect(screen.getByText("Rule 6(1)(e)")).toBeTruthy();
    expect(screen.getByText("MRP ₹49.00 (incl. of all taxes)")).toBeTruthy();
    expect(screen.getAllByText(en.mustPrint)).toHaveLength(2);
  });
});
