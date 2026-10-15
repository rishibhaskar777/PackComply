import { KeyboardEvent, useId, useRef } from "react";
import { AnimatePresence, m } from "motion/react";
import { LucideIcon, Monitor, Moon, Sun } from "lucide-react";
import { Button } from "../components/ui";
import { TranslationKey, useI18n } from "../i18n";
import { SPRING, useReducedMotion } from "../lib/motion";
import { originOf, THEME_ORDER, ThemePreference, useTheme } from "../theme/theme";

export const THEME_LABELS: Record<ThemePreference, TranslationKey> = { light: "themeLight", dark: "themeDark", system: "themeSystem" };
const THEME_ICONS: Record<ThemePreference, LucideIcon> = { light: Sun, dark: Moon, system: Monitor };

/** Top-bar button: cycles Light → Dark → System; the icon rotates and morphs into the next one. */
export function ThemeToggle({ className = "" }: { className?: string }) {
  const { t } = useI18n();
  const { preference, cycle } = useTheme();
  const reduced = useReducedMotion();
  const button = useRef<HTMLButtonElement>(null);
  const Icon = THEME_ICONS[preference];
  const label = `${t("themeToggle")}: ${t(THEME_LABELS[preference])}`;
  return (
    <Button ref={button} variant="ghost" size="icon" className={`theme-toggle ${className}`.trim()} aria-label={label} title={label}
      onClick={() => cycle(originOf(button.current))}
      icon={
        <AnimatePresence mode="popLayout" initial={false}>
          <m.span key={preference} className="theme-toggle__icon" data-theme-icon={preference}
            initial={reduced ? false : { rotate: -90, scale: 0.4, opacity: 0 }} animate={{ rotate: 0, scale: 1, opacity: 1 }}
            exit={reduced ? { opacity: 0, transition: { duration: 0 } } : { rotate: 90, scale: 0.4, opacity: 0 }} transition={SPRING}>
            <Icon size={16} aria-hidden="true" />
          </m.span>
        </AnimatePresence>
      } />
  );
}

/** User-menu control: a radio group of the three preferences with a sliding selection pill. */
export function ThemeSwitcher() {
  const { t } = useI18n();
  const { preference, setPreference } = useTheme();
  const pillId = useId();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  function choose(next: ThemePreference, index: number) { setPreference(next, originOf(refs.current[index])); }
  function onKeyDown(event: KeyboardEvent, index: number) {
    const step = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const next = (index + step + THEME_ORDER.length) % THEME_ORDER.length;
    choose(THEME_ORDER[next], next);
    refs.current[next]?.focus();
  }
  return (
    <div className="theme-switcher">
      <span className="theme-switcher__label" id={`${pillId}-label`}>{t("theme")}</span>
      <div className="tabs tabs--stretch" role="radiogroup" aria-labelledby={`${pillId}-label`}>
        {THEME_ORDER.map((option, index) => {
          const Icon = THEME_ICONS[option];
          const checked = option === preference;
          return (
            <button key={option} ref={element => { refs.current[index] = element; }} type="button" role="radio" aria-checked={checked} tabIndex={checked ? 0 : -1}
              className={checked ? "tabs__tab is-active" : "tabs__tab"} onClick={() => choose(option, index)} onKeyDown={event => onKeyDown(event, index)}>
              {checked && <m.span layoutId={`theme-pill-${pillId}`} className="tabs__pill" transition={SPRING} aria-hidden="true" />}
              <span className="tabs__label"><Icon size={14} aria-hidden="true" /><span>{t(THEME_LABELS[option])}</span></span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
