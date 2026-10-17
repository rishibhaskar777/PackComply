import type { Panel, PhotoInfo, Summary } from "../types";

export const CAPTURE_PANELS = ["BACK", "FRONT", "DATE_STAMP"] as const;
export const ALL_PANELS: Panel[] = ["BACK", "FRONT", "DATE_STAMP", "OTHER"];
const DATE_FIELDS = new Set(["manufacturing_date", "packing_date", "best_before", "use_by", "expiry_date", "batch_number"]);
const NAME_FIELDS = new Set(["product_name", "brand_owner"]);

/** Which photo is the natural place to look for a field: the date stamp, the front, or (for everything else) the back. */
export function panelForField(field: string | null | undefined): Panel {
  return field && DATE_FIELDS.has(field) ? "DATE_STAMP" : field && NAME_FIELDS.has(field) ? "FRONT" : "BACK";
}

/** The photos that are processed: the newest per panel (every OTHER photo counts). */
export const activePhotos = (images: PhotoInfo[] | undefined) => (images ?? []).filter(image => image.active);

export const hasPanel = (images: PhotoInfo[] | undefined, panel: Panel) => activePhotos(images).some(image => image.panel === panel);

/** The record's photos; a summary from before photos were tracked has just its single image (a back panel). */
export function summaryPhotos(summary: Pick<Summary, "images" | "image">): PhotoInfo[] {
  if (summary.images?.length) return summary.images;
  const image = summary.image;
  return image ? [{ id: image.id, panel: image.panel ?? "BACK", width: image.width, height: image.height, checksum: image.checksum, uploaded_at: "", active: true, original_filename: image.original_filename }] : [];
}
