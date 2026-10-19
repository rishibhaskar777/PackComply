import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronDown, CircleAlert, CircleCheck, FileDown, RefreshCw, ScanSearch, SearchX, TrendingUp, TriangleAlert, Upload } from "lucide-react";
import { ApiError, downloadSelfCheckReport, getJson, isExpired, isUnavailable } from "../../api/client";
import { navigate } from "../../app/router";
import { Alert, Button, Card, EmptyState, Skeleton, Tabs, useToast } from "../../components/ui";
import { PANEL_LABEL } from "../../components/ProgressPanel";
import { fill, useI18n } from "../../i18n";
import { buildFindingItems, FindingItem } from "../../lib/evidence";
import { activePhotos, summaryPhotos } from "../../lib/panels";
import { READINESS_TONE, readinessOf, ruleChip } from "../../lib/readiness";
import { usePhotoUrls } from "../../lib/usePhotoUrls";
import type { ProductsSummary, Summary } from "../../types";
import { formatDateTime } from "../record/AuditTrail";
import { EvidencePhoto } from "../record/EvidencePhoto";
import { fixesText } from "./ProductsPage";

type Failure = { status?: number; message: string; unavailable: boolean };
type Props = { inspectionId: string; onExpired: () => void; onTitle?: (title: string | null) => void };

/** A manufacturer's self-check result: is this label ready to print, and if not, exactly what to change on the artwork. */
export function ProductResultPage({ inspectionId, onExpired, onTitle }: Props) {
  const { t, locale } = useI18n();
  const toast = useToast();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [products, setProducts] = useState<ProductsSummary | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [loading, setLoading] = useState(true);
  const [reportBusy, setReportBusy] = useState(false);
  const [tab, setTab] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [showPassed, setShowPassed] = useState(false);
  const path = `/inspections/${encodeURIComponent(inspectionId)}`;

  const load = useCallback(async () => {
    setLoading(true); setFailure(null);
    try {
      setSummary(await getJson<Summary>(path, "inspection"));
      // The product history gives the "improved from 3 fixes to 0" line; the result stands without it.
      void getJson<ProductsSummary>("/products", "products").then(setProducts).catch(() => undefined);
    } catch (error) {
      if (isExpired(error)) onExpired();
      else { const e = error as ApiError; setFailure({ status: e.status, message: e.detail ?? t("historyLoadError"), unavailable: isUnavailable(error) }); }
      setSummary(null);
    } finally { setLoading(false); }
  }, [path, onExpired, t]);

  useEffect(() => { setSelectedId(null); setHoveredId(null); setTab(null); setShowPassed(false); void load(); }, [inspectionId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { onTitle?.(summary?.product_name ?? null); return () => onTitle?.(null); }, [summary?.product_name]); // eslint-disable-line react-hooks/exhaustive-deps

  const photos = useMemo(() => (summary ? summaryPhotos(summary) : []), [summary]);
  const tabs = useMemo(() => activePhotos(photos), [photos]);
  const photoUrls = usePhotoUrls(path, tabs.map(photo => photo.id));
  const currentPhoto = tabs.find(photo => photo.id === tab) ?? tabs.find(photo => photo.id === summary?.image?.id) ?? tabs[0] ?? null;
  const allItems: FindingItem[] = useMemo(() => (summary ? buildFindingItems(summary) : []), [summary]);
  // Fix cards: failed rules first, then items to verify on the pack, most severe first. Passed rules are listed apart.
  const todo = useMemo(() => allItems.filter(item => item.group === "violations" || item.group === "review"), [allItems]);
  const passed = useMemo(() => allItems.filter(item => item.group === "passed"), [allItems]);
  const photoItems = useMemo(() => todo.map(item => (item.boxes && item.imageId && item.imageId !== currentPhoto?.id ? { ...item, boxes: null } : item)), [todo, currentPhoto?.id]);

  function select(id: string | null) {
    setSelectedId(id);
    const item = id ? todo.find(entry => entry.finding.rule_id === id) : undefined;
    if (item?.imageId && item.imageId !== currentPhoto?.id && tabs.some(photo => photo.id === item.imageId)) setTab(item.imageId);
    if (id) document.getElementById("self-check-photo")?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }
  async function download() {
    if (!summary || reportBusy) return;
    setReportBusy(true);
    try { await downloadSelfCheckReport(summary.id, summary.inspection_number); toast(t("toastReportDownloaded")); }
    catch (error) { if (isExpired(error)) onExpired(); else toast(t("reportGenerationError"), "error"); }
    finally { setReportBusy(false); }
  }

  if (loading && !summary) return <ResultSkeleton label={t("loading")} />;
  if (failure?.status === 404 || failure?.status === 422) {
    return <div className="page"><EmptyState title={t("productNotFoundTitle")} description={t("productNotFoundBody")} icon={<SearchX size={16} />} action={<Button variant="outline" onClick={() => navigate("/products")}>{t("backToProducts")}</Button>} /></div>;
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
  const completed = summary.status === "COMPLETED" && compliance !== undefined;
  const fixes = compliance?.summary.failed ?? 0;
  const review = compliance?.summary.review ?? 0;
  const applicable = (compliance?.summary.passed ?? 0) + fixes + review;
  const state = readinessOf(fixes, review);
  const tone = READINESS_TONE[state];
  const count = fixes || review;
  const headline = state === "READY" ? t("bannerReady") : fill(t(state === "FIX" ? (fixes === 1 ? "bannerFixOne" : "bannerFixOther") : (review === 1 ? "bannerCheckOne" : "bannerCheckOther")), { count });
  const BannerIcon = state === "READY" ? CircleCheck : state === "FIX" ? CircleAlert : TriangleAlert;
  const history = products?.products.find(card => card.history.some(check => check.inspection_id === summary.id))?.history ?? [];
  const position = history.findIndex(check => check.inspection_id === summary.id);
  const before = position >= 0 ? history[position + 1] : undefined;
  const improvement = before && fixes < before.fixes ? fill(t("improvedBanner"), { from: fixesText(t, before.fixes), to: fixes }) : null;
  const when = formatDateTime(summary.completed_at ?? summary.created_at, locale);

  return (
    <div className="page maker-page result-page">
      <header className="record-header result-header">
        <div className="record-header__text">
          <p className="eyebrow">{t("selfCheckResult")}</p>
          <h1 className="record-title">{summary.product_name ?? t("recUntitled")}</h1>
        </div>
        <div className="page-header__actions record-actions">
          <Button variant="outline" icon={<FileDown size={16} aria-hidden="true" />} onClick={() => void download()} loading={reportBusy} disabled={!completed}>{reportBusy ? t("generatingPdf") : t("downloadSelfCheck")}</Button>
          <Button icon={<Upload size={16} aria-hidden="true" />} onClick={() => navigate(`/check?previous=${encodeURIComponent(summary.id)}`)}>{t("uploadCorrected")}</Button>
        </div>
      </header>

      {!completed && (
        <Alert variant={summary.status === "FAILED" ? "error" : "info"} title={summary.status === "FAILED" ? t("failed") : t("recNotProcessed")}>{summary.failure_message ?? t("recNotProcessedBody")}</Alert>
      )}

      {completed && (
        <section className={`readiness-banner readiness-banner--${tone}`} aria-live="polite" aria-labelledby="readiness-title">
          <BannerIcon size={26} aria-hidden="true" className="readiness-banner__icon" />
          <div className="readiness-banner__text">
            <h2 className="readiness-banner__title" id="readiness-title">{headline}</h2>
            <p className="readiness-banner__meta">{fill(t("bannerMeta"), { date: when, passed: compliance?.summary.passed ?? 0, total: applicable })}</p>
          </div>
          {improvement && <p className="readiness-banner__gain"><TrendingUp size={16} aria-hidden="true" />{improvement}</p>}
        </section>
      )}

      <div className="record-grid">
        <Card className="record-photo" headingLevel={2} id="self-check-photo" title={t("recPhotos")}>
          {tabs.length > 1 && (
            <Tabs<string> label={t("recPhotos")} value={currentPhoto?.id ?? ""} onChange={setTab} items={tabs.map(photo => ({ value: photo.id, label: t(PANEL_LABEL[photo.panel]) }))} />
          )}
          {currentPhoto
            ? <EvidencePhoto key={currentPhoto.id} src={photoUrls[currentPhoto.id] ?? null} image={currentPhoto} ocr={undefined} items={photoItems} selectedId={selectedId} hoveredId={hoveredId} onSelect={select} onHover={setHoveredId} zoomOnSelect />
            : <p className="muted-text">{t("noImage")}</p>}
          <p className="disclaimer-line">{t("selfAssessDisclaimer")}</p>
        </Card>

        <Card className="record-findings fix-panel" headingLevel={2} title={state === "READY" ? t("nothingToFix") : t("fixListTitle")} description={state === "READY" ? t("readyToPrintBody") : t("fixListHint")}>
          {completed && todo.length > 0 && (
            <ol className="fix-cards">
              {todo.map(item => <FixCard key={item.finding.rule_id} item={item} selected={item.finding.rule_id === selectedId} onShow={() => select(item.finding.rule_id === selectedId ? null : item.finding.rule_id)} />)}
            </ol>
          )}
          {completed && passed.length > 0 && (
            <div className="passed-block">
              <button type="button" className="passed-block__toggle" aria-expanded={showPassed} aria-controls="passed-list" onClick={() => setShowPassed(open => !open)}>
                <CircleCheck size={16} aria-hidden="true" />
                <span>{fill(t("declarationsCorrect"), { count: passed.length })}</span>
                <ChevronDown size={16} aria-hidden="true" className="passed-block__chevron" />
              </button>
              {showPassed && (
                <ul id="passed-list" className="passed-list">
                  {passed.map(item => <li key={item.finding.rule_id}><CircleCheck size={14} aria-hidden="true" />{item.finding.pass_text ?? item.finding.rule_name}</li>)}
                </ul>
              )}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}

function FixCard({ item, selected, onShow }: { item: FindingItem; selected: boolean; onShow: () => void }) {
  const { t } = useI18n();
  const finding = item.finding;
  const review = item.group === "review";
  const chip = finding.rule_chip ?? ruleChip(finding.legal_reference);
  const steps = finding.fix_steps ?? [];
  return (
    <li className={`fix-card fix-card--${review ? "review" : "violation"}${selected ? " is-selected" : ""}`} data-rule={finding.rule_id}>
      <div className="fix-card__head">
        <span className={`fix-card__number fix-card__number--${review ? "review" : "violation"} tabular`} aria-hidden="true">{item.number}</span>
        <div className="fix-card__title-block">
          <h3 className="fix-card__title">{finding.fix_title ?? finding.rule_name}</h3>
          <div className="fix-card__chips">
            {chip && <span className="rule-chip mono">{chip}</span>}
            {review && <span className="status-pill status-pill--review">{t("checkOnPack")}</span>}
          </div>
        </div>
      </div>
      <p className="fix-card__found"><span className="fix-card__label">{t("whatWeFound")}</span> {finding.message}</p>
      {steps.length > 0 && (
        <div className="fix-card__how">
          <p className="fix-card__label">{t("howToFix")}</p>
          <ul>{steps.map(step => <li key={step}>{step}</li>)}</ul>
        </div>
      )}
      {finding.example && (
        <div className="example-box">
          <p className="example-box__label">{t("exampleLabel")}</p>
          <p className="example-box__text mono">{finding.example}</p>
        </div>
      )}
      {item.boxes && (
        <Button variant="ghost" size="sm" className="fix-card__show" icon={<ScanSearch size={14} aria-hidden="true" />} aria-pressed={selected} onClick={onShow} aria-label={`${t("showOnLabel")}: ${finding.fix_title ?? finding.rule_name}`}>{t("showOnLabel")} →</Button>
      )}
    </li>
  );
}

function ResultSkeleton({ label }: { label: string }) {
  return (
    <div className="page maker-page" role="status" aria-live="polite">
      <span className="sr-only">{label}</span>
      <Skeleton width={320} height={34} />
      <Skeleton width="100%" height={72} />
      <div className="record-grid"><Skeleton width="100%" height={420} /><Skeleton width="100%" height={420} /></div>
    </div>
  );
}
