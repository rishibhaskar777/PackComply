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

  const liveStream = (capabilities: Record<string, unknown> = {}) => {
    const applyConstraints = vi.fn(async () => undefined);
    const track = { stop: vi.fn(), getCapabilities: () => capabilities, applyConstraints };
    return { track, stream: { getTracks: () => [track], getVideoTracks: () => [track] } };
  };
  const stubCamera = (getUserMedia: () => Promise<unknown>) => {
    Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia: vi.fn(getUserMedia) }, configurable: true });
    HTMLMediaElement.prototype.play = vi.fn(async () => undefined);
    return (navigator.mediaDevices as unknown as { getUserMedia: ReturnType<typeof vi.fn> }).getUserMedia;
  };
  const denied = (name: string) => () => Promise.reject(Object.assign(new Error("no"), { name }));

  it("does not ask for the camera on page load: the live preview waits for the shutter or Enable camera", async () => {
    const getUserMedia = stubCamera(async () => liveStream().stream);
    mount();
    await Promise.resolve();
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(document.querySelector("video")).toBeNull();
    expect(screen.getByRole("button", { name: en.capEnableCamera })).toBeTruthy();
  });

  it("opens a live rear-camera preview (1920px ideal) when the shutter is tapped in a secure context, and releases it on leaving", async () => {
    const { stream, track } = liveStream();
    const getUserMedia = stubCamera(async () => stream);
    const { unmount } = mount();
    fireEvent.click(screen.getByRole("button", { name: /Take photo/ }));
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(1));
    expect(getUserMedia.mock.calls[0][0]).toMatchObject({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 } } });
    await waitFor(() => expect(screen.queryByText(en.capCameraStarting)).toBeNull());
    expect(document.querySelector("video")).toBeTruthy();
    expect(document.querySelector(".cap-guide")).toBeTruthy();
    unmount();
    expect(track.stop).toHaveBeenCalled();
  });

  it("starts the preview from the Enable camera button too", async () => {
    const getUserMedia = stubCamera(async () => liveStream().stream);
    mount();
    fireEvent.click(screen.getByRole("button", { name: en.capEnableCamera }));
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(document.querySelector("video")).toBeTruthy());
  });

  it("starts the preview by itself, without a prompt, when the browser already remembers the permission", async () => {
    const getUserMedia = stubCamera(async () => liveStream().stream);
    Object.defineProperty(navigator, "permissions", { value: { query: vi.fn(async () => ({ state: "granted" })) }, configurable: true });
    mount();
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(1));
    Reflect.deleteProperty(navigator, "permissions");
  });

  it("offers the torch only when the camera supports it, and switches it on the track", async () => {
    const { stream, track } = liveStream({ torch: true });
    stubCamera(async () => stream);
    mount();
    fireEvent.click(screen.getByRole("button", { name: en.capEnableCamera }));
    const flash = await screen.findByRole("button", { name: en.capFlash });
    fireEvent.click(flash);
    await waitFor(() => expect(track.applyConstraints).toHaveBeenCalledWith({ advanced: [{ torch: true }] }));
    await waitFor(() => expect(flash.getAttribute("aria-pressed")).toBe("true"));
  });

  it("does not show a torch button when the camera has none", async () => {
    stubCamera(async () => liveStream({}).stream);
    mount();
    fireEvent.click(screen.getByRole("button", { name: en.capEnableCamera }));
    await waitFor(() => expect(document.querySelector("video")).toBeTruthy());
    expect(screen.queryByRole("button", { name: en.capFlash })).toBeNull();
  });

  it("explains a refused permission and offers Use phone camera instead of a dead screen", async () => {
    stubCamera(denied("NotAllowedError"));
    mount();
    fireEvent.click(screen.getByRole("button", { name: /Take photo/ }));
    expect(await screen.findByText(en.capCameraDenied)).toBeTruthy();
    expect(document.querySelector("video")).toBeNull();
    const camera = screen.getByTestId("camera-input") as HTMLInputElement;
    const click = vi.spyOn(camera, "click");
    fireEvent.click(screen.getByRole("button", { name: en.capUsePhoneCamera }));
    expect(click).toHaveBeenCalledTimes(1);
  });

  it("falls back to the native camera, with a one-line hint, when no camera is found; the next shutter tap opens it at once", async () => {
    stubCamera(denied("NotFoundError"));
    mount();
    fireEvent.click(screen.getByRole("button", { name: /Take photo/ }));
    expect(await screen.findByText(en.capCameraFailedHint)).toBeTruthy();
    const camera = screen.getByTestId("camera-input") as HTMLInputElement;
    const click = vi.spyOn(camera, "click");
    fireEvent.click(screen.getByRole("button", { name: /Take photo/ }));
    expect(click).toHaveBeenCalledTimes(1);
  });

  it("opens the native camera synchronously inside the tap (no await, no timer) on a page without a live camera", () => {
    vi.useFakeTimers();
    mount();
    const camera = screen.getByTestId("camera-input") as HTMLInputElement;
    const click = vi.spyOn(camera, "click");
    fireEvent.click(screen.getByRole("button", { name: /Take photo/ }));
    // Called before any timer fires or any promise resolves.
    expect(click).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it("keeps the camera input in the layout (visually hidden, not display:none) and lets the same photo be chosen again", async () => {
    const { props } = mount();
    const camera = screen.getByTestId("camera-input") as HTMLInputElement;
    expect(camera.className).toContain("sr-only");
    expect(camera.style.display).toBe("");
    for (let round = 0; round < 2; round += 1) {
      pick("camera-input", "same.jpg");
      const review = await screen.findByRole("dialog");
      fireEvent.click(within(review).getByRole("button", { name: en.capUsePhoto }));
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    }
    expect(props.onUse).toHaveBeenCalledTimes(2);
  });

  it("shows the insecure-page hint on plain http instead of a dead screen", () => {
    Object.defineProperty(window, "isSecureContext", { value: false, configurable: true });
    vi.stubGlobal("navigator", Object.assign(Object.create(navigator), { mediaDevices: { getUserMedia: vi.fn() } }));
    mount();
    expect(screen.getByText(en.capInsecureHint)).toBeTruthy();
    expect(screen.getByRole("button", { name: en.capUsePhoneCamera })).toBeTruthy();
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
