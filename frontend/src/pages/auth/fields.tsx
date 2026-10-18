import { ReactNode } from "react";
import { Eye, EyeOff } from "lucide-react";
import { Button } from "../../components/ui";
import { useI18n } from "../../i18n";

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
