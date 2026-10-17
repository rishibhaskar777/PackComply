import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../../i18n";
import { composeDate, composeMrp, composeQuantity, composeUsp, fieldKind, previewMrp, previewUsp, sanitiseAmount } from "../../lib/fieldInput";
import en from "../../locales/en.json";
import { FieldInput } from "./FieldInput";

const mount = (field: string, props: Partial<Parameters<typeof FieldInput>[0]> = {}) => {
  const onChange = vi.fn();
  render(<I18nProvider><FieldInput field={field} label={field} onChange={onChange} {...props} /></I18nProvider>);
  return { onChange, last: () => onChange.mock.calls.at(-1)?.[0] as string };
};
const chip = (name: string | RegExp) => screen.getByRole("radio", { name });

describe("field input: pure helpers", () => {
  it("maps each correctable field to its input kind", () => {
    expect(["mrp", "unit_sale_price", "net_quantity", "expiry_date", "best_before", "consumer_care_phone", "consumer_care_email", "product_name", "manufacturer_address", "brand_owner", "country_of_origin", "batch_number", "tax_inclusive", "fssai_license"].map(fieldKind))
      .toEqual(["mrp", "usp", "quantity", "date", "date", "phone", "email", "text", "text", "text", "text", "text", "yesno", "fssai"]);
  });

  it("keeps amounts to digits and a decimal point with at most two places", () => {
    expect(sanitiseAmount("₹12.567")).toBe("12.56");
    expect(sanitiseAmount("12,5")).toBe("12.5");
    expect(sanitiseAmount("abc")).toBe("");
    expect(sanitiseAmount("007")).toBe("7");
    expect(sanitiseAmount(".5")).toBe("0.5");
    expect(sanitiseAmount("1.2345", null)).toBe("1.2345");
  });

  it("composes the text the backend parsers read, and previews only complete values", () => {
    expect(composeMrp("480")).toBe("₹480");
    expect(previewMrp("480")).toBe("₹480.00");
    expect(previewMrp("48.")).toBe("");
    expect(composeUsp("0.48", "ml")).toBe("₹0.48 per ml");
    expect(composeUsp("0.48", "")).toBe("");
    expect(previewUsp("0.48", "ml")).toBe("₹0.48 per ml");
    expect(composeQuantity("500", "g")).toBe("500 g");
    expect(composeDate(8, 2026)).toBe("08/2026");
    expect(composeDate(0, 2026)).toBe("");
  });
});

describe("field input: per field type", () => {
  afterEach(cleanup);

  it("MRP: a fixed ₹ prefix, a decimal keyboard, and the ₹ amount as the value with a normalised preview", () => {
    const { last } = mount("mrp");
    const input = screen.getByRole("textbox", { name: "mrp" }) as HTMLInputElement;
    expect(input.getAttribute("inputmode")).toBe("decimal");
    expect(document.querySelector(".fi-prefix")?.textContent).toBe("₹");
    fireEvent.change(input, { target: { value: "480" } });
    expect(last()).toBe("₹480");
    expect(screen.getByText("₹480.00")).toBeTruthy(); // the normalised preview
    fireEvent.change(input, { target: { value: "12.3456" } });
    expect(input.value).toBe("12.34"); // letters and extra decimals never get in
    fireEvent.change(input, { target: { value: "" } });
    expect(last()).toBe("");
  });

  it("MRP on the phone sheet: the on-screen number pad types into a field that does not raise the system keyboard", () => {
    const { last } = mount("mrp", { keypad: true });
    const input = screen.getByRole("textbox", { name: "mrp" }) as HTMLInputElement;
    expect(input.getAttribute("inputmode")).toBe("none");
    const pad = within(screen.getByRole("group", { name: en.resKeypad }));
    for (const key of ["1", "2", ".", "5", "0"]) fireEvent.click(pad.getByRole("button", { name: key }));
    expect(input.value).toBe("12.50");
    expect(last()).toBe("₹12.50");
    fireEvent.click(pad.getByRole("button", { name: en.resKeypadDelete }));
    expect(last()).toBe("₹12.5");
  });

  it("unit sale price: amount plus unit chips, previewed as '₹0.48 per ml'; incomplete until both are set", () => {
    const { last } = mount("unit_sale_price");
    const labels = screen.getAllByRole("radio").map(radio => radio.textContent);
    expect(labels).toEqual(["per g", "per 100 g", "per kg", "per ml", "per 100 ml", "per L", "per unit", "per piece"]);
    fireEvent.change(screen.getByRole("textbox", { name: "unit_sale_price" }), { target: { value: "0.48" } });
    expect(last()).toBe(""); // no unit yet
    fireEvent.click(chip("per ml"));
    expect(last()).toBe("₹0.48 per ml");
    expect(screen.getByText("₹0.48 per ml")).toBeTruthy();
    fireEvent.click(chip("per 100 ml"));
    expect(last()).toBe("₹0.48 per 100 ml");
    expect(chip("per 100 ml").getAttribute("aria-checked")).toBe("true");
    expect(chip("per ml").getAttribute("aria-checked")).toBe("false");
    fireEvent.click(chip("per L"));
    expect(last()).toBe("₹0.48 per L");
  });

  it("net quantity: a number with g / kg / ml / L / units / pieces chips", () => {
    const { last } = mount("net_quantity");
    expect(screen.getAllByRole("radio").map(radio => radio.textContent)).toEqual(["g", "kg", "ml", "L", "units", "pieces"]);
    fireEvent.change(screen.getByRole("textbox", { name: "net_quantity" }), { target: { value: "1.5" } });
    fireEvent.click(chip("L"));
    expect(last()).toBe("1.5 L");
    fireEvent.click(chip("pieces"));
    expect(last()).toBe("1.5 pieces");
    expect(document.querySelector(".fi-prefix")).toBeNull(); // no currency on a quantity
  });

  it("manufacture / expiry date: a month and year picker that produces MM/YYYY", () => {
    const { last } = mount("expiry_date");
    const month = screen.getByRole("combobox", { name: en.fiMonth });
    fireEvent.change(month, { target: { value: "8" } });
    fireEvent.change(screen.getByRole("combobox", { name: en.fiYear }), { target: { value: "2027" } });
    expect(last()).toBe("08/2027");
    expect(screen.getByText(/2027/, { selector: "strong" })).toBeTruthy(); // "Aug 2027"
    expect(screen.queryByRole("radiogroup", { name: en.fiDateMode })).toBeNull(); // an expiry date has no 'N months' form
  });

  it("best before: also offers 'N months from manufacture'", () => {
    const { last } = mount("best_before");
    fireEvent.click(chip(en.fiModeMonths));
    fireEvent.change(screen.getByRole("textbox", { name: en.fiMonthsFromManufacture }), { target: { value: "9" } });
    expect(last()).toBe("9 months");
    expect(screen.getByText("9 months from manufacture", { selector: "strong" })).toBeTruthy();
    fireEvent.click(chip(en.fiModeMonthYear));
    fireEvent.change(screen.getByRole("combobox", { name: en.fiMonth }), { target: { value: "3" } });
    expect(last()).toMatch(/^03\/\d{4}$/);
  });

  it("text fields use the normal keyboard: no pad, no chips, no toggle", () => {
    for (const field of ["product_name", "brand_owner", "manufacturer", "manufacturer_address", "packer", "importer_address", "country_of_origin", "batch_number"]) {
      const { last } = mount(field);
      const input = screen.getByRole("textbox", { name: field }) as HTMLInputElement;
      expect(input.getAttribute("inputmode")).toBeNull();
      fireEvent.change(input, { target: { value: "Parle Products Pvt. Ltd., Mumbai 400056" } });
      expect(last()).toBe("Parle Products Pvt. Ltd., Mumbai 400056");
      expect(screen.queryByRole("group", { name: en.resKeypad })).toBeNull();
      expect(screen.queryByRole("radio")).toBeNull();
      expect(screen.queryByRole("button", { name: en.fiUseFullKeyboard })).toBeNull();
      cleanup();
    }
  });

  it("consumer care: a phone keyboard for the number and an e-mail keyboard for the address", () => {
    const phone = mount("consumer_care_phone");
    const phoneInput = screen.getByRole("textbox", { name: "consumer_care_phone" }) as HTMLInputElement;
    expect(phoneInput.getAttribute("inputmode")).toBe("tel");
    expect(phoneInput.type).toBe("tel");
    fireEvent.change(phoneInput, { target: { value: "1800 123 4567" } });
    expect(phone.last()).toBe("1800 123 4567");
    cleanup();
    const email = mount("consumer_care_email");
    const emailInput = screen.getByRole("textbox", { name: "consumer_care_email" }) as HTMLInputElement;
    expect(emailInput.getAttribute("inputmode")).toBe("email");
    expect(emailInput.type).toBe("email");
    fireEvent.change(emailInput, { target: { value: "care@parle.example" } });
    expect(email.last()).toBe("care@parle.example");
  });

  it("tax inclusive: yes or no", () => {
    const { last } = mount("tax_inclusive");
    fireEvent.click(chip(en.fiYes));
    expect(last()).toBe("yes");
    fireEvent.click(chip(en.fiNo));
    expect(last()).toBe("no");
  });

  it("'Use full keyboard' switches a structured field to free text, validated by the server, and back", () => {
    const { last } = mount("unit_sale_price");
    fireEvent.change(screen.getByRole("textbox", { name: "unit_sale_price" }), { target: { value: "9.6" } });
    fireEvent.click(chip("per 100 g"));
    fireEvent.click(screen.getByRole("button", { name: en.fiUseFullKeyboard }));
    const free = screen.getByRole("textbox", { name: "unit_sale_price" }) as HTMLInputElement;
    expect(free.value).toBe("₹9.6 per 100 g"); // starts from what the guided input built
    expect(free.getAttribute("inputmode")).toBe("text");
    expect(screen.queryByRole("radio")).toBeNull();
    fireEvent.change(free, { target: { value: "Rs. 0.48/ml" } });
    expect(last()).toBe("Rs. 0.48/ml");
    fireEvent.click(screen.getByRole("button", { name: en.fiUseGuided }));
    expect(screen.getAllByRole("radio")).toHaveLength(8);
    expect(last()).toBe("₹9.6 per 100 g");
  });

  it("offers the full keyboard on every structured field", () => {
    for (const field of ["mrp", "unit_sale_price", "net_quantity", "expiry_date", "consumer_care_phone", "fssai_license"]) {
      mount(field);
      expect(screen.getByRole("button", { name: en.fiUseFullKeyboard })).toBeTruthy();
      cleanup();
    }
  });
});
