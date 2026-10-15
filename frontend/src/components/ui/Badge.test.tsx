import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import en from "../../locales/en.json";
import { I18nProvider } from "../../i18n";
import { Badge } from "./Badge";

const cases: Array<[string, string, string]> = [
  ["NON_COMPLIANT", "violation", en.verdictNonCompliant], ["NEEDS_REVIEW", "review", en.verdictNeedsReview], ["COMPLIANT", "pass", en.verdictCompliant],
  ["PASS", "pass", en.pass], ["FAIL", "violation", en.fail], ["REVIEW", "review", en.warning], ["NOT_APPLICABLE", "neutral", en.notApplicable],
  ["LOW", "pass", en.low], ["MEDIUM", "review", en.medium], ["HIGH", "violation", en.high], ["CRITICAL", "critical", en.critical],
];

describe("Badge", () => {
  afterEach(cleanup);
  it.each(cases)("renders %s with the %s tone, a leading dot and its text", (variant, tone, label) => {
    const { container } = render(<I18nProvider><Badge variant={variant} /></I18nProvider>);
    const badge = container.querySelector(".badge")!;
    expect(badge.classList.contains(`badge--${tone}`)).toBe(true);
    expect(badge.getAttribute("data-variant")).toBe(variant);
    // Colour is never the only signal: the state is always named in text.
    expect(badge.textContent).toBe(label);
    expect(badge.querySelector(".badge__dot")?.getAttribute("aria-hidden")).toBe("true");
  });
  it("shows unknown values neutrally and allows custom text without a dot", () => {
    const { container } = render(<I18nProvider><Badge variant="SOMETHING_NEW" /><Badge tone="primary" dot={false}>HIGH</Badge></I18nProvider>);
    const [unknown, custom] = Array.from(container.querySelectorAll(".badge"));
    expect(unknown.className).toContain("badge--neutral");
    expect(unknown.textContent).toBe("SOMETHING_NEW");
    expect(custom.className).toContain("badge--primary");
    expect(custom.querySelector(".badge__dot")).toBeNull();
    expect(custom.textContent).toBe("HIGH");
  });
});
