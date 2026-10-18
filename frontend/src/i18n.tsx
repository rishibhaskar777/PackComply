import { createContext, ReactNode, useContext, useEffect, useMemo, useState } from "react";
import en from "./locales/en.json";

export const UI_LANGUAGES = [
  { code: "en", nativeName: "English", englishName: "English" }, { code: "hi", nativeName: "हिन्दी", englishName: "Hindi" }, { code: "as", nativeName: "অসমীয়া", englishName: "Assamese" }, { code: "bn", nativeName: "বাংলা", englishName: "Bengali" }, { code: "brx", nativeName: "बड़ो", englishName: "Bodo" }, { code: "doi", nativeName: "डोगरी", englishName: "Dogri" }, { code: "gu", nativeName: "ગુજરાતી", englishName: "Gujarati" }, { code: "kn", nativeName: "ಕನ್ನಡ", englishName: "Kannada" }, { code: "ks", nativeName: "کٲشُر", englishName: "Kashmiri" }, { code: "kok", nativeName: "कोंकणी", englishName: "Konkani" }, { code: "mai", nativeName: "मैथिली", englishName: "Maithili" }, { code: "ml", nativeName: "മലയാളം", englishName: "Malayalam" }, { code: "mni", nativeName: "মৈতৈলোন্", englishName: "Manipuri (Meitei)" }, { code: "mr", nativeName: "मराठी", englishName: "Marathi" }, { code: "ne", nativeName: "नेपाली", englishName: "Nepali" }, { code: "or", nativeName: "ଓଡ଼ିଆ", englishName: "Odia" }, { code: "pa", nativeName: "ਪੰਜਾਬੀ", englishName: "Punjabi" }, { code: "sa", nativeName: "संस्कृतम्", englishName: "Sanskrit" }, { code: "sat", nativeName: "ᱥᱟᱱᱛᱟᱲᱤ", englishName: "Santali" }, { code: "sd", nativeName: "سنڌي", englishName: "Sindhi" }, { code: "ta", nativeName: "தமிழ்", englishName: "Tamil" }, { code: "te", nativeName: "తెలుగు", englishName: "Telugu" }, { code: "ur", nativeName: "اردو", englishName: "Urdu" },
] as const;
export type Locale = (typeof UI_LANGUAGES)[number]["code"];
export const LANGUAGE_STORAGE_KEY = "packcomply-language";

export type TranslationKey = keyof typeof en;
type LocaleTranslations = Record<TranslationKey, string>;

function defineLocale(locale: Record<string, string>): LocaleTranslations {
  const englishKeys = Object.keys(en).sort();
  const localeKeys = Object.keys(locale).sort();
  if (englishKeys.length !== localeKeys.length || englishKeys.some((key, index) => key !== localeKeys[index])) {
    throw new Error("Each locale must provide every UI translation key.");
  }
  return locale as LocaleTranslations;
}

// Only English ships in the entry bundle. Every other language is its own chunk, fetched the first time it is selected and
// cached afterwards. A selected locale is never merged with English: all UI values come directly from that locale's complete,
// reviewed translation file.
const localeLoaders = import.meta.glob<{ default: Record<string, string> }>(["./locales/*.json", "!./locales/en.json"]);
const loaded = new Map<Locale, LocaleTranslations>([["en", defineLocale(en)]]);

/** The translations for a locale if they are already in memory (English always is). */
export function getLoadedLocale(locale: Locale): LocaleTranslations | undefined { return loaded.get(locale); }

/** Fetch (once) and validate a locale's translations. */
export async function loadLocale(locale: Locale): Promise<LocaleTranslations> {
  const cached = loaded.get(locale);
  if (cached) return cached;
  const loader = localeLoaders[`./locales/${locale}.json`];
  if (!loader) throw new Error(`Unknown locale: ${locale}`);
  const translations = defineLocale((await loader()).default);
  loaded.set(locale, translations);
  return translations;
}

type I18nValue = {
  /** The language the user chose; strings switch to it as soon as its file has loaded. */
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: TranslationKey) => string;
};

const I18nContext = createContext<I18nValue | null>(null);

function initialLocale(): Locale {
  let saved: string | null = null;
  try { saved = localStorage.getItem(LANGUAGE_STORAGE_KEY); } catch { /* storage blocked */ }
  return UI_LANGUAGES.some(({ code }) => code === saved) ? saved as Locale : "en";
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);
  // The dictionary in use. It trails `locale` by the time it takes to fetch the language file, so the screen never
  // shows a half-translated mixture; English is the fallback if a file cannot be fetched.
  const [translations, setTranslations] = useState<LocaleTranslations | undefined>(() => getLoadedLocale(locale));
  const [applied, setApplied] = useState<Locale>(translations ? locale : "en");

  useEffect(() => {
    try { localStorage.setItem(LANGUAGE_STORAGE_KEY, locale); } catch { /* storage blocked */ }
  }, [locale]);

  useEffect(() => {
    let current = true;
    const ready = getLoadedLocale(locale);
    if (ready) { setTranslations(ready); setApplied(locale); return; }
    loadLocale(locale)
      .then(next => { if (current) { setTranslations(next); setApplied(locale); } })
      .catch(() => { if (current) { setTranslations(getLoadedLocale("en")); setApplied("en"); } });
    return () => { current = false; };
  }, [locale]);

  useEffect(() => {
    document.documentElement.lang = applied;
    document.documentElement.dir = ["ks", "sd", "ur"].includes(applied) ? "rtl" : "ltr";
  }, [applied]);

  const value = useMemo<I18nValue>(() => {
    const dictionary = translations ?? loaded.get("en")!;
    return { locale, setLocale: setLocaleState, t: key => dictionary[key] };
  }, [locale, translations]);

  // A saved non-English language is fetched before the first paint of the UI, so it never flashes English.
  if (!translations) return null;
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const context = useContext(I18nContext);
  if (!context) throw new Error("useI18n must be used inside I18nProvider.");
  return context;
}

/** Replace {name} placeholders in a translated template. */
export function fill(template: string, values: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (match, name: string) => (name in values ? String(values[name]) : match));
}
