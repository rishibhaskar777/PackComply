import { CSSProperties, PointerEvent as ReactPointerEvent, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Maximize, Minus, Plus, ScanText } from "lucide-react";
import { Button, Sheet } from "../../components/ui";
import { fill, useI18n } from "../../i18n";
import { clampPan, FindingItem, GROUP_TONE, markerAnchor, MAX_ZOOM, MIN_ZOOM, boxRect, panToRect, scaleRect, Size, sourceSize, unionRect, zoomAt } from "../../lib/evidence";
import { useReducedMotion } from "../../lib/motion";
import { BBox, OcrLine, Summary } from "../../types";

type Props = {
  src: string | null;
  summary: Summary;
  items: FindingItem[];
  selectedId: string | null;
  hoveredId: string | null;
  onSelect: (id: string | null) => void;
  onHover: (id: string | null) => void;
};

const ZOOM_STEP = 1.5;
/** Markers that share a corner (two rules on one field) are fanned out by this many pixels. */
const MARKER_FAN = 18;

/** The photo with numbered evidence markers, zoom (buttons, ctrl/pinch wheel, touch pinch), drag-to-pan and the raw OCR text sheet. */
export function EvidencePhoto({ src, summary, items, selectedId, hoveredId, onSelect, onHover }: Props) {
  const { t } = useI18n();
  const reduced = useReducedMotion();
  const frame = useRef<HTMLDivElement>(null);
  const [natural, setNatural] = useState<Size | null>(null);
  const [rendered, setRendered] = useState<Size | null>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const [ocrOpen, setOcrOpen] = useState(false);
  const [ocrLine, setOcrLine] = useState<number | null>(null);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const gesture = useRef<{ distance: number; zoom: number } | null>(null);
  const moved = useRef(false);

  const lines: OcrLine[] = summary.ocr?.results ?? [];
  const allBoxes = useMemo(() => [...items.flatMap(item => item.boxes ?? []), ...lines.flatMap(line => (line.bbox ? [line.bbox] : []))], [items, lines]);
  const source = useMemo(() => sourceSize(summary.image, natural, allBoxes), [summary.image, natural, allBoxes]);

  // The rendered size follows the frame, so markers realign when the window or column is resized.
  useLayoutEffect(() => {
    const element = frame.current;
    if (!element) return;
    const measure = () => { const box = element.getBoundingClientRect(); if (box.width > 0) setRendered({ w: box.width, h: box.height }); };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [source?.w, source?.h]);

  // Re-clamp the pan whenever the frame changes size.
  useEffect(() => { if (rendered) setPan(current => clampPan(current, zoom, rendered)); }, [rendered, zoom]);

  const selected = items.find(item => item.finding.rule_id === selectedId) ?? null;
  const focusRect = useMemo(() => {
    if (!source || !rendered) return null;
    const rect = ocrLine !== null && lines[ocrLine]?.bbox ? boxRect(lines[ocrLine].bbox as BBox) : unionRect(selected?.boxes);
    return rect ? scaleRect(rect, source, rendered) : null;
  }, [source, rendered, selected, ocrLine, lines]);

  // Selecting a finding (or an OCR line) brings its box to the centre when the photo is zoomed in.
  useEffect(() => {
    if (focusRect && rendered && zoom > 1) setPan(panToRect(focusRect, zoom, rendered));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusRect]);

  const setZoomAround = useCallback((next: number, point?: { x: number; y: number }) => {
    if (!rendered) return;
    const around = point ?? { x: rendered.w / 2, y: rendered.h / 2 };
    const result = zoomAt(pan, zoom, next, around, rendered);
    setZoom(result.zoom); setPan(result.pan);
  }, [pan, zoom, rendered]);

  // React registers wheel listeners as passive, so the zoom gesture is attached natively.
  useEffect(() => {
    const element = frame.current;
    if (!element) return;
    function onWheel(event: WheelEvent) {
      if (!(event.ctrlKey || event.metaKey) || !rendered) return;
      event.preventDefault();
      const box = element!.getBoundingClientRect();
      setZoomAround(zoom * Math.exp(-event.deltaY * 0.01), { x: event.clientX - box.left, y: event.clientY - box.top });
    }
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [zoom, rendered, setZoomAround]);

  function onPointerDown(event: ReactPointerEvent) {
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    moved.current = false;
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      gesture.current = { distance: Math.hypot(a.x - b.x, a.y - b.y), zoom };
    }
    (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
  }
  function onPointerMove(event: ReactPointerEvent) {
    const previous = pointers.current.get(event.pointerId);
    if (!previous || !rendered) return;
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointers.current.size === 2 && gesture.current) {
      const [a, b] = [...pointers.current.values()];
      const box = frame.current!.getBoundingClientRect();
      setZoomAround(gesture.current.zoom * (Math.hypot(a.x - b.x, a.y - b.y) / gesture.current.distance), { x: (a.x + b.x) / 2 - box.left, y: (a.y + b.y) / 2 - box.top });
      moved.current = true;
      return;
    }
    if (zoom > 1) {
      const dx = event.clientX - previous.x, dy = event.clientY - previous.y;
      if (Math.abs(dx) + Math.abs(dy) > 0) { moved.current = true; setDragging(true); }
      setPan(current => clampPan({ x: current.x + dx, y: current.y + dy }, zoom, rendered));
    }
  }
  function onPointerUp(event: ReactPointerEvent) {
    pointers.current.delete(event.pointerId);
    if (pointers.current.size < 2) gesture.current = null;
    if (!pointers.current.size) setDragging(false);
  }
  function fit() { setZoom(1); setPan({ x: 0, y: 0 }); }

  const aspect = source ? `${source.w} / ${source.h}` : "3 / 4";
  const anchors = useMemo(() => {
    if (!source || !rendered) return [];
    const seen = new Map<string, number>();
    return items.flatMap(item => {
      const at = item.number !== null && item.boxes ? markerAnchor(item.boxes, source, rendered) : null;
      if (!at) return [];
      const key = `${Math.round(at.x / 6)}:${Math.round(at.y / 6)}`;
      const fan = seen.get(key) ?? 0;
      seen.set(key, fan + 1);
      return [{ item, x: at.x + fan * MARKER_FAN, y: at.y }];
    });
  }, [items, source, rendered]);
  const outlines = useMemo(() => {
    if (!source || !rendered) return [];
    const active = hoveredId ?? selectedId;
    const item = items.find(entry => entry.finding.rule_id === active);
    const boxes = ocrLine !== null && lines[ocrLine]?.bbox ? [lines[ocrLine].bbox as BBox] : item?.boxes ?? [];
    const union = unionRect(boxes);
    return union ? [{ ...scaleRect(union, source, rendered), whole: true }, ...boxes.flatMap(box => { const rect = boxRect(box); return rect ? [{ ...scaleRect(rect, source, rendered), whole: false }] : []; })] : [];
  }, [items, source, rendered, selectedId, hoveredId, ocrLine, lines]);

  const layerStyle: CSSProperties = { transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`, transition: dragging || reduced ? "none" : "transform var(--duration-base) var(--ease-out)" };

  return (
    <div className="evidence-viewer">
      <div className="evidence-toolbar">
        <Button size="sm" variant="outline" icon={<ScanText size={16} aria-hidden="true" />} onClick={() => setOcrOpen(true)} disabled={!lines.length}>{t("recShowOcrText")}</Button>
        <div className="evidence-zoom" role="group" aria-label={t("recZoom")}>
          <Button size="icon" variant="ghost" aria-label={t("recZoomOut")} onClick={() => setZoomAround(zoom / ZOOM_STEP)} disabled={zoom <= MIN_ZOOM}><Minus size={16} aria-hidden="true" /></Button>
          <span className="evidence-zoom__level mono tabular" aria-live="polite">{Math.round(zoom * 100)}%</span>
          <Button size="icon" variant="ghost" aria-label={t("recZoomIn")} onClick={() => setZoomAround(zoom * ZOOM_STEP)} disabled={zoom >= MAX_ZOOM}><Plus size={16} aria-hidden="true" /></Button>
          <Button size="sm" variant="ghost" icon={<Maximize size={16} aria-hidden="true" />} onClick={fit} disabled={zoom === 1 && pan.x === 0 && pan.y === 0}>{t("recFit")}</Button>
        </div>
      </div>
      <div ref={frame} className={`evidence-frame${zoom > 1 ? " is-zoomed" : ""}${dragging ? " is-dragging" : ""}`} style={{ aspectRatio: aspect }}
        onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}
        onClick={event => { if (!(event.target as HTMLElement).closest(".evidence-marker") && !moved.current) { onSelect(null); setOcrLine(null); } }}>
        <div className="evidence-layer" style={layerStyle}>
          {src ? <img className="evidence-image" src={src} alt={t("recPhotoAlt")} draggable={false}
            onLoad={event => setNatural({ w: event.currentTarget.naturalWidth, h: event.currentTarget.naturalHeight })} />
            : <div className="evidence-placeholder">{t("noImage")}</div>}
          {outlines.map((rect, index) => <span key={index} className={`evidence-box${rect.whole ? " evidence-box--whole" : ""}`} style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h }} aria-hidden="true" />)}
          {anchors.map(({ item, x, y }) => {
            const id = item.finding.rule_id, tone = GROUP_TONE[item.group];
            const state = id === selectedId ? " is-selected" : id === hoveredId ? " is-hovered" : "";
            return (
              <button key={id} type="button" className={`evidence-marker evidence-marker--${tone}${state}`} style={{ left: x, top: y, ["--zoom" as string]: zoom }} data-marker={id}
                aria-label={fill(t("recMarkerLabel"), { number: item.number ?? 0, title: item.finding.rule_name })} aria-pressed={id === selectedId}
                onPointerDown={event => event.stopPropagation()} onClick={() => { setOcrLine(null); onSelect(id === selectedId ? null : id); }}
                onMouseEnter={() => onHover(id)} onMouseLeave={() => onHover(null)} onFocus={() => onHover(id)} onBlur={() => onHover(null)}>
                <span className="evidence-marker__number tabular">{item.number}</span>
              </button>
            );
          })}
        </div>
      </div>
      <p className="evidence-caption">{t("recEvidenceCaption")}{summary.image?.checksum ? <> <span className="mono" title={summary.image.checksum}>{summary.image.checksum.slice(0, 8)}…</span></> : null}</p>
      <Sheet open={ocrOpen} onClose={() => setOcrOpen(false)} title={t("recOcrTitle")} description={summary.ocr ? `${summary.ocr.languages.join(" + ")}${summary.ocr.duration_ms ? ` · ${Math.round(summary.ocr.duration_ms / 1000)} s` : ""}` : undefined}>
        <ol className="ocr-lines">
          {lines.map((line, index) => (
            <li key={index}>
              <button type="button" className={`ocr-line${ocrLine === index ? " is-active" : ""}`} disabled={!line.bbox} aria-pressed={ocrLine === index}
                onClick={() => { onSelect(null); setOcrLine(ocrLine === index ? null : index); if (window.matchMedia?.("(max-width: 639px)").matches) setOcrOpen(false); }}>
                <span className="ocr-line__text">{line.text}</span>
                {line.confidence !== null && <span className="ocr-line__confidence mono tabular">{Math.round(line.confidence * 100)}%</span>}
              </button>
            </li>
          ))}
        </ol>
      </Sheet>
    </div>
  );
}

