"""Process-wide OCR readiness, set by the startup warm-up and read by GET /api/system/status."""
from importlib.util import find_spec
from threading import Lock
from typing import Literal

OcrState = Literal["ready", "warming_up", "unavailable"]

_lock = Lock()
_state: OcrState | None = None


def set_ocr_state(state: OcrState | None) -> None:
    """Record the warm-up outcome; None forgets it (tests, or warm-up disabled)."""
    global _state
    with _lock:
        _state = state


def get_ocr_state() -> OcrState:
    """The warm-up outcome, or, when no warm-up ran, whether the OCR engine is installed at all."""
    with _lock:
        state = _state
    if state is not None:
        return state
    # Without a warm-up the models load on the first request; an installed engine counts as ready.
    return "ready" if find_spec("paddleocr") is not None else "unavailable"
