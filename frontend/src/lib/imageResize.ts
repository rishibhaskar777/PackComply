import { fitWithin } from "./imageQuality";

export const JPEG_QUALITY = 0.92;

export type Prepared = { blob: Blob; width: number; height: number; resized: boolean };

type Decoded = { source: CanvasImageSource; width: number; height: number; release: () => void };

/**
 * Decode a photo with its EXIF orientation applied, so the pixels (and the size) are the way the
 * picture is meant to be seen. `createImageBitmap(..., { imageOrientation: "from-image" })` does that
 * explicitly; the <img> fallback relies on browsers applying EXIF orientation when they draw.
 */
export async function decodeOriented(blob: Blob): Promise<Decoded> {
  if (typeof createImageBitmap === "function") {
    const bitmap = await createImageBitmap(blob, { imageOrientation: "from-image" });
    return { source: bitmap, width: bitmap.width, height: bitmap.height, release: () => bitmap.close?.() };
  }
  const url = URL.createObjectURL(blob);
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    return { source: image, width: image.naturalWidth, height: image.naturalHeight, release: () => undefined };
  } finally { URL.revokeObjectURL(url); }
}

function toBlob(canvas: HTMLCanvasElement, type: string, quality: number): Promise<Blob> {
  return new Promise((resolve, reject) => canvas.toBlob(blob => (blob ? resolve(blob) : reject(new Error("encode"))), type, quality));
}

/**
 * Shrink a photo so its long side is at most `maxDimension` (the backend reads no more than that
 * anyway), correctly oriented, as JPEG. A photo already within the limit is returned untouched, so the
 * stored evidence is the exact file when no resizing was needed.
 *
 * The upload is the evidence: the backend stores what it receives (with its SHA-256), so a resized
 * photo is stored resized. The original is not kept: OCR never reads more than `maxDimension`.
 */
export async function prepareForUpload(blob: Blob, maxDimension: number): Promise<Prepared> {
  const decoded = await decodeOriented(blob);
  try {
    const { width, height } = fitWithin(decoded.width, decoded.height, maxDimension);
    const needsResize = width !== decoded.width || height !== decoded.height;
    if (!needsResize && (blob.type === "image/jpeg" || blob.type === "image/png")) return { blob, width: decoded.width, height: decoded.height, resized: false };
    const canvas = document.createElement("canvas");
    canvas.width = width; canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) return { blob, width: decoded.width, height: decoded.height, resized: false };
    context.imageSmoothingQuality = "high";
    context.drawImage(decoded.source, 0, 0, width, height);
    return { blob: await toBlob(canvas, "image/jpeg", JPEG_QUALITY), width, height, resized: true };
  } finally { decoded.release(); }
}
