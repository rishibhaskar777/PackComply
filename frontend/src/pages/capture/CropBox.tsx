import { PointerEvent, useRef } from "react";
import { CropFraction, CropHandle, dragCrop } from "../../lib/cropMath";

type Props = { value: CropFraction; onChange: (value: CropFraction) => void; label: string };

const HANDLES: CropHandle[] = ["nw", "ne", "sw", "se"];

/** A draggable crop rectangle laid over its parent (which must be positioned and sized like the photo). Drag the box to move it, a corner to resize. */
export function CropBox({ value, onChange, label }: Props) {
  const drag = useRef<{ handle: CropHandle; x: number; y: number; start: CropFraction; width: number; height: number } | null>(null);

  function begin(handle: CropHandle, event: PointerEvent<HTMLElement>) {
    const parent = event.currentTarget.closest(".cap-crop")?.getBoundingClientRect();
    if (!parent?.width || !parent.height) return;
    event.stopPropagation();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    drag.current = { handle, x: event.clientX, y: event.clientY, start: value, width: parent.width, height: parent.height };
  }
  function move(event: PointerEvent<HTMLElement>) {
    const state = drag.current;
    if (state) onChange(dragCrop(state.start, state.handle, (event.clientX - state.x) / state.width, (event.clientY - state.y) / state.height));
  }
  const end = () => { drag.current = null; };

  return (
    <div className="cap-crop" data-testid="crop-box">
      <div className="cap-crop__box" role="group" aria-label={label}
        style={{ left: `${value.x * 100}%`, top: `${value.y * 100}%`, width: `${value.w * 100}%`, height: `${value.h * 100}%` }}
        onPointerDown={event => begin("move", event)} onPointerMove={move} onPointerUp={end} onPointerCancel={end}>
        {HANDLES.map(handle => (
          <span key={handle} className={`cap-crop__handle cap-crop__handle--${handle}`} data-handle={handle}
            onPointerDown={event => begin(handle, event)} onPointerMove={move} onPointerUp={end} onPointerCancel={end} />
        ))}
      </div>
    </div>
  );
}
