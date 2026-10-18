import { SHARPNESS_MIN } from "./imageQuality";

/** Sharp for this long, continuously, before the shutter fires. */
export const SHARP_HOLD_MS = 400;
/** After this long without getting sharp the shutter can be pressed anyway. */
export const SHARP_OVERRIDE_MS = 2000;

export type GateView = {
  /** focusing: not sharp yet; ready: sharp long enough; unknown: sharpness cannot be measured here. */
  state: "focusing" | "ready" | "unknown";
  /** The manual shutter is allowed (sharp, unmeasurable, or the override delay has passed). */
  canShoot: boolean;
  /** Fire the auto-capture now (true once per gate until reset). */
  fire: boolean;
};

/** Decides when a live frame is steady and sharp enough to take the photo. Feed it a score per sample. */
export function createSharpnessGate({ threshold = SHARPNESS_MIN, holdMs = SHARP_HOLD_MS, overrideMs = SHARP_OVERRIDE_MS } = {}) {
  let startedAt: number | null = null;
  let sharpSince: number | null = null;
  let fired = false;
  return {
    update(score: number | null, now: number): GateView {
      if (startedAt === null) startedAt = now;
      if (score === null) return { state: "unknown", canShoot: true, fire: false };
      if (score >= threshold) { if (sharpSince === null) sharpSince = now; } else sharpSince = null;
      const ready = sharpSince !== null && now - sharpSince >= holdMs;
      const fire = ready && !fired;
      if (fire) fired = true;
      return { state: ready ? "ready" : "focusing", canShoot: ready || now - startedAt >= overrideMs, fire };
    },
    /** Forget the sharp streak (after a tap-to-focus, or a capture that failed). The override clock keeps running. */
    reset() { sharpSince = null; fired = false; },
  };
}
