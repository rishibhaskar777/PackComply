import { createContext, ReactNode, useContext, useEffect, useMemo, useState } from "react";
import en from "./locales/en.json";
import hi from "./locales/hi.json";
import as from "./locales/as.json";
import bn from "./locales/bn.json";
import brx from "./locales/brx.json";
import doi from "./locales/doi.json";
import gu from "./locales/gu.json";
import kn from "./locales/kn.json";
import ks from "./locales/ks.json";
import kok from "./locales/kok.json";
import mai from "./locales/mai.json";
import ml from "./locales/ml.json";
import mni from "./locales/mni.json";
import mr from "./locales/mr.json";
import ne from "./locales/ne.json";
import or from "./locales/or.json";
import pa from "./locales/pa.json";
import sa from "./locales/sa.json";
import sat from "./locales/sat.json";
import sd from "./locales/sd.json";
import ta from "./locales/ta.json";
import te from "./locales/te.json";
import ur from "./locales/ur.json";

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

// A selected locale is never merged with English: all UI values must come
// directly from the selected locale's complete, reviewed translation file.
const translations: Record<Locale, LocaleTranslations> = {
  en: defineLocale(en), hi: defineLocale(hi), as: defineLocale(as), bn: defineLocale(bn),
  brx: defineLocale(brx), doi: defineLocale(doi), gu: defineLocale(gu), kn: defineLocale(kn),
  ks: defineLocale(ks), kok: defineLocale(kok), mai: defineLocale(mai), ml: defineLocale(ml),
  mni: defineLocale(mni), mr: defineLocale(mr), ne: defineLocale(ne), or: defineLocale(or),
  pa: defineLocale(pa), sa: defineLocale(sa), sat: defineLocale(sat), sd: defineLocale(sd),
  ta: defineLocale(ta), te: defineLocale(te), ur: defineLocale(ur),
};
export { translations };

type I18nValue = {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: TranslationKey) => string;
};

const I18nContext = createContext<I18nValue | null>(null);

function initialLocale(): Locale {
  const saved = localStorage.getItem(LANGUAGE_STORAGE_KEY);
  return UI_LANGUAGES.some(({ code }) => code === saved) ? saved as Locale : "en";
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocale] = useState<Locale>(initialLocale);

  useEffect(() => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, locale);
    document.documentElement.lang = locale;
    document.documentElement.dir = ["ks", "sd", "ur"].includes(locale) ? "rtl" : "ltr";
  }, [locale]);

  const value = useMemo<I18nValue>(() => ({
    locale,
    setLocale,
    t: (key) => translations[locale][key],
  }), [locale]);

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
