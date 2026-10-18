import { describe, expect, it } from "vitest";
import { coverFit, dragCrop, expandWithin, FULL_CROP, GUIDE_ASPECT, guideFor, guideToPhotoRect, guideToRect, stagePointToFrame } from "./cropMath";

describe("framing guide maths", () => {
  it("builds a centred portrait ~4:3 guide inside the viewfinder", () => {
    const box = { w: 360, h: 470 };
    const rect = guideToRect(guideFor(box), box);
    expect(rect.h / rect.w).toBeCloseTo(GUIDE_ASPECT, 1);
    expect(rect.x + rect.w / 2).toBeCloseTo(box.w / 2, 0);
    expect(rect.y + rect.h).toBeLessThan(box.h);
  });

  it("maps the guide onto a portrait video that exactly fills a portrait stage (no cropping by cover)", () => {
    const stage = { w: 400, h: 800 }, video = { w: 1080, h: 2160 };
    const guide = { left: 0.1, top: 0.1, right: 0.1, bottom: 0.3 };
    expect(guideToPhotoRect(guide, stage, video, video, 0)).toEqual({ x: 108, y: 216, w: 864, h: 1296 });
  });

  it("accounts for object-fit: cover when a landscape frame is shown in a portrait stage", () => {
    // 1920x1080 video in a 400x800 stage: scale 800/1080, the sides of the frame are cropped away.
    const stage = { w: 400, h: 800 }, video = { w: 1920, h: 1080 };
    const fit = coverFit(stage, video);
    expect(fit.scale).toBeCloseTo(800 / 1080, 5);
    const rect = guideToPhotoRect({ left: 0, top: 0, right: 0, bottom: 0 }, stage, video, video, 0);
    expect(rect.h).toBe(1080);
    expect(rect.w).toBe(Math.round(400 / fit.scale));
    expect(rect.x).toBe(Math.round((1920 - 400 / fit.scale) / 2));
  });

  it("accounts for cover in a landscape stage showing a square frame", () => {
    const stage = { w: 800, h: 400 }, video = { w: 1000, h: 1000 };
    expect(guideToPhotoRect({ left: 0, top: 0, right: 0, bottom: 0 }, stage, video, video, 0)).toEqual({ x: 0, y: 250, w: 1000, h: 500 });
  });

  it("maps through a still with another aspect than the preview (4:3 photo, 16:9 video) and adds the 8% margin inside the photo", () => {
    const stage = { w: 1600, h: 900 }, video = { w: 1920, h: 1080 }, photo = { w: 4000, h: 3000 };
    expect(guideToPhotoRect({ left: 0, top: 0, right: 0, bottom: 0 }, stage, video, photo, 0)).toEqual({ x: 0, y: 375, w: 4000, h: 2250 });
    const framed = guideToPhotoRect({ left: 0.25, top: 0.25, right: 0.25, bottom: 0.25 }, stage, video, photo, 0.08);
    expect(framed.w).toBeCloseTo(2000 * 1.16, -1);
    expect(framed.x).toBeGreaterThanOrEqual(0);
    expect(framed.x + framed.w).toBeLessThanOrEqual(4000);
  });

  it("clamps the margin to the image and falls back to the whole photo while the stage is unmeasured", () => {
    expect(expandWithin({ x: 5, y: 5, w: 90, h: 90 }, 0.5, { w: 100, h: 100 })).toEqual({ x: 0, y: 0, w: 100, h: 100 });
    expect(guideToPhotoRect(guideFor({ w: 0, h: 0 }), { w: 0, h: 0 }, { w: 1, h: 1 }, { w: 640, h: 480 })).toEqual({ x: 0, y: 0, w: 640, h: 480 });
  });

  it("turns a tap in the stage into a normalised point of the (cover-cropped) video frame", () => {
    const stage = { w: 400, h: 800 }, video = { w: 1920, h: 1080 };
    expect(stagePointToFrame(200, 400, stage, video)).toEqual({ x: 0.5, y: 0.5 });
    expect(stagePointToFrame(0, 0, stage, video).y).toBe(0);
    expect(stagePointToFrame(0, 0, stage, video).x).toBeGreaterThan(0.3);
  });
});

describe("crop box dragging", () => {
  it("moves inside the image and resizes from a corner without going below the minimum", () => {
    const box = { x: 0.2, y: 0.2, w: 0.5, h: 0.5 };
    expect(dragCrop(box, "move", 0.9, -0.9)).toEqual({ x: 0.5, y: 0, w: 0.5, h: 0.5 });
    const grown = dragCrop(box, "se", 0.1, 0.1);
    expect([grown.x, grown.y, grown.w, grown.h].map(value => Math.round(value * 100))).toEqual([20, 20, 60, 60]);
    expect(dragCrop(box, "nw", 5, 5).w).toBeCloseTo(0.15);
    expect(dragCrop(FULL_CROP, "se", 0.3, 0.3)).toEqual(FULL_CROP);
  });
});
