import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { api, ApiError, getJson, isExpired } from "../../api/client";
import { navigate } from "../../app/router";
import { Alert, Button } from "../../components/ui";
import { PreviewPhoto, ProgressPanel } from "../../components/ProgressPanel";
import { useI18n } from "../../i18n";
import { CAPTURE_PANELS } from "../../lib/panels";
import { CapturedPhoto, releasePhoto, useInspectionRun } from "../../lib/useInspectionRun";
import { summaryPhotos } from "../../lib/panels";
import { usePhotoUrls } from "../../lib/usePhotoUrls";
import type { Panel, Summary } from "../../types";
import { CorrectionPayload } from "../record/Findings";
import { CaptureScreen, StepSpec } from "./CaptureScreen";
import { ResultScreen } from "./ResultScreen";

type Screen = "capture" | "processing" | "result";
type Props = {
  official: boolean;
  onExpired: () => void;
  onCompleted: () => void;
  /** Add (or retake) one photo of an existing inspection instead of starting a new one. */
  add?: { inspectionId: string; panel: Panel };
  forceFallback?: boolean;
};

const FLOW: StepSpec[] = CAPTURE_PANELS.map(panel => ({ panel, required: panel === "BACK" }));

/** #/capture: the field-app flow. Capture back, front and date stamp, watch the label being read, then act on the result. */
export function CapturePage({ official, onExpired, onCompleted, add, forceFallback }: Props) {
  const { t } = useI18n();
  const [screen, setScreen] = useState<Screen>("capture");
  const [photos, setPhotos] = useState<Partial<Record<Panel, CapturedPhoto>>>({});
  const [skipped, setSkipped] = useState<Set<Panel>>(new Set());
  const [adding, setAdding] = useState<{ inspectionId: string; panel: Panel } | null>(add ?? null);
  const [current, setCurrent] = useState<Panel>(add?.panel ?? "BACK");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const run = useInspectionRun({ onExpired });
  const photosRef = useRef(photos);
  photosRef.current = photos;

  const steps: StepSpec[] = adding ? [{ panel: adding.panel, required: true }] : FLOW;
  const inspectionId = adding?.inspectionId ?? run.inspectionId;
  const path = inspectionId ? `/inspections/${encodeURIComponent(inspectionId)}` : "";
  const activeImageIds = useMemo(() => (summary ? summaryPhotos(summary) : []).filter(image => image.active).map(image => image.id), [summary]);
  const photoUrls = usePhotoUrls(path, screen === "result" ? activeImageIds : []);

  // Release in-memory photos when the flow is left.
  useEffect(() => () => { Object.values(photosRef.current).forEach(photo => photo && releasePhoto(photo)); }, []);

  // Each screen change moves focus to its heading, so keyboard and screen-reader users land in the right place.
  useEffect(() => {
    const id = screen === "capture" ? "capture-heading" : screen === "processing" ? "processing-heading" : null;
    if (id) document.getElementById(id)?.focus({ preventScroll: true });
  }, [screen]);

  const loadSummary = useCallback(async (id: string) => {
    try {
      const result: Summary = await getJson(`/inspections/${encodeURIComponent(id)}`, "inspection");
      setSummary(result); setLoadError(null); setScreen("result");
    } catch (error) {
      if (isExpired(error)) return onExpired();
      setLoadError((error as ApiError).detail ?? t("historyLoadError"));
    }
  }, [onExpired, t]);

  useEffect(() => {
    if (run.phase === "done" && run.inspectionId) { onCompleted(); void loadSummary(run.inspectionId); }
  }, [run.phase, run.inspectionId]); // eslint-disable-line react-hooks/exhaustive-deps

  function start(next: Partial<Record<Panel, CapturedPhoto>>) {
    const list = CAPTURE_PANELS.map(panel => next[panel]).filter((photo): photo is CapturedPhoto => Boolean(photo));
    setScreen("processing");
    void run.run(adding ? list.filter(photo => photo.panel === adding.panel) : list, adding?.inspectionId);
  }
  function advance(next: Partial<Record<Panel, CapturedPhoto>>, skippedNow: Set<Panel>) {
    if (adding) return start(next);
    const index = FLOW.findIndex(step => step.panel === current);
    if (index < FLOW.length - 1) return setCurrent(FLOW[index + 1].panel);
    if (next.BACK) start(next); else setCurrent("BACK");
    void skippedNow;
  }
  function use(photo: CapturedPhoto) {
    const replaced = photos[photo.panel];
    if (replaced) releasePhoto(replaced);
    const next = { ...photos, [photo.panel]: photo };
    setPhotos(next);
    const nextSkipped = new Set(skipped); nextSkipped.delete(photo.panel); setSkipped(nextSkipped);
    advance(next, nextSkipped);
  }
  function skip() {
    const nextSkipped = new Set(skipped).add(current);
    setSkipped(nextSkipped);
    advance(photos, nextSkipped);
  }
  function close() { navigate(adding ? `/inspections/${adding.inspectionId}` : "/overview"); }

  async function correct(payload: CorrectionPayload): Promise<string | null> {
    if (!inspectionId) return null;
    try {
      const result: Summary = await (await api(`${path}/corrections`, "correction", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }, 60_000)).json();
      setSummary(result); onCompleted();
      return null;
    } catch (error) {
      if (isExpired(error)) { onExpired(); return null; }
      const detail = (error as ApiError).detail ?? "";
      return (error as ApiError).status === 422 ? detail.replace(/^\d+:\s*/, "") : t("recCorrectionError");
    }
  }
  /** From the result: back to the camera for one panel, on this same inspection. */
  function capture(panel: Panel) {
    if (!inspectionId) return;
    run.reset();
    setAdding({ inspectionId, panel }); setCurrent(panel); setScreen("capture");
  }

  const preview: PreviewPhoto[] = Object.values(photos).filter((photo): photo is CapturedPhoto => Boolean(photo)).map(photo => ({ panel: photo.panel, url: photo.url }));

  if (screen === "result" && summary) {
    return <div className="capture-root"><ResultScreen summary={summary} official={official} photoUrls={photoUrls} onSave={() => navigate(`/inspections/${summary.id}`)} onClose={close} onCapture={capture} onCorrect={correct} /></div>;
  }
  if (screen === "processing") {
    return (
      <div className="capture-root">
        <div className="cap-processing">
          <h1 id="processing-heading" tabIndex={-1} className="cap-processing__title">{t("capProcessing")}</h1>
          {run.error?.unreachable && (
            <Alert action={<Button size="sm" variant="outline" icon={<RefreshCw size={16} aria-hidden="true" />} onClick={() => void run.retry()}>{t("progRetryUpload")}</Button>}>{t("progUnreachable")}</Alert>
          )}
          {loadError && <Alert action={<Button size="sm" variant="outline" onClick={() => run.inspectionId && void loadSummary(run.inspectionId)}>{t("tryAgain")}</Button>}>{loadError}</Alert>}
          <ProgressPanel dark progress={run.progress} uploading={run.phase === "uploading"} photos={preview}
            error={run.error && !run.error.unreachable ? run.error : null} onRetry={() => void run.retry()} />
          {run.phase === "failed" && <Button variant="ghost" className="cap-btn" onClick={() => setScreen("capture")}>{t("capBackToCamera")}</Button>}
        </div>
      </div>
    );
  }
  return (
    <div className="capture-root">
      <CaptureScreen steps={steps} current={current} photos={photos} skipped={skipped} title={adding ? t("capAddTitle") : t("newInspection")}
        onStep={setCurrent} onUse={use} onSkip={skip} onClose={close} forceFallback={forceFallback} />
    </div>
  );
}
