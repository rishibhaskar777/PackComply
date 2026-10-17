import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../components/ui";
import { I18nProvider } from "../i18n";
import en from "../locales/en.json";
import { API, calledPaths, mockApi, response } from "../test/mockApi";
import type { AuditEvent, Finding, Summary } from "../types";
import { InspectionRecordPage } from "./InspectionRecordPage";

const box = (x: number, y: number, w: number, h: number) => [[x, y], [x + w, y], [x + w, y + h], [x, y + h]];
const finding = (rule_id: string, rule_name: string, status: Finding["status"], extra: Record<string, unknown> = {}) => ({ rule_id, rule_name, status, severity: "HIGH", message: `${rule_name} message`, field: null, value: null, legal_reference: "LM(PC) Rules 2011, Rule 6(1)(e)", fields: [], corrected: false, ...extra });

const base: Summary = {
  id: "i1", inspection_number: "PC-20261017-AAAA", status: "COMPLETED", created_at: "2026-10-17T09:30:00+00:00", completed_at: "2026-10-17T09:31:00+00:00",
  product_name: "Parle-G Gluco Biscuits", maker_name: "CF Foods Pvt Ltd", brand_name: null, review: { status: "UNREVIEWED" }, corrections: [],
  image: { id: "img", original_filename: "label.jpg", width: 1000, height: 800, checksum: "ab12cd34ef56".repeat(5) },
  ocr: { raw_text: "MRP Rs.\nNET WEIGHT 70g", languages: ["en", "hi"], duration_ms: 24800, results: [{ text: "MRP Rs.", confidence: 0.97, bbox: box(100, 100, 100, 20) }, { text: "NET WEIGHT 70g", confidence: 0.93, bbox: box(400, 300, 80, 20) }] },
  extraction: { fields: { mrp: { value: null, confidence: 0, needs_review: true }, mrp_declaration: { value: "present_without_value", confidence: 0.85, needs_review: true, source_bboxes: [box(100, 100, 100, 20)] }, net_quantity: { value: "70 g", confidence: 0.9, needs_review: false, source_bboxes: [box(400, 300, 80, 20)] } } },
  compliance: {
    overall_status: "REVIEW", verdict: "NEEDS_REVIEW", ruleset_label: "LM(PC) 2011 · ruleset v2", ruleset_version: "2", summary: { passed: 1, failed: 1, review: 1, not_applicable: 1 },
    findings: [
      finding("MRP_FORMAT", "Retail sale price (MRP)", "REVIEW", { field: "mrp_declaration", value: "present_without_value", source_bboxes: [box(100, 100, 100, 20)], fields: ["mrp_declaration"] }),
      finding("CONSUMER_CARE_FORMAT", "Consumer-care contact details", "FAIL", { field: "consumer_care_phone", message: "No consumer-care contact found" }),
      finding("NET_QUANTITY_FORMAT", "Net quantity", "PASS", { field: "net_quantity", value: "70 g", source_bboxes: [box(400, 300, 80, 20)], fields: ["net_quantity"], legal_reference: "LM(PC) Rules 2011, Rule 6(1)(c)" }),
      finding("IMPORTER_INFORMATION", "Importer", "NOT_APPLICABLE", { message: "Not an imported product" }),
    ],
  },
  risk: { risk_score: 44, risk_level: "MEDIUM", reasons: [] },
};
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));
/** The record after the inspector sets the MRP: that finding passes and the verdict improves. */
const corrected = (): Summary => {
  const next = clone(base);
  next.extraction!.fields.mrp = { value: "₹10.00", confidence: 1, needs_review: false, source: "INSPECTOR", source_bboxes: [box(100, 100, 100, 20)] };
  next.compliance!.findings[0] = finding("MRP_FORMAT", "Retail sale price (MRP)", "PASS", { field: "mrp", value: "₹10.00", source_bboxes: [box(100, 100, 100, 20)], fields: ["mrp"], corrected: true, message: "MRP ₹10.00 is a positive amount." });
  next.compliance!.verdict = "COMPLIANT"; next.compliance!.summary = { passed: 2, failed: 0, review: 0, not_applicable: 1 }; next.compliance!.findings = next.compliance!.findings.filter(item => item.rule_id !== "CONSUMER_CARE_FORMAT");
  next.corrections = [{ id: "c1", field_name: "mrp", original_value: null, corrected_value: "₹10.00", reason: "Verified on physical pack", corrected_by_name: "Asha", created_at: "2026-10-17T09:40:00+00:00" }];
  next.risk = { risk_score: 0, risk_level: "LOW", reasons: [] };
  return next;
};
const audit: AuditEvent[] = [
  { type: "CREATED", at: "2026-10-17T09:30:00+00:00", actor: "Asha", details: { inspection_number: "PC-20261017-AAAA" } },
  { type: "PHOTO_UPLOADED", at: "2026-10-17T09:30:05+00:00", actor: "Asha", details: { filename: "label.jpg", checksum: "ab12cd34ef56".repeat(5) } },
  { type: "OCR_COMPLETED", at: "2026-10-17T09:30:30+00:00", actor: null, details: { languages: ["en", "hi"], duration_ms: 24800 } },
  { type: "COMPLIANCE_EVALUATED", at: "2026-10-17T09:31:00+00:00", actor: null, details: { ruleset: "LM(PC) 2011 · ruleset v2", ruleset_version: "2", failed: 1, review: 1, passed: 1 } },
  { type: "CORRECTION", at: "2026-10-17T09:40:00+00:00", actor: "Asha", details: { field: "mrp", original: null, corrected: "₹10.00", reason: "OCR misread" } },
];

const routes = (summary: Summary = base) => ({ "GET /inspections/i1": response(summary), "GET /inspections/i1/audit": response(audit), "GET /inspections/i1/image": response({}) });
const renderRecord = (official = true, onExpired = () => undefined) => render(<I18nProvider><ToastProvider><InspectionRecordPage inspectionId="i1" official={official} onExpired={onExpired} /></ToastProvider></I18nProvider>);
const ready = () => screen.findByRole("heading", { level: 1, name: "Parle-G Gluco Biscuits" });
const marker = (id: string) => document.querySelector<HTMLElement>(`[data-marker="${id}"]`)!;
const row = (id: string) => document.querySelector<HTMLElement>(`[data-finding="${id}"]`)!;

const realRect = Element.prototype.getBoundingClientRect;
describe("inspection record page", () => {
  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => "blob:photo"); URL.revokeObjectURL = vi.fn();
    // jsdom has no layout: the photo frame renders 500 x 400 px, half its 1000 x 800 source.
    Element.prototype.getBoundingClientRect = function () { return this.classList?.contains("evidence-frame") ? ({ width: 500, height: 400, left: 0, top: 0, right: 500, bottom: 400, x: 0, y: 0, toJSON: () => ({}) } as DOMRect) : realRect.call(this); };
    localStorage.clear();
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); Element.prototype.getBoundingClientRect = realRect; });

  it("shows the product, verdict, meta line, stats and risk", async () => {
    mockApi(routes()); renderRecord(); await ready();
    expect(screen.getByText(en.verdictNeedsReview)).toBeTruthy();
    expect(screen.getByText("70 g")).toBeTruthy();
    expect(screen.getByText("CF Foods Pvt Ltd")).toBeTruthy();
    expect(document.querySelector(".record-meta")!.textContent).toContain("LM(PC) 2011 · ruleset v2");
    const stats = document.querySelector(".stat-strip")!;
    expect(within(stats as HTMLElement).getByText(en.recStatViolations).nextElementSibling?.textContent).toBe("1");
    expect(within(stats as HTMLElement).getByText(en.recStatToVerify).nextElementSibling?.textContent).toBe("1");
    expect(stats.textContent).toContain("44");
    expect(stats.textContent).toContain(en.medium);
    expect(screen.getByRole("heading", { level: 2, name: "Findings · 4 rules checked" })).toBeTruthy();
  });

  it("orders findings Violations, To verify, Passed and collapses Not applicable", async () => {
    mockApi(routes()); renderRecord(); await ready();
    const groups = [...document.querySelectorAll(".findings__group")].map(group => group.getAttribute("aria-label"));
    expect(groups).toEqual([en.recGroupViolations, en.recGroupReview, en.recGroupPassed, en.notApplicable]);
    expect([...document.querySelectorAll(".finding-row .num-badge")].map(badge => badge.textContent)).toEqual(["1", "2", "3"]);
    expect(screen.queryByText("Not an imported product")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: new RegExp(en.notApplicable) }));
    expect(screen.getByText("Not an imported product")).toBeTruthy();
    expect(within(row("MRP_FORMAT")).getByText("Rule 6(1)(e)")).toBeTruthy();
    expect(within(row("MRP_FORMAT")).getByText(`${en.recRead}: present_without_value`)).toBeTruthy();
  });

  it("places numbered markers from the boxes scaled to the rendered photo", async () => {
    mockApi(routes()); renderRecord(); await ready();
    const mrp = await waitFor(() => { const element = marker("MRP_FORMAT"); expect(element).toBeTruthy(); return element; });
    // MRP is finding 2, its box starts at (100, 100) in a 1000 x 800 photo shown at 500 x 400.
    expect(mrp.textContent).toBe("2");
    expect(mrp.style.left).toBe("50px"); expect(mrp.style.top).toBe("50px");
    expect(marker("NET_QUANTITY_FORMAT").style.left).toBe("200px"); expect(marker("NET_QUANTITY_FORMAT").style.top).toBe("150px");
    expect(marker("CONSUMER_CARE_FORMAT")).toBeNull(); // nothing was read, so nothing to mark
    expect(mrp.className).toContain("evidence-marker--review"); expect(marker("NET_QUANTITY_FORMAT").className).toContain("evidence-marker--pass");
  });

  it("keeps a row and its marker in sync for hover, click and a second click", async () => {
    mockApi(routes()); renderRecord(); await ready();
    await waitFor(() => expect(marker("MRP_FORMAT")).toBeTruthy());
    fireEvent.mouseEnter(row("MRP_FORMAT"));
    expect(marker("MRP_FORMAT").className).toContain("is-hovered"); expect(row("MRP_FORMAT").className).toContain("is-hovered");
    fireEvent.mouseLeave(row("MRP_FORMAT"));
    expect(marker("MRP_FORMAT").className).not.toContain("is-hovered");

    fireEvent.click(within(row("MRP_FORMAT")).getAllByRole("button")[0]);
    expect(marker("MRP_FORMAT").className).toContain("is-selected"); expect(row("MRP_FORMAT").className).toContain("is-selected");
    expect(document.querySelector(".evidence-box--whole")).toBeTruthy(); // the selected finding's full box

    fireEvent.click(marker("NET_QUANTITY_FORMAT"));
    expect(marker("NET_QUANTITY_FORMAT").getAttribute("aria-pressed")).toBe("true"); expect(row("NET_QUANTITY_FORMAT").className).toContain("is-selected");
    expect(row("MRP_FORMAT").className).not.toContain("is-selected");
    fireEvent.click(marker("NET_QUANTITY_FORMAT"));
    expect(row("NET_QUANTITY_FORMAT").className).not.toContain("is-selected");
  });

  it("moves the selection with the arrow keys", async () => {
    mockApi(routes()); renderRecord(); await ready();
    const first = within(row("CONSUMER_CARE_FORMAT")).getAllByRole("button")[0];
    first.focus();
    fireEvent.keyDown(first, { key: "ArrowDown" });
    expect(row("CONSUMER_CARE_FORMAT").className).toContain("is-selected"); // violations come first, then to-verify, then passed
    fireEvent.keyDown(first, { key: "ArrowDown" });
    expect(row("MRP_FORMAT").className).toContain("is-selected");
    fireEvent.keyDown(first, { key: "ArrowDown" });
    expect(row("NET_QUANTITY_FORMAT").className).toContain("is-selected");
    expect(marker("NET_QUANTITY_FORMAT").className).toContain("is-selected");
    fireEvent.keyDown(first, { key: "ArrowUp" });
    expect(row("MRP_FORMAT").className).toContain("is-selected");
  });

  it("opens the OCR text and highlights the box of the chosen line", async () => {
    mockApi(routes()); renderRecord(); await ready();
    fireEvent.click(screen.getByRole("button", { name: en.recShowOcrText }));
    const dialog = screen.getByRole("dialog", { name: en.recOcrTitle });
    fireEvent.click(within(dialog).getByText("NET WEIGHT 70g"));
    await waitFor(() => expect(document.querySelectorAll(".evidence-box").length).toBeGreaterThan(0));
    const whole = document.querySelector<HTMLElement>(".evidence-box--whole")!;
    expect(whole.style.left).toBe("200px"); expect(whole.style.width).toBe("40px");
  });

  it("zooms with the buttons within 1x-4x and fits again", async () => {
    mockApi(routes()); renderRecord(); await ready();
    const level = () => document.querySelector(".evidence-zoom__level")!.textContent;
    expect(level()).toBe("100%");
    fireEvent.click(screen.getByRole("button", { name: en.recZoomIn })); expect(level()).toBe("150%");
    for (let step = 0; step < 6; step += 1) fireEvent.click(screen.getByRole("button", { name: en.recZoomIn }));
    expect(level()).toBe("400%"); expect((screen.getByRole("button", { name: en.recZoomIn }) as HTMLButtonElement).disabled).toBe(true);
    expect(marker("MRP_FORMAT").style.getPropertyValue("--zoom")).toBe("4");
    fireEvent.click(screen.getByRole("button", { name: en.recFit })); expect(level()).toBe("100%");
  });

  it("shows one photo tab for the single back panel of an older record", async () => {
    mockApi(routes()); renderRecord(); await ready();
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map(tab => tab.textContent)).toEqual([en.panelBack]);
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    expect(screen.getByText(/Original photo kept as evidence/)).toBeTruthy();
  });

  it("submits a correction, updates the verdict, counts and risk, and toasts the outcome", async () => {
    const fetchMock = mockApi({ ...routes(), "POST /inspections/i1/corrections": response(corrected()) });
    renderRecord(); await ready();
    // The back panel is already photographed, so the row offers a retake rather than a new photo.
    expect(within(row("MRP_FORMAT")).queryByRole("button", { name: en.recAddPhoto })).toBeNull();
    expect(within(row("MRP_FORMAT")).getByRole("button", { name: en.recRetake }).hasAttribute("disabled")).toBe(false);
    fireEvent.click(within(row("MRP_FORMAT")).getByRole("button", { name: en.recEnterValue }));
    const form = within(screen.getByRole("form", { name: en.recEnterValue }));
    fireEvent.change(form.getByRole("textbox", { name: en.extractionMrp }), { target: { value: "10" } });
    fireEvent.change(form.getByRole("combobox", { name: en.recReasonLabel }), { target: { value: "recReasonMisread" } });
    fireEvent.change(form.getByRole("textbox", { name: en.recNoteLabel }), { target: { value: "stamped in the corner" } });
    fireEvent.click(form.getByRole("button", { name: en.recSaveValue }));

    expect(await screen.findByText("MRP set to ₹10.00 · verdict updated to Compliant")).toBeTruthy();
    const call = fetchMock.mock.calls.find(entry => String(entry[0]).endsWith("/inspections/i1/corrections"))!;
    expect(JSON.parse((call[1] as RequestInit).body as string)).toEqual({ field_name: "mrp", corrected_value: "₹10", reason: "OCR misread: stamped in the corner" });
    await waitFor(() => expect(document.querySelector(".record-verdict")!.textContent).toContain(en.verdictCompliant));
    const stats = document.querySelector(".stat-strip")!;
    await waitFor(() => expect(within(stats as HTMLElement).getByText(en.recStatPassed).nextElementSibling?.textContent).toBe("2"));
    await waitFor(() => expect(within(stats as HTMLElement).getByText(en.recStatViolations).nextElementSibling?.textContent).toBe("0"));
    expect(within(row("MRP_FORMAT")).getByText(en.recInspectorChip)).toBeTruthy();
    expect(screen.queryByRole("form", { name: en.recEnterValue })).toBeNull();
    expect(calledPaths(fetchMock).filter(path => path === "/inspections/i1/audit").length).toBe(2); // reloaded after the change
  });

  it("shows the server's validation message and keeps the form open", async () => {
    mockApi({ ...routes(), "POST /inspections/i1/corrections": response({ detail: "Enter the MRP as an amount in rupees, for example 10, 10.00 or ₹10.00." }, false, 422) });
    renderRecord(); await ready();
    fireEvent.click(within(row("MRP_FORMAT")).getByRole("button", { name: en.recEnterValue }));
    const form = within(screen.getByRole("form", { name: en.recEnterValue }));
    fireEvent.click(form.getByRole("button", { name: en.recSaveValue }));
    expect(await form.findByText(en.recValueRequired)).toBeTruthy(); // empty input never reaches the server
    fireEvent.change(form.getByRole("textbox", { name: en.extractionMrp }), { target: { value: "10" } });
    fireEvent.click(form.getByRole("button", { name: en.recSaveValue }));
    expect(await form.findByText(/Enter the MRP as an amount in rupees/)).toBeTruthy();
    expect(screen.getByRole("form", { name: en.recEnterValue })).toBeTruthy();
  });

  it("lets the inspector choose which field to enter when a rule has several", async () => {
    mockApi({ ...routes(), "POST /inspections/i1/corrections": response(corrected()) });
    renderRecord(); await ready();
    fireEvent.click(within(row("CONSUMER_CARE_FORMAT")).getByRole("button", { name: en.recEnterValue }));
    const form = within(screen.getByRole("form", { name: en.recEnterValue }));
    const select = form.getByRole("combobox", { name: en.recFieldLabel }) as HTMLSelectElement;
    expect([...select.options].map(option => option.value)).toEqual(["consumer_care_phone", "consumer_care_email", "consumer_care_website"]);
    expect(select.value).toBe("consumer_care_phone");
  });

  it("undoes a correction", async () => {
    const fetchMock = mockApi({ ...routes(corrected()), "DELETE /inspections/i1/corrections/c1": response(base) });
    renderRecord(); await ready();
    expect(within(row("MRP_FORMAT")).getByText(en.recInspectorChip)).toBeTruthy();
    fireEvent.click(within(row("MRP_FORMAT")).getByRole("button", { name: /Undo the inspector value for MRP/ }));
    expect(await screen.findByText(/Inspector value for MRP removed · verdict now Needs review/)).toBeTruthy();
    expect(fetchMock.mock.calls.some(call => (call[1] as RequestInit | undefined)?.method === "DELETE")).toBe(true);
    await waitFor(() => expect(within(row("MRP_FORMAT")).queryByText(en.recInspectorChip)).toBeNull());
  });

  it("asks for confirmation listing open items before verifying, then shows the verification", async () => {
    const verified: Summary = { ...clone(base), review: { status: "VERIFIED", by: "Asha Rao", at: "2026-10-17T10:00:00+00:00", remarks: "Matches the pack" } };
    const fetchMock = mockApi({ ...routes(), "POST /inspections/i1/verify": response(verified) });
    renderRecord(); await ready();
    fireEvent.change(screen.getByLabelText(en.recRemarksLabel), { target: { value: "  Matches the pack " } });
    fireEvent.click(screen.getByRole("button", { name: en.recMarkVerified }));
    const dialog = within(screen.getByRole("dialog", { name: en.recConfirmTitle }));
    expect(dialog.getByText("2 items are still open. Verifying records that you reviewed them.")).toBeTruthy();
    expect(dialog.getByText("Consumer-care contact details")).toBeTruthy(); expect(dialog.getByText("Retail sale price (MRP)")).toBeTruthy();
    expect(fetchMock.mock.calls.some(call => String(call[0]).endsWith("/verify"))).toBe(false);
    fireEvent.click(dialog.getByRole("button", { name: en.recVerifyAnyway }));
    expect(await screen.findByText(en.recToastVerified)).toBeTruthy();
    const call = fetchMock.mock.calls.find(entry => String(entry[0]).endsWith("/verify"))!;
    expect(JSON.parse((call[1] as RequestInit).body as string)).toEqual({ remarks: "Matches the pack" });
    await waitFor(() => expect(document.querySelector(".verified-pill")!.textContent).toContain("Verified by Asha Rao"));
    expect(screen.getByText("“Matches the pack”")).toBeTruthy();
    expect(screen.getByRole("button", { name: en.recVerifyAgain })).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("can be cancelled from the confirm dialog without verifying", async () => {
    const fetchMock = mockApi(routes()); renderRecord(); await ready();
    fireEvent.click(screen.getByRole("button", { name: en.recMarkVerified }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: en.recCancel }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(fetchMock.mock.calls.some(call => String(call[0]).endsWith("/verify"))).toBe(false);
  });

  it("verifies at once when nothing is open", async () => {
    const clean = corrected(); clean.compliance!.findings = clean.compliance!.findings.filter(item => item.status === "PASS");
    const fetchMock = mockApi({ ...routes(clean), "POST /inspections/i1/verify": response({ ...clean, review: { status: "VERIFIED", by: "Asha", at: "2026-10-17T10:00:00+00:00" } }) });
    renderRecord(); await ready();
    fireEvent.click(screen.getByRole("button", { name: en.recMarkVerified }));
    expect(await screen.findByText(en.recToastVerified)).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(fetchMock.mock.calls.some(call => String(call[0]).endsWith("/verify"))).toBe(true);
  });

  it("is read-only for manufacturers: self-check wording, no corrections, no verification, no risk", async () => {
    const maker = clone(base); maker.risk = null;
    mockApi(routes(maker)); renderRecord(false); await ready();
    expect(screen.getByText(en.recSelfCheck)).toBeTruthy();
    expect(screen.queryByRole("button", { name: en.recEnterValue })).toBeNull();
    expect(screen.queryByRole("button", { name: en.recMarkVerified })).toBeNull();
    expect(screen.queryByLabelText(en.recRemarksLabel)).toBeNull();
    expect(document.querySelector(".stat-strip__item--risk")).toBeNull();
    expect(screen.getByRole("button", { name: en.recExportPdf })).toBeTruthy();
  });

  it("renders the audit trail with checksum, OCR duration, ruleset and the correction", async () => {
    mockApi(routes()); renderRecord(); await ready();
    const trail = within(await screen.findByRole("heading", { level: 2, name: en.recAuditTitle }).then(heading => heading.closest("section")!));
    await waitFor(() => expect(document.querySelectorAll(".audit-item").length).toBe(5));
    expect([...document.querySelectorAll(".audit-item")].map(item => item.getAttribute("data-event"))).toEqual(["CREATED", "PHOTO_UPLOADED", "OCR_COMPLETED", "COMPLIANCE_EVALUATED", "CORRECTION"]);
    expect(trail.getByText("ab12cd34…")).toBeTruthy();
    expect(trail.getByText("25 s")).toBeTruthy();
    expect(trail.getByText("LM(PC) 2011 · ruleset v2")).toBeTruthy();
    expect(trail.getByText("1 violations · 1 to verify · 1 passed")).toBeTruthy();
    const correction = document.querySelector('[data-event="CORRECTION"]')!;
    expect(correction.textContent).toContain("₹10.00"); expect(correction.textContent).toContain("OCR misread"); expect(correction.textContent).toContain("Asha");
  });

  it("shows a skeleton while loading, then an empty state for a missing inspection", async () => {
    mockApi({ "GET /inspections/i1": response({ detail: "Inspection not found." }, false, 404) });
    renderRecord();
    expect(screen.getByRole("status").textContent).toContain(en.loading);
    expect(await screen.findByText(en.recNotFoundTitle)).toBeTruthy();
    expect(screen.getByText(en.recNotFoundBody)).toBeTruthy();
  });

  it("offers a retry when the service is unavailable", async () => {
    const fetchMock = mockApi({ ...routes(), "GET /inspections/i1": [response({ detail: "Database unavailable." }, false, 503), response(base)] });
    renderRecord();
    expect((await screen.findByRole("alert")).textContent).toContain(en.recUnavailableTitle);
    fireEvent.click(screen.getByRole("button", { name: en.tryAgain }));
    await ready();
    expect(fetchMock).toHaveBeenCalledWith(`${API}/inspections/i1`, expect.anything());
  });

  it("re-analyses on request", async () => {
    const fetchMock = mockApi({ ...routes(), "POST /inspections/i1/reanalyse": response(base) });
    renderRecord(); await ready();
    fireEvent.click(screen.getByRole("button", { name: en.recReanalyse }));
    expect(await screen.findByText(en.recToastReanalysed)).toBeTruthy();
    expect(fetchMock.mock.calls.some(call => String(call[0]).endsWith("/reanalyse"))).toBe(true);
  });
});

describe("record page helpers", () => {
  it("shortens legal references for the chip", async () => {
    const { ruleChip } = await import("./record/Findings");
    expect(ruleChip("LM(PC) Rules 2011, Rule 6(1)(e)")).toBe("Rule 6(1)(e)");
    expect(ruleChip("LM(PC) Rules 2011, Rule 6(11) (as amended 2022)")).toBe("Rule 6(11)");
    expect(ruleChip("FSS (Labelling and Display) Regulations 2020 — cross-check, not LM(PC)")).toBe("FSS (Labelling and Display)…");
    expect(ruleChip(null)).toBeNull();
  });

  it("reads timestamps without an offset as UTC", async () => {
    const { parseTimestamp } = await import("./record/AuditTrail");
    expect(parseTimestamp("2026-10-17T09:34:00").toISOString()).toBe("2026-10-17T09:34:00.000Z");
    expect(parseTimestamp("2026-10-17T09:34:00+05:30").toISOString()).toBe("2026-10-17T04:04:00.000Z");
    expect(parseTimestamp("2026-10-17T09:34:00Z").toISOString()).toBe("2026-10-17T09:34:00.000Z");
  });
});

describe("on a phone", () => {
  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => "blob:photo"); URL.revokeObjectURL = vi.fn();
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("max-width: 639px"), media: query, addEventListener: () => undefined, removeEventListener: () => undefined }));
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("keeps Mark verified in a sticky bar for officials only", async () => {
    mockApi(routes()); renderRecord(); await ready();
    const bar = document.querySelector<HTMLElement>(".verify-bar")!;
    expect(within(bar).getByRole("button", { name: en.recMarkVerified })).toBeTruthy();
    fireEvent.click(within(bar).getByRole("button", { name: en.recMarkVerified }));
    expect(screen.getByRole("dialog", { name: en.recConfirmTitle })).toBeTruthy();
    cleanup();
    mockApi(routes({ ...clone(base), risk: null })); renderRecord(false); await ready();
    expect(document.querySelector(".verify-bar")).toBeNull();
  });
});
