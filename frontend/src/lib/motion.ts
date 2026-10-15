import { useEffect, useRef, useState } from "react";
import type { Transition } from "motion/react";

// Shared motion language. CSS mirrors these in tokens.css (--duration-*, --ease-out).
export const DURATION = { fast: 0.15, base: 0.25, slow: 0.4 } as const;
export const EASE = [0.22, 1, 0.36, 1] as const;
export const SPRING: Transition = { type: "spring", stiffness: 400, damping: 32 };
export const STAGGER = 0.04;
/** Rows beyond this index appear together, so long tables never wait on their entrance. */
export const STAGGER_LIMIT = 12;
export const COUNT_UP_MS = 800;

export const fadeRise = (delay = 0): Transition => ({ duration: DURATION.slow, ease: EASE, delay });

const REDUCE_QUERY = "(prefers-reduced-motion: reduce)";

/**
 * True when motion should be skipped: the user asked for reduced motion, or the environment
 * cannot tell us (no matchMedia, e.g. server render or a test DOM). Instant is always safe.
 */
export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return true;
  return window.matchMedia(REDUCE_QUERY).matches;
}

export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(prefersReducedMotion);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia(REDUCE_QUERY);
    const update = () => setReduced(query.matches);
    query.addEventListener?.("change", update);
    return () => query.removeEventListener?.("change", update);
  }, []);
  return reduced;
}

const easeOutCubic = (progress: number) => 1 - Math.pow(1 - progress, 3);

/** Animate a number from its previous value (0 on mount) to `value`, easing out. Shows `value` at once under reduced motion. */
export function useCountUp(value: number, duration = COUNT_UP_MS): number {
  const reduced = useReducedMotion();
  const [display, setDisplay] = useState(() => (reduced ? value : 0));
  const from = useRef(reduced ? value : 0);
  useEffect(() => {
    if (reduced || from.current === value) { from.current = value; setDisplay(value); return; }
    const start = from.current;
    const startedAt = performance.now();
    let frame = requestAnimationFrame(function tick(now) {
      const progress = Math.min(1, (now - startedAt) / duration);
      const current = Math.round(start + (value - start) * easeOutCubic(progress));
      from.current = current;
      setDisplay(current);
      if (progress < 1) frame = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(frame);
  }, [value, reduced, duration]);
  return display;
}

/** True only for the first render of a component instance, so entrances play on first load and not on later updates. */
export function useFirstRender(): boolean {
  const first = useRef(true);
  useEffect(() => { first.current = false; }, []);
  return first.current;
}
