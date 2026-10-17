import type { BBox, Finding, FindingStatus, Summary } from "../types";

/** Axis-aligned rectangle; pixels of whatever space it was computed in. */
export type Rect = { x: number; y: number; w: number; h: number };
export type Size = { w: number; h: number };

export const MIN_ZOOM = 1;
export const MAX_ZOOM = 4;

/** The smallest rectangle covering every corner of the given OCR boxes (original-photo pixels), or null. */
export function unionRect(boxes: BBox[] | null | undefined): Rect | null {
  const points = (boxes ?? []).flat().filter(point => point.length >= 2 && Number.isFinite(point[0]) && Number.isFinite(point[1]));
  if (!points.length) return null;
  const xs = points.map(point => point[0]), ys = points.map(point => point[1]);
  const x = Math.min(...xs), y = Math.min(...ys);
  return { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y };
}

export function boxRect(box: BBox): Rect | null {
  return unionRect([box]);
}

/**
 * The pixel space OCR boxes live in. The backend maps every box back to the
 * original photo (undoing the OCR downscale and any rotation), and records that
 * photo's displayed size, so the stored size is the right reference. A size that
 * clearly cannot contain the boxes (legacy row, wrong record) falls back to the
 * size the browser decoded, which is the same photo.
 */
export function sourceSize(stored: { width?: number | null; height?: number | null } | undefined, natural: Size | null, boxes: BBox[]): Size | null {
  const extent = unionRect(boxes);
  const reach = extent ? { w: extent.x + extent.w, h: extent.y + extent.h } : { w: 0, h: 0 };
  const TOLERANCE = 1.02;
  if (stored?.width && stored.height && reach.w <= stored.width * TOLERANCE && reach.h <= stored.height * TOLERANCE) return { w: stored.width, h: stored.height };
  if (natural && natural.w > 0 && natural.h > 0) return natural;
  return stored?.width && stored.height ? { w: stored.width, h: stored.height } : null;
}

/** Map a rectangle from the source space to the rendered (unzoomed) photo. */
export function scaleRect(rect: Rect, source: Size, rendered: Size): Rect {
  const sx = rendered.w / source.w, sy = rendered.h / source.h;
  return { x: rect.x * sx, y: rect.y * sy, w: rect.w * sx, h: rect.h * sy };
}

/** Marker anchor: the top-left corner of the finding's boxes, so the badge sits beside the text rather than on it. */
export function markerAnchor(boxes: BBox[] | null | undefined, source: Size, rendered: Size): { x: number; y: number } | null {
  const rect = unionRect(boxes);
  if (!rect) return null;
  const scaled = scaleRect(rect, source, rendered);
  return { x: scaled.x, y: scaled.y };
}

export const clampZoom = (zoom: number) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom));

/** Keep a zoomed photo covering its frame: translate is 0 at the top-left and at most -(size * (zoom - 1)) the other way. */
export function clampPan(pan: { x: number; y: number }, zoom: number, frame: Size): { x: number; y: number } {
  const minX = -(frame.w * (zoom - 1)), minY = -(frame.h * (zoom - 1));
  // `+ 0` turns -0 into 0 so a photo at 1x has a plain zero pan.
  return { x: Math.min(0, Math.max(minX, pan.x)) + 0, y: Math.min(0, Math.max(minY, pan.y)) + 0 };
}

/** Zoom around a point of the frame so the photo point under it stays put. */
export function zoomAt(pan: { x: number; y: number }, zoom: number, nextZoom: number, point: { x: number; y: number }, frame: Size) {
  const next = clampZoom(nextZoom);
  const ratio = next / zoom;
  return { zoom: next, pan: clampPan({ x: point.x - (point.x - pan.x) * ratio, y: point.y - (point.y - pan.y) * ratio }, next, frame) };
}

/** Pan so the centre of a (rendered, unzoomed) rectangle is centred in the frame; zooms in if the photo is at 1x. */
export function panToRect(rect: Rect, zoom: number, frame: Size): { x: number; y: number } {
  return clampPan({ x: frame.w / 2 - (rect.x + rect.w / 2) * zoom, y: frame.h / 2 - (rect.y + rect.h / 2) * zoom }, zoom, frame);
}

// ------------------------------------------------------------------ findings model

export type Group = "violations" | "review" | "passed" | "notApplicable";
export type FindingItem = {
  finding: Finding;
  group: Group;
  /** 1-based badge number shared with the photo marker; N/A findings have none. */
  number: number | null;
  boxes: BBox[] | null;
  /** Value the inspector sees: what the rule read (the corrected one when corrected). */
  read: string | null;
};

const GROUP_OF: Record<FindingStatus, Group> = { FAIL: "violations", REVIEW: "review", PASS: "passed", NOT_APPLICABLE: "notApplicable" };
export const GROUP_ORDER: Group[] = ["violations", "review", "passed", "notApplicable"];
const SEVERITY_RANK: Record<string, number> = { CRITICAL: 5, HIGH: 4, WARNING: 3, MEDIUM: 3, INFO: 2, LOW: 1 };

/**
 * Findings grouped Violations, To verify, Passed, Not applicable, each by severity then rule code.
 * Numbers run across the first three groups in that order, so a badge always matches its marker.
 */
export function buildFindingItems(summary: Summary): FindingItem[] {
  const findings = summary.compliance?.findings ?? [];
  const fields = summary.extraction?.fields ?? {};
  const rank = (finding: Finding) => -(SEVERITY_RANK[finding.severity] ?? 0);
  const ordered = GROUP_ORDER.flatMap(group => findings
    .filter(finding => GROUP_OF[finding.status] === group)
    .sort((a, b) => rank(a) - rank(b) || a.rule_id.localeCompare(b.rule_id))
    .map(finding => ({ finding, group })));
  let counter = 0;
  return ordered.map(({ finding, group }) => {
    const field = finding.field ? fields[finding.field] : undefined;
    const boxes = group === "notApplicable" ? null : (finding.source_bboxes ?? field?.source_bboxes ?? null);
    return { finding, group, number: group === "notApplicable" ? null : ++counter, boxes: boxes?.length ? boxes : null, read: finding.value ?? field?.value ?? null };
  });
}

export const GROUP_TONE: Record<Group, "violation" | "review" | "pass" | "neutral"> = { violations: "violation", review: "review", passed: "pass", notApplicable: "neutral" };

/** Index of the next selectable item (arrow keys), wrapping; -1 when there is nothing to select. */
export function moveSelection(items: FindingItem[], current: string | null, delta: 1 | -1): string | null {
  const selectable = items.filter(item => item.group !== "notApplicable");
  if (!selectable.length) return null;
  const index = selectable.findIndex(item => item.finding.rule_id === current);
  const next = index === -1 ? (delta === 1 ? 0 : selectable.length - 1) : (index + delta + selectable.length) % selectable.length;
  return selectable[next].finding.rule_id;
}
