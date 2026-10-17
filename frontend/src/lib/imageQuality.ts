/**
 * Local photo-quality checks for the capture flow. Everything runs on a small downscaled copy of the
 * frame, in the browser; nothing is sent anywhere.
 */

/** Longest side of the copy the checks run on. */
export const QUALITY_SIZE = 320;
/** Variance of the Laplacian below this reads as blurry (measured on a QUALITY_SIZE copy). */
export const SHARPNESS_MIN = 60;
/** Mean luma (0-255) below this reads as too dark. */
export const BRIGHTNESS_MIN = 70;
/** A pixel counts as glare when every channel is at least this bright. */
export const GLARE_LEVEL = 252;
/** More than this share of glare pixels flags glare. */
export const GLARE_MAX_SHARE = 0.12;

export type QualityReport = {
  sharpness: number; brightness: number; glareShare: number;
  sharp: boolean; lightOk: boolean; glare: boolean;
  /** True when any check failed: the capture UI then recommends a retake. */
  retake: boolean;
};

type Pixels = { data: ArrayLike<number>; width: number; height: number };

const luma = (r: number, g: number, b: number) => 0.299 * r + 0.587 * g + 0.114 * b;

/** Grey levels (0-255) of RGBA pixels. */
export function toGray({ data, width, height }: Pixels): Float32Array {
  const gray = new Float32Array(width * height);
  for (let index = 0, offset = 0; index < gray.length; index++, offset += 4) gray[index] = luma(data[offset], data[offset + 1], data[offset + 2]);
  return gray;
}

/** Variance of the 4-neighbour Laplacian over the interior pixels: high for crisp edges, low for blur and flat areas. */
export function laplacianVariance(gray: ArrayLike<number>, width: number, height: number): number {
  if (width < 3 || height < 3) return 0;
  let sum = 0, sumSquares = 0, count = 0;
  for (let y = 1; y < height - 1; y++) {
    for (let x = 1; x < width - 1; x++) {
      const i = y * width + x;
      const value = 4 * gray[i] - gray[i - 1] - gray[i + 1] - gray[i - width] - gray[i + width];
      sum += value; sumSquares += value * value; count++;
    }
  }
  const mean = sum / count;
  return sumSquares / count - mean * mean;
}

export function meanLuma(gray: ArrayLike<number>): number {
  if (!gray.length) return 0;
  let total = 0;
  for (let index = 0; index < gray.length; index++) total += gray[index];
  return total / gray.length;
}

/** Share of pixels whose red, green and blue are all near-white. */
export function glareShare({ data, width, height }: Pixels, level = GLARE_LEVEL): number {
  const total = width * height;
  if (!total) return 0;
  let bright = 0;
  for (let offset = 0; offset < total * 4; offset += 4) if (data[offset] >= level && data[offset + 1] >= level && data[offset + 2] >= level) bright++;
  return bright / total;
}

export function assessPixels(pixels: Pixels): QualityReport {
  const gray = toGray(pixels);
  const sharpness = laplacianVariance(gray, pixels.width, pixels.height);
  const brightness = meanLuma(gray);
  const glare = glareShare(pixels);
  const report = { sharpness, brightness, glareShare: glare, sharp: sharpness >= SHARPNESS_MIN, lightOk: brightness >= BRIGHTNESS_MIN, glare: glare > GLARE_MAX_SHARE };
  return { ...report, retake: !report.sharp || !report.lightOk || report.glare };
}

/** Size that fits `width` x `height` inside a box of `max` on the long side, never enlarging. */
export function fitWithin(width: number, height: number, max: number): { width: number; height: number } {
  const scale = Math.min(1, max / Math.max(width, height));
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

/** Run the checks on any drawable (canvas, video frame, image), on a QUALITY_SIZE copy. Null where canvas is unavailable. */
export function assessSource(source: CanvasImageSource, width: number, height: number): QualityReport | null {
  if (typeof document === "undefined" || !width || !height) return null;
  const size = fitWithin(width, height, QUALITY_SIZE);
  const canvas = document.createElement("canvas");
  canvas.width = size.width; canvas.height = size.height;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) return null;
  context.drawImage(source, 0, 0, size.width, size.height);
  return assessPixels(context.getImageData(0, 0, size.width, size.height));
}
