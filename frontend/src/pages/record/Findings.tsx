import { FormEvent, KeyboardEvent, useEffect, useId, useRef, useState } from "react";
import { Camera, ChevronDown, PenLine, RotateCcw, Undo2, User } from "lucide-react";
import { Alert, Button, FormField, Input, Select } from "../../components/ui";
import { fill, TranslationKey, useI18n } from "../../i18n";
import { FindingItem, Group, GROUP_ORDER, GROUP_TONE, moveSelection } from "../../lib/evidence";
import { summaryPhotos } from "../../lib/panels";
import { photoActionFor } from "../../lib/photoActions";
import { FieldInput } from "./FieldInput";
import { FieldCorrection, Panel, Summary } from "../../types";

export const FIELD_LABELS: Record<string, TranslationKey> = { product_name: "extractionProductName", manufacturer: "extractionManufacturer", manufacturer_address: "extractionManufacturerAddress", packer: "extractionPacker", packer_address: "extractionPackerAddress", importer: "extractionImporter", importer_address: "extractionImporterAddress", mrp: "extractionMrp", net_quantity: "extractionNetQuantity", manufacturing_date: "extractionManufacturingDate", packing_date: "extractionPackingDate", best_before: "extractionBestBefore", use_by: "extractionUseBy", expiry_date: "extractionExpiryDate", consumer_care_phone: "extractionConsumerCarePhone", consumer_care_email: "extractionConsumerCareEmail", consumer_care_website: "extractionConsumerCareWebsite", brand_owner: "extractionBrandOwner", brand_owner_address: "extractionBrandOwnerAddress", tax_inclusive: "extractionTaxInclusive", fssai_license: "extractionFssaiLicense", country_of_origin: "extractionCountryOfOrigin", unit_sale_price: "extractionUnitSalePrice" };

/** Fields an inspector can enter to resolve each rule; the first is the default. */
export const CORRECTABLE: Record<string, string[]> = {
  MANUFACTURER_INFORMATION: ["manufacturer", "manufacturer_address", "brand_owner", "brand_owner_address", "packer", "packer_address"],
  IMPORTER_INFORMATION: ["importer", "importer_address"],
  COUNTRY_OF_ORIGIN: ["country_of_origin"],
  PACKER_INFORMATION: ["packer", "packer_address"],
  PRODUCT_IDENTITY: ["product_name"],
  NET_QUANTITY_FORMAT: ["net_quantity"],
  NET_QUANTITY_UNIT_SUITABILITY: ["net_quantity"],
  MRP_FORMAT: ["mrp"],
  MRP_TAX_INCLUSIVE: ["tax_inclusive"],
  UNIT_SALE_PRICE: ["unit_sale_price"],
  MANUFACTURE_DATE: ["manufacturing_date", "packing_date"],
  DATE_CONSISTENCY: ["manufacturing_date", "packing_date", "expiry_date", "best_before", "use_by"],
  CONSUMER_CARE_FORMAT: ["consumer_care_phone", "consumer_care_email", "consumer_care_website"],
  FSSAI_LICENSE_PRESENT: ["fssai_license"],
};

export const REASONS = ["recReasonPhysical", "recReasonMisread", "recReasonOther"] as const;
export const REASON_TEXT: Record<(typeof REASONS)[number], string> = { recReasonPhysical: "Verified on physical pack", recReasonMisread: "OCR misread", recReasonOther: "Other" };

/** The short rule reference for the chip: "Rule 6(1)(e)" out of "LM(PC) Rules 2011, Rule 6(1)(e)". */
export function ruleChip(reference: string | null | undefined): string | null {
  if (!reference) return null;
  const short = /Rule\s+\d+(?:\([^)]*\))*/.exec(reference)?.[0];
  // A non-LM(PC) reference ("FSS (Labelling and Display) Regulations 2020 — cross-check") keeps its name, shortened.
  const name = reference.split(/\s+[—–]\s+/)[0];
  return short ?? (name.length > 28 ? `${name.slice(0, 27)}…` : name);
}

export type CorrectionPayload = { field_name: string; corrected_value: string; reason: string };

type FindingsProps = {
  summary: Summary;
  items: FindingItem[];
  selectedId: string | null;
  hoveredId: string | null;
  onSelect: (id: string | null) => void;
  onHover: (id: string | null) => void;
  canEdit: boolean;
  busy: boolean;
  onCorrect: (payload: CorrectionPayload) => Promise<string | null>;
  onUndo: (correction: FieldCorrection) => void;
  /** Add a photo of a panel, or retake it (the new photo replaces the old one, which stays as evidence). Absent: photo actions are hidden. */
  onPhoto?: (panel: Panel, action: "add" | "retake") => void;
};

const GROUP_TITLE: Record<Group, TranslationKey> = { violations: "recGroupViolations", review: "recGroupReview", passed: "recGroupPassed", notApplicable: "notApplicable" };

/** Findings by group; arrow keys move the selection, hover and click stay in sync with the photo markers. */
export function Findings({ summary, items, selectedId, hoveredId, onSelect, onHover, canEdit, busy, onCorrect, onUndo, onPhoto }: FindingsProps) {
  const { t } = useI18n();
  const root = useRef<HTMLDivElement>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [naOpen, setNaOpen] = useState(false);
  const corrections = summary.corrections ?? [];
  const photos = summaryPhotos(summary);

  function onKeyDown(event: KeyboardEvent) {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    if ((event.target as HTMLElement).closest("input, select, textarea, form")) return;
    event.preventDefault();
    const next = moveSelection(items, selectedId, event.key === "ArrowDown" ? 1 : -1);
    onSelect(next);
    if (next) requestAnimationFrame(() => root.current?.querySelector<HTMLElement>(`[data-finding="${CSS.escape(next)}"] .finding-row__main`)?.focus());
  }

  const byGroup = (group: Group) => items.filter(item => item.group === group);
  return (
    <div className="findings" ref={root} onKeyDown={onKeyDown}>
      {GROUP_ORDER.map(group => {
        const rows = byGroup(group);
        if (!rows.length) return null;
        if (group === "notApplicable") {
          return (
            <section className="findings__group findings__group--na" key={group} aria-label={t(GROUP_TITLE[group])}>
              <button type="button" className="findings__na-toggle" aria-expanded={naOpen} onClick={() => setNaOpen(open => !open)}>
                <ChevronDown size={16} aria-hidden="true" className={naOpen ? "is-open" : ""} />
                <span className="findings__group-title">{t(GROUP_TITLE[group])}</span>
                <span className="findings__count tabular">{rows.length}</span>
                {!naOpen && <span className="findings__na-line">{rows.map(row => row.finding.rule_name).join(" · ")}</span>}
              </button>
              {naOpen && <ul className="findings__na-list">{rows.map(row => <li key={row.finding.rule_id}><strong>{row.finding.rule_name}</strong><span>{row.finding.message}</span></li>)}</ul>}
            </section>
          );
        }
        return (
          <section className="findings__group" key={group} aria-label={t(GROUP_TITLE[group])}>
            <h3 className={`findings__group-title findings__group-title--${GROUP_TONE[group]}`}>{t(GROUP_TITLE[group])} <span className="findings__count tabular">{rows.length}</span></h3>
            <ul className="findings__list">
              {rows.map(item => {
                const id = item.finding.rule_id;
                const correction = item.finding.corrected ? [...corrections].reverse().find(entry => item.finding.fields?.includes(entry.field_name) || entry.field_name === item.finding.field) : undefined;
                const options = CORRECTABLE[id] ?? (item.finding.field ? [item.finding.field] : []);
                const open = item.group === "violations" || item.group === "review";
                const actionable = canEdit && open && options.length > 0;
                const photo = onPhoto && open ? photoActionFor(item, photos, options) : null;
                return (
                  <li key={id} data-finding={id} className={`finding-row finding-row--${GROUP_TONE[group]}${selectedId === id ? " is-selected" : ""}${hoveredId === id ? " is-hovered" : ""}`}
                    onMouseEnter={() => onHover(id)} onMouseLeave={() => onHover(null)}>
                    <button type="button" className="finding-row__main" aria-pressed={selectedId === id} onClick={() => onSelect(selectedId === id ? null : id)}>
                      <span className={`num-badge num-badge--${GROUP_TONE[group]}`} aria-hidden="true">{item.number}</span>
                      <span className="finding-row__body">
                        <span className="finding-row__title">
                          <span className="finding-row__name">{item.finding.rule_name}</span>
                          {ruleChip(item.finding.legal_reference) && <span className="rule-chip mono" title={item.finding.legal_reference ?? undefined}>{ruleChip(item.finding.legal_reference)}</span>}
                          {item.finding.corrected && <span className="inspector-chip"><User size={12} aria-hidden="true" />{t("recInspectorChip")}</span>}
                        </span>
                        <span className="finding-row__text">{item.finding.message}</span>
                        <span className="finding-row__read mono">{t("recRead")}: {item.read ?? t("notDetected")}</span>
                      </span>
                    </button>
                    {(actionable || correction || photo?.action) && (
                      <div className="finding-row__actions">
                        {actionable && <Button size="sm" variant="outline" icon={<PenLine size={14} aria-hidden="true" />} aria-expanded={editing === id} onClick={() => { onSelect(id); setEditing(editing === id ? null : id); }} disabled={busy}>{t("recEnterValue")}</Button>}
                        {photo?.action === "add" && <Button size="sm" variant="ghost" icon={<Camera size={14} aria-hidden="true" />} onClick={() => onPhoto!(photo.panel, "add")} disabled={busy}>{t("recAddPhoto")}</Button>}
                        {photo?.action === "retake" && <Button size="sm" variant="ghost" icon={<RotateCcw size={14} aria-hidden="true" />} onClick={() => onPhoto!(photo.panel, "retake")} disabled={busy}>{t("recRetake")}</Button>}
                        {correction && canEdit && <Button size="sm" variant="ghost" icon={<Undo2 size={14} aria-hidden="true" />} aria-label={fill(t("recUndoLabel"), { field: t(FIELD_LABELS[correction.field_name] ?? "productInformation") })} onClick={() => onUndo(correction)} disabled={busy}>{t("recUndo")}</Button>}
                      </div>
                    )}
                    {editing === id && actionable && (
                      <CorrectionForm item={item} options={options} busy={busy} onCancel={() => setEditing(null)}
                        onSubmit={async payload => { const failure = await onCorrect(payload); if (!failure) setEditing(null); return failure; }} />
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

type FormProps = { item: FindingItem; options: string[]; busy: boolean; onCancel: () => void; onSubmit: (payload: CorrectionPayload) => Promise<string | null> };

/** Inline correction form: field (when the rule has several), value, reason and an optional note. */
function CorrectionForm({ item, options, busy, onCancel, onSubmit }: FormProps) {
  const { t } = useI18n();
  const id = useId();
  const initial = item.finding.field && options.includes(item.finding.field) ? item.finding.field : options[0];
  const [field, setField] = useState(initial);
  const [value, setValue] = useState("");
  const [reason, setReason] = useState<(typeof REASONS)[number]>("recReasonPhysical");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!value.trim()) { setError(t("recValueRequired")); return; }
    setError(null);
    const text = REASON_TEXT[reason];
    setError(await onSubmit({ field_name: field, corrected_value: value.trim(), reason: note.trim() ? `${text}: ${note.trim()}` : text }));
  }
  return (
    <form className="correction-form" onSubmit={submit} onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); onCancel(); } }} aria-label={t("recEnterValue")}>
      {error && <Alert>{error}</Alert>}
      <div className="correction-form__grid">
        {options.length > 1 && (
          <FormField label={t("recFieldLabel")} htmlFor={`${id}-field`}>
            <Select id={`${id}-field`} value={field} onChange={event => { setField(event.target.value); setValue(""); }}>
              {options.map(name => <option key={name} value={name}>{t(FIELD_LABELS[name] ?? "productInformation")}</option>)}
            </Select>
          </FormField>
        )}
        <div className="form-field correction-form__value">
          <span className="form-field__label">{options.length > 1 ? t("recValueLabel") : t(FIELD_LABELS[field] ?? "productInformation")}</span>
          <FieldInput key={field} field={field} label={t(FIELD_LABELS[field] ?? "productInformation")} autoFocus invalid={Boolean(error)} onChange={setValue} />
        </div>
        <FormField label={t("recReasonLabel")} htmlFor={`${id}-reason`}>
          <Select id={`${id}-reason`} value={reason} onChange={event => setReason(event.target.value as (typeof REASONS)[number])}>
            {REASONS.map(key => <option key={key} value={key}>{t(key)}</option>)}
          </Select>
        </FormField>
        <FormField label={t("recNoteLabel")} htmlFor={`${id}-note`}>
          <Input id={`${id}-note`} value={note} onChange={event => setNote(event.target.value)} maxLength={200} autoComplete="off" />
        </FormField>
      </div>
      <div className="correction-form__actions">
        <Button type="submit" size="sm" loading={busy}>{t("recSaveValue")}</Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel} disabled={busy}>{t("recCancel")}</Button>
      </div>
    </form>
  );
}

export function valueHint(field: string): TranslationKey {
  if (field === "mrp") return "recHintMrp";
  if (field === "net_quantity") return "recHintQuantity";
  if (field === "unit_sale_price") return "recHintUnitPrice";
  if (["manufacturing_date", "packing_date", "expiry_date", "best_before", "use_by"].includes(field)) return "recHintDate";
  if (field === "tax_inclusive") return "recHintYesNo";
  if (field === "fssai_license") return "recHintFssai";
  return "recHintText";
}

