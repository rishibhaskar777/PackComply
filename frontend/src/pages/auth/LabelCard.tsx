import { CSSProperties } from "react";
import { Check, TriangleAlert } from "lucide-react";
import { TranslationKey, useI18n } from "../../i18n";

type Row = { key: TranslationKey; value: string; rule: string; status: "PASS" | "REVIEW" };

// A fictional product. Values are printed label text, shown as printed (like OCR text, never translated).
const PRODUCT = "GLUCO BISCUITS";
const ROWS: Row[] = [
  { key: "labelNetQuantity", value: "70 g", rule: "6(1)(c)", status: "PASS" },
  { key: "labelMrp", value: "MRP ₹10.00 (incl. of all taxes)", rule: "6(1)(e)", status: "PASS" },
  { key: "labelManufactureDate", value: "MFD 08/2026", rule: "6(1)(d)", status: "PASS" },
  { key: "labelManufacturedBy", value: "Sample Foods Pvt Ltd, Mumbai 400057", rule: "6(1)(a)", status: "PASS" },
  { key: "labelConsumerCare", value: "care@samplefoods.in · 1800-…", rule: "6(2)", status: "REVIEW" },
];

// The scan line crosses the rows in the first 75% of each 4.8 s loop; each chip pops as the line reaches its row.
const LOOP_S = 4.8;
const SWEEP = 0.75;
const chipDelay = (index: number, count: number) => `${(((index + 0.5) / count) * SWEEP * LOOP_S).toFixed(2)}s`;

function RuleChip({ rule, status, index, count, animate }: { rule: string; status: Row["status"]; index: number; count: number; animate: boolean }) {
  const { t } = useI18n();
  const pass = status === "PASS";
  return (
    <span className={`rule-chip rule-chip--${pass ? "pass" : "review"}${animate ? " is-animated" : ""}`} style={animate ? { "--chip-delay": chipDelay(index, count) } as CSSProperties : undefined}>
      {pass ? <Check size={12} strokeWidth={2.5} aria-hidden="true" /> : <TriangleAlert size={12} strokeWidth={2.5} aria-hidden="true" />}
      <span className="rule-chip__rule mono"><span className="rule-chip__word">{t("ruleShort")} </span>{rule}</span>
      <span className="rule-chip__status">{pass ? t("pass") : t("statusReview")}</span>
    </span>
  );
}

/** Illustrative back-of-pack label with a scan line sweeping over it; static under reduced motion. */
export function LabelCard({ reduced }: { reduced: boolean }) {
  const { t } = useI18n();
  return (
    <figure className={`label-card${reduced ? "" : " is-floating"}`} role="img" aria-label={t("sampleLabelCaption")}>
      <div className="label-card__inner" aria-hidden="true">
        <div className="label-card__header">
          <span className="label-card__product">{PRODUCT}</span>
          <span className="label-card__panel mono">{t("backPanel")}</span>
        </div>
        <div className="label-card__rows">
          {!reduced && <span className="label-card__scanner" data-testid="scan-line"><span className="label-card__scan-line" /></span>}
          {ROWS.map((row, index) => (
            <div className="label-card__row" key={row.rule}>
              <div className="label-card__field">
                <span className="label-card__key">{t(row.key)}</span>
                <span className="label-card__value mono">{row.value}</span>
              </div>
              <RuleChip rule={row.rule} status={row.status} index={index} count={ROWS.length} animate={!reduced} />
            </div>
          ))}
        </div>
      </div>
    </figure>
  );
}

/** Phone and tablet: three rule chips popping in turn, in place of the label card. */
export function RuleStrip({ reduced }: { reduced: boolean }) {
  const { t } = useI18n();
  const rows = ROWS.slice(0, 3);
  return (
    <div className="rule-strip" role="img" aria-label={t("rulesStripLabel")}>
      {rows.map((row, index) => (
        <span className="rule-strip__item" key={row.rule} aria-hidden="true">
          <span className="rule-strip__key">{t(row.key)}</span>
          <RuleChip rule={row.rule} status={row.status} index={index} count={rows.length} animate={!reduced} />
        </span>
      ))}
    </div>
  );
}
