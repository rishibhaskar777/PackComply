import { useEffect } from "react";
import { prefersReducedMotion } from "./motion";

const SURFACES = ".card, .stat-card";

/**
 * One delegated pointer listener for every card: writes the cursor position into --spot-x/--spot-y
 * on the hovered card, at most once per animation frame. Off for touch, coarse pointers and reduced motion.
 */
export function useSpotlight() {
  useEffect(() => {
    if (prefersReducedMotion() || !window.matchMedia("(hover: hover) and (pointer: fine)").matches) return;
    let frame = 0;
    let last: PointerEvent | null = null;
    function update() {
      frame = 0;
      const surface = last && (last.target as Element | null)?.closest?.<HTMLElement>(SURFACES);
      if (!last || !surface) return;
      const rect = surface.getBoundingClientRect();
      surface.style.setProperty("--spot-x", `${last.clientX - rect.left}px`);
      surface.style.setProperty("--spot-y", `${last.clientY - rect.top}px`);
    }
    function onPointerMove(event: PointerEvent) {
      last = event;
      if (!frame) frame = requestAnimationFrame(update);
    }
    document.addEventListener("pointermove", onPointerMove, { passive: true });
    return () => { document.removeEventListener("pointermove", onPointerMove); cancelAnimationFrame(frame); };
  }, []);
}
