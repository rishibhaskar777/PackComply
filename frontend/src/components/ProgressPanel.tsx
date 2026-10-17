import { useEffect, useState } from "react";
import { Check, CircleAlert, LoaderCircle, RefreshCw, X } from "lucide-react";
import { Button } from "./ui";
import { fill, TranslationKey, useI18n } from "../i18n";
import { useReducedMotion } from "../lib/motion";
import type { Panel, Progress, ProgressStep } from "../types";

export const PANEL_LABEL: Record<Panel, TranslationKey> = { BACK: "panelBack", FRONT: "panelFront", DATE_STAMP: "panelDate", OTHER: "panelOther" };
const ERROR_KEYS: Record<string, TranslationKey> = { OCR: "progErrOcr", extraction: "progErrExtraction", compliance: "progErrCompliance", risk: "progErrRisk", interrupted: "progErrInterrupted", unknown: "progErrUnknown" };

export type PreviewPhoto = { id?: string; panel: Panel; url: string };

type Props = {
  /** The latest server snapshot; null while photos are still uploading. */
  progress: Progress | null;
  uploading?: boolean;
  photos: PreviewPhoto[];
  /** Why the run stopped, when it did (shown with a "Try again" button). */
  error?: { message: string; unreachable: boolean; stage?: string | null } | null;
  onRetry?: () => void;
  retryLabel?: string;
  /** Dark field-app styling. */
  dark?: boolean;
  className?: string;
};

export const formatElapsed = (milliseconds: number) => {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
};

function languageName(code: string, locale: string, fallback: string) {
  try { return new Intl.DisplayNames([locale], { type: "language" }).of(code) ?? fallback; } catch { return fallback; }
}

/** Short names for the languages read on a photo: "EN + हिन्दी", or just "EN" for a date stamp. */
const SHORT_LANGUAGE: Record<string, string> = { en: "EN", hi: "हिन्दी" };
export const languageBadge = (codes: string[]) => codes.map(code => SHORT_LANGUAGE[code] ?? code.toUpperCase()).join(" + ");

/** Animated tick: the stroke draws itself once the step is done (static under reduced motion). */
function StepIcon({ status }: { status: ProgressStep["status"] }) {
  if (status === "running") return <LoaderCircle className="step-icon step-icon--running" size={18} aria-hidden="true" />;
  if (status === "failed") return <X className="step-icon step-icon--failed" size={18} aria-hidden="true" />;
  if (status === "done") {
    return (
      <svg className="step-icon step-icon--done" width="18" height="18" viewBox="0 0 18 18" aria-hidden="true" focusable="false">
        <circle className="step-icon__disc" cx="9" cy="9" r="8" />
        <path className="step-icon__tick" d="M5.2 9.4 7.9 12 12.9 6.4" />
      </svg>
    );
  }
  return <span className="step-icon step-icon--pending" aria-hidden="true" />;
}

/**
 * Live processing progress: a vertical step list, a thin overall bar, elapsed time and the photo being
 * read with a scan line while OCR runs. Percentages are the server's, counted from finished steps only.
 */
export function ProgressPanel({ progress, uploading = false, photos, error, onRetry, retryLabel, dark = false, className = "" }: Props) {
  const { t, locale } = useI18n();
  const reduced = useReducedMotion();
  // The server says how long the run has taken; between snapshots the clock only counts real time forward.
  const [received, setReceived] = useState(() => ({ at: Date.now(), elapsed: progress?.elapsed_ms ?? 0 }));
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => { setReceived({ at: Date.now(), elapsed: progress?.elapsed_ms ?? 0 }); setNow(Date.now()); }, [progress?.version, progress?.elapsed_ms]);
  const running = uploading || progress?.state === "running";
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, [running]);

  const elapsed = progress?.state === "running" || !progress ? received.elapsed + (now - received.at) : progress.elapsed_ms;
  const steps = progress?.steps ?? [];
  const reading = steps.find(step => step.kind === "read" && step.status === "running") ?? steps.find(step => step.kind === "prepare" && step.status === "running");
  const current = steps.find(step => step.status === "running") ?? steps.find(step => step.status === "failed");
  const previewPanel = reading?.panel ?? current?.panel ?? photos[0]?.panel;
  const preview = photos.find(photo => photo.panel === previewPanel) ?? photos[0];
  const scanning = Boolean(reading) && !error;
  const percent = progress ? progress.percent : 0;

  const label = (step: ProgressStep) => {
    switch (step.kind) {
      case "prepare": return fill(t("progPrepare"), { panel: step.panel ? t(PANEL_LABEL[step.panel]).toLowerCase() : "" });
      case "read":
        // One step per photo: the languages run in parallel, so they share one line and one time.
        if (step.languages?.length) return fill(t("progReadMerged"), { languages: languageBadge(step.languages) });
        return fill(t("progRead"), { language: step.language ? languageName(step.language, locale, step.language) : "" });
      case "link": return t("progLink");
      case "rules": return fill(t("progRules"), { count: step.count ?? 14 });
      case "risk": return t("progRisk");
      default: return step.label;
    }
  };
  const statusText = (status: ProgressStep["status"]) => t(status === "done" ? "progStatusDone" : status === "running" ? "progStatusRunning" : status === "failed" ? "progStatusFailed" : "progStatusPending");
  const announcement = error ? (ERROR_KEYS[error.stage ?? ""] ? t(ERROR_KEYS[error.stage ?? ""]) : error.message)
    : uploading && !progress ? t("progUploading") : current ? label(current) : progress?.state === "completed" ? t("progAllDone") : "";
  const failureText = error ? (error.unreachable ? t("progUnreachable") : ERROR_KEYS[error.stage ?? ""] ? t(ERROR_KEYS[error.stage ?? ""]) : error.message || t("progErrUnknown")) : null;

  return (
    <section className={`progress-panel${dark ? " progress-panel--dark" : ""}${reduced ? " is-static" : ""} ${className}`.trim()} aria-label={t("progStepsLabel")}>
      {preview && (
        <div className={`scan-frame${scanning ? " is-scanning" : ""}`}>
          <img className="scan-frame__image" src={preview.url} alt={t("progPreviewAlt")} />
          {scanning && <span className="scan-frame__line" aria-hidden="true" />}
          <span className="scan-frame__panel">{t(PANEL_LABEL[preview.panel])}</span>
        </div>
      )}
      <div className="progress-panel__body">
        <div className="progress-panel__summary">
          <div className="pp-bar" role="progressbar" aria-label={t("progOverall")} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
            <span className="pp-bar__fill" style={{ width: `${percent}%` }} />
          </div>
          <p className="progress-panel__numbers">
            <span className="tabular">{percent}%</span>
            <span className="mono tabular" aria-label={fill(t("progElapsed"), { time: formatElapsed(elapsed) })}>{formatElapsed(elapsed)}</span>
          </p>
        </div>
        <p className="sr-only" role="status" aria-live="polite">{announcement}</p>
        <ol className="step-list">
          {uploading && !progress && (
            <li className="step-list__item is-running"><StepIcon status="running" /><span className="step-list__label">{t("progUploading")}</span></li>
          )}
          {steps.map(step => (
            <li key={step.id} className={`step-list__item is-${step.status}`} data-step={step.id} data-status={step.status}>
              <StepIcon status={step.status} />
              <span className="step-list__label">{label(step)}{step.kind === "read" && step.panel && <span className="step-list__panel"> · {t(PANEL_LABEL[step.panel])}</span>}<span className="sr-only"> — {statusText(step.status)}</span></span>
              {step.status === "done" && <span className="step-list__time mono tabular">{step.cached ? t("progCached") : step.duration_ms !== null && step.duration_ms !== undefined ? `${(step.duration_ms / 1000).toFixed(1)} s` : ""}</span>}
            </li>
          ))}
        </ol>
        {failureText && (
          <div className="progress-panel__error" role="alert">
            <CircleAlert size={18} aria-hidden="true" />
            <p>{failureText}</p>
            {onRetry && <Button size="sm" variant="outline" icon={<RefreshCw size={16} aria-hidden="true" />} onClick={onRetry}>{retryLabel ?? t("tryAgain")}</Button>}
          </div>
        )}
        {progress?.state === "completed" && <p className="progress-panel__done"><Check size={16} aria-hidden="true" />{t("progAllDone")}</p>}
      </div>
    </section>
  );
}
