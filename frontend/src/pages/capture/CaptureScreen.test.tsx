import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../../i18n";
import type { QualityReport } from "../../lib/imageQuality";
import en from "../../locales/en.json";
import type { Panel } from "../../types";
import { CaptureScreen, StepSpec } from "./CaptureScreen";

const good: QualityReport = { sharpness: 400, brightness: 140, glareShare: 0, sharp: true, lightOk: true, glare: false, retake: false };
const bad: QualityReport = { sharpness: 10, brightness: 40, glareShare: 0.3, sharp: false, lightOk: false, glare: true, retake: true };
let report: QualityReport = good;

vi.mock("../../lib/imageQuality", async importOriginal => ({ ...(await importOriginal<typeof import("../../lib/imageQuality")>()), assessSource: () => report }));
vi.mock("../../lib/imageResize", async importOriginal => ({ ...(await importOriginal<typeof import("../../lib/imageResize")>()), decodeOriented: async () => ({ source: {}, width: 800, height: 600, release: () => undefined }) }));

const STEPS: StepSpec[] = [{ panel: "BACK", required: true }, { panel: "FRONT", required: false }, { panel: "DATE_STAMP", required: false }];

function mount(overrides: Partial<Parameters<typeof CaptureScreen>[0]> = {}, current: Panel = "BACK") {
  const props = { steps: STEPS, current, photos: {}, skipped: new Set<Panel>(), title: en.newInspection, onStep: vi.fn(), onUse: vi.fn(), onSkip: vi.fn(), onClose: vi.fn(), ...overrides };
  return { props, ...render(<I18nProvider><CaptureScreen {...props} /></I18nProvider>) };
}
const pick = (testId: string, name = "pack.jpg") => fireEvent.change(screen.getByTestId(testId), { target: { files: [new File(["x"], name, { type: "image/jpeg" })] } });

describe("capture screen", () => {
  beforeEach(() => {
    report = good;
    URL.createObjectURL = vi.fn(() => "blob:photo"); URL.revokeObjectURL = vi.fn();
    Object.defineProperty(window, "isSecureContext", { value: true, configurable: true });
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); Reflect.deleteProperty(navigator, "mediaDevices"); });

  it("shows the three steps with required/optional labels and the hint for the current panel", () => {
    mount();
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map(tab => tab.textContent)).toEqual([
      `1${en.panelBack}${en.capRequired}`, `2${en.panelFront}${en.capOptional}`, `3${en.panelDate}${en.capOptional}`,
    ]);
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    expect(screen.getByText("Fit the MRP, net weight and maker block inside the frame.")).toBeTruthy();
  });

  it("switches steps, changes the hint, and offers Skip only on the optional ones", () => {
    const { props, rerender } = mount();
    expect(screen.queryByRole("button", { name: new RegExp(en.capSkip) })).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: new RegExp(en.panelDate) }));
    expect(props.onStep).toHaveBeenCalledWith("DATE_STAMP");
    rerender(<I18nProvider><CaptureScreen {...props} current="DATE_STAMP" /></I18nProvider>);
    expect(screen.getByText("Get close to the printed date/batch stamp.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: new RegExp(en.capSkip) }));
    expect(props.onSkip).toHaveBeenCalledTimes(1);
  });

  it("marks a captured step with its thumbnail and a check, and a skipped step as skipped", () => {
    const photo = { id: "p1", panel: "BACK" as Panel, blob: new Blob(["x"]), name: "back.jpg", url: "blob:back" };
    mount({ photos: { BACK: photo }, skipped: new Set<Panel>(["FRONT"]) });
    const back = screen.getByRole("tab", { name: new RegExp(en.panelBack) });
    expect(back.querySelector("img")?.getAttribute("src")).toBe("blob:back");
    expect(within(back).getByText(en.capCaptured)).toBeTruthy();
    expect(within(screen.getByRole("tab", { name: new RegExp(en.panelFront) })).getByText(en.capSkipped)).toBeTruthy();
  });

  it("falls back to the native camera input when getUserMedia is not available", () => {
    mount();
    expect(document.querySelector("video")).toBeNull();
    expect(screen.getByText(en.capFallbackTitle)).toBeTruthy();
    const camera = screen.getByTestId("camera-input") as HTMLInputElement;
    expect(camera.getAttribute("capture")).toBe("environment");
    expect(camera.accept).toBe("image/*");
    const click = vi.spyOn(camera, "click");
    fireEvent.click(screen.getByRole("button", { name: /Take photo/ }));
    expect(click).toHaveBeenCalled();
    // The gallery input has no capture attribute.
    expect(screen.getByTestId("gallery-input").hasAttribute("capture")).toBe(false);
  });

  it("falls back on an insecure page even if the browser exposes getUserMedia", () => {
    Object.defineProperty(window, "isSecureContext", { value: false, configurable: true });
    vi.stubGlobal("navigator", Object.assign(Object.create(navigator), { mediaDevices: { getUserMedia: vi.fn() } }));
    mount();
    expect(document.querySelector("video")).toBeNull();
    expect(screen.getByText(en.capFallbackTitle)).toBeTruthy();
  });

  it("opens a live rear-camera preview when getUserMedia is available in a secure context", async () => {
    const stop = vi.fn();
    const track = { stop, getCapabilities: () => ({}) };
    const stream = { getTracks: () => [track], getVideoTracks: () => [track] };
    const getUserMedia = vi.fn(async () => stream);
    Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia }, configurable: true });
    HTMLMediaElement.prototype.play = vi.fn(async () => undefined);
    const { unmount } = mount();
    await waitFor(() => expect(getUserMedia).toHaveBeenCalled());
    expect((getUserMedia.mock.calls as unknown[][])[0][0]).toMatchObject({ video: { facingMode: { ideal: "environment" } } });
    await waitFor(() => expect(screen.queryByText(en.capCameraStarting)).toBeNull());
    expect(document.querySelector("video")).toBeTruthy();
    expect(document.querySelector(".cap-guide")).toBeTruthy();
    unmount();
    expect(stop).toHaveBeenCalled();
  });

  it("falls back, with a note, when the camera permission is refused", async () => {
    Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia: vi.fn(async () => { throw Object.assign(new Error("no"), { name: "NotAllowedError" }); }) }, configurable: true });
    mount();
    expect(await screen.findByText(en.capCameraDenied)).toBeTruthy();
    expect(document.querySelector("video")).toBeNull();
  });

  it("reviews a good photo with green chips and uses it for the current panel", async () => {
    const { props } = mount();
    pick("camera-input");
    const review = await screen.findByRole("dialog", { name: new RegExp(en.capReviewTitle) });
    const chips = within(review).getAllByRole("listitem");
    expect(chips.map(chip => [chip.textContent, chip.getAttribute("data-ok")])).toEqual([[en.capSharp, "true"], [en.capLightGood, "true"], [en.capNoGlare, "true"]]);
    expect(within(review).queryByText(en.capRetakeRecommended)).toBeNull();
    fireEvent.click(within(review).getByRole("button", { name: en.capUsePhoto }));
    expect(props.onUse).toHaveBeenCalledTimes(1);
    expect((props.onUse as ReturnType<typeof vi.fn>).mock.calls[0][0]).toMatchObject({ panel: "BACK", name: "pack.jpg" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("recommends a retake for a blurry, dark, glary photo but still lets the inspector use it", async () => {
    report = bad;
    const { props } = mount();
    pick("gallery-input");
    const review = await screen.findByRole("dialog");
    expect(within(review).getAllByRole("listitem").map(chip => [chip.textContent, chip.getAttribute("data-ok")])).toEqual([[en.capBlurry, "false"], [en.capLightDark, "false"], [en.capGlare, "false"]]);
    expect(within(review).getByText(en.capRetakeRecommended)).toBeTruthy();
    fireEvent.click(within(review).getByRole("button", { name: en.capRetake }));
    expect(props.onUse).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
    pick("gallery-input");
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: en.capUseAnyway }));
    expect(props.onUse).toHaveBeenCalledTimes(1);
  });

  it("labels every camera control for assistive technology", () => {
    mount();
    for (const name of [en.capClose, en.capHelp, new RegExp(en.capGallery), /Take photo/]) expect(screen.getByRole("button", { name })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: en.capHelp }));
    expect(screen.getByText(en.capHelpTitle)).toBeTruthy();
  });
});
