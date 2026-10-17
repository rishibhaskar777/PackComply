/** Why a live preview is impossible on this page, or null if it can be tried: an insecure page (plain http on a LAN IP) or a browser without getUserMedia. */
export function liveCameraBlocker(): "insecure" | "unsupported" | null {
  if (typeof window === "undefined" || typeof navigator === "undefined") return "unsupported";
  if (window.isSecureContext === false) return "insecure";
  return typeof navigator.mediaDevices?.getUserMedia === "function" ? null : "unsupported";
}

/** Can this page show a live camera preview? It needs getUserMedia in a secure context (HTTPS or localhost). */
export function liveCameraSupported(): boolean {
  return liveCameraBlocker() === null;
}

/** True when the browser has already been told "allow" for the camera, so starting it will not prompt. */
export async function cameraPermissionGranted(): Promise<boolean> {
  try {
    const status = await navigator.permissions?.query({ name: "camera" as PermissionName });
    return status?.state === "granted";
  } catch { return false; }
}

export type CameraHandle = { stream: MediaStream; track: MediaStreamTrack | undefined; torch: boolean; stop: () => void; setTorch: (on: boolean) => Promise<boolean> };

/** Open the rear camera, as large as the device allows up to 1080p. */
export async function openRearCamera(): Promise<CameraHandle> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: false,
    video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } },
  });
  const track = stream.getVideoTracks()[0];
  const capabilities = (track?.getCapabilities?.() ?? {}) as MediaTrackCapabilities & { torch?: boolean };
  return {
    stream, track, torch: capabilities.torch === true,
    stop: () => stream.getTracks().forEach(item => item.stop()),
    async setTorch(on: boolean) {
      try { await track?.applyConstraints({ advanced: [{ torch: on } as MediaTrackConstraintSet] }); return on; } catch { return false; }
    },
  };
}

/** The camera error as a short key the UI can word: permission refused, nothing found, or anything else. */
export function cameraErrorKind(error: unknown): "denied" | "missing" | "other" {
  const name = (error as { name?: string })?.name;
  if (name === "NotAllowedError" || name === "SecurityError") return "denied";
  if (name === "NotFoundError" || name === "OverconstrainedError") return "missing";
  return "other";
}
