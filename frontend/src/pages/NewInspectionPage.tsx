import { ChangeEvent, DragEvent, useEffect, useRef, useState } from "react";
import { ImageUp, RefreshCw, X } from "lucide-react";
import { getJson } from "../api/client";
import { navigate } from "../app/router";
import { Alert, Button, Card, useToast } from "../components/ui";
import { PageHeader } from "../components/PageHeader";
import { PANEL_LABEL, ProgressPanel } from "../components/ProgressPanel";
import { fill, useI18n } from "../i18n";
import { CAPTURE_PANELS } from "../lib/panels";
import { CapturedPhoto, newPhoto, releasePhoto, useInspectionRun } from "../lib/useInspectionRun";
import type { Panel } from "../types";

const ACCEPTED = ["image/png", "image/jpeg"];
const FALLBACK_LIMIT = 10 * 1024 * 1024;

export const formatMegabytes = (bytes: number) => `${Math.round((bytes / (1024 * 1024)) * 10) / 10} MB`;
const formatSize = (bytes: number) => (bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : formatMegabytes(bytes));

type Props = { onExpired: () => void; onCompleted: () => void };
type Slots = Partial<Record<Panel, CapturedPhoto>>;

/** #/new: add the back panel (required), and optionally the front and date stamp; they are uploaded, read and checked together. */
export function NewInspectionPage({ onExpired, onCompleted }: Props) {
  const { t } = useI18n();
  const toast = useToast();
  const [slots, setSlots] = useState<Slots>({});
  const [limit, setLimit] = useState(FALLBACK_LIMIT);
  const [dragging, setDragging] = useState<Panel | null>(null);
  const [problem, setProblem] = useState<{ panel: Panel; message: string } | null>(null);
  const slotsRef = useRef(slots);
  slotsRef.current = slots;
  const run = useInspectionRun({ onExpired });
  const busy = run.phase === "uploading" || run.phase === "processing";

  useEffect(() => {
    getJson<{ max_upload_bytes: number }>("/inspections/config", "config").then(config => setLimit(config.max_upload_bytes)).catch(() => undefined);
  }, []);
  useEffect(() => () => { Object.values(slotsRef.current).forEach(photo => photo && releasePhoto(photo)); }, []);

  // A finished run opens the record.
  useEffect(() => {
    if (run.phase === "done" && run.inspectionId) { onCompleted(); toast(t("toastInspectionSaved")); navigate(`/inspections/${run.inspectionId}`); }
  }, [run.phase, run.inspectionId]); // eslint-disable-line react-hooks/exhaustive-deps

  function choose(panel: Panel, candidate: File | undefined | null) {
    if (!candidate || busy) return;
    if (!ACCEPTED.includes(candidate.type)) { setProblem({ panel, message: t("newBadType") }); return; }
    if (candidate.size > limit) { setProblem({ panel, message: fill(t("newTooLarge"), { limit: formatMegabytes(limit) }) }); return; }
    setProblem(null);
    const photo = newPhoto(panel, candidate, candidate.name);
    const before = slotsRef.current;
    if (before[panel]) releasePhoto(before[panel]!);
    const next = { ...before, [panel]: photo };
    setSlots(next);
    // The photo goes up, and starts being read, as soon as it is chosen. After a finished or failed run a
    // different set of photos is a different inspection, so everything is staged again.
    if (run.phase !== "idle") { run.reset(); restage(next, photo); } else run.stage(photo);
  }
  function restage(all: Slots, ...first: CapturedPhoto[]) {
    const rest = CAPTURE_PANELS.map(item => all[item]).filter((item): item is CapturedPhoto => Boolean(item) && !first.includes(item as CapturedPhoto));
    [...rest, ...first].forEach(item => run.stage(item));
  }
  function clear(panel: Panel) {
    // A removed photo may already be on the server: start a fresh inspection with the ones that remain.
    const { [panel]: removed, ...rest } = slotsRef.current;
    if (removed) releasePhoto(removed);
    setSlots(rest);
    run.reset();
    restage(rest);
  }
  function onDrop(event: DragEvent, panel: Panel) { event.preventDefault(); setDragging(null); choose(panel, event.dataTransfer.files?.[0]); }

  const photos = CAPTURE_PANELS.map(panel => slots[panel]).filter((photo): photo is CapturedPhoto => Boolean(photo));
  const started = run.phase !== "idle";
  function start() { if (slots.BACK && !busy) void run.run(photos); }

  return (
    <div className="page new-page">
      <PageHeader title={t("newInspection")} subtitle={t("legalNotice")} />
      {run.error?.unreachable && <Alert action={<Button size="sm" variant="outline" icon={<RefreshCw size={16} aria-hidden="true" />} onClick={() => void run.retry()} disabled={busy}>{t("progRetryUpload")}</Button>}>{t("progUnreachable")}</Alert>}
      <Card title={t("newPhotosTitle")} description={fill(t("newDropHint"), { limit: formatMegabytes(limit) })}>
        <div className="new-slots">
          {CAPTURE_PANELS.map(panel => {
            const photo = slots[panel];
            const required = panel === "BACK";
            return (
              <div key={panel} className="new-slot">
                <p className="new-slot__label"><strong>{t(PANEL_LABEL[panel])}</strong> <span className="muted-text">{required ? t("capRequired") : t("capOptional")}</span></p>
                <label className={`dropzone dropzone--slot${dragging === panel ? " is-dragging" : ""}${busy ? " is-busy" : ""}${photo ? " has-preview" : ""}`}
                  onDragOver={event => { event.preventDefault(); if (!busy) setDragging(panel); }} onDragLeave={() => setDragging(null)} onDrop={event => onDrop(event, panel)}>
                  <input className="sr-only" type="file" accept="image/png,image/jpeg" disabled={busy} aria-label={fill(t("newChoosePanel"), { panel: t(PANEL_LABEL[panel]) })}
                    onChange={(event: ChangeEvent<HTMLInputElement>) => { choose(panel, event.target.files?.[0]); event.target.value = ""; }} />
                  {photo
                    ? <img className="dropzone__preview" src={photo.url} alt={fill(t("newPreviewAlt"), { panel: t(PANEL_LABEL[panel]) })} />
                    : <>
                      <span className="dropzone__icon" aria-hidden="true"><ImageUp size={20} /></span>
                      <span className="dropzone__label">{t("newDropTitle")}</span>
                      <span className="dropzone__hint">{t("newBrowse")}</span>
                    </>}
                </label>
                {problem?.panel === panel && <p className="form-error" role="alert">{problem.message}</p>}
                {photo && (
                  <div className="new-file">
                    <p className="new-file__name"><span className="mono">{photo.name}</span><span className="muted-text mono">{formatSize(photo.blob.size)}</span></p>
                    {!busy && <Button size="sm" variant="ghost" icon={<X size={14} aria-hidden="true" />} onClick={() => clear(panel)} aria-label={fill(t("newRemovePanel"), { panel: t(PANEL_LABEL[panel]) })}>{t("newRemove")}</Button>}
                  </div>
                )}
              </div>
            );
          })}
        </div>
        {started && (
          <div className="new-progress">
            <ProgressPanel progress={run.progress} uploading={run.phase === "uploading"} photos={photos.map(photo => ({ panel: photo.panel, url: photo.url }))}
              error={run.error && !run.error.unreachable ? run.error : null} onRetry={() => void run.retry()} />
          </div>
        )}
        <div className="page-footer-actions new-actions">
          <Button size="lg" icon={<ImageUp size={16} aria-hidden="true" />} onClick={start} disabled={!slots.BACK || busy} loading={busy}>{t("newStart")}</Button>
          {!slots.BACK && <span className="muted-text">{t("newNeedBack")}</span>}
        </div>
      </Card>
    </div>
  );
}
