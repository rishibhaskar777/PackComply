import { describe, expect, it } from "vitest";
import { assessPixels, BRIGHTNESS_MIN, fitWithin, glareShare, laplacianVariance, meanLuma, SHARPNESS_MIN, toGray } from "./imageQuality";

const SIZE = 64;

/** RGBA pixels from a per-pixel grey function. */
function pixels(grey: (x: number, y: number) => number, width = SIZE, height = SIZE) {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const value = grey(x, y), offset = (y * width + x) * 4;
    data[offset] = data[offset + 1] = data[offset + 2] = value; data[offset + 3] = 255;
  }
  return { data, width, height };
}

/** Text-like detail: a fine checkerboard in the mid-grey range, so it is neither dark nor blown out. */
const sharp = () => pixels((x, y) => ((x >> 1) + (y >> 1)) % 2 ? 190 : 90);
/** The same picture after a box blur: edges smeared into a gentle gradient. */
function blurred() {
  const gray = toGray(sharp());
  const out = new Float32Array(gray.length);
  const radius = 3;
  for (let y = 0; y < SIZE; y++) for (let x = 0; x < SIZE; x++) {
    let total = 0, count = 0;
    for (let dy = -radius; dy <= radius; dy++) for (let dx = -radius; dx <= radius; dx++) {
      const px = Math.min(SIZE - 1, Math.max(0, x + dx)), py = Math.min(SIZE - 1, Math.max(0, y + dy));
      total += gray[py * SIZE + px]; count++;
    }
    out[y * SIZE + x] = total / count;
  }
  return pixels((x, y) => out[y * SIZE + x]);
}

describe("photo quality checks", () => {
  it("measures a sharp, detailed picture well above the sharpness threshold and a blurred one below it", () => {
    const crisp = laplacianVariance(toGray(sharp()), SIZE, SIZE);
    const soft = laplacianVariance(toGray(blurred()), SIZE, SIZE);
    expect(crisp).toBeGreaterThan(SHARPNESS_MIN * 10);
    expect(soft).toBeLessThan(SHARPNESS_MIN);
    expect(crisp).toBeGreaterThan(soft * 50);
  });

  it("calls a flat picture blurry: no edges, no variance", () => {
    expect(laplacianVariance(toGray(pixels(() => 128)), SIZE, SIZE)).toBe(0);
    expect(laplacianVariance(new Float32Array(4), 2, 2)).toBe(0);
  });

  it("computes the mean luma with the Rec. 601 weights", () => {
    expect(meanLuma(toGray(pixels(() => 200)))).toBeCloseTo(200, 3);
    const red = { data: new Uint8ClampedArray([255, 0, 0, 255]), width: 1, height: 1 };
    expect(meanLuma(toGray(red))).toBeCloseTo(0.299 * 255, 3);
    expect(meanLuma([])).toBe(0);
  });

  it("flags a dark picture, however sharp", () => {
    const report = assessPixels(pixels((x, y) => ((x >> 1) + (y >> 1)) % 2 ? 45 : 5));
    expect(report.brightness).toBeLessThan(BRIGHTNESS_MIN);
    expect(report.lightOk).toBe(false);
    expect(report.retake).toBe(true);
  });

  it("flags glare when a large share of pixels is blown out, and not a few specks", () => {
    const patch = pixels((x, y) => (x < 24 && y < 24 ? 255 : (x + y) % 2 ? 170 : 90));
    expect(glareShare(patch)).toBeCloseTo((24 * 24) / (SIZE * SIZE), 5);
    expect(assessPixels(patch).glare).toBe(true);
    const specks = pixels((x, y) => (x === 3 && y === 3 ? 255 : (x + y) % 2 ? 170 : 90));
    expect(assessPixels(specks).glare).toBe(false);
  });

  it("passes a sharp, well-lit picture without glare", () => {
    expect(assessPixels(sharp())).toMatchObject({ sharp: true, lightOk: true, glare: false, retake: false });
  });

  it("only counts a pixel as glare when all three channels are near white", () => {
    expect(glareShare({ data: new Uint8ClampedArray([255, 255, 120, 255]), width: 1, height: 1 })).toBe(0);
  });
});

describe("fitWithin", () => {
  it("shrinks the long side to the limit, keeps the aspect ratio and never enlarges", () => {
    expect(fitWithin(4000, 3000, 1280)).toEqual({ width: 1280, height: 960 });
    expect(fitWithin(3000, 4000, 1280)).toEqual({ width: 960, height: 1280 });
    expect(fitWithin(800, 600, 1280)).toEqual({ width: 800, height: 600 });
  });
});
