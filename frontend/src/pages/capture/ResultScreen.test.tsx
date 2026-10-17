import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../../i18n";
import en from "../../locales/en.json";
import type { Finding, Summary } from "../../types";
import { ResultScreen } from "./ResultScreen";

const box = (x: number, y: number, w: number, h: number) => [[x, y], [x + w, y], [x + w, y + h], [x, y + h]];
const finding = (rule_id: string, rule_name: string, status: Finding["status"], extra: Record<string, unknown> = {}) => ({ rule_id, rule_name, status, severity: "HIGH", message: `${rule_name} message`, field: null, value: null, fields: [], corrected: false, ...extra });

const summary: Summary = {
  id: "i1", inspection_number: "PC-1", status: "COMPLETED", product_name: "Gluco Biscuits", review: { status: "UNREVIEWED" }, corrections: [],
  image: { id: "back", original_filename: "b.jpg", width: 1000, height: 800, panel: "BACK" },
  images: [
    { id: "back", panel: "BACK", width: 1000, height: 800, uploaded_at: "2026-10-17T09:00:00Z", active: true },
    { id: "front", panel: "FRONT", width: 800, height: 1000, uploaded_at: "2026-10-17T09:01:00Z", active: true },
    { id: "old", panel: "FRONT", width: 800, height: 1000, uploaded_at: "2026-10-17T08:00:00Z", active: false },
  ],
  extraction: { fields: { net_quantity: { value: "70 g", confidence: 0.9, needs_review: false, image_id: "back" }, mrp: { value: null, confidence: 0, needs_review: true } } },
  compliance: {
    overall_status: "FAIL", verdict: "NON_COMPLIANT", summary: { passed: 2, failed: 2, review: 0, not_applicable: 0 },
    findings: [
      finding("MRP_FORMAT", "Retail sale price (MRP)", "FAIL", { field: "mrp", message: "No MRP found" }),
      finding("MANUFACTURE_DATE", "Date of manufacture", "FAIL", { field: "manufacturing_date", message: "No date found" }),
      finding("NET_QUANTITY_FORMAT", "Net quantity", "PASS", { field: "net_quantity", value: "70 g", source_bboxes: box(100, 80, 200, 40) && [box(100, 80, 200, 40)], image_id: "back" }),
      finding("PRODUCT_IDENTITY", "Product identity", "PASS", { field: "product_name", value: "Gluco Biscuits", source_bboxes: [box(80, 100, 400, 60)], image_id: "front" }),
    ],
  },
  risk: { risk_score: 62, risk_level: "HIGH", reasons: [] },
};

const mount = (props: Partial<Parameters<typeof ResultScreen>[0]> = {}) => {
  const handlers = { onSave: vi.fn(), onClose: vi.fn(), onCapture: vi.fn(), onCorrect: vi.fn(async () => null) };
  render(<I18nProvider><ResultScreen summary={summary} official photoUrls={{ back: "blob:back", front: "blob:front" }} {...handlers} {...props} /></I18nProvider>);
  return handlers;
};
const item = (rule: string) => document.querySelector<HTMLElement>(`[data-rule="${rule}"]`)!;

describe("result screen", () => {
  beforeEach(() => { URL.createObjectURL = vi.fn(() => "blob:x"); URL.revokeObjectURL = vi.fn(); });
  afterEach(cleanup);

  it("shows the verdict, risk for officials, name, quantity and counts", () => {
    mount();
    expect(screen.getByText(en.verdictNonCompliant)).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: "Gluco Biscuits" })).toBeTruthy();
    expect(document.querySelector(".res-risk")!.textContent).toBe(`62 · ${en.high}`);
    expect(screen.getByText("70 g")).toBeTruthy();
    expect(document.querySelector(".res-counts")!.textContent).toContain(`2 ${en.recStatViolations}`);
  });

  it("hides the risk score from manufacturers", () => {
    mount({ official: false });
    expect(document.querySelector(".res-risk")).toBeNull();
    expect(within(item("MRP_FORMAT")).queryByRole("button", { name: en.recEnterValue })).toBeNull();
  });

  it("lists what to check on the pack, numbered like the photo markers", () => {
    mount();
    expect(screen.getByRole("heading", { name: "Check on the pack · 2" })).toBeTruthy();
    expect(Array.from(document.querySelectorAll(".res-list > li")).map(li => li.getAttribute("data-rule"))).toEqual(["MANUFACTURE_DATE", "MRP_FORMAT"]); // same severity: by rule code
    expect(within(item("MANUFACTURE_DATE")).getByText("1")).toBeTruthy();
    expect(within(item("MRP_FORMAT")).getByText("2")).toBeTruthy();
  });

  it("offers Add photo when the panel was not photographed and Retake when it was", () => {
    const { onCapture } = mount();
    // MRP lives on the back panel, which exists: retake. The date stamp has no photo: add one.
    expect(within(item("MRP_FORMAT")).queryByRole("button", { name: en.recAddPhoto })).toBeNull();
    fireEvent.click(within(item("MRP_FORMAT")).getByRole("button", { name: en.recRetake }));
    expect(onCapture).toHaveBeenLastCalledWith("BACK");
    expect(within(item("MANUFACTURE_DATE")).queryByRole("button", { name: en.recRetake })).toBeNull();
    fireEvent.click(within(item("MANUFACTURE_DATE")).getByRole("button", { name: en.recAddPhoto }));
    expect(onCapture).toHaveBeenLastCalledWith("DATE_STAMP");
  });

  it("opens a correction sheet with a number pad for amounts and saves the typed value", async () => {
    const { onCorrect } = mount();
    fireEvent.click(within(item("MRP_FORMAT")).getByRole("button", { name: en.recEnterValue }));
    const sheet = await screen.findByRole("dialog", { name: "Enter MRP" });
    const pad = within(within(sheet).getByRole("group", { name: en.resKeypad }));
    for (const key of ["1", "2", ".", "5", "0"]) fireEvent.click(pad.getByRole("button", { name: key }));
    expect((within(sheet).getByRole("textbox", { name: en.extractionMrp }) as HTMLInputElement).value).toBe("12.50");
    fireEvent.click(pad.getByRole("button", { name: en.resKeypadDelete }));
    expect((within(sheet).getByRole("textbox", { name: en.extractionMrp }) as HTMLInputElement).value).toBe("12.5");
    fireEvent.click(within(sheet).getByRole("button", { name: en.recSaveValue }));
    await waitFor(() => expect(onCorrect).toHaveBeenCalledWith({ field_name: "mrp", corrected_value: "₹12.5", reason: "Verified on physical pack" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("keeps the sheet open and shows the server's message when a value is rejected", async () => {
    mount({ onCorrect: vi.fn(async () => "Enter the MRP as an amount in rupees.") });
    fireEvent.click(within(item("MRP_FORMAT")).getByRole("button", { name: en.recEnterValue }));
    const sheet = await screen.findByRole("dialog");
    fireEvent.click(within(sheet).getByRole("button", { name: "5" }));
    fireEvent.click(within(sheet).getByRole("button", { name: en.recSaveValue }));
    expect(await within(sheet).findByText("Enter the MRP as an amount in rupees.")).toBeTruthy();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("collapses the declarations that passed until asked", () => {
    mount();
    const toggle = screen.getByRole("button", { name: /2 declarations passed/ });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(document.querySelector(".res-passed__list")).toBeNull();
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(within(document.querySelector<HTMLElement>(".res-passed__list")!).getByText("Net quantity")).toBeTruthy();
  });

  it("saves to the inspection record and closes", () => {
    const { onSave, onClose } = mount();
    fireEvent.click(screen.getByRole("button", { name: en.resSave }));
    expect(onSave).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: en.capClose }));
    expect(onClose).toHaveBeenCalled();
  });

  it("shows the markers of the photo on screen, and switches photo with the tab", () => {
    mount();
    expect(screen.getAllByRole("tab").map(tab => tab.textContent)).toEqual([en.panelBack, en.panelFront]); // the replaced front photo is not offered
    // Markers exist only for findings with evidence on the photo being shown: the net quantity is on the back, the product name on the front.
    const markers = () => Array.from(document.querySelectorAll(".res-photo .evidence-marker")).map(marker => marker.getAttribute("aria-label"));
    expect(markers()).toEqual(["Finding 3: Net quantity"]);
    expect(screen.getByAltText(en.resPhotoAlt).getAttribute("src")).toBe("blob:back");
    fireEvent.click(screen.getByRole("tab", { name: en.panelFront }));
    expect(screen.getByRole("tab", { name: en.panelFront }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByAltText(en.resPhotoAlt).getAttribute("src")).toBe("blob:front");
    expect(markers()).toEqual(["Finding 4: Product identity"]);
  });
});
