import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { FieldValue, labelFields } from "./App";
import en from "./locales/en.json";
import { I18nProvider } from "./i18n";

const show = (name: string, value: string | null) => render(<I18nProvider><FieldValue name={name} value={value} /></I18nProvider>);

describe("extraction field values", () => {
  afterEach(cleanup);
  it("lists every manufacturing unit readably", () => {
    show("manufacturing_units", JSON.stringify([
      { code: "HP", name: "CF FOODS PVT LTD", address: "BALLARI ROAD, KA-583231", licence: "10014043000891" },
      { code: "MY", name: "KOIEL FOOD PROCE'S", address: null, licence: null },
    ]));
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toBe(`(HP) CF FOODS PVT LTD, BALLARI ROAD, KA-583231 · ${en.extractionFssaiLicense}: 10014043000891`);
    expect(items[1].textContent).toBe("(MY) KOIEL FOOD PROCE'S");
  });
  it("translates MRP declaration and tax statuses", () => {
    show("mrp_declaration", "present_without_value");
    expect(screen.getByText(en.mrpDeclarationPresentWithoutValue)).toBeTruthy();
    cleanup();
    show("tax_inclusive", "true");
    expect(screen.getByText(en.taxInclusiveYes)).toBeTruthy();
  });
  it("lists label fields but not the evidence the compliance rules read", () => {
    const fields = { mrp: "₹45", declaration_status: "{\"mrp\": {\"state\": \"DECLARED_WITH_VALUE\"}}", ocr_coverage: "{\"text_lines\": 30}", net_quantity: "200 g" };
    expect(labelFields(fields).map(([name]) => name)).toEqual(["mrp", "net_quantity"]);
  });
  it("shows plain values, malformed unit data and missing values safely", () => {
    show("fssai_license", "10013022002245");
    expect(screen.getByText("10013022002245")).toBeTruthy();
    cleanup();
    show("manufacturing_units", "not json");
    expect(screen.getByText("not json")).toBeTruthy();
    cleanup();
    show("batch_number", null);
    expect(screen.getByText(en.notDetected)).toBeTruthy();
  });
});
