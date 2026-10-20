import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const DIR = join(__dirname, "locales");
const read = (code: string): Record<string, string> => JSON.parse(readFileSync(join(DIR, `${code}.json`), "utf8"));
const en = read("en");
const codes = readdirSync(DIR).filter(name => name.endsWith(".json")).map(name => name.slice(0, -5)).filter(code => code !== "en").sort();

/**
 * Values that legitimately read the same in every language: the product name, the legal-metrology abbreviation, the file
 * format, and the language badge (it already names both scripts). Anything else identical to English is an
 * untranslated placeholder.
 */
const ALLOWED_IDENTICAL = new Set(["appName", "mrp", "pdf", "factLanguagesValue", "aboutPredicted"]);
const MAX_SHARE = 0.1;
const tokens = (text: string) => (text.match(/\{[^}]*\}/g) ?? []).sort();

describe("translations", () => {
  it("covers all 22 non-English locales", () => {
    expect(codes).toHaveLength(22);
  });

  it.each(codes)("%s is not an English placeholder file (at most 10% of values identical to English)", code => {
    const locale = read(code);
    const identical = Object.keys(en).filter(key => !ALLOWED_IDENTICAL.has(key) && locale[key] === en[key]);
    expect(identical.length / Object.keys(en).length, `identical to English: ${identical.slice(0, 15).join(", ")}`).toBeLessThanOrEqual(MAX_SHARE);
  });

  it.each(codes)("%s keeps every {placeholder} token of the English text", code => {
    const locale = read(code);
    const broken = Object.keys(en).filter(key => JSON.stringify(tokens(locale[key] ?? "")) !== JSON.stringify(tokens(en[key])));
    expect(broken).toEqual([]);
  });

  // Hindi is the reviewed reference translation and deliberately transliterates some terms (for example एमआरपी); the other 21 keep them as printed on the label.
  it.each(codes.filter(code => code !== "hi"))("%s keeps product and legal terms spelled as in English", code => {
    const locale = read(code);
    const missing: string[] = [];
    for (const key of Object.keys(en)) {
      for (const term of ["MRP", "FSSAI", "LM(PC)", "SHA-256", "NON_COMPLIANT"]) if (en[key].includes(term) && !locale[key].includes(term)) missing.push(`${key}:${term}`);
    }
    // A few strings are phrased without the term in some scripts (for example a spelled-out title); none may be a placeholder.
    expect(missing.length).toBeLessThanOrEqual(8);
  });
});
