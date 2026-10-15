import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ComplianceResults, FieldValue } from "./App";
import en from "./locales/en.json";
import { I18nProvider } from "./i18n";

const compliance = {
  overall_status: "REVIEW", verdict: "NEEDS_REVIEW", ruleset_label: "LM(PC) 2011 ruleset v1",
  summary: { passed: 1, failed: 0, review: 1, not_applicable: 1 },
  findings: [
    { rule_id: "MRP_FORMAT", rule_name: "Retail sale price (MRP)", status: "REVIEW" as const, severity: "HIGH", message: "Price stamped separately.", field: "mrp_declaration", value: "present_without_value", legal_reference: "LM(PC) Rules 2011, Rule 6(1)(e)" },
    { rule_id: "COUNTRY_OF_ORIGIN", rule_name: "Country of origin (imported goods)", status: "NOT_APPLICABLE" as const, severity: "HIGH", message: "Not imported.", field: "country_of_origin", value: null, legal_reference: "LM(PC) Rules 2011, Rule 6(1)(aa)" },
    { rule_id: "PRODUCT_IDENTITY", rule_name: "Common or generic name of the commodity", status: "PASS" as const, severity: "WARNING", message: "Found.", field: "product_name", value: "Parle-G", legal_reference: null },
  ],
};

describe("compliance results", () => {
  afterEach(cleanup);
  it("shows the verdict, the ruleset and each finding's legal reference", () => {
    render(<I18nProvider><ComplianceResults compliance={compliance} /></I18nProvider>);
    expect(screen.getByRole("heading").textContent).toBe(`${en.compliance}: ${en.verdictNeedsReview}`);
    expect(screen.getByText(`${en.checkedAgainst}: LM(PC) 2011 ruleset v1`)).toBeTruthy();
    expect(screen.getByText(`${en.legalReference}: LM(PC) Rules 2011, Rule 6(1)(e)`)).toBeTruthy();
    expect(screen.getByText(new RegExp(`${en.notApplicable}: 1`))).toBeTruthy();
  });
  it("styles not-applicable findings neutrally, never as issues", () => {
    const { container } = render(<I18nProvider><ComplianceResults compliance={compliance} /></I18nProvider>);
    const neutral = container.querySelector(".finding.finding--not-applicable");
    expect(neutral?.textContent).toContain("Country of origin");
    expect(neutral?.querySelector('[data-variant="NOT_APPLICABLE"]')?.textContent).toBe(en.notApplicable);
    expect(container.querySelectorAll('[data-variant="FAIL"]')).toHaveLength(0);
  });
  it("falls back to the overall status for results stored before verdicts", () => {
    render(<I18nProvider><ComplianceResults compliance={{ ...compliance, verdict: null, ruleset_label: null }} /></I18nProvider>);
    expect(screen.getByRole("heading").textContent).toBe(`${en.compliance}: REVIEW`);
    expect(screen.queryByText(new RegExp(en.checkedAgainst))).toBeNull();
  });
  it("translates the new extraction declarations", () => {
    render(<I18nProvider><FieldValue name="consumer_care_declaration" value="present_without_details" /></I18nProvider>);
    expect(screen.getByText(en.consumerCareDeclarationPresentWithoutDetails)).toBeTruthy();
  });
});
