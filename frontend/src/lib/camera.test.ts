import { afterEach, describe, expect, it, vi } from "vitest";
import { cameraErrorKind, cameraPermissionGranted, liveCameraBlocker, liveCameraSupported } from "./camera";

const secure = (value: boolean | undefined) => Object.defineProperty(window, "isSecureContext", { value, configurable: true });

describe("camera support detection", () => {
  afterEach(() => { vi.unstubAllGlobals(); Reflect.deleteProperty(navigator, "mediaDevices"); Reflect.deleteProperty(navigator, "permissions"); secure(true); });

  it("allows a live preview in a secure context with getUserMedia", () => {
    secure(true);
    Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia: vi.fn() }, configurable: true });
    expect(liveCameraBlocker()).toBeNull();
    expect(liveCameraSupported()).toBe(true);
  });

  it("detects an insecure page (http on a LAN address), even when the browser exposes getUserMedia", () => {
    secure(false);
    Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia: vi.fn() }, configurable: true });
    expect(liveCameraBlocker()).toBe("insecure");
    expect(liveCameraSupported()).toBe(false);
  });

  it("detects a browser without getUserMedia", () => {
    secure(true);
    expect(liveCameraBlocker()).toBe("unsupported");
  });

  it("words camera errors: refused, nothing found, anything else", () => {
    expect(cameraErrorKind({ name: "NotAllowedError" })).toBe("denied");
    expect(cameraErrorKind({ name: "SecurityError" })).toBe("denied");
    expect(cameraErrorKind({ name: "NotFoundError" })).toBe("missing");
    expect(cameraErrorKind({ name: "NotReadableError" })).toBe("other");
  });

  it("reports a remembered permission without prompting, and tolerates browsers that cannot say", async () => {
    Object.defineProperty(navigator, "permissions", { value: { query: vi.fn(async () => ({ state: "granted" })) }, configurable: true });
    expect(await cameraPermissionGranted()).toBe(true);
    Object.defineProperty(navigator, "permissions", { value: { query: vi.fn(async () => ({ state: "prompt" })) }, configurable: true });
    expect(await cameraPermissionGranted()).toBe(false);
    Object.defineProperty(navigator, "permissions", { value: { query: vi.fn(async () => { throw new TypeError("unsupported"); }) }, configurable: true });
    expect(await cameraPermissionGranted()).toBe(false);
  });
});
