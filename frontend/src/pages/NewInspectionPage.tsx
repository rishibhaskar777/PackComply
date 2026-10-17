import { ChangeEvent, DragEvent, useEffect, useRef, useState } from "react";
import { ImageUp, RefreshCw, X } from "lucide-react";
import { api, ApiError, getJson, isExpired } from "../api/client";
import { navigate } from "../app/router";
import { Alert, Button, Card, useToast } from "../components/ui";
import { PageHeader } from "../components/PageHeader";
import { fill, TranslationKey, useI18n } from "../i18n";
import { Summary } from "../types";

const ACCEPTED = ["image/png", "image/jpeg"];
const FALLBACK_LIMIT = 10 * 1024 * 1024;
const POLL_MS = 1500;
const STAGE_KEYS: Record<string, TranslationKey> = { IMAGE_UPLOADED: "imageUploadedStatus", OCR_PROCESSING: "ocrProcessing", OCR_COMPLETED: "ocrCompleted", EXTRACTION_PROCESSING: "extractionProcessing", EXTRACTION_COMPLETED: "extractionCompleted", COMPLIANCE_PROCESSING: "complianceProcessing" };

export const formatMegabytes = (bytes: number) => `${Math.round((bytes / (1024 * 1024)) * 10) / 10} MB`;

type Props = { onExpired: () => void; onCompleted: () => void };

/** #/new: choose or drop a label photo, then upload, read and check it; the finished record opens at #/inspections/:id. */
export function NewInspectionPage({ onExpired, onCompleted }: Props) {
  const { t } = useI18n();
  const toast = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [limit, setLimit] = useState(FALLBACK_LIMIT);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  // Kept across retries so a failed run resumes instead of creating a second inspection.
  const created = useRef<{ id: string; uploaded: boolean } | null>(null);

  useEffect(() => {
    getJson<{ max_upload_bytes: number }>("/inspections/config", "config").then(config => setLimit(config.max_upload_bytes)).catch(() => undefined);
  }, []);
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);

  function choose(candidate: File | undefined | null) {
    if (!candidate) return;
    setError(null); created.current = null;
    if (!ACCEPTED.includes(candidate.type)) { setProblem(t("newBadType")); return; }
    if (candidate.size > limit) { setProblem(fill(t("newTooLarge"), { limit: formatMegabytes(limit) })); return; }
    setProblem(null); setFile(candidate); setPreview(URL.createObjectURL(candidate));
  }
  function clear() { setFile(null); setPreview(null); setProblem(null); setError(null); created.current = null; if (input.current) input.current.value = ""; }
  function onDrop(event: DragEvent) { event.preventDefault(); setDragging(false); if (!busy) choose(event.dataTransfer.files?.[0]); }

  async function start() {
    if (!file || busy) return;
    setBusy(true); setError(null); setStage(null);
    let timer: number | undefined;
    try {
      if (!created.current) created.current = { id: (await (await api("/inspections", "inspection", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })).json()).id, uploaded: false };
      const id = created.current.id;
      if (!created.current.uploaded) {
        const form = new FormData(); form.append("image", file);
        await api(`/inspections/${id}/image`, "upload", { method: "POST", body: form });
        created.current.uploaded = true;
      }
      // /process blocks until the label is read; the persisted status shows how far it has got.
      const poll = async () => {
        try { const current: Summary = await getJson(`/inspections/${id}`, "inspection"); setStage(current.status); } catch { /* the next tick tries again */ }
        timer = window.setTimeout(poll, POLL_MS);
      };
      timer = window.setTimeout(poll, POLL_MS);
      const result: Summary = await (await api(`/inspections/${id}/process`, "process", { method: "POST" }, 300_000)).json();
      if (result.status === "COMPLETED") { onCompleted(); toast(t("toastInspectionSaved")); }
      navigate(`/inspections/${id}`);
    } catch (e) {
      if (isExpired(e)) return onExpired();
      setError((e as ApiError).detail ?? t("newStartError"));
    } finally { window.clearTimeout(timer); setBusy(false); setStage(null); }
  }

  const stageLabel = stage && STAGE_KEYS[stage] ? t(STAGE_KEYS[stage]) : null;
  return (
    <div className="page new-page">
      <PageHeader title={t("newInspection")} subtitle={t("legalNotice")} />
      {error && <Alert action={<Button size="sm" variant="outline" icon={<RefreshCw size={16} aria-hidden="true" />} onClick={() => void start()} disabled={busy}>{t("tryAgain")}</Button>}>{t("error")}: {error}</Alert>}
      <Card title={t("uploadImage")} description={fill(t("newDropHint"), { limit: formatMegabytes(limit) })}>
        <label className={`dropzone dropzone--large${dragging ? " is-dragging" : ""}${busy ? " is-busy" : ""}${preview ? " has-preview" : ""}`}
          onDragOver={event => { event.preventDefault(); if (!busy) setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={onDrop}>
          <input ref={input} className="sr-only" type="file" accept="image/png,image/jpeg" disabled={busy} onChange={(event: ChangeEvent<HTMLInputElement>) => choose(event.target.files?.[0])} />
          {preview
            ? <img className="dropzone__preview" src={preview} alt={t("imagePreview")} />
            : <>
              <span className="dropzone__icon" aria-hidden="true"><ImageUp size={20} /></span>
              <span className="dropzone__label">{t("newDropTitle")}</span>
              <span className="dropzone__hint">{t("newBrowse")}</span>
            </>}
        </label>
        {problem && <p className="form-error" role="alert">{problem}</p>}
        {file && (
          <div className="new-file">
            <p className="new-file__name"><span className="mono">{file.name}</span><span className="muted-text mono">{formatMegabytes(file.size)}</span></p>
            {!busy && <Button size="sm" variant="ghost" icon={<X size={14} aria-hidden="true" />} onClick={clear}>{t("newRemove")}</Button>}
          </div>
        )}
        {busy && (
          <div className="progress" role="status" aria-live="polite">
            <p className="progress__label">{stageLabel ?? t("newReading")}</p>
            <div className="progress-bar progress-bar--indeterminate" aria-hidden="true"><span /></div>
          </div>
        )}
        <div className="page-footer-actions new-actions">
          <Button size="lg" icon={<ImageUp size={16} aria-hidden="true" />} onClick={() => void start()} disabled={!file || busy} loading={busy}>{t("newStart")}</Button>
        </div>
      </Card>
    </div>
  );
}
