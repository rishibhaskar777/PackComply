import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../components/ui";
import { I18nProvider } from "../i18n";
import en from "../locales/en.json";
import { calledPaths, mockApi, response } from "../test/mockApi";
import type { AuditEvent, Finding, Progress, Summary } from "../types";
import { InspectionRecordPage } from "./InspectionRecordPage";

const box = (x: number, y: number, w: number, h: number) => [[x, y], [x + w, y], [x + w, y + h], [x, y + h]];
const finding = (rule_id: string, rule_name: string, status: Finding["status"], extra: Record<string, unknown> = {}) => ({ rule_id, rule_name, status, severity: "HIGH", message: `${rule_name} message`, field: null, value: null, fields: [], corrected: false, ...extra });

const photo = (id: string, panel: "BACK" | "FRONT" | "DATE_STAMP", active = true) => ({ id, panel, width: 1000, height: 800, checksum: `${id}-sum`, uploaded_at: "2026-10-17T09:30:00+00:00", active });
const base: Summary = {
  id: "i1", inspection_number: "PC-1", status: "COMPLETED", created_at: "2026-10-17T09:30:00+00:00", completed_at: "2026-10-17T09:31:00+00:00",
  product_name: "Gluco Biscuits", review: { status: "UNREVIEWED" }, corrections: [],
  image: { id: "img-b", original_filename: "b.jpg", width: 1000, height: 800, checksum: "b".repeat(64), panel: "BACK" },
  images: [photo("img-b", "BACK"), photo("img-f", "FRONT")],
  ocr: { image_id: "img-b", raw_text: "MRP", languages: ["en"], duration_ms: 1000, results: [{ text: "MRP", confidence: 0.9, bbox: box(100, 100, 100, 20) }] },
  extraction: { fields: { mrp: { value: null, confidence: 0, needs_review: true, image_id: "img-b" }, product_name: { value: "Gluco Biscuits", confidence: 0.8, needs_review: false, image_id: "img-f", panel: "FRONT" } } },
  compliance: {
    overall_status: "FAIL", verdict: "NON_COMPLIANT", summary: { passed: 1, failed: 1, review: 1, not_applicable: 0 },
    findings: [
      finding("MRP_FORMAT", "Retail sale price (MRP)", "REVIEW", { field: "mrp", source_bboxes: [box(100, 100, 100, 20)], image_id: "img-b" }),
      finding("PRODUCT_IDENTITY", "Product identity", "PASS", { field: "product_name", value: "Gluco Biscuits", source_bboxes: [box(300, 200, 200, 40)], image_id: "img-f" }),
      finding("MANUFACTURE_DATE", "Date of manufacture", "FAIL", { field: "manufacturing_date", message: "No manufacturing date found" }),
    ],
  },
};
const withDate = (): Summary => ({
  ...base, images: [...base.images!, photo("img-d", "DATE_STAMP")],
  compliance: { ...base.compliance!, summary: { passed: 2, failed: 0, review: 1, not_applicable: 0 }, findings: base.compliance!.findings.map(item => (item.rule_id === "MANUFACTURE_DATE" ? finding("MANUFACTURE_DATE", "Date of manufacture", "PASS", { field: "manufacturing_date", value: "03/2026", source_bboxes: [box(50, 50, 80, 20)], image_id: "img-d" }) : item)) },
});
const progress = (state: Progress["state"]): Progress => ({ inspection_id: "i1", state, stage: null, percent: state === "completed" ? 100 : 25, completed_steps: 1, total_steps: 4, started_at: null, elapsed_ms: 800, error: null, version: 1, steps: [{ id: "d:prepare", kind: "prepare", label: "", status: state === "completed" ? "done" : "running", panel: "DATE_STAMP" }, { id: "link", kind: "link", label: "", status: "pending" }] });
const audit: AuditEvent[] = [
  { type: "CREATED", at: "2026-10-17T09:30:00+00:00", actor: "Asha", details: {} },
  { type: "PHOTO_ADDED", at: "2026-10-17T09:50:00+00:00", actor: "Asha", details: { panel: "DATE_STAMP", filename: "date.jpg", checksum: "ab12cd34ef56".repeat(5) } },
];
const routes = (summary: Summary = base) => ({
  "GET /inspections/i1": summary, "GET /inspections/config": response({ max_upload_bytes: 10485760 }), "GET /inspections/i1/audit": response(audit),
  "GET /inspections/i1/images/img-b": response({}), "GET /inspections/i1/images/img-f": response({}), "GET /inspections/i1/images/img-d": response({}),
});
const asReply = (value: unknown) => ((value as { ok?: boolean }).ok !== undefined ? value : response(value));
const wrap = (routesMap: Record<string, unknown>) => Object.fromEntries(Object.entries(routesMap).map(([key, value]) => [key, Array.isArray(value) ? value.map(asReply) : asReply(value)])) as Parameters<typeof mockApi>[0];
const renderRecord = () => render(<I18nProvider><ToastProvider><InspectionRecordPage inspectionId="i1" official onExpired={() => undefined} /></ToastProvider></I18nProvider>);
const ready = () => screen.findByRole("heading", { level: 1, name: "Gluco Biscuits" });
const row = (id: string) => document.querySelector<HTMLElement>(`[data-finding="${id}"]`)!;
const marker = (id: string) => document.querySelector<HTMLElement>(`[data-marker="${id}"]`);

const realRect = Element.prototype.getBoundingClientRect;
describe("record page photos", () => {
  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => "blob:photo"); URL.revokeObjectURL = vi.fn();
    Element.prototype.getBoundingClientRect = function () { return this.classList?.contains("evidence-frame") ? ({ width: 500, height: 400, left: 0, top: 0, right: 500, bottom: 400, x: 0, y: 0, toJSON: () => ({}) } as DOMRect) : realRect.call(this); };
    vi.stubGlobal("createImageBitmap", vi.fn(async () => ({ width: 800, height: 600, close: () => undefined })));
    localStorage.clear();
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); Element.prototype.getBoundingClientRect = realRect; });

  it("has one tab per uploaded panel, with a thumbnail, back first; replaced photos get no tab", async () => {
    mockApi(wrap({ ...routes(), "GET /inspections/i1": { ...base, images: [...base.images!, photo("img-old", "FRONT", false)] } }));
    renderRecord(); await ready();
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map(tab => tab.textContent)).toEqual([en.panelBack, en.panelFront]);
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    await waitFor(() => expect(tabs[1].querySelector("img.photo-tab__thumb")?.getAttribute("src")).toBe("blob:photo"));
  });

  it("shows markers only for findings whose evidence is on the photo being shown", async () => {
    mockApi(wrap(routes()));
    renderRecord(); await ready();
    expect(marker("MRP_FORMAT")).toBeTruthy();
    expect(marker("PRODUCT_IDENTITY")).toBeNull(); // its evidence is on the front photo
    fireEvent.click(screen.getByRole("tab", { name: en.panelFront }));
    expect(marker("PRODUCT_IDENTITY")).toBeTruthy();
    expect(marker("MRP_FORMAT")).toBeNull();
    // The finding list keeps every number whichever photo is shown.
    expect(within(row("MRP_FORMAT")).getByText("2")).toBeTruthy();
  });

  it("switches to the photo a selected finding's evidence is on", async () => {
    mockApi(wrap(routes()));
    renderRecord(); await ready();
    expect(screen.getByRole("tab", { name: en.panelBack }).getAttribute("aria-selected")).toBe("true");
    fireEvent.click(within(row("PRODUCT_IDENTITY")).getByRole("button", { name: /Product identity/ }));
    expect(screen.getByRole("tab", { name: en.panelFront }).getAttribute("aria-selected")).toBe("true");
    expect(marker("PRODUCT_IDENTITY")!.getAttribute("aria-pressed")).toBe("true");
    // A finding on the other photo brings the back tab forward again.
    fireEvent.click(within(row("MRP_FORMAT")).getByRole("button", { name: /Retail sale price/ }));
    expect(screen.getByRole("tab", { name: en.panelBack }).getAttribute("aria-selected")).toBe("true");
  });

  it("offers Add photo for a panel with no photo and Retake for one that has it", async () => {
    mockApi(wrap(routes()));
    renderRecord(); await ready();
    expect(within(row("MANUFACTURE_DATE")).getByRole("button", { name: en.recAddPhoto })).toBeTruthy();
    expect(within(row("MANUFACTURE_DATE")).queryByRole("button", { name: en.recRetake })).toBeNull();
    expect(within(row("MRP_FORMAT")).getByRole("button", { name: en.recRetake })).toBeTruthy();
    expect(within(row("MRP_FORMAT")).queryByRole("button", { name: en.recAddPhoto })).toBeNull();
  });

  it("adds a photo from a dialog, shows live progress inline, then refreshes the record with the new tab", async () => {
    const fetchMock = mockApi(wrap({
      ...routes(),
      "GET /inspections/i1": [response(base), response(withDate())],
      "POST /inspections/i1/image": response({ id: "img-d", panel: "DATE_STAMP" }, true, 201),
      "POST /inspections/i1/process": response(progress("running"), true, 202),
      "GET /inspections/i1/progress": response(progress("completed")),
    }));
    renderRecord(); await ready();
    fireEvent.click(within(row("MANUFACTURE_DATE")).getByRole("button", { name: en.recAddPhoto }));
    const dialog = await screen.findByRole("dialog", { name: en.recAddPhotoTitle });
    expect((within(dialog).getByRole("radio", { name: en.panelDate }) as HTMLInputElement).checked).toBe(true); // the date stamp is preselected
    expect((within(dialog).getByRole("button", { name: en.recAddPhotoStart }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText(en.recAddPhotoChoose), { target: { files: [new File(["x"], "date.png", { type: "image/png" })] } });
    fireEvent.click(within(dialog).getByRole("button", { name: en.recAddPhotoStart }));

    expect(await screen.findByRole("heading", { level: 2, name: en.recPhotoProgress })).toBeTruthy();
    await waitFor(() => expect(screen.getAllByRole("tab").map(tab => tab.textContent)).toEqual([en.panelBack, en.panelFront, en.panelDate]));
    const upload = fetchMock.mock.calls.find(call => String(call[0]).endsWith("/inspections/i1/image"))!;
    expect(((upload[1] as RequestInit).body as FormData).get("panel")).toBe("DATE_STAMP");
    const paths = calledPaths(fetchMock);
    expect(paths.indexOf("/inspections/i1/process")).toBeGreaterThan(paths.indexOf("/inspections/i1/image"));
    expect(await screen.findByText(en.recToastPhotos)).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("heading", { level: 2, name: en.recPhotoProgress })).toBeNull());
    expect(within(row("MANUFACTURE_DATE")).queryByRole("button", { name: en.recAddPhoto })).toBeNull();
  });

  it("retakes a photo: the panel is fixed, and the new file goes up for that panel", async () => {
    const fetchMock = mockApi(wrap({
      ...routes(),
      "POST /inspections/i1/image": response({ id: "img-b2", panel: "BACK" }, true, 201),
      "POST /inspections/i1/process": response(progress("running"), true, 202),
      "GET /inspections/i1/progress": response(progress("completed")),
    }));
    renderRecord(); await ready();
    fireEvent.click(within(row("MRP_FORMAT")).getByRole("button", { name: en.recRetake }));
    const dialog = await screen.findByRole("dialog", { name: `Retake ${en.panelBack}` });
    expect(within(dialog).queryByRole("radio")).toBeNull();
    fireEvent.change(within(dialog).getByLabelText(en.recAddPhotoChoose), { target: { files: [new File(["x"], "again.jpg", { type: "image/jpeg" })] } });
    fireEvent.click(within(dialog).getByRole("button", { name: en.recAddPhotoStart }));
    await waitFor(() => expect(fetchMock.mock.calls.some(call => String(call[0]).endsWith("/inspections/i1/process"))).toBe(true));
    const upload = fetchMock.mock.calls.find(call => String(call[0]).endsWith("/inspections/i1/image"))!;
    expect(((upload[1] as RequestInit).body as FormData).get("panel")).toBe("BACK");
  });

  it("rejects an unsupported file in the dialog before uploading", async () => {
    const fetchMock = mockApi(wrap(routes()));
    renderRecord(); await ready();
    fireEvent.click(within(row("MANUFACTURE_DATE")).getByRole("button", { name: en.recAddPhoto }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText(en.recAddPhotoChoose), { target: { files: [new File(["x"], "n.txt", { type: "text/plain" })] } });
    expect(within(dialog).getByRole("alert").textContent).toBe(en.newBadType);
    expect(fetchMock.mock.calls.some(call => (call[1] as RequestInit | undefined)?.method === "POST")).toBe(false);
  });

  it("titles a photo added after the first with its panel and shows the checksum in the audit trail", async () => {
    mockApi(wrap(routes()));
    renderRecord(); await ready();
    const event = await waitFor(() => { const found = document.querySelector<HTMLElement>('[data-event="PHOTO_ADDED"]'); expect(found).toBeTruthy(); return found!; });
    expect(event.textContent).toContain(`Photo added (${en.panelDate})`);
    expect(event.textContent).toContain("ab12cd34");
  });
});
