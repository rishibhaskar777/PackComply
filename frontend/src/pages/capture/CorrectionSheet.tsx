import { FormEvent, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { Alert, Button, FormField, Select } from "../../components/ui";
import { fill, useI18n } from "../../i18n";
import { CorrectionPayload, FIELD_LABELS, REASONS, REASON_TEXT } from "../record/Findings";
import { FieldInput } from "../record/FieldInput";

type Props = {
  fields: string[];
  onSubmit: (payload: CorrectionPayload) => Promise<string | null>;
  onClose: () => void;
};

/** Bottom sheet to enter a value with the input that suits the field: number pad for amounts, unit chips, month and year, or the normal keyboard. */
export function CorrectionSheet({ fields, onSubmit, onClose }: Props) {
  const { t } = useI18n();
  const [field, setField] = useState(fields[0]);
  const [value, setValue] = useState("");
  const [reason, setReason] = useState<(typeof REASONS)[number]>("recReasonPhysical");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const previous = useRef<HTMLElement | null>(document.activeElement as HTMLElement | null);

  useEffect(() => {
    const opener = previous.current;
    panel.current?.querySelector<HTMLElement>("input, select, button.fi-chip")?.focus();
    return () => opener?.focus?.();
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!value.trim()) { setError(t("recValueRequired")); return; }
    setBusy(true); setError(null);
    const message = await onSubmit({ field_name: field, corrected_value: value.trim(), reason: REASON_TEXT[reason] });
    setBusy(false);
    if (message) setError(message); else onClose();
  }

  return createPortal(
    <div className="cap-modal">
      <div className="cap-modal__scrim" onClick={onClose} aria-hidden="true" />
      <div ref={panel} className="cap-modal__panel" role="dialog" aria-modal="true" aria-labelledby="correction-title" tabIndex={-1}
        onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); onClose(); } }}>
        <header className="cap-modal__header">
          <h2 id="correction-title">{fill(t("resEnterFor"), { field: t(FIELD_LABELS[field] ?? "productInformation") })}</h2>
          <button type="button" className="cap-icon-button cap-icon-button--plain" aria-label={t("dismiss")} onClick={onClose}><X size={20} aria-hidden="true" /></button>
        </header>
        <form onSubmit={submit} className="cap-modal__form">
          {error && <Alert>{error}</Alert>}
          {fields.length > 1 && (
            <FormField label={t("recFieldLabel")} htmlFor="cap-field">
              <Select id="cap-field" value={field} onChange={event => { setField(event.target.value); setValue(""); setError(null); }}>
                {fields.map(name => <option key={name} value={name}>{t(FIELD_LABELS[name] ?? "productInformation")}</option>)}
              </Select>
            </FormField>
          )}
          <div className="form-field">
            <span className="form-field__label">{t("recValueLabel")}</span>
            <FieldInput key={field} field={field} label={t(FIELD_LABELS[field] ?? "productInformation")} keypad invalid={Boolean(error)} onChange={setValue} />
          </div>
          <FormField label={t("recReasonLabel")} htmlFor="cap-reason">
            <Select id="cap-reason" value={reason} onChange={event => setReason(event.target.value as (typeof REASONS)[number])}>
              {REASONS.map(key => <option key={key} value={key}>{t(key)}</option>)}
            </Select>
          </FormField>
          <div className="cap-modal__actions">
            <Button type="button" size="lg" variant="ghost" className="cap-btn" onClick={onClose} disabled={busy}>{t("recCancel")}</Button>
            <Button type="submit" size="lg" className="cap-btn" loading={busy}>{t("recSaveValue")}</Button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  );
}
