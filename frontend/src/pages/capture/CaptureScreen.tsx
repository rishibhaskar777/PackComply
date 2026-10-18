import { ChangeEvent, MouseEvent, TouchEvent, useCallback, useEffect, useRef, useState } from "react";
import { Camera, Check, HelpCircle, ImageIcon, Minus, Plus, SkipForward, SwitchCamera, X, Zap, ZapOff } from "lucide-react";
import { Button } from "../../components/ui";
import { PANEL_LABEL } from "../../components/ProgressPanel";
import { fill, TranslationKey, useI18n } from "../../i18n";
import { cameraErrorKind, cameraPermissionGranted, CameraHandle, capturePhoto, imageCaptureSupported, liveCameraBlocker, openRearCamera } from "../../lib/camera";
import { CropFraction, cropFractionToRect, FULL_CROP, GUIDE_MARGIN, guideFor, guideToPhotoRect, Size, stagePointToFrame } from "../../lib/cropMath";
import { assessSource, QualityReport, sharpnessOfRegion } from "../../lib/imageQuality";
import { cropToCanvas, decodeOriented, JPEG_QUALITY, toBlob } from "../../lib/imageResize";
import { createSharpnessGate, GateView } from "../../lib/sharpnessGate";
import { CapturedPhoto, newPhoto } from "../../lib/useInspectionRun";
import type { Panel } from "../../types";
import { CropBox } from "./CropBox";
import { QualityChips } from "./QualityChips";

const HINTS: Partial<Record<Panel, TranslationKey>> = { BACK: "capHintBack", FRONT: "capHintFront", DATE_STAMP: "capHintDate", OTHER: "capHintOther" };

export type StepSpec = { panel: Panel; required: boolean };
type Candidate = { blob: Blob; url: string; quality: QualityReport | null; name: string; source: "live" | "file" };
type LastShot = { method: string; photo: string; crop: string; sharpness: number | null };
/** idle: a live preview is possible but not asked for yet (no permission prompt until the user taps). */
type CameraState = "idle" | "starting" | "live" | "fallback" | "denied";
/** noimagecapture / nofocus: a live preview would work but cannot give a sharp still, so the phone camera is the default. */
type FallbackReason = "insecure" | "unsupported" | "missing" | "error" | "noimagecapture" | "nofocus" | null;

const AUTO_KEY = "packcomply.autoCapture";
const LIVE_SAMPLE_MS = 160;
const readAuto = () => { try { return localStorage.getItem(AUTO_KEY) !== "0"; } catch { return true; } };

type Props = {
  steps: StepSpec[];
  current: Panel;
  photos: Partial<Record<Panel, CapturedPhoto>>;
  skipped: Set<Panel>;
  title: string;
  onStep: (panel: Panel) => void;
  onUse: (photo: CapturedPhoto) => void;
  onSkip: () => void;
  onClose: () => void;
  /** Force the native-camera fallback (tests, or a page known to be insecure). */
  forceFallback?: boolean;
};

/** Screen A: the viewfinder, the step switcher, the shutter and the review sheet. */
export function CaptureScreen({ steps, current, photos, skipped, title, onStep, onUse, onSkip, onClose, forceFallback = false }: Props) {
  const { t } = useI18n();
  const video = useRef<HTMLVideoElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const camera = useRef<CameraHandle | null>(null);
  const cameraInput = useRef<HTMLInputElement>(null);
  const galleryInput = useRef<HTMLInputElement>(null);
  const sheet = useRef<HTMLDivElement>(null);
  const [blocker] = useState(() => (forceFallback ? null : liveCameraBlocker()));
  const [weakLive] = useState(() => !forceFallback && !blocker && !imageCaptureSupported());
  const [cameraState, setCameraState] = useState<CameraState>(() => (forceFallback || blocker || weakLive ? "fallback" : "idle"));
  const [reason, setReason] = useState<FallbackReason>(() => (forceFallback ? null : blocker ?? (weakLive ? "noimagecapture" : null)));
  const [torch, setTorch] = useState(false);
  const [torchAvailable, setTorchAvailable] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [checking, setChecking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [pressed, setPressed] = useState(false);
  const mounted = useRef(true);
  const [stageSize, setStageSize] = useState<Size>({ w: 0, h: 0 });
  const stageSizeRef = useRef(stageSize);
  const forceLive = useRef(false);
  const busy = useRef(false);
  const gate = useRef<ReturnType<typeof createSharpnessGate> | null>(null);
  const snapRef = useRef<() => void>(() => undefined);
  const [view, setView] = useState<GateView>({ state: "focusing", canShoot: false, fire: false });
  const [autoCapture, setAutoCapture] = useState(readAuto);
  const autoRef = useRef(autoCapture);
  const [lens, setLens] = useState<{ count: number; index: number; zoom: CameraHandle["zoom"] }>({ count: 0, index: 0, zoom: null });
  const [ring, setRing] = useState<{ x: number; y: number; key: number } | null>(null);
  const [debugOpen, setDebugOpen] = useState(false);
  const debugRef = useRef(false);
  const helpTaps = useRef<number[]>([]);
  const [score, setScore] = useState<number | null>(null);
  const [lastShot, setLastShot] = useState<LastShot | null>(null);
  const [cropping, setCropping] = useState(false);
  const [crop, setCrop] = useState<CropFraction>(FULL_CROP);
  const guide = guideFor(stageSize);

  const startCamera = useCallback((options: { deviceId?: string; force?: boolean } = {}) => {
    if (options.force) forceLive.current = true;
    setCameraState("starting");
    openRearCamera({ deviceId: options.deviceId }).then(handle => {
      if (!mounted.current) { handle.stop(); return; }
      // A lens that cannot focus gives blurry labels: the phone camera becomes the default and live stays an option.
      if (!forceLive.current && !handle.autofocus) { handle.stop(); setReason("nofocus"); setCameraState("fallback"); return; }
      camera.current = handle;
      setTorchAvailable(handle.torch);
      setTorch(false);
      setLens({ count: handle.lenses.length, index: Math.max(0, handle.lenses.findIndex(item => item.deviceId === handle.deviceId)), zoom: handle.zoom });
      if (video.current) { video.current.srcObject = handle.stream; void video.current.play?.().catch(() => undefined); }
      setCameraState("live");
    }).catch(error => {
      if (!mounted.current) return;
      const kind = cameraErrorKind(error);
      setReason(kind === "missing" ? "missing" : "error");
      setCameraState(kind === "denied" ? "denied" : "fallback");
    });
  }, []);

  // The camera is never opened on page load: permission is asked when the user taps the shutter or "Enable camera".
  // If the browser already remembers "allow", the preview simply starts.
  useEffect(() => {
    mounted.current = true;
    if (cameraState === "idle") void cameraPermissionGranted().then(granted => { if (granted && mounted.current) startCamera(); });
    return () => { mounted.current = false; camera.current?.stop(); camera.current = null; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Review sheet: focus moves into it, and back to the shutter when it closes.
  useEffect(() => { if (candidate) sheet.current?.focus(); }, [candidate]);
  useEffect(() => () => { if (candidate) URL.revokeObjectURL(candidate.url); }, [candidate]);
  useEffect(() => {
    autoRef.current = autoCapture;
    try { localStorage.setItem(AUTO_KEY, autoCapture ? "1" : "0"); } catch { /* not remembered */ }
  }, [autoCapture]);
  useEffect(() => { debugRef.current = debugOpen; }, [debugOpen]);

  // The guide is sized from the measured viewfinder (about 4:3 portrait), so it follows rotation and resizes.
  useEffect(() => {
    const measure = () => {
      const box = stage.current?.getBoundingClientRect();
      const next = { w: Math.round(box?.width ?? 0), h: Math.round(box?.height ?? 0) };
      stageSizeRef.current = next;
      setStageSize(previous => (previous.w === next.w && previous.h === next.h ? previous : next));
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  // While the preview is live, watch the sharpness of the guide area ~6 times a second. The shutter waits for a steady, sharp
  // picture (and fires by itself if Auto-capture is on); after 2 s it can be pressed anyway.
  useEffect(() => {
    if (cameraState !== "live" || candidate || checking) return;
    const watcher = createSharpnessGate();
    gate.current = watcher;
    setView({ state: "focusing", canShoot: false, fire: false });
    const canvas = document.createElement("canvas");
    const timer = window.setInterval(() => {
      const element = video.current;
      if (!element?.videoWidth || busy.current) return;
      const frame = { w: element.videoWidth, h: element.videoHeight };
      const region = guideToPhotoRect(guideFor(stageSizeRef.current), stageSizeRef.current, frame, frame, 0);
      const value = sharpnessOfRegion(element, region, canvas);
      if (debugRef.current) setScore(value);
      const next = watcher.update(value, performance.now());
      setView(previous => (previous.state === next.state && previous.canShoot === next.canShoot ? previous : next));
      if (next.fire && autoRef.current) snapRef.current();
    }, LIVE_SAMPLE_MS);
    return () => { window.clearInterval(timer); gate.current = null; };
  }, [cameraState, candidate, checking]);

  const present = useCallback(async (blob: Blob, name: string, quality?: QualityReport | null, source: Candidate["source"] = "file") => {
    setProblem(null);
    setCropping(false); setCrop(FULL_CROP);
    let report = quality ?? null;
    if (quality === undefined) {
      setChecking(true);
      try {
        const decoded = await decodeOriented(blob);
        report = assessSource(decoded.source, decoded.width, decoded.height);
        decoded.release();
      } catch { setProblem(t("capBadPhoto")); setChecking(false); return; }
      setChecking(false);
    }
    setCandidate({ blob, url: URL.createObjectURL(blob), quality: report, name, source });
  }, [t]);

  /** Take the still (ImageCapture when possible), crop it to the guide, then judge sharpness/light/glare on the crop. */
  async function snap() {
    const element = video.current, cam = camera.current;
    if (!element || !cam || busy.current) return;
    busy.current = true; setChecking(true); setProblem(null);
    try {
      const still = await capturePhoto(cam, element);
      if (!still) { setProblem(t("capBadPhoto")); gate.current?.reset(); return; }
      try {
        const size = stageSizeRef.current;
        const rect = guideToPhotoRect(guideFor(size), size, { w: element.videoWidth, h: element.videoHeight }, { w: still.width, h: still.height }, GUIDE_MARGIN);
        const canvas = cropToCanvas(still.source, rect);
        if (!canvas) { setProblem(t("capBadPhoto")); gate.current?.reset(); return; }
        const quality = assessSource(canvas, canvas.width, canvas.height);
        setLastShot({ method: still.method, photo: `${still.width}x${still.height}`, crop: `${canvas.width}x${canvas.height}`, sharpness: quality ? Math.round(quality.sharpness) : null });
        const blob = await toBlob(canvas, "image/jpeg", JPEG_QUALITY);
        await present(blob, `${current.toLowerCase()}.jpg`, quality, "live");
      } finally { still.release(); }
    } catch { setProblem(t("capBadPhoto")); gate.current?.reset(); } finally { busy.current = false; setChecking(false); }
  }
  snapRef.current = () => { void snap(); };

  /** Tap on the preview: show the ring and, where the camera supports it, focus there. */
  function tapToFocus(event: MouseEvent<HTMLVideoElement>) {
    const box = stage.current?.getBoundingClientRect();
    const at = { x: event.clientX - (box?.left ?? 0), y: event.clientY - (box?.top ?? 0) };
    setRing({ ...at, key: Date.now() });
    window.setTimeout(() => setRing(null), 900);
    gate.current?.reset();
    const element = video.current;
    if (!element?.videoWidth) return;
    const point = stagePointToFrame(at.x, at.y, stageSizeRef.current, { w: element.videoWidth, h: element.videoHeight });
    void camera.current?.focusAt(point.x, point.y);
  }
  async function changeZoom(value: number) {
    const applied = await camera.current?.setZoom(value);
    if (applied != null) setLens(previous => (previous.zoom ? { ...previous, zoom: { ...previous.zoom, value: applied } } : previous));
  }
  const pinch = useRef<{ distance: number; zoom: number } | null>(null);
  const touchDistance = (touches: TouchEvent<HTMLVideoElement>["touches"]) => Math.hypot(touches[0].clientX - touches[1].clientX, touches[0].clientY - touches[1].clientY);
  function pinchStart(event: TouchEvent<HTMLVideoElement>) {
    pinch.current = event.touches.length === 2 && lens.zoom ? { distance: touchDistance(event.touches), zoom: lens.zoom.value } : null;
  }
  function pinchMove(event: TouchEvent<HTMLVideoElement>) {
    if (pinch.current && event.touches.length === 2) void changeZoom(pinch.current.zoom * (touchDistance(event.touches) / pinch.current.distance));
  }
  function switchLens() {
    const cam = camera.current;
    if (!cam || cam.lenses.length < 2) return;
    const next = cam.lenses[(lens.index + 1) % cam.lenses.length];
    cam.stop(); camera.current = null;
    startCamera({ deviceId: next.deviceId, force: true });
  }
  /** Five quick taps on the help icon open the camera diagnostics instead. */
  function helpClick() {
    const now = Date.now();
    helpTaps.current = [...helpTaps.current.filter(at => now - at < 3000), now];
    if (helpTaps.current.length >= 5) { helpTaps.current = []; setDebugOpen(open => !open); return; }
    setHelpOpen(open => !open);
  }

  /** Must stay synchronous: browsers only open the native camera for a click() made inside the tap itself. */
  function openNativeCamera() { cameraInput.current?.click(); }
  function shutter() {
    if (shutterWaiting) return;
    setPressed(true); window.setTimeout(() => setPressed(false), 160);
    if (cameraState === "live") void snap();
    else if (cameraState === "idle") startCamera();
    else openNativeCamera();
  }
  function onFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) void present(file, file.name);
  }
  async function toggleTorch() { if (camera.current) setTorch(await camera.current.setTorch(!torch)); }

  async function usePhoto() {
    if (!candidate) return;
    let blob = candidate.blob;
    if (cropping) {
      try {
        const decoded = await decodeOriented(blob);
        const canvas = cropToCanvas(decoded.source, cropFractionToRect(crop, { w: decoded.width, h: decoded.height }));
        decoded.release();
        if (canvas) blob = await toBlob(canvas, "image/jpeg", JPEG_QUALITY);
      } catch { setProblem(t("capBadPhoto")); return; }
    }
    onUse(newPhoto(current, blob, candidate.name));
    setCandidate(null);
  }

  const live = cameraState === "live";
  const shutterWaiting = cameraState === "starting" || (live && !view.canShoot);
  const fallbackText = cameraState === "idle" ? t("capIdleBody") : cameraState === "denied" ? t("capCameraDenied")
    : reason === "insecure" ? t("capInsecureHint") : reason === "missing" || reason === "error" ? t("capCameraFailedHint")
      : reason === "noimagecapture" ? t("capNoStillHint") : reason === "nofocus" ? t("capNoFocusHint") : t("capFallbackBody");
  const liveOptional = cameraState === "fallback" && (reason === "noimagecapture" || reason === "nofocus");
  const step = steps.find(item => item.panel === current);
  const retakeAdvised = candidate?.quality?.retake ?? false;
  const blurry = candidate?.quality ? !candidate.quality.sharp : false;
  const stepText = (panel: Panel) => t(PANEL_LABEL[panel]);
  const zoom = lens.zoom;
  const diagnostics = debugOpen ? JSON.stringify({
    ...(camera.current?.diagnostics() ?? { camera: "not started" }),
    imageCaptureAvailable: imageCaptureSupported(), cameraState, reason, sharpnessNow: score === null ? null : Math.round(score), gate: view.state, lastShot,
  }, null, 1) : "";

  return (
    <div className="cap" role="region" aria-label={title}>
      <header className="cap-top">
        <button type="button" className="cap-icon-button" aria-label={t("capClose")} onClick={onClose}><X size={22} aria-hidden="true" /></button>
        <h1 className="cap-top__title" id="capture-heading" tabIndex={-1}>{title}</h1>
        <div className="cap-top__actions">
          {torchAvailable && <button type="button" className="cap-icon-button" aria-label={t("capFlash")} aria-pressed={torch} onClick={() => void toggleTorch()}>{torch ? <Zap size={20} aria-hidden="true" /> : <ZapOff size={20} aria-hidden="true" />}</button>}
          <button type="button" className="cap-icon-button" aria-label={t("capHelp")} aria-expanded={helpOpen} aria-controls="capture-tips" onClick={helpClick}><HelpCircle size={20} aria-hidden="true" /></button>
        </div>
      </header>

      <div className="cap-steps" role="tablist" aria-label={t("capSteps")}>
        {steps.map((item, index) => {
          const photo = photos[item.panel];
          const selected = item.panel === current;
          return (
            <button key={item.panel} type="button" role="tab" aria-selected={selected} className={`cap-step${selected ? " is-active" : ""}${photo ? " is-done" : ""}`} onClick={() => onStep(item.panel)}>
              <span className="cap-step__thumb" aria-hidden="true">
                {photo ? <><img src={photo.url} alt="" /><span className="cap-step__check"><Check size={12} /></span></> : <span className="cap-step__number">{index + 1}</span>}
              </span>
              <span className="cap-step__text">
                <span className="cap-step__name">{stepText(item.panel)}</span>
                <span className="cap-step__meta">{photo ? t("capCaptured") : skipped.has(item.panel) ? t("capSkipped") : item.required ? t("capRequired") : t("capOptional")}</span>
              </span>
            </button>
          );
        })}
      </div>

      <div className="cap-stage" ref={stage}>
        {live || cameraState === "starting"
          ? <video ref={video} className="cap-video" playsInline muted autoPlay aria-label={t("capViewfinder")} onClick={tapToFocus} onTouchStart={pinchStart} onTouchMove={pinchMove} />
          : (
            <div className="cap-fallback">
              <Camera size={36} aria-hidden="true" />
              <p className="cap-fallback__title">{cameraState === "idle" ? t("capIdleTitle") : t("capFallbackTitle")}</p>
              <p className="cap-fallback__body">{fallbackText}</p>
              {cameraState === "idle"
                ? <Button className="cap-btn" onClick={() => startCamera()}>{t("capEnableCamera")}</Button>
                : <Button className="cap-btn" onClick={openNativeCamera}>{liveOptional ? t("capPhoneCameraBest") : t("capUsePhoneCamera")}</Button>}
              {liveOptional && <Button variant="outline" className="cap-btn" onClick={() => startCamera({ force: true })}>{t("capTryLive")}</Button>}
            </div>
          )}
        {cameraState === "starting" && <p className="cap-status" role="status">{t("capCameraStarting")}</p>}
        {live && !checking && !problem && (
          <p className={`cap-status cap-status--${view.state}`} role="status">{view.state === "focusing" ? t("capHoldSteady") : view.state === "ready" ? t("capInFocus") : t("capHoldGuidance")}</p>
        )}
        <div className="cap-guide" aria-hidden="true">
          <span style={{ inset: `${guide.top * 100}% ${guide.right * 100}% ${guide.bottom * 100}% ${guide.left * 100}%` }} />
        </div>
        {ring && <span key={ring.key} className="cap-focus-ring" style={{ left: ring.x, top: ring.y }} aria-hidden="true" />}
        {live && (lens.count > 1 || zoom) && (
          <div className="cap-lens">
            {lens.count > 1 && <button type="button" className="cap-pill" onClick={switchLens} aria-label={t("capLens")}><SwitchCamera size={16} aria-hidden="true" />{t("capLens")} {lens.index + 1}/{lens.count}</button>}
            {zoom && (
              <div className="cap-zoom" role="group" aria-label={t("capZoom")}>
                <button type="button" className="cap-pill cap-pill--icon" aria-label={t("capZoomOut")} onClick={() => void changeZoom(zoom.value - Math.max(zoom.step, 0.25))}><Minus size={16} aria-hidden="true" /></button>
                <span className="cap-zoom__value">{zoom.value.toFixed(1)}×</span>
                <button type="button" className="cap-pill cap-pill--icon" aria-label={t("capZoomIn")} onClick={() => void changeZoom(zoom.value + Math.max(zoom.step, 0.25))}><Plus size={16} aria-hidden="true" /></button>
              </div>
            )}
          </div>
        )}
        <p className="cap-hint" role="note">{t(HINTS[current] ?? "capHintOther")}{live && <><br />{t("capHoldGuidance")}</>}</p>
        {checking && <p className="cap-status cap-status--checking" role="status">{t("capChecking")}</p>}
        {problem && <p className="cap-status cap-status--problem" role="alert">{problem}</p>}
        {helpOpen && (
          <div className="cap-tips" id="capture-tips">
            <p className="cap-tips__title">{t("capHelpTitle")}</p>
            <ul><li>{t("capTip1")}</li><li>{t("capTip2")}</li><li>{t("capTip3")}</li></ul>
            <p className="cap-tips__note">{t("capKeepsOnDevice")}</p>
          </div>
        )}
        {debugOpen && <pre className="cap-debug" aria-label={t("capDebugTitle")} data-testid="camera-debug">{diagnostics}</pre>}
      </div>

      {(live || cameraState === "idle") && (
        <div className="cap-options">
          <button type="button" className="cap-pill" onClick={openNativeCamera}><Camera size={16} aria-hidden="true" />{t("capPhoneCameraBest")}</button>
          {live && (
            <label className="cap-toggle">
              <input type="checkbox" checked={autoCapture} onChange={event => setAutoCapture(event.target.checked)} />
              <span>{t("capAutoCapture")}</span>
            </label>
          )}
        </div>
      )}

      <footer className="cap-bottom">
        <button type="button" className="cap-side-button" onClick={() => galleryInput.current?.click()}><ImageIcon size={22} aria-hidden="true" /><span>{t("capGallery")}</span></button>
        <button type="button" className={`cap-shutter${pressed ? " is-pressed" : ""}`} aria-label={fill(t("capShutter"), { panel: stepText(current).toLowerCase() })} onClick={shutter} disabled={shutterWaiting}><span /></button>
        {step && !step.required
          ? <button type="button" className="cap-side-button" onClick={onSkip}><SkipForward size={22} aria-hidden="true" /><span>{t("capSkip")}</span></button>
          : <span className="cap-side-button cap-side-button--empty" aria-hidden="true" />}
      </footer>

      <input ref={cameraInput} className="sr-only" type="file" accept="image/*" capture="environment" tabIndex={-1} aria-hidden="true" data-testid="camera-input" onChange={onFile} />
      <input ref={galleryInput} className="sr-only" type="file" accept="image/*" tabIndex={-1} aria-hidden="true" data-testid="gallery-input" onChange={onFile} />

      {candidate && (
        <div className="cap-review-scrim">
          <div ref={sheet} className="cap-review" role="dialog" aria-modal="true" aria-labelledby="cap-review-title" tabIndex={-1}
            onKeyDown={event => { if (event.key === "Escape") setCandidate(null); }}>
            <h2 id="cap-review-title" className="cap-review__title">{t("capReviewTitle")} · {stepText(current)}</h2>
            <div className="cap-review__frame">
              <img className="cap-review__photo" src={candidate.url} alt={t("capReviewAlt")} />
              {cropping && <CropBox value={crop} onChange={setCrop} label={t("capCropBox")} />}
            </div>
            <QualityChips report={candidate.quality} />
            {retakeAdvised && <p className="cap-review__advice" role="status">{blurry ? t("capPhotoBlurry") : t("capRetakeRecommended")}</p>}
            {candidate.source === "file" && (
              <Button variant="ghost" className="cap-btn cap-btn--block" aria-pressed={cropping} onClick={() => { setCropping(value => !value); setCrop(FULL_CROP); }}>{cropping ? t("capNoCrop") : t("capCrop")}</Button>
            )}
            <div className="cap-review__actions">
              <Button size="lg" variant={retakeAdvised ? "primary" : "outline"} className="cap-btn" onClick={() => setCandidate(null)}>{t("capRetake")}</Button>
              <Button size="lg" variant={retakeAdvised ? "outline" : "primary"} className="cap-btn" data-autofocus onClick={() => void usePhoto()}>{retakeAdvised ? t("capUseAnyway") : t("capUsePhoto")}</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
