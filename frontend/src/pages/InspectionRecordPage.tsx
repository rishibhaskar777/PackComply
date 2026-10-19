import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, m } from "motion/react";
import { BadgeCheck, Camera, FileDown, RefreshCw, SearchX, ShieldCheck } from "lucide-react";
import { api, ApiError, downloadReport, getJson, isExpired, isUnavailable } from "../api/client";
import { navigate } from "../app/router";
import { Alert, Badge, Button, Card, CountUp, Dialog, EmptyState, Input, RiskBar, Skeleton, Tabs, useToast } from "../components/ui";
import { PANEL_LABEL, ProgressPanel } from "../components/ProgressPanel";
import { fill, TranslationKey, useI18n } from "../i18n";
import { buildFindingItems, FindingItem } from "../lib/evidence";
import { DURATION, EASE, useMediaQuery, useReducedMotion } from "../lib/motion";
import { activePhotos, summaryPhotos } from "../lib/panels";
import { newPhoto, useInspectionRun } from "../lib/useInspectionRun";
import { usePhotoUrls } from "../lib/usePhotoUrls";
import { AuditEvent, FieldCorrection, Panel, Progress, Summary } from "../types";
import { AddPhotoDialog } from "./record/AddPhotoDialog";
import { AuditTrail, formatDateTime } from "./record/AuditTrail";
import { EvidencePhoto } from "./record/EvidencePhoto";
import { CorrectionPayload, FIELD_LABELS, Findings } from "./record/Findings";

const VERDICT_LABEL: Record<string, TranslationKey> = { COMPLIANT: "verdictCompliant", NON_COMPLIANT: "verdictNonCompliant", NEEDS_REVIEW: "verdictNeedsReview" };
const RISK_LABEL: Record<string, TranslationKey> = { LOW: "low", MEDIUM: "medium", HIGH: "high", CRITICAL: "critical" };
type Failure = { status?: number; message: string; unavailable: boolean };

type Props = { inspectionId: string; official: boolean; /** Shown in the official record header; the record belongs to the signed-in inspector. */ inspectorName?: string; onExpired: () => void; onChanged?: () => void; onTitle?: (title: string | null) => void };

/** The inspection record: evidence photo with markers, findings with inspector corrections, verification and the audit trail. */
export function InspectionRecordPage({ inspectionId, official, inspectorName, onExpired, onChanged, onTitle }: Props) {
  const { t, locale } = useI18n();
  const toast = useToast();
  const reduced = useReducedMotion();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [audit, setAudit] = useState<AuditEvent[] | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [reportBusy, setReportBusy] = useState(false);
  const [tab, setTab] = useState<string | null>(null);
  const [photoDialog, setPhotoDialog] = useState<{ panel: Panel; mode: "add" | "retake" } | null>(null);
  const [uploadLimit, setUploadLimit] = useState(10 * 1024 * 1024);
  const [preview, setPreview] = useState<{ panel: Panel; url: string }[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [remarks, setRemarks] = useState("");
  const [confirming, setConfirming] = useState(false);
  const isPhone = useMediaQuery("(max-width: 639px)");
  const path = `/inspections/${encodeURIComponent(inspectionId)}`;

  const fail = useCallback((error: unknown, fallback: string): Failure | null => {
    if (isExpired(error)) { onExpired(); return null; }
    const e = error as ApiError;
    return { status: e.status, message: e.detail ?? fallback, unavailable: isUnavailable(error) };
  }, [onExpired]);

  const loadAudit = useCallback(async () => {
    try { setAudit(await (await api(`${path}/audit`, "audit")).json()); } catch { /* the trail is supplementary; the record still works */ }
  }, [path]);

  const load = useCallback(async () => {
    setLoading(true); setFailure(null);
    try {
      const result: Summary = await (await api(path, "inspection")).json();
      setSummary(result); setRemarks(result.review?.remarks ?? "");
      void loadAudit();
    } catch (error) { setFailure(fail(error, t("historyLoadError"))); setSummary(null); }
    finally { setLoading(false); }
  }, [path, loadAudit, fail, t]);

  const run = useInspectionRun({ onExpired });
  const running = run.phase === "uploading" || run.phase === "processing";
  useEffect(() => { setSelectedId(null); setHoveredId(null); setTab(null); void load(); }, [inspectionId]);
  useEffect(() => { getJson<{ max_upload_bytes: number }>("/inspections/config", "config").then(config => setUploadLimit(config.max_upload_bytes)).catch(() => undefined); }, []);
  useEffect(() => { onTitle?.(summary?.inspection_number ?? null); return () => onTitle?.(null); }, [summary?.inspection_number]);

  // Every active photo is fetched with the session cookie and shown from an object URL.
  const photos = useMemo(() => (summary ? summaryPhotos(summary) : []), [summary]);
  const tabs = useMemo(() => activePhotos(photos), [photos]);
  const photoUrls = usePhotoUrls(path, tabs.map(photo => photo.id));
  const currentPhoto = tabs.find(photo => photo.id === tab) ?? tabs.find(photo => photo.id === summary?.image?.id) ?? tabs[0] ?? null;

  // Photos whose text could not be read: the record is complete without them, with the affected rules in Needs review.
  const unreadPhotos = useMemo(() => (summary?.images ?? []).filter(photo => photo.active && photo.read_error), [summary]);

  const items: FindingItem[] = useMemo(() => (summary ? buildFindingItems(summary) : []), [summary]);
  // Markers only for findings whose evidence is on the photo being shown; their numbers stay in the list.
  const photoItems = useMemo(() => items.map(item => (item.boxes && item.imageId && item.imageId !== currentPhoto?.id ? { ...item, boxes: null } : item)), [items, currentPhoto?.id]);
  useEffect(() => () => { preview.forEach(item => URL.revokeObjectURL(item.url)); }, [preview]);
  const verdict = summary?.compliance?.verdict ?? null;
  const verdictText = (value: string | null | undefined) => (value && VERDICT_LABEL[value] ? t(VERDICT_LABEL[value]) : "");

  async function send(request: string, operation: string, init: RequestInit = {}): Promise<Summary | null> {
    setBusy(true);
    try {
      const result: Summary = await (await api(request, operation, { ...init, headers: { "Content-Type": "application/json", ...init.headers } }, 60_000)).json();
      setSummary(result); void loadAudit(); onChanged?.();
      return result;
    } finally { setBusy(false); }
  }

  /** Returns an error message to show in the form, or null when saved. */
  async function correct(payload: CorrectionPayload): Promise<string | null> {
    try {
      const result = await send(`${path}/corrections`, "correction", { method: "POST", body: JSON.stringify(payload) });
      if (result) {
        const value = result.extraction?.fields[payload.field_name]?.value ?? payload.corrected_value;
        toast(fill(t("recToastCorrected"), { field: t(FIELD_LABELS[payload.field_name] ?? "productInformation"), value, verdict: verdictText(result.compliance?.verdict) }));
      }
      return null;
    } catch (error) {
      if (isExpired(error)) { onExpired(); return null; }
      const detail = (error as ApiError).detail ?? "";
      return (error as ApiError).status === 422 ? detail.replace(/^\d+:\s*/, "") : t("recCorrectionError");
    }
  }
  async function undo(correction: FieldCorrection) {
    try {
      const result = await send(`${path}/corrections/${correction.id}`, "undo", { method: "DELETE" });
      if (result) toast(fill(t("recToastUndone"), { field: t(FIELD_LABELS[correction.field_name] ?? "productInformation"), verdict: verdictText(result.compliance?.verdict) }));
    } catch (error) { if (isExpired(error)) onExpired(); else toast(t("recCorrectionError"), "error"); }
  }
  async function verify() {
    setConfirming(false);
    try {
      const result = await send(`${path}/verify`, "verify", { method: "POST", body: JSON.stringify({ remarks: remarks.trim() || null }) });
      if (result) toast(t("recToastVerified"));
    } catch (error) { if (isExpired(error)) onExpired(); else toast(t("recVerifyError"), "error"); }
  }
  async function reanalyse() {
    try {
      const result = await send(`${path}/reanalyse`, "reanalyse", { method: "POST" });
      if (result) toast(t("recToastReanalysed"));
    } catch (error) { if (isExpired(error)) onExpired(); else toast(t("recReanalyseError"), "error"); }
  }
  /** Resume processing (finished photos are not read again) and refresh the record when it ends. */
  async function settle(final: Progress | null) {
    if (final?.state === "completed") { await load(); onChanged?.(); toast(t("recToastPhotos")); run.reset(); }
  }
  async function finishRun(photos: Parameters<typeof run.run>[0]) {
    setPreview(photos.map(photo => ({ panel: photo.panel, url: photo.url })));
    await settle(await run.run(photos, inspectionId));
  }
  async function retryProcessing() { await finishRun([]); }
  /** Add or retake a photo: the capture flow on a phone, a dialog on larger screens. */
  function addPhoto(panel: Panel, action: "add" | "retake") {
    if (isPhone) navigate(`/capture?add=${encodeURIComponent(inspectionId)}&panel=${panel}`);
    else setPhotoDialog({ panel, mode: action });
  }
  async function submitPhoto(panel: Panel, file: File) {
    setPhotoDialog(null);
    await finishRun([newPhoto(panel, file, file.name)]);
  }
  function select(id: string | null) {
    setSelectedId(id);
    // Evidence on another photo brings that photo's tab forward.
    const item = id ? items.find(entry => entry.finding.rule_id === id) : undefined;
    if (item?.imageId && item.imageId !== currentPhoto?.id && tabs.some(photo => photo.id === item.imageId)) setTab(item.imageId);
  }
  async function exportPdf() {
    if (!summary || reportBusy) return;
    setReportBusy(true);
    try { await downloadReport(summary.id, summary.inspection_number); toast(t("toastReportDownloaded")); }
    catch (error) { if (isExpired(error)) onExpired(); else toast(t("reportGenerationError"), "error"); }
    finally { setReportBusy(false); }
  }

  if (loading && !summary) return <RecordSkeleton label={t("loading")} />;
  if (failure?.status === 404) {
    return <div className="page"><EmptyState title={t("recNotFoundTitle")} description={t("recNotFoundBody")} icon={<SearchX size={16} />} action={<Button variant="outline" onClick={() => navigate("/inspections")}>{t("recBackToList")}</Button>} /></div>;
  }
  if (failure || !summary) {
    return (
      <div className="page">
        <Alert title={failure?.unavailable ? t("recUnavailableTitle") : t("error")} action={<Button size="sm" variant="outline" icon={<RefreshCw size={16} aria-hidden="true" />} onClick={() => void load()}>{t("tryAgain")}</Button>}>
          {failure?.unavailable ? t("recUnavailableBody") : failure?.message}
        </Alert>
      </div>
    );
  }

  const compliance = summary.compliance;
  const completed = summary.status === "COMPLETED";
  const fields = summary.extraction?.fields ?? {};
  const openItems = items.filter(item => item.group === "violations" || item.group === "review");
  const verified = summary.review?.status === "VERIFIED";
  const canEdit = official && completed;
  const maker = summary.maker_name ?? summary.brand_name;
  const when = formatDateTime(summary.completed_at ?? summary.created_at, locale);
  // The official record leads with who/when/which rules; the self-check view keeps the product-first line.
  const meta = (official
    ? [summary.inspection_number, inspectorName ? fill(t("recInspectorMeta"), { name: inspectorName }) : null, when, compliance?.ruleset_label, fields.net_quantity?.value, maker]
    : [fields.net_quantity?.value, maker, when, compliance?.ruleset_label]).filter(Boolean) as string[];
  const counts = { violations: compliance?.summary.failed ?? 0, review: compliance?.summary.review ?? 0, passed: compliance?.summary.passed ?? 0 };
  const risk = summary.risk;
  const fade = reduced ? { initial: false as const } : { initial: { opacity: 0, y: 4 }, animate: { opacity: 1, y: 0 }, exit: { opacity: 0, y: -4 }, transition: { duration: DURATION.base, ease: EASE } };

  return (
    <div className="page record-page" aria-busy={busy || running || undefined}>
      <header className="record-header">
        <div className="record-header__text">
          {official && <p className="eyebrow record-eyebrow">{t("officialRecord")}</p>}
          <div className="record-header__title-row">
            <h1 className="record-title">{summary.product_name ?? t("recUntitled")}</h1>
            <AnimatePresence mode="wait" initial={false}>
              {verdict && <m.span key={verdict} className="record-verdict" {...fade}><Badge variant={verdict} /></m.span>}
            </AnimatePresence>
            {verified && <span className="verified-pill"><BadgeCheck size={14} aria-hidden="true" />{fill(t("recVerifiedBy"), { name: summary.review?.by ?? "", date: formatDateTime(summary.review?.at, locale) })}</span>}
          </div>
          <p className="record-meta">
            {official ? null : <span className="record-meta__kind">{t("recSelfCheck")}</span>}
            {meta.map((part, index) => <span key={index} className={index === 0 && (official || fields.net_quantity?.value) ? "mono" : undefined}>{part}</span>)}
          </p>
        </div>
        <div className="record-header__side">
          <div className="page-header__actions record-actions">
            {completed && <Button variant="outline" icon={<Camera size={16} aria-hidden="true" />} onClick={() => addPhoto(photos.some(photo => photo.active && photo.panel === "FRONT") ? (photos.some(photo => photo.active && photo.panel === "DATE_STAMP") ? "OTHER" : "DATE_STAMP") : "FRONT", "add")} disabled={busy || running}>{t("recAddPhoto")}</Button>}
            <Button variant="outline" icon={<RefreshCw size={16} aria-hidden="true" />} onClick={() => void reanalyse()} disabled={busy || running || !summary.ocr}>{t("recReanalyse")}</Button>
            <Button icon={<FileDown size={16} aria-hidden="true" />} onClick={() => void exportPdf()} loading={reportBusy}>{reportBusy ? t("generatingPdf") : t("recExportPdf")}</Button>
          </div>
          {compliance && (
            <dl className="stat-strip">
              <div className="stat-strip__item stat-strip__item--violation"><dt>{t("recStatViolations")}</dt><dd className="tabular"><CountUp value={counts.violations} /></dd></div>
              <div className="stat-strip__item stat-strip__item--review"><dt>{t("recStatToVerify")}</dt><dd className="tabular"><CountUp value={counts.review} /></dd></div>
              <div className="stat-strip__item stat-strip__item--pass"><dt>{t("recStatPassed")}</dt><dd className="tabular"><CountUp value={counts.passed} /></dd></div>
              {risk && <div className="stat-strip__item stat-strip__item--risk"><dt>{t("risk")}</dt><dd><span className="tabular"><CountUp value={risk.risk_score} /></span> · {RISK_LABEL[risk.risk_level] ? t(RISK_LABEL[risk.risk_level]) : risk.risk_level}<RiskBar score={risk.risk_score} /></dd></div>}
            </dl>
          )}
        </div>
      </header>

      {!completed && (
        <Alert variant={summary.status === "FAILED" ? "error" : "info"} title={summary.status === "FAILED" ? t("failed") : t("recNotProcessed")}
          action={summary.image ? <Button size="sm" variant="outline" icon={<RefreshCw size={16} aria-hidden="true" />} onClick={() => void retryProcessing()} loading={busy || running}>{t("tryAgain")}</Button> : undefined}>
          {summary.failure_message ?? t("recNotProcessedBody")}
        </Alert>
      )}

      {unreadPhotos.length > 0 && run.phase === "idle" && (
        <Alert variant="info" title={t("recPhotoUnreadTitle")}
          action={<Button size="sm" variant="outline" icon={<RefreshCw size={16} aria-hidden="true" />} onClick={() => void retryProcessing()} loading={busy || running}>{t("progRetryPhoto")}</Button>}>
          {fill(t("recPhotoUnreadBody"), { panels: unreadPhotos.map(photo => t(PANEL_LABEL[photo.panel]).toLowerCase()).join(", ") })}
        </Alert>
      )}

      {run.phase !== "idle" && (
        <Card className="photo-add-progress" headingLevel={2} title={t("recPhotoProgress")}
          action={run.phase === "failed" ? <Button size="sm" variant="ghost" onClick={() => { run.reset(); }}>{t("recDismiss")}</Button> : undefined}>
          <ProgressPanel progress={run.progress} uploading={run.phase === "uploading"} photos={preview} error={run.error} onRetry={() => void run.retry().then(settle)} />
        </Card>
      )}

      <div className="record-grid">
        <Card className="record-photo" headingLevel={2} title={t("recPhotos")}
          action={<span className="mono record-photo__caption">{summary.ocr ? `${summary.ocr.languages.join(" + ")}${summary.ocr.duration_ms ? ` · ${Math.max(1, Math.round(summary.ocr.duration_ms / 1000))} s` : ""}` : ""}</span>}>
          {tabs.length > 0 && (
            <Tabs<string> label={t("recPhotos")} value={currentPhoto?.id ?? ""} onChange={setTab} items={tabs.map(photo => ({
              value: photo.id,
              label: <>{photoUrls[photo.id] && <img className="photo-tab__thumb" src={photoUrls[photo.id]} alt="" />}{t(PANEL_LABEL[photo.panel])}</>,
            }))} />
          )}
          {currentPhoto
            ? <EvidencePhoto key={currentPhoto.id} src={photoUrls[currentPhoto.id] ?? null} image={currentPhoto} ocr={currentPhoto.id === summary.image?.id ? summary.ocr : undefined} items={photoItems} selectedId={selectedId} hoveredId={hoveredId} onSelect={select} onHover={setHoveredId} />
            : <p className="muted-text">{t("noImage")}</p>}
        </Card>

        <Card className="record-findings" headingLevel={2} title={fill(t("recFindingsTitle"), { count: compliance?.findings.length ?? 0 })}
          description={canEdit ? t("recFindingsHint") : official ? undefined : t("recSelfCheckHint")}>
          {compliance
            ? <Findings summary={summary} items={items} selectedId={selectedId} hoveredId={hoveredId} onSelect={select} onHover={setHoveredId} canEdit={canEdit && !running} busy={busy || running} onCorrect={correct} onUndo={undo} onPhoto={completed && !running ? addPhoto : undefined} />
            : <p className="muted-text">{t("noResults")}</p>}
          {official && completed && (
            <footer className="review-footer">
              {verified && (
                <div className="review-footer__summary" role="status">
                  <ShieldCheck size={16} aria-hidden="true" />
                  <p><strong>{fill(t("recVerifiedBy"), { name: summary.review?.by ?? "", date: formatDateTime(summary.review?.at, locale) })}</strong>{summary.review?.remarks && <span>“{summary.review.remarks}”</span>}</p>
                </div>
              )}
              <label className="review-footer__label" htmlFor="inspector-remarks">{t("recRemarksLabel")}</label>
              <div className="review-footer__row">
                <Input id="inspector-remarks" value={remarks} onChange={event => setRemarks(event.target.value)} maxLength={2000} placeholder={t("recRemarksPlaceholder")} autoComplete="off" />
                <Button className="review-footer__verify" icon={<BadgeCheck size={16} aria-hidden="true" />} loading={busy} disabled={busy} onClick={() => (openItems.length ? setConfirming(true) : void verify())}>{verified ? t("recVerifyAgain") : t("recMarkVerified")}</Button>
              </div>
            </footer>
          )}
        </Card>
      </div>

      <AuditTrail events={audit} loading={loading} />

      {canEdit && isPhone && (
        <div className="verify-bar">
          <Button className="verify-bar__button" icon={<BadgeCheck size={16} aria-hidden="true" />} disabled={busy} onClick={() => (openItems.length ? setConfirming(true) : void verify())}>{verified ? t("recVerifyAgain") : t("recMarkVerified")}</Button>
        </div>
      )}

      <AddPhotoDialog open={photoDialog !== null} mode={photoDialog?.mode ?? "add"} initialPanel={photoDialog?.panel ?? "FRONT"} photos={photos} limit={uploadLimit}
        onClose={() => setPhotoDialog(null)} onSubmit={(panel, file) => void submitPhoto(panel, file)} />

      <Dialog open={confirming} onClose={() => setConfirming(false)} title={t("recConfirmTitle")} description={fill(t("recConfirmBody"), { count: openItems.length })}
        footer={<><Button variant="ghost" onClick={() => setConfirming(false)}>{t("recCancel")}</Button><Button data-autofocus icon={<BadgeCheck size={16} aria-hidden="true" />} onClick={() => void verify()}>{t("recVerifyAnyway")}</Button></>}>
        <ul className="confirm-list">
          {openItems.map(item => <li key={item.finding.rule_id}><Badge variant={item.finding.status} dot={false} /> <span>{item.finding.rule_name}</span></li>)}
        </ul>
      </Dialog>
    </div>
  );
}

function RecordSkeleton({ label }: { label: string }) {
  return (
    <div className="page record-page" role="status" aria-live="polite">
      <span className="sr-only">{label}</span>
      <div className="record-header"><div className="record-header__text"><Skeleton width={280} height={30} /><Skeleton width={360} height={14} className="record-skeleton__line" /></div><Skeleton width={320} height={56} /></div>
      <div className="record-grid">
        <Card><Skeleton width="100%" height={420} /></Card>
        <Card>{[0, 1, 2, 3].map(index => <Skeleton key={index} width="100%" height={64} className="record-skeleton__row" />)}</Card>
      </div>
    </div>
  );
}
