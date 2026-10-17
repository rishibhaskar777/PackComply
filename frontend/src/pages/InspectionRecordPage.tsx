import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, m } from "motion/react";
import { BadgeCheck, FileDown, RefreshCw, SearchX, ShieldCheck } from "lucide-react";
import { api, ApiError, downloadReport, isExpired, isUnavailable } from "../api/client";
import { navigate } from "../app/router";
import { Alert, Badge, Button, Card, CountUp, Dialog, EmptyState, Input, RiskBar, Skeleton, Tabs, useToast } from "../components/ui";
import { fill, TranslationKey, useI18n } from "../i18n";
import { buildFindingItems, FindingItem } from "../lib/evidence";
import { DURATION, EASE, useMediaQuery, useReducedMotion } from "../lib/motion";
import { AuditEvent, FieldCorrection, Summary } from "../types";
import { AuditTrail, formatDateTime } from "./record/AuditTrail";
import { EvidencePhoto } from "./record/EvidencePhoto";
import { CorrectionPayload, FIELD_LABELS, Findings } from "./record/Findings";

const VERDICT_LABEL: Record<string, TranslationKey> = { COMPLIANT: "verdictCompliant", NON_COMPLIANT: "verdictNonCompliant", NEEDS_REVIEW: "verdictNeedsReview" };
const RISK_LABEL: Record<string, TranslationKey> = { LOW: "low", MEDIUM: "medium", HIGH: "high", CRITICAL: "critical" };
type PhotoTab = "back" | "front" | "date";
type Failure = { status?: number; message: string; unavailable: boolean };

type Props = { inspectionId: string; official: boolean; onExpired: () => void; onChanged?: () => void; onTitle?: (title: string | null) => void };

/** The inspection record: evidence photo with markers, findings with inspector corrections, verification and the audit trail. */
export function InspectionRecordPage({ inspectionId, official, onExpired, onChanged, onTitle }: Props) {
  const { t, locale } = useI18n();
  const toast = useToast();
  const reduced = useReducedMotion();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [audit, setAudit] = useState<AuditEvent[] | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [reportBusy, setReportBusy] = useState(false);
  const [photo, setPhoto] = useState<string | null>(null);
  const [tab, setTab] = useState<PhotoTab>("back");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [remarks, setRemarks] = useState("");
  const [confirming, setConfirming] = useState(false);
  const photoUrl = useRef<string | null>(null);
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

  useEffect(() => { setSelectedId(null); setHoveredId(null); void load(); }, [inspectionId]);
  useEffect(() => { onTitle?.(summary?.inspection_number ?? null); return () => onTitle?.(null); }, [summary?.inspection_number]);

  // The evidence photo is fetched with the session cookie and shown from an object URL.
  const hasImage = Boolean(summary?.image);
  useEffect(() => {
    if (!hasImage) { setPhoto(null); return; }
    let cancelled = false;
    api(`${path}/image`, "image").then(response => response.blob()).then(blob => {
      if (cancelled) return;
      if (photoUrl.current) URL.revokeObjectURL(photoUrl.current);
      photoUrl.current = URL.createObjectURL(blob); setPhoto(photoUrl.current);
    }).catch(() => { if (!cancelled) setPhoto(null); });
    return () => { cancelled = true; };
  }, [hasImage, path, summary?.image?.id]);
  useEffect(() => () => { if (photoUrl.current) URL.revokeObjectURL(photoUrl.current); }, []);

  const items: FindingItem[] = useMemo(() => (summary ? buildFindingItems(summary) : []), [summary]);
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
  async function retryProcessing() {
    setBusy(true);
    try { setSummary(await (await api(`${path}/process`, "process", { method: "POST" }, 300_000)).json()); void loadAudit(); onChanged?.(); }
    catch (error) { const next = fail(error, t("historyLoadError")); if (next) setFailure(next); }
    finally { setBusy(false); }
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
  const meta = [fields.net_quantity?.value, maker, formatDateTime(summary.completed_at ?? summary.created_at, locale), compliance?.ruleset_label].filter(Boolean) as string[];
  const counts = { violations: compliance?.summary.failed ?? 0, review: compliance?.summary.review ?? 0, passed: compliance?.summary.passed ?? 0 };
  const risk = summary.risk;
  const fade = reduced ? { initial: false as const } : { initial: { opacity: 0, y: 4 }, animate: { opacity: 1, y: 0 }, exit: { opacity: 0, y: -4 }, transition: { duration: DURATION.base, ease: EASE } };

  return (
    <div className="page record-page" aria-busy={busy || undefined}>
      <header className="record-header">
        <div className="record-header__text">
          <div className="record-header__title-row">
            <h1 className="record-title">{summary.product_name ?? t("recUntitled")}</h1>
            <AnimatePresence mode="wait" initial={false}>
              {verdict && <m.span key={verdict} className="record-verdict" {...fade}><Badge variant={verdict} /></m.span>}
            </AnimatePresence>
            {verified && <span className="verified-pill"><BadgeCheck size={14} aria-hidden="true" />{fill(t("recVerifiedBy"), { name: summary.review?.by ?? "", date: formatDateTime(summary.review?.at, locale) })}</span>}
          </div>
          <p className="record-meta">
            {official ? null : <span className="record-meta__kind">{t("recSelfCheck")}</span>}
            {meta.map((part, index) => <span key={index} className={index === 0 && fields.net_quantity?.value ? "mono" : undefined}>{part}</span>)}
          </p>
        </div>
        <div className="record-header__side">
          <div className="page-header__actions record-actions">
            <Button variant="outline" icon={<RefreshCw size={16} aria-hidden="true" />} onClick={() => void reanalyse()} disabled={busy || !summary.ocr}>{t("recReanalyse")}</Button>
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
          action={summary.image ? <Button size="sm" variant="outline" icon={<RefreshCw size={16} aria-hidden="true" />} onClick={() => void retryProcessing()} loading={busy}>{t("tryAgain")}</Button> : undefined}>
          {summary.failure_message ?? t("recNotProcessedBody")}
        </Alert>
      )}

      <div className="record-grid">
        <Card className="record-photo" headingLevel={2} title={t("recPhotos")}
          action={<span className="mono record-photo__caption">{summary.ocr ? `${summary.ocr.languages.join(" + ")}${summary.ocr.duration_ms ? ` · ${Math.max(1, Math.round(summary.ocr.duration_ms / 1000))} s` : ""}` : ""}</span>}>
          <Tabs<PhotoTab> label={t("recPhotos")} value={tab} onChange={setTab} items={[
            { value: "back", label: t("recPhotoBack") },
            { value: "front", label: t("recPhotoFront"), disabled: true, title: t("recMultiPhoto") },
            { value: "date", label: t("recPhotoDate"), disabled: true, title: t("recMultiPhoto") },
          ]} />
          {summary.image
            ? <EvidencePhoto src={photo} summary={summary} items={items} selectedId={selectedId} hoveredId={hoveredId} onSelect={setSelectedId} onHover={setHoveredId} />
            : <p className="muted-text">{t("noImage")}</p>}
        </Card>

        <Card className="record-findings" headingLevel={2} title={fill(t("recFindingsTitle"), { count: compliance?.findings.length ?? 0 })}
          description={canEdit ? t("recFindingsHint") : official ? undefined : t("recSelfCheckHint")}>
          {compliance
            ? <Findings summary={summary} items={items} selectedId={selectedId} hoveredId={hoveredId} onSelect={setSelectedId} onHover={setHoveredId} canEdit={canEdit} busy={busy} onCorrect={correct} onUndo={undo} />
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
