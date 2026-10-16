import { FormEvent, ReactNode, useEffect, useId, useRef, useState } from "react";
import { AnimatePresence, m } from "motion/react";
import { ArrowLeft, Check, Eye, EyeOff, Info, KeyRound, Lock, User } from "lucide-react";
import { api, ApiError, isUnavailable } from "../../api/client";
import { Alert, Button, Input } from "../../components/ui";
import { fill, TranslationKey, useI18n } from "../../i18n";
import { DURATION, EASE } from "../../lib/motion";
import { OTP_LENGTH, OtpInput } from "./OtpInput";
import { PasswordStrength } from "./PasswordStrength";

export const RESET_CODE_MINUTES = 15;
type Step = "request" | "code" | "done";

const post = (path: string, body: unknown) => api(path, "password reset", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

/** Map a confirm failure to a message; the server's policy reasons are matched to translated text. */
function confirmError(error: unknown): TranslationKey {
  const { status, detail = "" } = error as ApiError;
  if (status === 400) return "invalidResetCode";
  if (status === 422) return /common/i.test(detail) ? "passwordTooCommon" : /username/i.test(detail) ? "passwordSameAsUsername" : "passwordTooShort";
  if (status === 429) return "tooManyAttempts";
  return isUnavailable(error) ? "serverUnavailable" : "resetError";
}

/** Three steps in the sign-in column: request a code, enter it with a new password, done. */
export function ForgotPassword({ onBack, reduced }: { onBack: () => void; reduced: boolean }) {
  const { t } = useI18n();
  const [step, setStep] = useState<Step>("request");
  const [username, setUsername] = useState("");
  const [digits, setDigits] = useState<string[]>(() => Array(OTP_LENGTH).fill(""));
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<TranslationKey | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const hintId = useId();

  // Move focus to the heading of each step, including on arrival from the sign-in form.
  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, [step]);

  function go(next: Step) { setError(null); setStep(next); }

  async function requestCode(event: FormEvent) {
    event.preventDefault();
    if (!username.trim()) return;
    setBusy(true); setError(null);
    try {
      await post("/auth/password-reset/request", { username: username.trim() });
      setDigits(Array(OTP_LENGTH).fill("")); setPassword("");
      go("code");
    } catch (e) {
      setError((e as ApiError).status === 429 ? "tooManyAttempts" : isUnavailable(e) ? "serverUnavailable" : "resetError");
    } finally { setBusy(false); }
  }

  async function confirm(event: FormEvent) {
    event.preventDefault();
    const code = digits.join("");
    if (code.length !== OTP_LENGTH) return setError("codeIncomplete");
    if (password.length < 8) return setError("passwordTooShort");
    if (password.trim().toLowerCase() === username.trim().toLowerCase()) return setError("passwordSameAsUsername");
    setBusy(true); setError(null);
    try {
      await post("/auth/password-reset/confirm", { username: username.trim(), code, new_password: password });
      go("done");
    } catch (e) {
      const message = confirmError(e);
      if (message === "invalidResetCode") setDigits(Array(OTP_LENGTH).fill(""));
      setError(message);
    } finally { setBusy(false); }
  }

  const title: Record<Step, TranslationKey> = { request: "resetTitle", code: "enterCodeTitle", done: "passwordUpdated" };
  const body: Record<Step, ReactNode> = {
    request: (
      <form className="signin-form" onSubmit={requestCode}>
        <IconField icon={<User size={16} />} label={t("username")} id="reset-username">
          <Input id="reset-username" className="signin-input" autoComplete="username" required value={username} onChange={e => setUsername(e.target.value)} />
        </IconField>
        {error && <Alert>{t(error)}</Alert>}
        <Button type="submit" className="signin-submit" loading={busy}>{busy ? t("requestingCode") : t("requestResetCode")}</Button>
        <p className="signin-note"><Info size={14} aria-hidden="true" /><span>{t("resetOfflineNote")}</span></p>
      </form>
    ),
    code: (
      <form className="signin-form" onSubmit={confirm} noValidate>
        <OtpInput value={digits} onChange={next => { setDigits(next); if (error === "invalidResetCode" || error === "codeIncomplete") setError(null); }} invalid={error === "invalidResetCode"} disabled={busy} />
        <div className="form-field">
          <label className="form-field__label" htmlFor="reset-password">{t("newPassword")}</label>
          <span className="icon-input">
            <Lock className="icon-input__icon" size={16} aria-hidden="true" />
            <Input id="reset-password" className="signin-input icon-input__control icon-input__control--toggle" type={showPassword ? "text" : "password"} autoComplete="new-password" required minLength={8}
              aria-describedby={hintId} value={password} onChange={e => setPassword(e.target.value)} />
            <PasswordToggle shown={showPassword} onToggle={() => setShowPassword(!showPassword)} />
          </span>
          <PasswordStrength password={password} username={username} hintId={hintId} />
        </div>
        {error && <Alert>{t(error)}</Alert>}
        <Button type="submit" className="signin-submit" loading={busy}>{busy ? t("settingPassword") : t("setNewPassword")}</Button>
        <button type="button" className="signin-link signin-link--center" onClick={() => go("request")}>{t("requestNewCode")}</button>
      </form>
    ),
    done: (
      <div className="signin-form">
        <Button className="signin-submit" onClick={onBack}>{t("backToSignIn")}</Button>
      </div>
    ),
  };

  const content = (
    <div className="signin-step">
      {step !== "done" && (
        <button type="button" className="signin-link signin-back" onClick={step === "code" ? () => go("request") : onBack}>
          <ArrowLeft size={16} aria-hidden="true" />{step === "code" ? t("back") : t("backToSignIn")}
        </button>
      )}
      <span className={`signin-tile signin-tile--${step === "done" ? "pass" : "primary"}`} aria-hidden="true">
        {step === "done" ? <Check size={22} strokeWidth={2.4} /> : <KeyRound size={20} />}
      </span>
      <div className="signin-heading">
        <h1 className="signin-title" ref={heading} tabIndex={-1}>{t(title[step])}</h1>
        <p className="signin-subtitle">
          {step === "request" ? t("resetIntro") : step === "code" ? fill(t("codeFor"), { username: username.trim(), minutes: RESET_CODE_MINUTES }) : t("signedOutEverywhere")}
        </p>
      </div>
      {body[step]}
    </div>
  );

  if (reduced) return <div key={step}>{content}</div>;
  return (
    <AnimatePresence mode="wait" initial={false}>
      <m.div key={step} initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0, transition: { duration: DURATION.base, ease: EASE } }} exit={{ opacity: 0, x: -16, transition: { duration: DURATION.fast, ease: EASE } }}>
        {content}
      </m.div>
    </AnimatePresence>
  );
}

/** Label above an input that carries a leading icon. */
export function IconField({ icon, label, id, labelAside, children }: { icon: ReactNode; label: ReactNode; id: string; labelAside?: ReactNode; children: ReactNode }) {
  return (
    <div className="form-field">
      <div className="form-field__row">
        <label className="form-field__label" htmlFor={id}>{label}</label>
        {labelAside}
      </div>
      <span className="icon-input">
        <span className="icon-input__icon" aria-hidden="true">{icon}</span>
        {children}
      </span>
    </div>
  );
}

export function PasswordToggle({ shown, onToggle }: { shown: boolean; onToggle: () => void }) {
  const { t } = useI18n();
  return (
    <Button variant="ghost" size="icon" className="icon-input__toggle" aria-label={shown ? t("hidePassword") : t("showPassword")} aria-pressed={shown} onClick={onToggle}
      icon={shown ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />} />
  );
}
