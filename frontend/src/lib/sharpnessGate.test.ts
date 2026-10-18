import { describe, expect, it } from "vitest";
import { SHARPNESS_MIN } from "./imageQuality";
import { createSharpnessGate } from "./sharpnessGate";

const SHARP = SHARPNESS_MIN * 5, BLURRY = SHARPNESS_MIN / 5;

describe("sharpness gate", () => {
  it("waits until the picture has been sharp for 400 ms, then fires once", () => {
    const gate = createSharpnessGate();
    expect(gate.update(BLURRY, 0)).toMatchObject({ state: "focusing", canShoot: false, fire: false });
    expect(gate.update(SHARP, 100)).toMatchObject({ state: "focusing", fire: false });
    expect(gate.update(SHARP, 450)).toMatchObject({ state: "focusing", fire: false });
    expect(gate.update(SHARP, 500)).toMatchObject({ state: "ready", canShoot: true, fire: true });
    expect(gate.update(SHARP, 600)).toMatchObject({ state: "ready", fire: false });
  });

  it("restarts the streak when the picture goes soft again", () => {
    const gate = createSharpnessGate();
    gate.update(SHARP, 0); gate.update(BLURRY, 300);
    expect(gate.update(SHARP, 400)).toMatchObject({ state: "focusing", fire: false });
    expect(gate.update(SHARP, 800).fire).toBe(true);
  });

  it("lets the shutter be pressed manually after 2 s without ever auto-firing", () => {
    const gate = createSharpnessGate();
    expect(gate.update(BLURRY, 0).canShoot).toBe(false);
    expect(gate.update(BLURRY, 1900).canShoot).toBe(false);
    expect(gate.update(BLURRY, 2000)).toMatchObject({ state: "focusing", canShoot: true, fire: false });
  });

  it("allows the shutter when sharpness cannot be measured, and can fire again after a reset", () => {
    const gate = createSharpnessGate();
    expect(gate.update(null, 0)).toMatchObject({ state: "unknown", canShoot: true, fire: false });
    gate.update(SHARP, 10);
    expect(gate.update(SHARP, 500).fire).toBe(true);
    gate.reset();
    gate.update(SHARP, 600);
    expect(gate.update(SHARP, 1000).fire).toBe(true);
  });
});
