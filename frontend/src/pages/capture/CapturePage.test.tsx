import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../components/ui";
import { I18nProvider } from "../../i18n";
import en from "../../locales/en.json";
import { calledPaths, mockApi, response } from "../../test/mockApi";
import type { Progress, Summary } from "../../types";
import { CapturePage } from "./CapturePage";

vi.mock("../../lib/imageQuality", async importOriginal => ({
  ...(await importOriginal<typeof import("../../lib/imageQuality")>()),
  assessSource: () => ({ sharpness: 400, brightness: 140, glareShare: 0, sharp: true, lightOk: true, glare: false, retake: false }),
}));

const progress = (state: Progress["state"], extra: Partial<Progress> = {}): Progress => ({
  inspection_id: "n1", state, stage: null, percent: state === "completed" ? 100 : 20, completed_steps: 1, total_steps: 5, started_at: null, elapsed_ms: 900, error: null, version: 1,
  steps: [
    { id: "b:prepare", kind: "prepare", label: "", status: "done", panel: "BACK", image_id: "img-b" },
    { id: "b:read:en", kind: "read", label: "", status: state === "completed" ? "done" : "running", panel: "BACK", language: "en", image_id: "img-b" },
    { id: "link", kind: "link", label: "", status: state === "completed" ? "done" : "pending" },
  ], ...extra,
});
const summary: Summary = {
  id: "n1", inspection_number: "PC-1", status: "COMPLETED", product_name: "Gluco Biscuits", review: { status: "UNREVIEWED" }, corrections: [],
  image: { id: "img-b", original_filename: "b.jpg", width: 1000, height: 800, panel: "BACK" },
  images: [{ id: "img-b", panel: "BACK", width: 1000, height: 800, uploaded_at: "2026-10-17T09:00:00Z", active: true }, { id: "img-d", panel: "DATE_STAMP", width: 800, height: 600, uploaded_at: "2026-10-17T09:01:00Z", active: true }],
  extraction: { fields: {} },
  compliance: { overall_status: "REVIEW", verdict: "NEEDS_REVIEW", summary: { passed: 0, failed: 0, review: 1, not_applicable: 0 }, findings: [{ rule_id: "MRP_FORMAT", rule_name: "Retail sale price (MRP)", status: "REVIEW", severity: "HIGH", message: "MRP key found but no price", field: "mrp", value: null }] },
};
const routes = () => ({
  "GET /inspections/config": response({ max_upload_bytes: 10485760, ocr_max_image_dimension: 1280 }),
  "POST /inspections": response({ id: "n1" }, true, 201),
  "POST /inspections/n1/image": response({ id: "img", panel: "BACK" }, true, 201),
  "POST /inspections/n1/process": response(progress("running"), true, 202),
  "GET /inspections/n1/progress": response(progress("completed")),
  "GET /inspections/n1": response(summary),
  "GET /inspections/n1/images/img-b": response({}), "GET /inspections/n1/images/img-d": response({}),
});
const pick = (name: string) => fireEvent.change(screen.getByTestId("camera-input"), { target: { files: [new File(["x"], name, { type: "image/jpeg" })] } });
const takeAndUse = async (name: string) => { pick(name); fireEvent.click(await screen.findByRole("button", { name: en.capUsePhoto })); };
const mount = (props: Partial<Parameters<typeof CapturePage>[0]> = {}) => render(<I18nProvider><ToastProvider><CapturePage official onExpired={vi.fn()} onCompleted={vi.fn()} forceFallback {...props} /></ToastProvider></I18nProvider>);

describe("field-app capture flow", () => {
  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => "blob:photo"); URL.revokeObjectURL = vi.fn();
    vi.stubGlobal("createImageBitmap", vi.fn(async () => ({ width: 800, height: 600, close: () => undefined })));
    window.location.hash = "#/capture";
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("captures back and date stamp (skipping the front), uploads with panels, follows progress and shows the result", async () => {
    const fetchMock = mockApi(routes());
    mount();
    await takeAndUse("back.jpg");
    expect(screen.getByRole("tab", { name: new RegExp(en.panelFront) }).getAttribute("aria-selected")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: new RegExp(en.capSkip) }));
    expect(screen.getByRole("tab", { name: new RegExp(en.panelDate) }).getAttribute("aria-selected")).toBe("true");
    await takeAndUse("date.jpg");

    // Screen B, then screen C.
    expect(await screen.findByRole("heading", { name: en.capProcessing })).toBeTruthy();
    expect(await screen.findByRole("heading", { level: 2, name: "Gluco Biscuits" })).toBeTruthy();
    const uploads = fetchMock.mock.calls.filter(call => (call[1] as RequestInit | undefined)?.method === "POST" && String(call[0]).includes("/inspections/n1/image")).map(call => ((call[1] as RequestInit).body as FormData).get("panel"));
    expect(uploads).toEqual(["BACK", "DATE_STAMP"]);
    const paths = calledPaths(fetchMock);
    expect(paths.filter(path => path === "/inspections")).toHaveLength(1);
    expect(paths.indexOf("/inspections/n1/process")).toBeGreaterThan(paths.lastIndexOf("/inspections/n1/image?process_now=true"));
    expect(paths).toContain("/inspections/n1/progress");

    fireEvent.click(screen.getByRole("button", { name: en.resSave }));
    expect(window.location.hash).toBe("#/inspections/n1");
  });

  it("creates the inspection and uploads each photo, asking for its text to be read, as soon as it is accepted", async () => {
    const fetchMock = mockApi(routes());
    mount();
    await takeAndUse("back.jpg");
    // Still capturing: no processing has been asked for, but the photo is already up and being read.
    await waitFor(() => expect(calledPaths(fetchMock)).toContain("/inspections/n1/image?process_now=true"));
    expect(calledPaths(fetchMock)).not.toContain("/inspections/n1/process");
    expect(calledPaths(fetchMock).filter(path => path === "/inspections")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: new RegExp(en.capSkip) }));
    await takeAndUse("date.jpg");
    await screen.findByRole("heading", { level: 2, name: "Gluco Biscuits" });
    const paths = calledPaths(fetchMock);
    // The final step only starts processing: every photo was uploaded before it, with its panel, once.
    expect(paths.filter(path => path === "/inspections/n1/image?process_now=true")).toHaveLength(2);
    expect(paths.filter(path => path === "/inspections/n1/process")).toHaveLength(1);
    expect(paths.indexOf("/inspections/n1/process")).toBeGreaterThan(paths.lastIndexOf("/inspections/n1/image?process_now=true"));
  });

  it("discards the draft when the camera is closed before processing starts", async () => {
    const fetchMock = mockApi({ ...routes(), "DELETE /inspections/n1": response({}, true, 204) });
    const view = mount();
    await takeAndUse("back.jpg");
    await waitFor(() => expect(calledPaths(fetchMock)).toContain("/inspections/n1/image?process_now=true"));
    view.unmount();
    await waitFor(() => expect(fetchMock.mock.calls.some(call => (call[1] as RequestInit | undefined)?.method === "DELETE" && String(call[0]).endsWith("/inspections/n1"))).toBe(true));
  });

  it("will not start without the back panel: finishing with the back panel missing returns to it", async () => {
    mockApi(routes());
    mount();
    fireEvent.click(screen.getByRole("tab", { name: new RegExp(en.panelFront) }));
    await takeAndUse("front.jpg");
    fireEvent.click(screen.getByRole("button", { name: new RegExp(en.capSkip) })); // skip the date
    expect(screen.getByRole("tab", { name: new RegExp(en.panelBack) }).getAttribute("aria-selected")).toBe("true");
    expect(screen.queryByRole("heading", { name: en.capProcessing })).toBeNull();
  });

  it("keeps the photos and offers Retry upload when the server cannot be reached, then resumes", async () => {
    const unreachable = () => Promise.reject(new TypeError("Failed to fetch"));
    // The first failure is the background upload while capturing, the second is the final run; the third attempt is the retry.
    const fetchMock = mockApi({ ...routes(), "POST /inspections": [unreachable as never, unreachable as never, response({ id: "n1" }, true, 201)] });
    mount();
    await takeAndUse("back.jpg");
    fireEvent.click(screen.getByRole("button", { name: new RegExp(en.capSkip) }));
    fireEvent.click(screen.getByRole("button", { name: new RegExp(en.capSkip) }));
    const alert = await screen.findByText(en.progUnreachable);
    expect(alert).toBeTruthy();
    expect(screen.getAllByRole("img").some(image => image.getAttribute("src") === "blob:photo")).toBe(true); // the preview of the held photo
    fireEvent.click(screen.getByRole("button", { name: en.progRetryUpload }));
    expect(await screen.findByRole("heading", { level: 2, name: "Gluco Biscuits" })).toBeTruthy();
    expect(calledPaths(fetchMock).filter(path => path.split("?")[0] === "/inspections/n1/image")).toHaveLength(1);
  });

  it("goes back to the camera for one panel from a result action and adds the photo to the same inspection", async () => {
    const fetchMock = mockApi(routes());
    mount();
    await takeAndUse("back.jpg");
    fireEvent.click(screen.getByRole("button", { name: new RegExp(en.capSkip) }));
    fireEvent.click(screen.getByRole("button", { name: new RegExp(en.capSkip) }));
    await screen.findByRole("heading", { level: 2, name: "Gluco Biscuits" });
    // The MRP finding points at the back panel, which exists: Retake.
    fireEvent.click(within(document.querySelector<HTMLElement>('[data-rule="MRP_FORMAT"]')!).getByRole("button", { name: en.recRetake }));
    expect(await screen.findByRole("heading", { name: en.capAddTitle })).toBeTruthy();
    expect(screen.getAllByRole("tab")).toHaveLength(1);
    await takeAndUse("back2.jpg");
    await screen.findByRole("heading", { level: 2, name: "Gluco Biscuits" });
    const calls = calledPaths(fetchMock);
    expect(calls.filter(path => path === "/inspections")).toHaveLength(1); // the same inspection
    expect(calls.filter(path => path.split("?")[0] === "/inspections/n1/image")).toHaveLength(2);
    expect(calls.filter(path => path === "/inspections/n1/process")).toHaveLength(2);
  });

  it("opens straight on the requested panel to add a photo to an existing record", async () => {
    const fetchMock = mockApi(routes());
    mount({ add: { inspectionId: "n1", panel: "DATE_STAMP" } });
    expect(screen.getByRole("heading", { name: en.capAddTitle })).toBeTruthy();
    expect(screen.getByRole("tab", { name: new RegExp(en.panelDate) }).getAttribute("aria-selected")).toBe("true");
    await takeAndUse("date.jpg");
    await screen.findByRole("heading", { level: 2, name: "Gluco Biscuits" });
    const calls = calledPaths(fetchMock);
    expect(calls).not.toContain("/inspections");
    const upload = fetchMock.mock.calls.find(call => (call[1] as RequestInit | undefined)?.method === "POST" && String(call[0]).includes("/inspections/n1/image"))!;
    expect(((upload[1] as RequestInit).body as FormData).get("panel")).toBe("DATE_STAMP");
    await waitFor(() => expect(calls).toContain("/inspections/n1/process"));
  });
});
