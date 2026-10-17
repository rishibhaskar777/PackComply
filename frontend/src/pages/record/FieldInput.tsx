import { useId, useState } from "react";
import { Input, Select } from "../../components/ui";
import { fill, TranslationKey, useI18n } from "../../i18n";
import {
  allowsShelfLife, composeDate, composeMrp, composeQuantity, composeShelfLife, composeUsp, fieldKind, FieldKind, hasFullKeyboardToggle, previewDate, previewMrp, previewQuantity,
  previewUsp, QUANTITY_UNITS, sanitiseAmount, USP_UNITS, yearOptions,
} from "../../lib/fieldInput";
import { NumericKeypad } from "../capture/NumericKeypad";

type Props = {
  field: string;
  /** Receives the text to send to the API, "" while the input is incomplete. */
  onChange: (value: string) => void;
  /** Show the large on-screen number pad (the phone sheet); desktop uses the system numeric keyboard. */
  keypad?: boolean;
  /** Accessible name of the primary control (the field's label). */
  label: string;
  autoFocus?: boolean;
  invalid?: boolean;
};

type Parts = { amount: string; unit: string; month: number; year: number; months: string; shelfLife: boolean };

const UNIT_NAME: Record<string, TranslationKey> = { unit: "fiUnitUnit", piece: "fiUnitPiece", units: "fiUnitUnits", pieces: "fiUnitPieces" };

/** Build the text the API receives (and its preview) from the structured parts. */
function compose(kind: FieldKind, parts: Parts): { value: string; preview: string } {
  switch (kind) {
    case "mrp": return { value: composeMrp(parts.amount), preview: previewMrp(parts.amount) };
    case "usp": return { value: composeUsp(parts.amount, parts.unit), preview: previewUsp(parts.amount, parts.unit) };
    case "quantity": return { value: composeQuantity(parts.amount, parts.unit), preview: previewQuantity(parts.amount, parts.unit) };
    default: return { value: "", preview: "" };
  }
}

/**
 * One input per kind of field, the same on the phone sheet and the desktop record page: an amount with a fixed ₹ and
 * unit chips, a month and year picker, a phone or e-mail keyboard, or plain text. "Use full keyboard" always lets the
 * inspector type the value freely; the server checks it with the same parsers either way.
 */
export function FieldInput({ field, onChange, keypad = false, label, autoFocus = false, invalid = false }: Props) {
  const { t, locale } = useI18n();
  const id = useId();
  const kind = fieldKind(field);
  const now = new Date();
  const [parts, setParts] = useState<Parts>({ amount: "", unit: "", month: 0, year: now.getFullYear(), months: "", shelfLife: false });
  const [free, setFree] = useState(false);
  const [text, setText] = useState("");

  const shelf = kind === "date" && parts.shelfLife;
  const dateValue = kind === "date" ? (shelf ? composeShelfLife(parts.months) : composeDate(parts.month, parts.year)) : "";
  const built = kind === "date" ? { value: dateValue, preview: shelf ? (parts.months ? fill(t("fiMonthsCount"), { count: parts.months }) : "") : previewDate(dateValue, locale) } : compose(kind, parts);

  function patch(next: Partial<Parts>) {
    const merged = { ...parts, ...next };
    setParts(merged);
    if (kind === "date") onChange(merged.shelfLife ? composeShelfLife(merged.months) : composeDate(merged.month, merged.year));
    else onChange(compose(kind, merged).value);
  }
  function toggleFree() {
    const next = !free;
    setFree(next);
    // The typed text starts from what the guided input had built.
    if (next) { setText(built.value); onChange(built.value); } else onChange(built.value);
  }
  const unitLabel = (unit: string) => (UNIT_NAME[unit] ? t(UNIT_NAME[unit]) : unit);

  const toggle = hasFullKeyboardToggle(kind) && (
    <button type="button" className="fi-toggle" aria-pressed={free} onClick={toggleFree}>{free ? t("fiUseGuided") : t("fiUseFullKeyboard")}</button>
  );
  const preview = !free && built.preview ? <p className="fi-preview" aria-live="polite">{t("fiPreview")}: <strong>{built.preview}</strong></p> : null;

  // ---- free text: any kind can be typed out; the backend parsers validate it exactly the same way.
  if (free) {
    return (
      <div className="fi">
        <Input value={text} onChange={event => { setText(event.target.value); onChange(event.target.value); }} aria-label={label} aria-invalid={invalid || undefined} maxLength={300} autoComplete="off" inputMode="text" autoFocus />
        <p className="fi-hint">{t("fiFreeHint")}</p>
        {toggle}
      </div>
    );
  }

  const amountField = (decimals: number | null, prefix: string | null, name: string) => (
    <div className="fi-amount">
      {prefix && <span className="fi-prefix" aria-hidden="true">{prefix}</span>}
      <Input className={prefix ? "fi-amount__input fi-amount__input--prefixed" : "fi-amount__input"} aria-label={name} aria-invalid={invalid || undefined} value={parts.amount} autoComplete="off" autoFocus={autoFocus}
        inputMode={keypad ? "none" : "decimal"} readOnly={keypad} placeholder="0" onChange={event => patch({ amount: sanitiseAmount(event.target.value, decimals) })} />
    </div>
  );
  const pad = keypad && <NumericKeypad extra={["."]} onKey={key => patch({ amount: sanitiseAmount(parts.amount + key, kind === "quantity" ? 3 : 2) })} onBackspace={() => patch({ amount: parts.amount.slice(0, -1) })} />;
  const chips = (values: readonly string[], format: (unit: string) => string) => (
    <div className="fi-chips" role="radiogroup" aria-label={t("fiUnit")}>
      {values.map(unit => (
        <button key={unit} type="button" role="radio" aria-checked={parts.unit === unit} className={parts.unit === unit ? "fi-chip is-selected" : "fi-chip"} onClick={() => patch({ unit })}>{format(unit)}</button>
      ))}
    </div>
  );

  switch (kind) {
    case "mrp":
      return <div className="fi">{amountField(2, "₹", label)}{pad}{preview}{toggle}</div>;
    case "usp":
      return <div className="fi">{amountField(2, "₹", label)}{pad}{chips(USP_UNITS, unit => fill(t("fiPer"), { unit: unitLabel(unit) }))}{preview}{toggle}</div>;
    case "quantity":
      return <div className="fi">{amountField(3, null, label)}{pad}{chips(QUANTITY_UNITS, unitLabel)}{preview}{toggle}</div>;
    case "date":
      return (
        <div className="fi">
          {allowsShelfLife(field) && (
            <div className="fi-chips" role="radiogroup" aria-label={t("fiDateMode")}>
              <button type="button" role="radio" aria-checked={!parts.shelfLife} className={!parts.shelfLife ? "fi-chip is-selected" : "fi-chip"} onClick={() => patch({ shelfLife: false })}>{t("fiModeMonthYear")}</button>
              <button type="button" role="radio" aria-checked={parts.shelfLife} className={parts.shelfLife ? "fi-chip is-selected" : "fi-chip"} onClick={() => patch({ shelfLife: true })}>{t("fiModeMonths")}</button>
            </div>
          )}
          {shelf ? (
            <>
              <div className="fi-amount">
                <Input className="fi-amount__input" aria-label={t("fiMonthsFromManufacture")} value={parts.months} inputMode={keypad ? "none" : "numeric"} readOnly={keypad} autoComplete="off" autoFocus={autoFocus} placeholder="0"
                  onChange={event => patch({ months: event.target.value.replace(/\D/g, "").slice(0, 2) })} />
                <span className="fi-suffix">{t("fiMonthsFromManufacture")}</span>
              </div>
              {keypad && <NumericKeypad extra={[]} onKey={key => patch({ months: (parts.months + key).replace(/\D/g, "").slice(0, 2) })} onBackspace={() => patch({ months: parts.months.slice(0, -1) })} />}
            </>
          ) : (
            <div className="fi-date">
              <Select aria-label={t("fiMonth")} aria-invalid={invalid || undefined} value={parts.month} autoFocus={autoFocus} onChange={event => patch({ month: Number(event.target.value) })}>
                <option value={0}>{t("fiMonth")}</option>
                {Array.from({ length: 12 }, (_, index) => <option key={index} value={index + 1}>{monthName(index, locale)}</option>)}
              </Select>
              <Select aria-label={t("fiYear")} value={parts.year} onChange={event => patch({ year: Number(event.target.value) })}>
                {yearOptions(now).map(year => <option key={year} value={year}>{year}</option>)}
              </Select>
            </div>
          )}
          {preview}{toggle}
        </div>
      );
    case "phone":
      return <div className="fi"><Input type="tel" inputMode="tel" autoComplete="tel" aria-label={label} aria-invalid={invalid || undefined} autoFocus={autoFocus} maxLength={60} value={text} placeholder="1800 123 4567" onChange={event => { setText(event.target.value); onChange(event.target.value); }} />{toggle}</div>;
    case "email":
      return <div className="fi"><Input type="email" inputMode="email" autoComplete="email" aria-label={label} aria-invalid={invalid || undefined} autoFocus={autoFocus} maxLength={120} value={text} placeholder="care@example.com" onChange={event => { setText(event.target.value); onChange(event.target.value); }} /></div>;
    case "url":
      return <div className="fi"><Input type="url" inputMode="url" autoComplete="url" aria-label={label} aria-invalid={invalid || undefined} autoFocus={autoFocus} maxLength={200} value={text} placeholder="www.example.com" onChange={event => { setText(event.target.value); onChange(event.target.value); }} /></div>;
    case "fssai":
      return <div className="fi"><Input inputMode="numeric" autoComplete="off" aria-label={label} aria-invalid={invalid || undefined} autoFocus={autoFocus} maxLength={20} value={text} placeholder="14 digits" onChange={event => { setText(event.target.value); onChange(event.target.value); }} />{toggle}</div>;
    case "yesno":
      return (
        <div className="fi">
          <div className="fi-chips" role="radiogroup" aria-label={label}>
            {(["yes", "no"] as const).map(answer => (
              <button key={answer} type="button" role="radio" aria-checked={parts.unit === answer} className={parts.unit === answer ? "fi-chip is-selected" : "fi-chip"} onClick={() => { setParts({ ...parts, unit: answer }); onChange(answer); }}>{t(answer === "yes" ? "fiYes" : "fiNo")}</button>
            ))}
          </div>
        </div>
      );
    default:
      return <div className="fi"><Input aria-label={label} aria-invalid={invalid || undefined} autoFocus={autoFocus} maxLength={300} autoComplete="off" value={text} onChange={event => { setText(event.target.value); onChange(event.target.value); }} /></div>;
  }
}

function monthName(index: number, locale: string): string {
  try { return new Intl.DateTimeFormat(locale, { month: "long" }).format(new Date(2026, index, 1)); } catch { return String(index + 1); }
}
