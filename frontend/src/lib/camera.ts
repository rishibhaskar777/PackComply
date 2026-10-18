import { decodeOriented } from "./imageResize";

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

const LENS_KEY = "packcomply.cameraDeviceId";
/** Largest still we ask ImageCapture for (long side), so a 100 MP sensor does not exhaust memory. */
const MAX_STILL_WIDTH = 4608;

export type LensRole = "main" | "tele" | "wide" | "macro";
export type Lens = { deviceId: string; label: string; role: LensRole };
export type LensProbe = { continuous: boolean; maxWidth: number };

type ExtendedCapabilities = MediaTrackCapabilities & { torch?: boolean; focusMode?: string[]; zoom?: { min: number; max: number; step?: number } };
type ExtendedSettings = MediaTrackSettings & { focusMode?: string; zoom?: number; pointsOfInterest?: unknown };
type CaptureConstructor = new (track: MediaStreamTrack) => {
  takePhoto: (settings?: object) => Promise<Blob>;
  grabFrame: () => Promise<ImageBitmap>;
  getPhotoCapabilities?: () => Promise<{ imageWidth?: { max?: number } }>;
};

export type Zoom = { min: number; max: number; step: number; value: number };

export type CameraHandle = {
  stream: MediaStream; track: MediaStreamTrack | undefined; torch: boolean; stop: () => void; setTorch: (on: boolean) => Promise<boolean>;
  deviceId: string; label: string; lenses: Lens[];
  /** The lens can focus by itself (continuous or single-shot). Without it a live photo is a gamble. */
  autofocus: boolean;
  /** ImageCapture exists, so a real full-resolution still can be taken. */
  imageCapture: boolean;
  zoom: Zoom | null;
  setZoom: (value: number) => Promise<number | null>;
  /** Focus on a normalised point of the frame, then back to continuous. False if the camera cannot. */
  focusAt: (x: number, y: number) => Promise<boolean>;
  takePhoto: () => Promise<Blob | null>;
  grabFrame: () => Promise<ImageBitmap | null>;
  diagnostics: () => Record<string, unknown>;
};

/** Does this browser have ImageCapture (a real autofocused still, not a video frame)? */
export function imageCaptureSupported(): boolean {
  return typeof (globalThis as { ImageCapture?: unknown }).ImageCapture === "function";
}

export function readStoredLens(): string | null {
  try { return localStorage.getItem(LENS_KEY); } catch { return null; }
}
function storeLens(deviceId: string) {
  try { localStorage.setItem(LENS_KEY, deviceId); } catch { /* private mode: just forget */ }
}

function classify(label: string): LensRole {
  if (/macro/i.test(label)) return "macro";
  if (/ultra|wide|0\.5/i.test(label)) return "wide";
  if (/tele|zoom|\b[2-9]x\b/i.test(label)) return "tele";
  return "main";
}
const ROLE_ORDER: Record<LensRole, number> = { main: 0, tele: 1, wide: 2, macro: 3 };

/**
 * The rear cameras of a phone, best first. Android Chrome names them "camera2 0, facing back": index 0 is usually the
 * main sensor, and the ultra-wide (fixed focus) and macro lenses are pushed behind it. Depth/IR sensors are dropped.
 */
export function rankBackCameras(devices: Array<Pick<MediaDeviceInfo, "deviceId" | "label" | "kind">>): Lens[] {
  const inputs = devices.filter(device => device.kind === "videoinput" && device.deviceId && !/depth|tof|infrared|\bir\b/i.test(device.label));
  const labelledBack = inputs.filter(device => /back|rear|environment/i.test(device.label));
  const back = labelledBack.length ? labelledBack : inputs.filter(device => !/front|user|selfie/i.test(device.label));
  const indexOf = (label: string) => Number(/camera2?\s+(\d+)/i.exec(label)?.[1] ?? 99);
  return back
    .map((device, order) => ({ lens: { deviceId: device.deviceId, label: device.label, role: classify(device.label) } as Lens, order }))
    .sort((a, b) => ROLE_ORDER[a.lens.role] - ROLE_ORDER[b.lens.role] || indexOf(a.lens.label) - indexOf(b.lens.label) || a.order - b.order)
    .map(item => item.lens);
}

/**
 * Pick the lens to use. A lone main camera needs no probing; with several candidates of the best kind, `probe` reads
 * each one's capabilities and the winner has continuous focus, then the largest sensor width, then the better rank.
 */
export async function chooseLens(lenses: Lens[], probe: (lens: Lens) => Promise<LensProbe | null>): Promise<Lens | undefined> {
  if (!lenses.length) return undefined;
  const tier = lenses.filter(lens => lens.role === lenses[0].role).slice(0, 3);
  if (tier.length === 1) return tier[0];
  const probed = await Promise.all(tier.map(async (lens, rank) => ({ lens, rank, info: await probe(lens).catch(() => null) })));
  probed.sort((a, b) => Number(b.info?.continuous ?? false) - Number(a.info?.continuous ?? false) || (b.info?.maxWidth ?? 0) - (a.info?.maxWidth ?? 0) || a.rank - b.rank);
  return probed[0].lens;
}

function videoConstraints(deviceId: string | undefined, size: { w: number; h: number }): MediaStreamConstraints {
  return {
    audio: false,
    video: { ...(deviceId ? { deviceId: { exact: deviceId } } : { facingMode: { ideal: "environment" } }), width: { ideal: size.w }, height: { ideal: size.h }, frameRate: { ideal: 30 } },
  };
}

/** Ask for 4K, then 1080p, then (if a remembered lens is gone) any rear camera. */
async function getStream(deviceId: string | undefined): Promise<MediaStream> {
  const attempts: Array<[string | undefined, { w: number; h: number }]> = [[deviceId, { w: 3840, h: 2160 }], [deviceId, { w: 1920, h: 1080 }]];
  if (deviceId) attempts.push([undefined, { w: 1920, h: 1080 }]);
  let failure: unknown;
  for (const [id, size] of attempts) {
    try { return await navigator.mediaDevices.getUserMedia(videoConstraints(id, size)); } catch (error) {
      failure = error;
      if (cameraErrorKind(error) === "denied") break;
    }
  }
  throw failure;
}

async function listLenses(): Promise<Lens[]> {
  try { return rankBackCameras((await navigator.mediaDevices.enumerateDevices?.()) ?? []); } catch { return []; }
}

const closeStream = (stream: MediaStream) => stream.getTracks().forEach(item => item.stop());

/** Briefly open another lens to read what it can do. */
async function probeLens(lens: Lens): Promise<LensProbe | null> {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { deviceId: { exact: lens.deviceId } } });
    const caps = (stream.getVideoTracks()[0]?.getCapabilities?.() ?? {}) as ExtendedCapabilities;
    closeStream(stream);
    return { continuous: caps.focusMode?.includes("continuous") ?? false, maxWidth: caps.width?.max ?? 0 };
  } catch { return null; }
}

/**
 * Open the main rear camera at the highest resolution it offers. `deviceId` forces one lens (the lens switcher); otherwise the
 * remembered lens is used, else the best of the rear cameras is picked (see rankBackCameras / chooseLens).
 */
export async function openRearCamera(options: { deviceId?: string } = {}): Promise<CameraHandle> {
  const wanted = options.deviceId ?? readStoredLens() ?? undefined;
  let stream = await getStream(wanted);
  const lenses = await listLenses();
  let track = stream.getVideoTracks()[0];
  let current = (track?.getSettings?.() as ExtendedSettings | undefined)?.deviceId;

  let target: Lens | undefined = lenses.find(lens => lens.deviceId === wanted);
  if (!target && lenses.length) {
    const currentCaps = (track?.getCapabilities?.() ?? {}) as ExtendedCapabilities;
    target = await chooseLens(lenses, lens => (lens.deviceId === current
      ? Promise.resolve({ continuous: currentCaps.focusMode?.includes("continuous") ?? false, maxWidth: currentCaps.width?.max ?? 0 })
      : probeLens(lens)));
  }
  if (target && current && target.deviceId !== current) {
    try {
      const next = await getStream(target.deviceId);
      closeStream(stream); stream = next; track = stream.getVideoTracks()[0];
      current = (track?.getSettings?.() as ExtendedSettings | undefined)?.deviceId ?? target.deviceId;
    } catch { /* keep the stream we already have */ }
  }
  const deviceId = current ?? target?.deviceId ?? "";
  if (deviceId && lenses.length) storeLens(deviceId);

  const capabilities = (track?.getCapabilities?.() ?? {}) as ExtendedCapabilities;
  const focusModes = capabilities.focusMode ?? [];
  const continuous = focusModes.includes("continuous");
  const lens = lenses.find(item => item.deviceId === deviceId);
  const label = lens?.label ?? track?.label ?? "";
  const apply = async (constraint: Record<string, unknown>) => {
    try { await track?.applyConstraints({ advanced: [constraint as MediaTrackConstraintSet] }); return true; } catch { return false; }
  };
  if (continuous) await apply({ focusMode: "continuous" });

  // A wide lens is held further back to focus, so start zoomed in; otherwise stay at 1x.
  let zoom: Zoom | null = null;
  if (capabilities.zoom && capabilities.zoom.max > capabilities.zoom.min) {
    const { min, max } = capabilities.zoom;
    const start = Math.min(max, Math.max(min, classify(label) === "wide" ? 1.5 : 1));
    zoom = { min, max, step: capabilities.zoom.step ?? 0.1, value: min };
    if (start > min && await apply({ zoom: start })) zoom.value = start;
  }

  const Capture = (globalThis as unknown as { ImageCapture?: CaptureConstructor }).ImageCapture;
  const imageCapture = track && Capture ? new Capture(track) : null;
  const pointsOfInterest = track ? "pointsOfInterest" in ((track.getSettings?.() ?? {}) as object) || "pointsOfInterest" in capabilities : false;

  const handle: CameraHandle = {
    stream, track, torch: capabilities.torch === true, deviceId, label, lenses,
    autofocus: continuous || focusModes.includes("single-shot"),
    imageCapture: imageCapture !== null,
    zoom,
    stop: () => closeStream(stream),
    async setTorch(on: boolean) { return (await apply({ torch: on })) ? on : false; },
    async setZoom(value: number) {
      if (!handle.zoom) return null;
      const clamped = Math.min(handle.zoom.max, Math.max(handle.zoom.min, value));
      if (!(await apply({ zoom: clamped }))) return null;
      handle.zoom = { ...handle.zoom, value: clamped };
      return clamped;
    },
    async focusAt(x: number, y: number) {
      if (!pointsOfInterest || !(await apply({ pointsOfInterest: [{ x, y }], focusMode: "single-shot" }))) return false;
      window.setTimeout(() => { if (continuous) void apply({ focusMode: "continuous" }); }, 1500);
      return true;
    },
    async takePhoto() {
      if (!imageCapture) return null;
      let imageWidth: number | undefined;
      try { const max = (await imageCapture.getPhotoCapabilities?.())?.imageWidth?.max; if (max) imageWidth = Math.min(max, MAX_STILL_WIDTH); } catch { /* ask for the default size */ }
      return imageCapture.takePhoto(imageWidth ? { imageWidth } : undefined);
    },
    async grabFrame() { return imageCapture ? imageCapture.grabFrame() : null; },
    diagnostics: () => ({
      label, deviceId, lenses: lenses.map(item => `${item.role}: ${item.label}`),
      settings: track?.getSettings?.() ?? {}, capabilities: track?.getCapabilities?.() ?? {},
      imageCapture: imageCapture !== null, autofocus: handle.autofocus, zoom: handle.zoom,
    }),
  };
  return handle;
}

/** A captured still ready to crop: its pixels, size and how it was obtained. */
export type Still = { source: CanvasImageSource; width: number; height: number; method: "takePhoto" | "grabFrame" | "video"; release: () => void };

/**
 * Take the best still the camera can give: a full-resolution autofocused photo (ImageCapture.takePhoto), else a grabbed
 * frame, else the current <video> frame drawn as-is. A failure at one step just drops to the next.
 */
export async function capturePhoto(camera: CameraHandle, video: HTMLVideoElement | null): Promise<Still | null> {
  if (camera.imageCapture) {
    try {
      const blob = await camera.takePhoto();
      if (blob) {
        const decoded = await decodeOriented(blob);
        return { source: decoded.source, width: decoded.width, height: decoded.height, method: "takePhoto", release: decoded.release };
      }
    } catch { /* fall through to grabFrame */ }
    try {
      const bitmap = await camera.grabFrame();
      if (bitmap) return { source: bitmap, width: bitmap.width, height: bitmap.height, method: "grabFrame", release: () => bitmap.close?.() };
    } catch { /* fall through to the video frame */ }
  }
  if (video?.videoWidth) return { source: video, width: video.videoWidth, height: video.videoHeight, method: "video", release: () => undefined };
  return null;
}

/** The camera error as a short key the UI can word: permission refused, nothing found, or anything else. */
export function cameraErrorKind(error: unknown): "denied" | "missing" | "other" {
  const name = (error as { name?: string })?.name;
  if (name === "NotAllowedError" || name === "SecurityError") return "denied";
  if (name === "NotFoundError" || name === "OverconstrainedError") return "missing";
  return "other";
}
