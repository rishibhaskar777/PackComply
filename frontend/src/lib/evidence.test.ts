import { describe, expect, it } from "vitest";
import { buildFindingItems, clampPan, markerAnchor, moveSelection, panToRect, scaleRect, sourceSize, unionRect, zoomAt } from "./evidence";
import type { Finding, Summary } from "../types";

const box = (x: number, y: number, w: number, h: number) => [[x, y], [x + w, y], [x + w, y + h], [x, y + h]];

describe("evidence geometry", () => {
  it("unions every corner of every box", () => {
    expect(unionRect([box(100, 200, 50, 20), box(180, 190, 40, 40)])).toEqual({ x: 100, y: 190, w: 120, h: 40 });
    expect(unionRect(null)).toBeNull();
    expect(unionRect([])).toBeNull();
  });

  it("scales boxes from the stored photo size to the rendered size", () => {
    // 832 x 718 photo shown 416 px wide (359 high): everything halves.
    const rect = scaleRect({ x: 200, y: 100, w: 60, h: 20 }, { w: 832, h: 718 }, { w: 416, h: 359 });
    expect(rect).toEqual({ x: 100, y: 50, w: 30, h: 10 });
    expect(markerAnchor([box(200, 100, 60, 20)], { w: 832, h: 718 }, { w: 416, h: 359 })).toEqual({ x: 100, y: 50 });
  });

  it("handles photos the OCR downscaled: boxes are already in original pixels, so the stored size is the reference", () => {
    // 4000 x 3000 original, OCR ran on 1280 max-dimension. Boxes come back in 4000-wide space.
    const boxes = [box(2000, 1500, 400, 100)];
    const source = sourceSize({ width: 4000, height: 3000 }, { w: 1280, h: 960 }, boxes);
    expect(source).toEqual({ w: 4000, h: 3000 });
    // Rendered 600 px wide: scale 0.15.
    expect(markerAnchor(boxes, source!, { w: 600, h: 450 })).toEqual({ x: 300, y: 225 });
  });

  it("falls back to the decoded size when the stored size cannot contain the boxes", () => {
    const boxes = [box(2000, 1500, 400, 100)];
    expect(sourceSize({ width: 1280, height: 960 }, { w: 4000, h: 3000 }, boxes)).toEqual({ w: 4000, h: 3000 });
    expect(sourceSize({ width: null, height: null }, { w: 800, h: 600 }, [])).toEqual({ w: 800, h: 600 });
    expect(sourceSize(undefined, null, boxes)).toBeNull();
  });

  it("keeps a zoomed photo covering its frame", () => {
    const frame = { w: 600, h: 400 };
    expect(clampPan({ x: 50, y: 50 }, 2, frame)).toEqual({ x: 0, y: 0 });
    expect(clampPan({ x: -900, y: -900 }, 2, frame)).toEqual({ x: -600, y: -400 });
    expect(clampPan({ x: -10, y: -10 }, 1, frame)).toEqual({ x: 0, y: 0 });
  });

  it("zooms around a point so the photo point under it stays put, within 1x-4x", () => {
    const frame = { w: 600, h: 400 };
    const { zoom, pan } = zoomAt({ x: 0, y: 0 }, 1, 2, { x: 300, y: 200 }, frame);
    expect(zoom).toBe(2);
    expect(pan).toEqual({ x: -300, y: -200 });
    expect(zoomAt({ x: 0, y: 0 }, 1, 9, { x: 0, y: 0 }, frame).zoom).toBe(4);
    expect(zoomAt({ x: 0, y: 0 }, 2, 0.2, { x: 0, y: 0 }, frame).zoom).toBe(1);
  });

  it("pans to centre a box, clamped to the photo", () => {
    const frame = { w: 600, h: 400 };
    expect(panToRect({ x: 300, y: 200, w: 0, h: 0 }, 2, frame)).toEqual({ x: -300, y: -200 });
    expect(panToRect({ x: 0, y: 0, w: 20, h: 20 }, 2, frame)).toEqual({ x: 0, y: 0 });
  });
});

const finding = (rule_id: string, status: Finding["status"], severity: string, field: string | null, source_bboxes?: number[][][]): Finding => ({ rule_id, rule_name: rule_id, status, severity, message: "", field, value: null, source_bboxes });
const summary = (findings: Finding[], fields: Summary["extraction"] = { fields: {} }): Summary => ({ id: "1", inspection_number: "PC-1", status: "COMPLETED", compliance: { overall_status: "REVIEW", summary: { passed: 0, failed: 0, review: 0 }, findings }, extraction: fields });

describe("finding model", () => {
  it("groups Violations, To verify, Passed, Not applicable and numbers the first three across groups", () => {
    const items = buildFindingItems(summary([
      finding("B_PASS", "PASS", "LOW", "x"), finding("A_REVIEW", "REVIEW", "HIGH", "x"), finding("Z_FAIL", "FAIL", "WARNING", "x"),
      finding("A_FAIL", "FAIL", "HIGH", "x"), finding("N_A", "NOT_APPLICABLE", "LOW", "x"),
    ]));
    expect(items.map(item => [item.finding.rule_id, item.group, item.number])).toEqual([
      ["A_FAIL", "violations", 1], ["Z_FAIL", "violations", 2], ["A_REVIEW", "review", 3], ["B_PASS", "passed", 4], ["N_A", "notApplicable", null],
    ]);
  });

  it("takes boxes from the finding, else from its field, and never for not-applicable rules", () => {
    const items = buildFindingItems(summary([
      finding("OWN", "PASS", "LOW", "a", [box(1, 1, 2, 2)]), finding("FROM_FIELD", "PASS", "LOW", "b"), finding("NONE", "FAIL", "LOW", null), finding("NA", "NOT_APPLICABLE", "LOW", "b", [box(1, 1, 2, 2)]),
    ], { fields: { b: { value: "70 g", confidence: 1, needs_review: false, source_bboxes: [box(5, 5, 5, 5)] } } }));
    const byId = Object.fromEntries(items.map(item => [item.finding.rule_id, item]));
    expect(byId.OWN.boxes).toEqual([box(1, 1, 2, 2)]);
    expect(byId.FROM_FIELD.boxes).toEqual([box(5, 5, 5, 5)]);
    expect(byId.FROM_FIELD.read).toBe("70 g");
    expect(byId.NONE.boxes).toBeNull();
    expect(byId.NA.boxes).toBeNull();
  });

  it("moves the selection through selectable findings, wrapping, skipping not-applicable", () => {
    const items = buildFindingItems(summary([finding("A", "FAIL", "HIGH", null), finding("B", "PASS", "LOW", null), finding("N", "NOT_APPLICABLE", "LOW", null)]));
    expect(moveSelection(items, null, 1)).toBe("A");
    expect(moveSelection(items, null, -1)).toBe("B");
    expect(moveSelection(items, "A", 1)).toBe("B");
    expect(moveSelection(items, "B", 1)).toBe("A");
    expect(moveSelection(items, "A", -1)).toBe("B");
    expect(moveSelection([], null, 1)).toBeNull();
  });
});
