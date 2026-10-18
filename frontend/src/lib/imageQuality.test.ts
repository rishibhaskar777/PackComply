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

describe("blur gate on label-like images", () => {
  const W = 240, H = 320;
  /** Label-like text: rows of 2px dark strokes (a hashed glyph grid), as seen at the 320px quality size: moderate contrast. */
  const labelGrey = () => {
    const grey = new Float32Array(W * H).fill(205);
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      const row = Math.floor(y / 16), col = Math.floor(x / 8), inRow = y % 16, inCol = x % 8;
      if (inRow < 3 || inRow > 11 || inCol > 5) continue;
      const bits = (row * 7919 + col * 104729 + 13) % 16;
      const stroke = (inCol < 2 && bits & 1) || (inCol > 3 && bits & 2) || (inRow < 5 && bits & 4) || (inRow > 8 && inRow < 11 && bits & 8);
      if (stroke) grey[y * W + x] = 95;
    }
    return grey;
  };
  const asPixels = (grey: Float32Array) => ({ data: Uint8ClampedArray.from({ length: W * H * 4 }, (_, i) => (i % 4 === 3 ? 255 : grey[Math.floor(i / 4)])), width: W, height: H });
  /** Average each pixel over the given offsets: a box (defocus) or a diagonal streak (hand motion). */
  const smear = (grey: Float32Array, offsets: Array<[number, number]>) => {
    const out = new Float32Array(grey.length);
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      let total = 0;
      for (const [dx, dy] of offsets) total += grey[Math.min(H - 1, Math.max(0, y + dy)) * W + Math.min(W - 1, Math.max(0, x + dx))];
      out[y * W + x] = total / offsets.length;
    }
    return out;
  };
  const box = (radius: number) => { const out: Array<[number, number]> = []; for (let dy = -radius; dy <= radius; dy++) for (let dx = -radius; dx <= radius; dx++) out.push([dx, dy]); return out; };
  const streak = (length: number) => Array.from({ length }, (_, i): [number, number] => [i - (length >> 1), i - (length >> 1)]);
  const score = (grey: Float32Array) => assessPixels(asPixels(grey));


  it("passes a sharp label image, and one with the slight softness of a normal phone photo", () => {
    expect(score(labelGrey()).sharp).toBe(true);
    expect(score(smear(labelGrey(), box(1))).sharp).toBe(true);
  });

  it("fails heavy focus blur and heavy motion blur of the same label", () => {
    expect(score(smear(labelGrey(), box(3))).sharp).toBe(false);
    expect(score(smear(labelGrey(), streak(41))).sharp).toBe(false);
  });
});
