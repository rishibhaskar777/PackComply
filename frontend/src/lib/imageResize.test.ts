import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { JPEG_QUALITY, prepareForUpload } from "./imageResize";

type Bitmap = { width: number; height: number; close: () => void };
const drawImage = vi.fn();
const toBlob = vi.fn((callback: BlobCallback, type?: string) => callback(new Blob(["jpeg"], { type })));
let createdCanvas: { width: number; height: number } | null = null;

function stubCanvas() {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(function (this: HTMLCanvasElement) {
    createdCanvas = this;
    return { drawImage, imageSmoothingQuality: "low" } as unknown as CanvasRenderingContext2D;
  } as never);
  vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(toBlob as never);
}
function stubBitmap(width: number, height: number) {
  const bitmap: Bitmap = { width, height, close: vi.fn() };
  const create = vi.fn(async () => bitmap);
  vi.stubGlobal("createImageBitmap", create);
  return { bitmap, create };
}

describe("prepareForUpload", () => {
  beforeEach(() => { drawImage.mockClear(); toBlob.mockClear(); createdCanvas = null; stubCanvas(); });
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it("decodes with the EXIF orientation applied, so the pixels and size are the way the picture is meant to be seen", async () => {
    const { create } = stubBitmap(3000, 4000); // a portrait photo: the browser has already turned the sideways sensor data
    const photo = new Blob(["raw"], { type: "image/jpeg" });
    const result = await prepareForUpload(photo, 1280);
    expect(create).toHaveBeenCalledWith(photo, { imageOrientation: "from-image" });
    expect(createdCanvas).toMatchObject({ width: 960, height: 1280 }); // portrait stays portrait
    expect(result).toMatchObject({ width: 960, height: 1280, resized: true });
    expect(result.blob.type).toBe("image/jpeg");
    expect(toBlob).toHaveBeenCalledWith(expect.any(Function), "image/jpeg", JPEG_QUALITY);
  });

  it("scales a landscape photo to the OCR size on its long side", async () => {
    const { bitmap } = stubBitmap(4032, 3024);
    const result = await prepareForUpload(new Blob(["raw"], { type: "image/jpeg" }), 1280);
    expect(createdCanvas).toMatchObject({ width: 1280, height: 960 });
    expect(drawImage).toHaveBeenCalledWith(bitmap, 0, 0, 1280, 960);
    expect(bitmap.close).toHaveBeenCalled();
    expect(result.resized).toBe(true);
  });

  it("returns a photo already within the limit untouched", async () => {
    stubBitmap(1000, 700);
    const photo = new Blob(["raw"], { type: "image/png" });
    const result = await prepareForUpload(photo, 1280);
    expect(result).toEqual({ blob: photo, width: 1000, height: 700, resized: false });
    expect(drawImage).not.toHaveBeenCalled();
  });
});
