import { ChangeEvent, useCallback, useEffect, useRef, useState } from "react";
import { Camera, Check, HelpCircle, ImageIcon, SkipForward, X, Zap, ZapOff } from "lucide-react";
import { Button } from "../../components/ui";
import { PANEL_LABEL } from "../../components/ProgressPanel";
import { fill, TranslationKey, useI18n } from "../../i18n";
import { cameraErrorKind, CameraHandle, liveCameraSupported, openRearCamera } from "../../lib/camera";
import { assessSource, QualityReport } from "../../lib/imageQuality";
import { decodeOriented } from "../../lib/imageResize";
import { CapturedPhoto, newPhoto } from "../../lib/useInspectionRun";
import type { Panel } from "../../types";
import { QualityChips } from "./QualityChips";

const HINTS: Partial<Record<Panel, TranslationKey>> = { BACK: "capHintBack", FRONT: "capHintFront", DATE_STAMP: "capHintDate", OTHER: "capHintOther" };

export type StepSpec = { panel: Panel; required: boolean };
type Candidate = { blob: Blob; url: string; quality: QualityReport | null; name: string };
type CameraState = "starting" | "live" | "fallback" | "denied";

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
  const camera = useRef<CameraHandle | null>(null);
  const cameraInput = useRef<HTMLInputElement>(null);
  const galleryInput = useRef<HTMLInputElement>(null);
  const sheet = useRef<HTMLDivElement>(null);
  const [cameraState, setCameraState] = useState<CameraState>(() => (!forceFallback && liveCameraSupported() ? "starting" : "fallback"));
  const [torch, setTorch] = useState(false);
  const [torchAvailable, setTorchAvailable] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [checking, setChecking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [pressed, setPressed] = useState(false);

  // Open the rear camera when a live preview is possible; fall back to the native camera otherwise.
  useEffect(() => {
    if (forceFallback || !liveCameraSupported()) return;
    let cancelled = false;
    openRearCamera().then(handle => {
      if (cancelled) { handle.stop(); return; }
      camera.current = handle;
      setTorchAvailable(handle.torch);
      if (video.current) { video.current.srcObject = handle.stream; void video.current.play?.().catch(() => undefined); }
      setCameraState("live");
    }).catch(error => { if (!cancelled) setCameraState(cameraErrorKind(error) === "denied" ? "denied" : "fallback"); });
    return () => { cancelled = true; camera.current?.stop(); camera.current = null; };
  }, [forceFallback]);

  // Review sheet: focus moves into it, and back to the shutter when it closes.
  useEffect(() => { if (candidate) sheet.current?.focus(); }, [candidate]);
  useEffect(() => () => { if (candidate) URL.revokeObjectURL(candidate.url); }, [candidate]);

  const present = useCallback(async (blob: Blob, name: string, quality?: QualityReport | null) => {
    setProblem(null);
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
    setCandidate({ blob, url: URL.createObjectURL(blob), quality: report, name });
  }, [t]);

  function snapFrame() {
    const element = video.current;
    if (!element || !element.videoWidth) return;
    const canvas = document.createElement("canvas");
    canvas.width = element.videoWidth; canvas.height = element.videoHeight;
    canvas.getContext("2d")?.drawImage(element, 0, 0, canvas.width, canvas.height);
    const quality = assessSource(canvas, canvas.width, canvas.height);
    canvas.toBlob(blob => { if (blob) void present(blob, `${current.toLowerCase()}.jpg`, quality); }, "image/jpeg", 0.92);
  }
  function shutter() {
    setPressed(true); window.setTimeout(() => setPressed(false), 160);
    if (cameraState === "live") snapFrame();
    else cameraInput.current?.click();
  }
  function onFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) void present(file, file.name);
  }
  async function toggleTorch() { if (camera.current) setTorch(await camera.current.setTorch(!torch)); }

  function usePhoto() {
    if (!candidate) return;
    onUse(newPhoto(current, candidate.blob, candidate.name));
    setCandidate(null);
  }
  const step = steps.find(item => item.panel === current);
  const retakeAdvised = candidate?.quality?.retake ?? false;
  const stepText = (panel: Panel) => t(PANEL_LABEL[panel]);

  return (
    <div className="cap" role="region" aria-label={title}>
      <header className="cap-top">
        <button type="button" className="cap-icon-button" aria-label={t("capClose")} onClick={onClose}><X size={22} aria-hidden="true" /></button>
        <h1 className="cap-top__title" id="capture-heading" tabIndex={-1}>{title}</h1>
        <div className="cap-top__actions">
          {torchAvailable && <button type="button" className="cap-icon-button" aria-label={t("capFlash")} aria-pressed={torch} onClick={() => void toggleTorch()}>{torch ? <Zap size={20} aria-hidden="true" /> : <ZapOff size={20} aria-hidden="true" />}</button>}
          <button type="button" className="cap-icon-button" aria-label={t("capHelp")} aria-expanded={helpOpen} aria-controls="capture-tips" onClick={() => setHelpOpen(open => !open)}><HelpCircle size={20} aria-hidden="true" /></button>
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

      <div className="cap-stage">
        {cameraState === "live" || cameraState === "starting"
          ? <video ref={video} className="cap-video" playsInline muted autoPlay aria-label={t("capViewfinder")} />
          : (
            <div className="cap-fallback">
              <Camera size={36} aria-hidden="true" />
              <p className="cap-fallback__title">{t("capFallbackTitle")}</p>
              <p className="cap-fallback__body">{cameraState === "denied" ? t("capCameraDenied") : t("capFallbackBody")}</p>
            </div>
          )}
        {cameraState === "starting" && <p className="cap-status" role="status">{t("capCameraStarting")}</p>}
        <div className="cap-guide" aria-hidden="true"><span /></div>
        <p className="cap-hint" role="note">{t(HINTS[current] ?? "capHintOther")}</p>
        {checking && <p className="cap-status cap-status--checking" role="status">{t("capChecking")}</p>}
        {problem && <p className="cap-status cap-status--problem" role="alert">{problem}</p>}
        {helpOpen && (
          <div className="cap-tips" id="capture-tips">
            <p className="cap-tips__title">{t("capHelpTitle")}</p>
            <ul><li>{t("capTip1")}</li><li>{t("capTip2")}</li><li>{t("capTip3")}</li></ul>
            <p className="cap-tips__note">{t("capKeepsOnDevice")}</p>
          </div>
        )}
      </div>

      <footer className="cap-bottom">
        <button type="button" className="cap-side-button" onClick={() => galleryInput.current?.click()}><ImageIcon size={22} aria-hidden="true" /><span>{t("capGallery")}</span></button>
        <button type="button" className={`cap-shutter${pressed ? " is-pressed" : ""}`} aria-label={fill(t("capShutter"), { panel: stepText(current).toLowerCase() })} onClick={shutter} disabled={cameraState === "starting"}><span /></button>
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
            <img className="cap-review__photo" src={candidate.url} alt={t("capReviewAlt")} />
            <QualityChips report={candidate.quality} />
            {retakeAdvised && <p className="cap-review__advice" role="status">{t("capRetakeRecommended")}</p>}
            <div className="cap-review__actions">
              <Button size="lg" variant={retakeAdvised ? "primary" : "outline"} className="cap-btn" onClick={() => setCandidate(null)}>{t("capRetake")}</Button>
              <Button size="lg" variant={retakeAdvised ? "outline" : "primary"} className="cap-btn" data-autofocus onClick={usePhoto}>{retakeAdvised ? t("capUseAnyway") : t("capUsePhoto")}</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
