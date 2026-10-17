/** Can this page show a live camera preview? It needs getUserMedia in a secure context (HTTPS or localhost). */
export function liveCameraSupported(): boolean {
  if (typeof window === "undefined" || typeof navigator === "undefined") return false;
  return window.isSecureContext !== false && typeof navigator.mediaDevices?.getUserMedia === "function";
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
