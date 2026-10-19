import { useEffect, useMemo, useRef, useState } from "react";
import { Camera, ChevronDown, PenLine, RotateCcw, X } from "lucide-react";
import { Badge, Button } from "../../components/ui";
import { PANEL_LABEL } from "../../components/ProgressPanel";
import { fill, TranslationKey, useI18n } from "../../i18n";
import { buildFindingItems, FindingItem } from "../../lib/evidence";
import { photoActionFor } from "../../lib/photoActions";
import { activePhotos, summaryPhotos } from "../../lib/panels";
import type { Panel, Summary } from "../../types";
import { CORRECTABLE, CorrectionPayload } from "../record/Findings";
import { BottomSheet } from "./BottomSheet";
import { CorrectionSheet } from "./CorrectionSheet";
import { MarkerPhoto } from "./MarkerPhoto";

const RISK_LABEL: Record<string, TranslationKey> = { LOW: "low", MEDIUM: "medium", HIGH: "high", CRITICAL: "critical" };

type Props = {
  summary: Summary;
  official: boolean;
  /** Object URLs of the stored photos by image id. */
  photoUrls: Record<string, string>;
  onSave: () => void;
  onClose: () => void;
  /** Go back to the camera for this panel (a new photo, or a retake that replaces the old one). */
  onCapture: (panel: Panel) => void;
  onCorrect: (payload: CorrectionPayload) => Promise<string | null>;
};

/** What the finding is about, as the fields an inspector can enter for it. */
const fieldsFor = (item: FindingItem) => CORRECTABLE[item.finding.rule_id] ?? (item.finding.field ? [item.finding.field] : []);

/** Screen C: the photo with markers and a draggable sheet: verdict, what to check on the pack, what passed. */
export function ResultScreen({ summary, official, photoUrls, onSave, onClose, onCapture, onCorrect }: Props) {
  const { t } = useI18n();
  const items = useMemo(() => buildFindingItems(summary), [summary]);
  const allPhotos = useMemo(() => summaryPhotos(summary), [summary]);
  const images = activePhotos(allPhotos);
  const [imageId, setImageId] = useState<string | null>(summary.image?.id ?? images[0]?.id ?? null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [passedOpen, setPassedOpen] = useState(false);
  const [entering, setEntering] = useState<FindingItem | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, []);

  const open = items.filter(item => item.group === "violations" || item.group === "review");
  const passed = items.filter(item => item.group === "passed");
  const photo = images.find(item => item.id === imageId) ?? images[0];
  const verdict = summary.compliance?.verdict ?? null;
  const risk = official ? summary.risk : null;
  const quantity = summary.extraction?.fields.net_quantity?.value;

  function select(item: FindingItem) {
    const next = selectedId === item.finding.rule_id ? null : item.finding.rule_id;
    setSelectedId(next);
    if (next && item.imageId) setImageId(item.imageId);
  }

  return (
    <div className="res">
      <header className="res-top">
        <button type="button" className="cap-icon-button" aria-label={t("capClose")} onClick={onClose}><X size={22} aria-hidden="true" /></button>
        <h1 className="res-top__title" tabIndex={-1} ref={heading}>{t("resTitle")}</h1>
        {images.length > 1 && (
          <div className="res-tabs" role="tablist" aria-label={t("recPhotos")}>
            {images.map(image => (
              <button key={image.id} type="button" role="tab" aria-selected={image.id === photo?.id} className={`res-tab${image.id === photo?.id ? " is-active" : ""}`} onClick={() => setImageId(image.id)}>{t(PANEL_LABEL[image.panel])}</button>
            ))}
          </div>
        )}
      </header>
      <div className="res-photo">
        {photo && <MarkerPhoto src={photoUrls[photo.id] ?? null} image={photo} items={items} selectedId={selectedId} onSelect={id => { setSelectedId(id); }} />}
      </div>

      <BottomSheet label={t("resSheet")} initial={1}
        footer={<Button size="lg" className="cap-btn cap-btn--block" onClick={onSave}>{t(official ? "resSave" : "resOpenResult")}</Button>}>
        <section className="res-summary" aria-label={t("resSummary")}>
          <div className="res-summary__badges">
            {verdict && <Badge variant={verdict} />}
            {risk && <span className="res-risk"><span className="mono tabular">{risk.risk_score}</span> · {RISK_LABEL[risk.risk_level] ? t(RISK_LABEL[risk.risk_level]) : risk.risk_level}</span>}
          </div>
          <h2 className="res-summary__name">{summary.product_name ?? t("recUntitled")}</h2>
          {quantity && <p className="res-summary__qty"><span className="muted-text">{t("extractionNetQuantity")}</span> <span className="mono">{quantity}</span></p>}
          <p className="res-counts" aria-label={t("resCounts")}>
            <span className="res-count res-count--violation"><strong className="tabular">{summary.compliance?.summary.failed ?? 0}</strong> {t("recStatViolations")}</span>
            <span className="res-count res-count--review"><strong className="tabular">{summary.compliance?.summary.review ?? 0}</strong> {t("recStatToVerify")}</span>
            <span className="res-count res-count--pass"><strong className="tabular">{summary.compliance?.summary.passed ?? 0}</strong> {t("recStatPassed")}</span>
          </p>
        </section>

        <section aria-labelledby="res-check-title">
          <h3 id="res-check-title" className="res-section-title">{fill(t("resCheck"), { count: open.length })}</h3>
          {open.length === 0 && <p className="muted-text res-empty">{t("resNothing")}</p>}
          <ul className="res-list">
            {open.map(item => {
              const id = item.finding.rule_id;
              const fields = fieldsFor(item);
              const { panel, action } = photoActionFor(item, allPhotos, fields);
              return (
                <li key={id} className={`res-item res-item--${item.group}${id === selectedId ? " is-selected" : ""}`} data-rule={id}>
                  <button type="button" className="res-item__main" aria-pressed={id === selectedId} onClick={() => select(item)}>
                    <span className={`res-num res-num--${item.group}`}>{item.number}</span>
                    <span className="res-item__text">
                      <span className="res-item__title">{!official && item.finding.fix_title ? item.finding.fix_title : item.finding.rule_name}</span>
                      <span className="res-item__message">{item.finding.message}</span>
                      {!official && item.finding.example ? <span className="res-item__value mono">{item.finding.example}</span> : item.read && <span className="res-item__value mono">{item.read}</span>}
                    </span>
                  </button>
                  <div className="res-item__actions">
                    {official && fields.length > 0 && <Button size="sm" variant="outline" className="cap-action" icon={<PenLine size={16} aria-hidden="true" />} onClick={() => setEntering(item)}>{t("recEnterValue")}</Button>}
                    {action === "add" && <Button size="sm" variant="outline" className="cap-action" icon={<Camera size={16} aria-hidden="true" />} onClick={() => onCapture(panel)}>{t("recAddPhoto")}</Button>}
                    {action === "retake" && <Button size="sm" variant="outline" className="cap-action" icon={<RotateCcw size={16} aria-hidden="true" />} onClick={() => onCapture(panel)}>{t("recRetake")}</Button>}
                  </div>
                </li>
              );
            })}
          </ul>
        </section>

        {passed.length > 0 && (
          <section className="res-passed">
            <button type="button" className="res-passed__toggle" aria-expanded={passedOpen} aria-controls="res-passed-list" onClick={() => setPassedOpen(value => !value)}>
              <span>{fill(t("resPassedCount"), { count: passed.length })}</span>
              <ChevronDown size={18} aria-hidden="true" className={passedOpen ? "is-open" : ""} />
            </button>
            {passedOpen && (
              <ul className="res-passed__list" id="res-passed-list">
                {passed.map(item => <li key={item.finding.rule_id}><Badge variant="PASS" dot={false} /> <span>{item.finding.rule_name}</span>{item.read && <span className="mono res-passed__value">{item.read}</span>}</li>)}
              </ul>
            )}
          </section>
        )}
      </BottomSheet>

      {entering && <CorrectionSheet fields={fieldsFor(entering)} onSubmit={onCorrect} onClose={() => setEntering(null)} />}
    </div>
  );
}
