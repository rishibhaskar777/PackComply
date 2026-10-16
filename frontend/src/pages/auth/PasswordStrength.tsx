import { TranslationKey, fill, useI18n } from "../../i18n";

const LEVELS: TranslationKey[] = ["strengthWeak", "strengthWeak", "strengthFair", "strengthGood", "strengthStrong"];

/** 0–4: length first, then character variety. Under 8 characters, or equal to the username, is always weak. */
export function passwordScore(password: string, username = ""): number {
  if (!password) return 0;
  let score = 0;
  if (password.length >= 8) score++;
  if (password.length >= 12) score++;
  if (/[a-z]/.test(password) && /[A-Z]/.test(password)) score++;
  if (/\d/.test(password) && /[^A-Za-z0-9]/.test(password)) score++;
  else if (/\d|[^A-Za-z0-9]/.test(password) && password.length >= 10) score++;
  if (password.length < 8 || (username && password.trim().toLowerCase() === username.trim().toLowerCase())) return Math.min(score, 1);
  return Math.max(1, Math.min(4, score));
}

/** Four-segment meter with its level in words, and the policy hint under it. */
export function PasswordStrength({ password, username, hintId }: { password: string; username: string; hintId: string }) {
  const { t } = useI18n();
  const score = passwordScore(password, username);
  const filled = password ? Math.max(1, score) : 0;
  const tone = filled <= 1 ? "weak" : filled === 2 ? "fair" : filled === 3 ? "good" : "strong";
  return (
    <div className="strength">
      <div className="strength__row">
        <span className={`strength__bars strength__bars--${tone}`} aria-hidden="true">
          {[1, 2, 3, 4].map(segment => <span key={segment} className={segment <= filled ? "strength__bar is-filled" : "strength__bar"} />)}
        </span>
        {password && <span className="strength__label" aria-live="polite">{fill(t("passwordStrength"), { level: t(LEVELS[score]) })}</span>}
      </div>
      <p className="form-field__hint" id={hintId}>{t("passwordHint")}</p>
    </div>
  );
}
