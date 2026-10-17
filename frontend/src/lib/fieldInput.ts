/**
 * What an inspector types for each correctable field, and how it is turned into the text the API receives.
 *
 * The composed text is exactly what the backend's parsers accept ("₹480", "₹0.48 per ml", "500 g", "08/2026",
 * "9 months"), so the server stays the source of truth: these helpers only build the text and preview it.
 */

export type FieldKind = "mrp" | "usp" | "quantity" | "date" | "phone" | "email" | "url" | "fssai" | "yesno" | "text";

const DATE_FIELDS = ["manufacturing_date", "packing_date", "expiry_date", "best_before", "use_by"];
/** Date fields a label may state as a shelf life ("Best before 9 months from manufacture"). */
const SHELF_LIFE_FIELDS = ["best_before", "use_by"];

export function fieldKind(field: string): FieldKind {
  if (field === "mrp") return "mrp";
  if (field === "unit_sale_price") return "usp";
  if (field === "net_quantity") return "quantity";
  if (DATE_FIELDS.includes(field)) return "date";
  if (field === "consumer_care_phone") return "phone";
  if (field === "consumer_care_email") return "email";
  if (field === "consumer_care_website") return "url";
  if (field === "fssai_license") return "fssai";
  if (field === "tax_inclusive") return "yesno";
  return "text";
}

/** Kinds that have a structured input and can be switched to free text. */
export const hasFullKeyboardToggle = (kind: FieldKind) => ["mrp", "usp", "quantity", "date", "phone", "fssai"].includes(kind);
export const allowsShelfLife = (field: string) => SHELF_LIFE_FIELDS.includes(field);

/** The unit-price units, as the text after "per". */
export const USP_UNITS = ["g", "100 g", "kg", "ml", "100 ml", "L", "unit", "piece"] as const;
export const QUANTITY_UNITS = ["g", "kg", "ml", "L", "units", "pieces"] as const;

/** Keep digits and at most one decimal point, with at most `decimals` places (`null`: any number). */
export function sanitiseAmount(raw: string, decimals: number | null = 2): string {
  const cleaned = raw.replace(/,/g, ".").replace(/[^\d.]/g, "");
  const [whole, ...rest] = cleaned.split(".");
  if (!rest.length) return whole.replace(/^0+(?=\d)/, "");
  const fraction = rest.join("");
  return `${whole.replace(/^0+(?=\d)/, "") || "0"}.${decimals === null ? fraction : fraction.slice(0, decimals)}`;
}

const complete = (amount: string, decimals = 2) => new RegExp(`^\\d+(\\.\\d{1,${decimals}})?$`).test(amount);

export const composeMrp = (amount: string) => (amount ? `₹${amount}` : "");
export const previewMrp = (amount: string) => (complete(amount) && Number(amount) > 0 ? `₹${Number(amount).toFixed(2)}` : "");

export const composeUsp = (amount: string, unit: string) => (amount && unit ? `₹${amount} per ${unit}` : "");
export const previewUsp = (amount: string, unit: string) => (complete(amount) && Number(amount) > 0 && unit ? `₹${amount} per ${unit}` : "");

export const composeQuantity = (amount: string, unit: string) => (amount && unit ? `${amount} ${unit}` : "");
export const previewQuantity = (amount: string, unit: string) => (complete(amount, 3) && Number(amount) > 0 && unit ? `${amount} ${unit}` : "");

export const composeDate = (month: number, year: number | null) => (month && year ? `${String(month).padStart(2, "0")}/${year}` : "");
export const composeShelfLife = (months: string) => (months ? `${months} months` : "");

/** "Aug 2026" for a month/year value; anything else is shown as typed. */
export function previewDate(value: string, locale: string): string {
  const monthYear = /^(\d{1,2})\/(\d{4})$/.exec(value);
  if (!monthYear || Number(monthYear[1]) < 1 || Number(monthYear[1]) > 12) return value;
  try { return new Intl.DateTimeFormat(locale, { month: "short", year: "numeric" }).format(new Date(Number(monthYear[2]), Number(monthYear[1]) - 1, 1)); } catch { return value; }
}

/** The years offered in the year picker: a few back (old stock) and well ahead (shelf life). */
export function yearOptions(now = new Date()): number[] {
  const year = now.getFullYear();
  return Array.from({ length: 22 }, (_, index) => year + 12 - index);
}
