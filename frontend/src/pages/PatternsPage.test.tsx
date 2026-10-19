import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../i18n";
import { mockApi, response } from "../test/mockApi";
import { PatternsPage } from "./PatternsPage";

const pattern = (type: string) => ({ pattern_key: "MRP_FORMAT", pattern_type: type, title: `MRP ${type}`, description: "d", occurrence_count: 2, first_seen: "2026-10-01T00:00:00Z", last_seen: "2026-10-02T00:00:00Z", severity: "HIGH", inspection_ids: ["a", "b"] });

describe("repeat issues", () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it("renders a rule that is both a violation pattern and a warning pattern without duplicate React keys", async () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
    mockApi({ "GET /patterns": response([pattern("COMPLIANCE_VIOLATION"), pattern("COMPLIANCE_WARNING")]) });
    render(<I18nProvider><PatternsPage onExpired={() => undefined} /></I18nProvider>);
    expect(await screen.findByText("MRP COMPLIANCE_VIOLATION")).toBeTruthy();
    expect(screen.getByText("MRP COMPLIANCE_WARNING")).toBeTruthy();
    expect(errors.mock.calls.filter(call => String(call[0]).includes("same key"))).toEqual([]);
  });
});
