import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../../i18n";
import en from "../../locales/en.json";
import type { Panel } from "../../types";
import { CaptureScreen, StepSpec } from "./CaptureScreen";

let sharpness: number | null = 0;
vi.mock("../../lib/imageQuality", async importOriginal => ({
  ...(await importOriginal<typeof import("../../lib/imageQuality")>()),
  assessSource: () => ({ sharpness: 400, brightness: 140, glareShare: 0, sharp: true, lightOk: true, glare: false, retake: false }),
  sharpnessOfRegion: () => sharpness,
}));
vi.mock("../../lib/imageResize", async importOriginal => ({ ...(await importOriginal<typeof import("../../lib/imageResize")>()), decodeOriented: async () => ({ source: {}, width: 800, height: 600, release: () => undefined }) }));

const STEPS: StepSpec[] = [{ panel: "BACK", required: true }];
const mount = (current: Panel = "BACK") => {
  const props = { steps: STEPS, current, photos: {}, skipped: new Set<Panel>(), title: en.newInspection, onStep: vi.fn(), onUse: vi.fn(), onSkip: vi.fn(), onClose: vi.fn() };
  return { props, ...render(<I18nProvider><CaptureScreen {...props} /></I18nProvider>) };
};

const takePhoto = vi.fn(async () => new Blob(["x"], { type: "image/jpeg" }));
const stubImageCapture = () => vi.stubGlobal("ImageCapture", class { takePhoto = takePhoto; grabFrame = vi.fn(); });
const stubCamera = (capabilities: Record<string, unknown>) => {
  const track = { stop: vi.fn(), getCapabilities: () => capabilities, getSettings: () => ({}), applyConstraints: vi.fn(async () => undefined) };
  const getUserMedia = vi.fn(async () => ({ getTracks: () => [track], getVideoTracks: () => [track] }));
  Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia }, configurable: true });
  HTMLMediaElement.prototype.play = vi.fn(async () => undefined);
  return { track, getUserMedia };
};
const shutterButton = () => screen.getByRole("button", { name: /Take photo/ }) as HTMLButtonElement;

describe("capture screen: sharp live capture", () => {
  beforeEach(() => {
    sharpness = 0;
    takePhoto.mockClear();
    URL.createObjectURL = vi.fn(() => "blob:photo"); URL.revokeObjectURL = vi.fn();
    Object.defineProperty(window, "isSecureContext", { value: true, configurable: true });
    Object.defineProperty(HTMLVideoElement.prototype, "videoWidth", { get: () => 1280, configurable: true });
    Object.defineProperty(HTMLVideoElement.prototype, "videoHeight", { get: () => 720, configurable: true });
    HTMLCanvasElement.prototype.getContext = (() => null) as never;
    localStorage.clear();
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); Reflect.deleteProperty(navigator, "mediaDevices"); });

  it("makes the phone camera the default when the browser has no ImageCapture, keeping live preview as an option", async () => {
    const { getUserMedia } = stubCamera({ focusMode: ["continuous"] });
    mount();
    expect(screen.getByText(en.capNoStillHint)).toBeTruthy();
    const input = screen.getByTestId("camera-input") as HTMLInputElement;
    const click = vi.spyOn(input, "click");
    fireEvent.click(shutterButton());
    expect(click).toHaveBeenCalledTimes(1);
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: en.capPhoneCameraBest })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: en.capTryLive }));
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(document.querySelector("video")).toBeTruthy());
  });

  it("falls back to the phone camera when the opened lens cannot focus by itself, and releases the camera", async () => {
    stubImageCapture();
    const { track } = stubCamera({});
    mount();
    fireEvent.click(screen.getByRole("button", { name: en.capEnableCamera }));
    expect(await screen.findByText(en.capNoFocusHint)).toBeTruthy();
    expect(track.stop).toHaveBeenCalled();
    expect(document.querySelector("video")).toBeNull();
    expect(screen.getByRole("button", { name: en.capPhoneCameraBest })).toBeTruthy();
    expect(screen.getByRole("button", { name: en.capTryLive })).toBeTruthy();
  });

  it("holds the shutter until the picture is sharp, then takes a real photo by itself (Auto-capture)", async () => {
    stubImageCapture();
    stubCamera({ focusMode: ["continuous"] });
    vi.useFakeTimers();
    mount();
    fireEvent.click(screen.getByRole("button", { name: en.capEnableCamera }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByText(en.capHoldSteady)).toBeTruthy();
    expect(shutterButton().disabled).toBe(true);

    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(takePhoto).not.toHaveBeenCalled();
    expect(shutterButton().disabled).toBe(true);

    sharpness = 5000;
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });
    expect(takePhoto).not.toHaveBeenCalled(); // sharp, but not yet for 400 ms
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(takePhoto).toHaveBeenCalledTimes(1);
  });

  it("lets the shutter be pressed after 2 s even if it never gets sharp, and does not auto-fire when Auto-capture is off", async () => {
    stubImageCapture();
    stubCamera({ focusMode: ["continuous"] });
    vi.useFakeTimers();
    mount();
    fireEvent.click(screen.getByRole("button", { name: en.capEnableCamera }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    fireEvent.click(screen.getByLabelText(en.capAutoCapture));
    sharpness = 5000;
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(takePhoto).not.toHaveBeenCalled();
    expect(shutterButton().disabled).toBe(false);
    fireEvent.click(shutterButton());
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(takePhoto).toHaveBeenCalledTimes(1);
  });

  it("opens the camera diagnostics after five taps on the help icon", async () => {
    stubImageCapture();
    stubCamera({ focusMode: ["continuous"] });
    mount();
    const help = screen.getByRole("button", { name: en.capHelp });
    for (let tap = 0; tap < 4; tap += 1) fireEvent.click(help);
    expect(screen.queryByTestId("camera-debug")).toBeNull();
    fireEvent.click(help);
    const panel = screen.getByTestId("camera-debug");
    expect(panel.textContent).toContain("imageCaptureAvailable");
  });

  it("offers an optional crop for gallery or native photos only, and not by default", async () => {
    const { props } = mount();
    fireEvent.change(screen.getByTestId("gallery-input"), { target: { files: [new File(["x"], "pack.jpg", { type: "image/jpeg" })] } });
    const review = await screen.findByRole("dialog");
    expect(screen.queryByTestId("crop-box")).toBeNull();
    fireEvent.click(within(review).getByRole("button", { name: en.capCrop }));
    expect(screen.getByTestId("crop-box")).toBeTruthy();
    fireEvent.click(within(review).getByRole("button", { name: en.capNoCrop }));
    expect(screen.queryByTestId("crop-box")).toBeNull();
    fireEvent.click(within(review).getByRole("button", { name: en.capUsePhoto }));
    await waitFor(() => expect(props.onUse).toHaveBeenCalledTimes(1));
  });
});
