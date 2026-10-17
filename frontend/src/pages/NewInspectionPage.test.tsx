import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../components/ui";
import { I18nProvider } from "../i18n";
import en from "../locales/en.json";
import { calledPaths, mockApi, response } from "../test/mockApi";
import { NewInspectionPage } from "./NewInspectionPage";

const routes = () => ({
  "GET /inspections/config": response({ max_upload_bytes: 10485760, ocr_max_image_dimension: 1280 }),
  "POST /inspections": response({ id: "n1" }, true, 201),
  "POST /inspections/n1/image": response({ id: "img", panel: "BACK" }, true, 201),
  "POST /inspections/n1/process": response({ inspection_id: "n1", state: "running", percent: 0, steps: [], version: 1, elapsed_ms: 0, total_steps: 0, completed_steps: 0, started_at: null, error: null }, true, 202),
  "GET /inspections/n1/progress": response({ inspection_id: "n1", state: "completed", percent: 100, steps: [], version: 2, elapsed_ms: 5000, total_steps: 0, completed_steps: 0, started_at: null, error: null }),
  "DELETE /inspections/n1": response({}, true, 204),
});
const mount = () => render(<I18nProvider><ToastProvider><NewInspectionPage onExpired={vi.fn()} onCompleted={vi.fn()} /></ToastProvider></I18nProvider>);
const choose = (panel: string, name: string) => fireEvent.change(screen.getByLabelText(`${en.newChoosePanel.replace("{panel}", panel)}`), { target: { files: [new File(["x"], name, { type: "image/jpeg" })] } });

describe("new inspection page pipelines the photos", () => {
  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => "blob:photo"); URL.revokeObjectURL = vi.fn();
    vi.stubGlobal("createImageBitmap", vi.fn(async () => ({ width: 800, height: 600, close: () => undefined })));
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.location.hash = ""; });

  it("creates the inspection and uploads (and starts reading) each photo as soon as it is chosen; Start only processes", async () => {
    const fetchMock = mockApi(routes());
    mount();
    choose(en.panelBack, "back.jpg");
    await waitFor(() => expect(calledPaths(fetchMock)).toContain("/inspections/n1/image?process_now=true"));
    expect(calledPaths(fetchMock)).not.toContain("/inspections/n1/process");
    choose(en.panelFront, "front.jpg");
    await waitFor(() => expect(calledPaths(fetchMock).filter(path => path === "/inspections/n1/image?process_now=true")).toHaveLength(2));
    expect(calledPaths(fetchMock).filter(path => path === "/inspections")).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: en.newStart }));
    await waitFor(() => expect(calledPaths(fetchMock)).toContain("/inspections/n1/process"));
    // Nothing is uploaded twice by Start.
    expect(calledPaths(fetchMock).filter(path => path.startsWith("/inspections/n1/image"))).toHaveLength(2);
    await waitFor(() => expect(window.location.hash).toBe("#/inspections/n1"));
  });

  it("starts a fresh inspection with the remaining photos when one is removed after upload", async () => {
    const fetchMock = mockApi({ ...routes(), "POST /inspections": [response({ id: "n1" }, true, 201), response({ id: "n2" }, true, 201)], "POST /inspections/n2/image": response({ id: "img2" }, true, 201) });
    mount();
    choose(en.panelBack, "back.jpg");
    choose(en.panelFront, "front.jpg");
    await waitFor(() => expect(calledPaths(fetchMock).filter(path => path === "/inspections/n1/image?process_now=true")).toHaveLength(2));
    fireEvent.click(screen.getByRole("button", { name: new RegExp(`${en.newRemovePanel.replace("{panel}", en.panelFront)}`) }));
    // The removed photo may already be on the server, so the draft is dropped and the back photo goes up again on a new inspection.
    await waitFor(() => expect(calledPaths(fetchMock)).toContain("/inspections/n2/image?process_now=true"));
    expect(fetchMock.mock.calls.some(call => (call[1] as RequestInit | undefined)?.method === "DELETE" && String(call[0]).endsWith("/inspections/n1"))).toBe(true);
  });
});
