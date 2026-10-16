import { readdir, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const localeDirectory = fileURLToPath(new URL("../src/locales/", import.meta.url));
const requiredLocales = [
  "en", "hi", "as", "bn", "brx", "doi", "gu", "kn", "ks", "kok", "mai", "ml",
  "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur",
];
const requiredAuthKeys = [
  "login",
  "username",
  "password",
  "signIn",
  "signingIn",
  "invalidCredentials",
  "sessionExpired",
  "logout",
  "welcome",
  "startNewInspection",
  "authenticationLoading",
  "authenticationError",
  "authHeadline",
  "authDescription",
  "authFeatureAi",
  "authFeatureOcr",
  "authFeatureCompliance",
  "register",
  "createAccount",
  "email",
  "confirmPassword",
  "showPassword",
  "hidePassword",
  "registerAccount",
  "registering",
  "passwordsDoNotMatch",
  "invalidRegistration",
  "registrationError",
  // Sign-in page and offline password reset
  "welcomeBack",
  "signInSubtitle",
  "forgotPassword",
  "keepSignedIn",
  "newManufacturer",
  "createManufacturerAccount",
  "backToSignIn",
  "lockoutNote",
  "offlineFooter",
  "portalPill",
  "statusReady",
  "statusBackendOffline",
  "statusDatabaseUnavailable",
  "resetTitle",
  "resetIntro",
  "requestResetCode",
  "resetOfflineNote",
  "enterCodeTitle",
  "codeFor",
  "resetCodeLabel",
  "newPassword",
  "setNewPassword",
  "passwordUpdated",
  "signedOutEverywhere",
  "invalidResetCode",
];

const localeFiles = new Set(
  (await readdir(localeDirectory))
    .filter((file) => file.endsWith(".json"))
    .map((file) => file.slice(0, -".json".length)),
);
const unexpectedLocales = [...localeFiles].filter((locale) => !requiredLocales.includes(locale));
const missingLocaleFiles = requiredLocales.filter((locale) => !localeFiles.has(locale));
const missingByLocale = new Map();

for (const locale of requiredLocales.filter((name) => localeFiles.has(name))) {
  const contents = JSON.parse(await readFile(`${localeDirectory}${locale}.json`, "utf8"));
  const missingKeys = requiredAuthKeys.filter((key) => !(key in contents));
  if (missingKeys.length) missingByLocale.set(locale, missingKeys);
}

console.log(`Locales checked: ${requiredLocales.length}/23`);
console.log(`Locales with missing auth keys: ${missingByLocale.size + missingLocaleFiles.length}`);
if (missingLocaleFiles.length) console.log(`Missing locale files: ${missingLocaleFiles.join(", ")}`);
for (const [locale, keys] of missingByLocale) console.log(`${locale}: ${keys.join(", ")}`);
if (!missingLocaleFiles.length && !missingByLocale.size) console.log("Missing keys: none");
if (unexpectedLocales.length) console.log(`Unexpected locale files: ${unexpectedLocales.join(", ")}`);

if (missingLocaleFiles.length || missingByLocale.size || unexpectedLocales.length) process.exitCode = 1;
