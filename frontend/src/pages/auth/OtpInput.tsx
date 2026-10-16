import { ClipboardEvent, KeyboardEvent, useId, useRef } from "react";
import { fill, useI18n } from "../../i18n";

export const OTP_LENGTH = 6;

type OtpInputProps = { value: string[]; onChange: (digits: string[]) => void; invalid?: boolean; disabled?: boolean };

/** Six digit boxes with one group label: typing advances, Backspace steps back, and a pasted code fills every box. */
export function OtpInput({ value, onChange, invalid = false, disabled = false }: OtpInputProps) {
  const { t } = useI18n();
  const labelId = useId();
  const boxes = useRef<(HTMLInputElement | null)[]>([]);
  const focus = (index: number) => boxes.current[Math.max(0, Math.min(OTP_LENGTH - 1, index))]?.focus();

  /** Write digits starting at `index` (a paste or an autofilled code may carry several). */
  function put(index: number, digits: string) {
    const clean = digits.replace(/\D/g, "").slice(0, OTP_LENGTH - index);
    if (!clean) return false;
    const next = [...value];
    for (let offset = 0; offset < clean.length; offset++) next[index + offset] = clean[offset];
    onChange(next);
    focus(index + clean.length);
    return true;
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>, index: number) {
    if (event.key === "Backspace") {
      event.preventDefault();
      const next = [...value];
      if (next[index]) { next[index] = ""; onChange(next); }
      else if (index > 0) { next[index - 1] = ""; onChange(next); focus(index - 1); }
    } else if (event.key === "ArrowLeft") { event.preventDefault(); focus(index - 1); }
    else if (event.key === "ArrowRight") { event.preventDefault(); focus(index + 1); }
  }

  function onPaste(event: ClipboardEvent<HTMLInputElement>, index: number) {
    event.preventDefault();
    const text = event.clipboardData.getData("text");
    // A whole code always fills from the first box, wherever it was pasted.
    put(text.replace(/\D/g, "").length >= OTP_LENGTH ? 0 : index, text);
  }

  return (
    <div className="otp">
      <span className="form-field__label" id={labelId}>{t("resetCodeLabel")}</span>
      <div className={`otp__boxes${invalid ? " is-invalid" : ""}`} role="group" aria-labelledby={labelId}>
        {Array.from({ length: OTP_LENGTH }, (_, index) => (
          <input key={index} ref={element => { boxes.current[index] = element; }}
            className="input otp__box mono" inputMode="numeric" pattern="[0-9]*" maxLength={OTP_LENGTH}
            autoComplete={index === 0 ? "one-time-code" : "off"} disabled={disabled} aria-invalid={invalid || undefined}
            aria-label={fill(t("otpDigit"), { index: index + 1, count: OTP_LENGTH })} value={value[index] ?? ""}
            onFocus={event => event.currentTarget.select()}
            onKeyDown={event => onKeyDown(event, index)} onPaste={event => onPaste(event, index)}
            onChange={event => {
              const typed = event.target.value.replace(value[index] ?? "", "") || event.target.value;
              if (!put(index, typed)) { const next = [...value]; next[index] = ""; onChange(next); }
            }} />
        ))}
      </div>
    </div>
  );
}
