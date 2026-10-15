import { useId } from "react";
import { Languages } from "lucide-react";
import { Select } from "./ui";
import { Locale, UI_LANGUAGES, useI18n } from "../i18n";

export function LanguageSelector({ compact = false }: { compact?: boolean }) {
  const { locale, setLocale, t } = useI18n();
  const id = useId();
  return (
    <div className={compact ? "language-selector language-selector--compact" : "language-selector"}>
      <label htmlFor={id} className="language-selector__label"><Languages size={16} aria-hidden="true" /><span>{t("language")}</span></label>
      <Select id={id} value={locale} onChange={event => setLocale(event.target.value as Locale)}>
        {UI_LANGUAGES.map(language => <option key={language.code} value={language.code}>{language.nativeName} — {language.englishName}</option>)}
      </Select>
    </div>
  );
}
