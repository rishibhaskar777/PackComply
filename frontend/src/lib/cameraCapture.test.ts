import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { capturePhoto, chooseLens, imageCaptureSupported, openRearCamera, rankBackCameras } from "./camera";

const device = (deviceId: string, label: string) => ({ deviceId, label, kind: "videoinput" as const });

describe("lens selection", () => {
  it("puts the main back camera first and pushes ultra-wide and macro lenses behind it, dropping the front and depth cameras", () => {
    const ranked = rankBackCameras([
      device("front", "camera2 1, facing front"), device("macro", "camera2 4, facing back macro"), device("wide", "camera2 2, facing back ultra wide"),
      device("main", "camera2 0, facing back"), device("depth", "camera2 5, facing back depth"), device("tele", "camera2 3, facing back"),
    ]);
    expect(ranked.map(lens => lens.deviceId)).toEqual(["main", "tele", "wide", "macro"]);
    expect(ranked.map(lens => lens.role)).toEqual(["main", "main", "wide", "macro"]);
  });

  it("avoids 0.5 and wide lenses by label and keeps all cameras when none says back", () => {
    expect(rankBackCameras([device("a", "Camera 0.5x"), device("b", "Camera")]).map(lens => lens.deviceId)).toEqual(["b", "a"]);
  });

  it("probes several main candidates and prefers continuous focus, then the widest sensor", async () => {
    const lenses = rankBackCameras([device("a", "camera2 0, facing back"), device("b", "camera2 1, facing back"), device("c", "camera2 2, facing back")]);
    const info: Record<string, { continuous: boolean; maxWidth: number }> = { a: { continuous: false, maxWidth: 4000 }, b: { continuous: true, maxWidth: 1920 }, c: { continuous: true, maxWidth: 4032 } };
    const probe = vi.fn(async (lens: { deviceId: string }) => info[lens.deviceId]);
    expect((await chooseLens(lenses, probe))?.deviceId).toBe("c");
    expect(probe).toHaveBeenCalledTimes(3);
  });

  it("does not probe when there is a single main camera", async () => {
    const probe = vi.fn();
    const lenses = rankBackCameras([device("a", "camera2 0, facing back"), device("w", "camera2 2, facing back ultra wide")]);
    expect((await chooseLens(lenses, probe))?.deviceId).toBe("a");
    expect(probe).not.toHaveBeenCalled();
  });
});

describe("opening the rear camera", () => {
  type Track = ReturnType<typeof makeTrack>;
  const makeTrack = (deviceId: string, capabilities: Record<string, unknown>, settings: Record<string, unknown> = {}) => ({
    stop: vi.fn(), label: deviceId, getCapabilities: () => capabilities, getSettings: () => ({ deviceId, ...settings }), applyConstraints: vi.fn(async () => undefined),
  });
  const streamOf = (track: Track) => ({ getTracks: () => [track], getVideoTracks: () => [track] });
  const install = (getUserMedia: ReturnType<typeof vi.fn>, devices: Array<{ deviceId: string; label: string; kind: string }> = []) =>
    Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia, enumerateDevices: vi.fn(async () => devices) }, configurable: true });
  beforeEach(() => localStorage.clear());
  afterEach(() => { vi.unstubAllGlobals(); Reflect.deleteProperty(navigator, "mediaDevices"); localStorage.clear(); });

  it("asks for 4K at 30 fps, switches to the main lens when the default one is the ultra-wide, and remembers it", async () => {
    const wide = makeTrack("wide", { focusMode: ["manual"] });
    const main = makeTrack("main", { focusMode: ["continuous", "manual"], zoom: { min: 1, max: 8, step: 0.1 } });
    const getUserMedia = vi.fn(async (constraints: { video: { deviceId?: { exact: string } } }) => streamOf(constraints.video.deviceId?.exact === "main" ? main : wide));
    install(getUserMedia, [device("wide", "camera2 2, facing back ultra wide"), device("main", "camera2 0, facing back")]);
    const camera = await openRearCamera();
    expect(getUserMedia.mock.calls[0][0]).toMatchObject({ video: { facingMode: { ideal: "environment" }, width: { ideal: 3840 }, height: { ideal: 2160 }, frameRate: { ideal: 30 } } });
    expect(getUserMedia.mock.calls[1][0]).toMatchObject({ video: { deviceId: { exact: "main" } } });
    expect(wide.stop).toHaveBeenCalled();
    expect(camera.deviceId).toBe("main");
    expect(camera.autofocus).toBe(true);
    expect(localStorage.getItem("packcomply.cameraDeviceId")).toBe("main");
    // A remembered lens is requested directly next time.
    await openRearCamera();
    expect(getUserMedia.mock.calls[2][0]).toMatchObject({ video: { deviceId: { exact: "main" } } });
  });

  it("applies continuous focus only when the lens supports it", async () => {
    const supported = makeTrack("a", { focusMode: ["continuous"] });
    install(vi.fn(async () => streamOf(supported)));
    await openRearCamera();
    expect(supported.applyConstraints).toHaveBeenCalledWith({ advanced: [{ focusMode: "continuous" }] });

    const fixed = makeTrack("b", {});
    install(vi.fn(async () => streamOf(fixed)));
    const camera = await openRearCamera();
    expect(fixed.applyConstraints).not.toHaveBeenCalled();
    expect(camera.autofocus).toBe(false);
    expect(await camera.focusAt(0.5, 0.5)).toBe(false);
  });

  it("starts a wide lens zoomed in, and only focuses at a point when pointsOfInterest is supported", async () => {
    const wide = makeTrack("w", { focusMode: ["continuous"], zoom: { min: 1, max: 4, step: 0.1 } }, { pointsOfInterest: [] });
    install(vi.fn(async () => streamOf(wide)), [device("w", "camera2 2, facing back ultra wide")]);
    const camera = await openRearCamera();
    expect(wide.applyConstraints).toHaveBeenCalledWith({ advanced: [{ zoom: 1.5 }] });
    expect(camera.zoom?.value).toBe(1.5);
    expect(await camera.setZoom(10)).toBe(4);
    expect(await camera.focusAt(0.25, 0.75)).toBe(true);
    expect(wide.applyConstraints).toHaveBeenCalledWith({ advanced: [{ pointsOfInterest: [{ x: 0.25, y: 0.75 }], focusMode: "single-shot" }] });
  });

  it("falls back to 1080p when 4K is refused", async () => {
    const track = makeTrack("a", { focusMode: ["continuous"] });
    const getUserMedia = vi.fn().mockRejectedValueOnce(Object.assign(new Error("x"), { name: "OverconstrainedError" })).mockResolvedValue(streamOf(track));
    install(getUserMedia);
    await openRearCamera();
    expect(getUserMedia.mock.calls[1][0]).toMatchObject({ video: { width: { ideal: 1920 }, height: { ideal: 1080 } } });
  });
});

describe("taking the photo", () => {
  const frame = { videoWidth: 1280, videoHeight: 720 } as HTMLVideoElement;
  const fail = () => vi.fn(async () => { throw new Error("nope"); });
  const handle = (overrides: Record<string, unknown>) => ({ imageCapture: true, takePhoto: vi.fn(async () => new Blob(["x"])), grabFrame: vi.fn(async () => ({ width: 640, height: 480, close: vi.fn() })), ...overrides }) as never;
  beforeEach(() => vi.stubGlobal("createImageBitmap", vi.fn(async () => ({ width: 4000, height: 3000, close: vi.fn() }))));
  afterEach(() => vi.unstubAllGlobals());

  it("uses takePhoto (a full-resolution still) when ImageCapture is available", async () => {
    const camera = handle({});
    const still = await capturePhoto(camera, frame);
    expect(still).toMatchObject({ method: "takePhoto", width: 4000, height: 3000 });
    expect((camera as { grabFrame: unknown }).grabFrame).not.toHaveBeenCalled();
  });

  it("falls back to grabFrame, then to the video frame", async () => {
    expect((await capturePhoto(handle({ takePhoto: fail() }), frame))?.method).toBe("grabFrame");
    expect(await capturePhoto(handle({ takePhoto: fail(), grabFrame: fail() }), frame)).toMatchObject({ method: "video", width: 1280, height: 720 });
    expect((await capturePhoto(handle({ imageCapture: false }), frame))?.method).toBe("video");
    expect(await capturePhoto(handle({ imageCapture: false }), null)).toBeNull();
  });

  it("reports whether ImageCapture exists", () => {
    expect(imageCaptureSupported()).toBe(false);
    vi.stubGlobal("ImageCapture", class {});
    expect(imageCaptureSupported()).toBe(true);
  });
});
