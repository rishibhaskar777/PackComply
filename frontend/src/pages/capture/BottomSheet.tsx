import { KeyboardEvent, PointerEvent as ReactPointerEvent, ReactNode, useEffect, useRef, useState } from "react";
import { useI18n } from "../../i18n";

/** The sheet's visible height as a share of its container at each rest position, smallest first. */
export const SNAPS = [0.3, 0.6, 0.92] as const;

/**
 * The rest position a release lands on. A fast flick goes to the next snap in its direction;
 * otherwise the nearest one wins. `position` is the visible share (0-1); `velocity` is share per second, up is positive.
 */
export function nearestSnap(position: number, velocity: number, snaps: readonly number[] = SNAPS): number {
  const FLICK = 0.9;
  const sorted = [...snaps].sort((a, b) => a - b);
  if (Math.abs(velocity) > FLICK) {
    const next = velocity > 0 ? sorted.find(snap => snap > position + 0.02) : [...sorted].reverse().find(snap => snap < position - 0.02);
    return next ?? (velocity > 0 ? sorted[sorted.length - 1] : sorted[0]);
  }
  return sorted.reduce((best, snap) => (Math.abs(snap - position) < Math.abs(best - position) ? snap : best), sorted[0]);
}

type Props = { label: string; children: ReactNode; footer?: ReactNode; snaps?: readonly number[]; initial?: number; onSnap?: (snap: number) => void; className?: string };

/** A draggable bottom sheet with snap points. The handle is also a button (and arrow-key slider) for non-touch use. */
export function BottomSheet({ label, children, footer, snaps = SNAPS, initial = 1, onSnap, className = "" }: Props) {
  const { t } = useI18n();
  const [snap, setSnap] = useState(snaps[Math.min(initial, snaps.length - 1)]);
  const [drag, setDrag] = useState<number | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const gesture = useRef<{ startY: number; startSnap: number; last: { y: number; t: number }; velocity: number; height: number } | null>(null);
  const top = snaps[snaps.length - 1];

  useEffect(() => { onSnap?.(snap); }, [snap]); // eslint-disable-line react-hooks/exhaustive-deps

  function onPointerDown(event: ReactPointerEvent<HTMLElement>) {
    const height = root.current?.parentElement?.getBoundingClientRect().height || window.innerHeight;
    gesture.current = { startY: event.clientY, startSnap: snap, last: { y: event.clientY, t: performance.now() }, velocity: 0, height };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }
  function onPointerMove(event: ReactPointerEvent<HTMLElement>) {
    const g = gesture.current;
    if (!g) return;
    const now = performance.now();
    const dt = Math.max(1, now - g.last.t);
    g.velocity = ((g.last.y - event.clientY) / g.height) / (dt / 1000);
    g.last = { y: event.clientY, t: now };
    const next = g.startSnap + (g.startY - event.clientY) / g.height;
    setDrag(Math.min(top, Math.max(snaps[0] * 0.6, next)));
  }
  function onPointerUp() {
    const g = gesture.current;
    gesture.current = null;
    if (!g || drag === null) { setDrag(null); return; }
    setSnap(nearestSnap(drag, g.velocity, snaps));
    setDrag(null);
  }
  function onKeyDown(event: KeyboardEvent) {
    const index = snaps.indexOf(snap);
    if (event.key === "ArrowUp" && index < snaps.length - 1) { event.preventDefault(); setSnap(snaps[index + 1]); }
    else if (event.key === "ArrowDown" && index > 0) { event.preventDefault(); setSnap(snaps[index - 1]); }
    else if (event.key === "Home") { event.preventDefault(); setSnap(snaps[0]); }
    else if (event.key === "End") { event.preventDefault(); setSnap(top); }
  }
  function cycle() {
    const index = snaps.indexOf(snap);
    setSnap(snaps[(index + 1) % snaps.length]);
  }

  const visible = drag ?? snap;
  const index = snaps.indexOf(snap);
  return (
    <div ref={root} className={`bottom-sheet${drag !== null ? " is-dragging" : ""} ${className}`.trim()} role="region" aria-label={label}
      style={{ height: `${top * 100}%`, transform: `translateY(${(1 - visible / top) * 100}%)` }}>
      <div className="bottom-sheet__grip" onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}>
        <button type="button" className="bottom-sheet__handle" role="slider" aria-label={t("resSheetResize")} aria-orientation="vertical"
          aria-valuemin={0} aria-valuemax={snaps.length - 1} aria-valuenow={index} aria-valuetext={t(index === snaps.length - 1 ? "resSheetFull" : index === 0 ? "resSheetPeek" : "resSheetHalf")}
          onKeyDown={onKeyDown} onClick={cycle}><span aria-hidden="true" /></button>
      </div>
      <div className={`bottom-sheet__body${snap === top ? " is-scrollable" : ""}`}>{children}</div>
      {footer && <div className="bottom-sheet__footer">{footer}</div>}
    </div>
  );
}
