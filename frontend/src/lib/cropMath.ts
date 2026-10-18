/** Geometry for the framing guide: mapping what the viewfinder shows onto the pixels of a video frame or photo. */

export type Rect = { x: number; y: number; w: number; h: number };
export type Size = { w: number; h: number };
/** The guide as fractions of the viewfinder box, measured from each edge. */
export type GuideFractions = { left: number; top: number; right: number; bottom: number };

/** Extra pixels kept around the guide (share of the guide's width/height on each side). */
export const GUIDE_MARGIN = 0.08;
/** Label guide: portrait 4:3 (height : width). */
export const GUIDE_ASPECT = 4 / 3;
/** Fallback guide used until the viewfinder has been measured. */
export const DEFAULT_GUIDE: GuideFractions = { left: 0.06, top: 0.05, right: 0.06, bottom: 0.22 };

/** The guide for a viewfinder of `box`: about 4:3 portrait, centred, as large as the box allows (room is left at the bottom for the hint). */
export function guideFor(box: Size): GuideFractions {
  if (!box.w || !box.h) return DEFAULT_GUIDE;
  const maxWidth = Math.min(0.9 * box.w, (0.73 * box.h) / GUIDE_ASPECT);
  const width = maxWidth / box.w;
  const height = (maxWidth * GUIDE_ASPECT) / box.h;
  const left = (1 - width) / 2;
  const top = 0.05;
  return { left, top, right: left, bottom: Math.max(0, 1 - top - height) };
}

/** `object-fit: cover` of a `src` drawn into a `box`: the scale and the (negative) offset of the drawn image. */
export function coverFit(box: Size, src: Size): { scale: number; offsetX: number; offsetY: number } {
  const scale = Math.max(box.w / src.w, box.h / src.h);
  return { scale, offsetX: (box.w - src.w * scale) / 2, offsetY: (box.h - src.h * scale) / 2 };
}

/** The part of `src` (in its own pixels) that is visible in the `region` of a `box` it is covering. */
export function boxRegionToSource(region: Rect, box: Size, src: Size): Rect {
  const { scale, offsetX, offsetY } = coverFit(box, src);
  return { x: (region.x - offsetX) / scale, y: (region.y - offsetY) / scale, w: region.w / scale, h: region.h / scale };
}

export function guideToRect(guide: GuideFractions, box: Size): Rect {
  return { x: guide.left * box.w, y: guide.top * box.h, w: (1 - guide.left - guide.right) * box.w, h: (1 - guide.top - guide.bottom) * box.h };
}

/** Grow a rectangle by `margin` of its own size on every side, then keep it inside `bounds`. */
export function expandWithin(rect: Rect, margin: number, bounds: Size): Rect {
  const x = Math.max(0, rect.x - rect.w * margin), y = Math.max(0, rect.y - rect.h * margin);
  const right = Math.min(bounds.w, rect.x + rect.w * (1 + margin)), bottom = Math.min(bounds.h, rect.y + rect.h * (1 + margin));
  return { x, y, w: Math.max(1, right - x), h: Math.max(1, bottom - y) };
}

const rounded = (rect: Rect): Rect => ({ x: Math.round(rect.x), y: Math.round(rect.y), w: Math.max(1, Math.round(rect.w)), h: Math.max(1, Math.round(rect.h)) });

/**
 * Where the guide (+ margin) lies in a captured image. The viewfinder (`stage`) shows the `video` frame with
 * object-fit: cover; the captured `photo` may have another aspect than the video (a 4:3 still next to a 16:9
 * preview), in which case the video is itself a centred cover-crop of the photo.
 * Returns pixels of the photo; pass the video size as the photo size for a plain frame grab.
 */
export function guideToPhotoRect(guide: GuideFractions, stage: Size, video: Size, photo: Size, margin = GUIDE_MARGIN): Rect {
  if (!stage.w || !stage.h || !video.w || !video.h || !photo.w || !photo.h) return { x: 0, y: 0, w: photo.w, h: photo.h };
  const inVideo = boxRegionToSource(guideToRect(guide, stage), stage, video);
  const inPhoto = boxRegionToSource(inVideo, video, photo);
  return rounded(expandWithin(inPhoto, margin, photo));
}

/** A tap at (`x`,`y`) in the viewfinder as normalised 0-1 coordinates of the video frame (for pointsOfInterest). */
export function stagePointToFrame(x: number, y: number, stage: Size, video: Size): { x: number; y: number } {
  if (!stage.w || !stage.h || !video.w || !video.h) return { x: 0.5, y: 0.5 };
  const { scale, offsetX, offsetY } = coverFit(stage, video);
  const clamp = (value: number) => Math.min(1, Math.max(0, value));
  return { x: clamp((x - offsetX) / scale / video.w), y: clamp((y - offsetY) / scale / video.h) };
}

/** A crop box as fractions (0-1) of an image. */
export type CropFraction = { x: number; y: number; w: number; h: number };
export type CropHandle = "move" | "nw" | "ne" | "sw" | "se";
export const FULL_CROP: CropFraction = { x: 0, y: 0, w: 1, h: 1 };
export const MIN_CROP = 0.15;

/** The crop box after dragging `handle` by (`dx`,`dy`) (fractions of the image), kept inside the image and above the minimum size. */
export function dragCrop(start: CropFraction, handle: CropHandle, dx: number, dy: number, min = MIN_CROP): CropFraction {
  if (handle === "move") {
    return { ...start, x: Math.min(1 - start.w, Math.max(0, start.x + dx)), y: Math.min(1 - start.h, Math.max(0, start.y + dy)) };
  }
  let left = start.x, top = start.y, right = start.x + start.w, bottom = start.y + start.h;
  if (handle.includes("w")) left = Math.min(right - min, Math.max(0, left + dx));
  if (handle.includes("e")) right = Math.max(left + min, Math.min(1, right + dx));
  if (handle.includes("n")) top = Math.min(bottom - min, Math.max(0, top + dy));
  if (handle.includes("s")) bottom = Math.max(top + min, Math.min(1, bottom + dy));
  return { x: left, y: top, w: right - left, h: bottom - top };
}

export function cropFractionToRect(crop: CropFraction, size: Size): Rect {
  return rounded({ x: crop.x * size.w, y: crop.y * size.h, w: crop.w * size.w, h: crop.h * size.h });
}
