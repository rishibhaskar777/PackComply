import type { Panel, PhotoInfo } from "../types";
import type { FindingItem } from "./evidence";
import { activePhotos, hasPanel, panelForField } from "./panels";

export const MAX_PHOTOS = 4;

/** Which panel a finding points at: the panel of the photo its evidence is on, else the natural home of its field. */
export function panelOfItem(item: FindingItem, images: PhotoInfo[] | undefined, fields: string[]): Panel {
  const onImage = item.imageId ? (images ?? []).find(image => image.id === item.imageId) : undefined;
  return onImage?.panel ?? panelForField(item.finding.field ?? fields[0]);
}

/**
 * The photo action a finding offers: "retake" when its panel is already photographed (the new photo
 * replaces it and the old one stays as evidence), "add" when it is not and there is room, otherwise none.
 */
export function photoActionFor(item: FindingItem, images: PhotoInfo[] | undefined, fields: string[]): { panel: Panel; action: "retake" | "add" | null } {
  const panel = panelOfItem(item, images, fields);
  if (hasPanel(images, panel)) return { panel, action: "retake" };
  return { panel, action: activePhotos(images).length < MAX_PHOTOS ? "add" : null };
}
