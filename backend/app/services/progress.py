"""Live progress of an inspection's processing run.

Fine-grained steps live in memory only (a restart loses them; the stored
inspection status is the fallback). Every percentage is derived from steps that
have actually finished; nothing here is a timer or an estimate.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID

PENDING, RUNNING, DONE, FAILED = "pending", "running", "done", "failed"
STATE_IDLE, STATE_RUNNING, STATE_COMPLETED, STATE_FAILED = "idle", "running", "completed", "failed"
_KEEP = 300
_LANGUAGE_NAMES = {"en": "English", "hi": "Hindi", "mr": "Marathi", "ta": "Tamil", "te": "Telugu", "ur": "Urdu", "ne": "Nepali", "sa": "Sanskrit", "mai": "Maithili", "gom": "Konkani", "ka": "Kannada"}


def language_name(code: str) -> str:
    return _LANGUAGE_NAMES.get(code, code)


class AlreadyRunning(RuntimeError):
    """A processing run for this inspection is already in progress."""


@dataclass
class Step:
    id: str
    kind: str  # prepare | read | link | rules | risk
    label: str
    status: str = PENDING
    panel: str | None = None
    language: str | None = None
    languages: list[str] | None = None
    image_id: str | None = None
    count: int | None = None
    cached: bool = False
    message: str | None = None
    started_at: float | None = None
    duration_ms: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "label": self.label, "status": self.status, "panel": self.panel, "language": self.language, "languages": self.languages,
                "image_id": self.image_id, "count": self.count, "cached": self.cached, "message": self.message, "duration_ms": self.duration_ms}


class Reporter(Protocol):
    """What the workflow needs to report; the no-op ``NullReporter`` serves scripts and tests."""
    def start(self, step_id: str) -> None: ...
    def done(self, step_id: str, *, cached: bool = False) -> None: ...
    def finish(self, step_id: str, duration_ms: int, *, cached: bool = False) -> None: ...
    def fail(self, step_id: str | None, message: str, stage: str | None = None) -> None: ...
    def skip(self, step_id: str, message: str) -> None: ...
    def current_image(self, image_id: str | None) -> None: ...


class NullReporter:
    def start(self, step_id: str) -> None: ...
    def done(self, step_id: str, *, cached: bool = False) -> None: ...
    def finish(self, step_id: str, duration_ms: int, *, cached: bool = False) -> None: ...
    def fail(self, step_id: str | None, message: str, stage: str | None = None) -> None: ...
    def skip(self, step_id: str, message: str) -> None: ...
    def current_image(self, image_id: str | None) -> None: ...


@dataclass
class JobProgress:
    inspection_id: UUID
    steps: list[Step]
    state: str = STATE_RUNNING
    started_wall: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started: float = field(default_factory=time.monotonic)
    finished: float | None = None
    error: dict[str, Any] | None = None
    image_id: str | None = None
    version: int = 0
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    # ---------------------------------------------------------- reporter
    def _bump(self) -> None:
        self.version += 1

    def _step(self, step_id: str) -> Step | None:
        return next((step for step in self.steps if step.id == step_id), None)

    def start(self, step_id: str) -> None:
        with self._lock:
            step = self._step(step_id)
            if step and step.status == PENDING:
                step.status, step.started_at = RUNNING, time.monotonic()
                self._bump()

    def done(self, step_id: str, *, cached: bool = False) -> None:
        with self._lock:
            step = self._step(step_id)
            if step and step.status in (PENDING, RUNNING):
                if step.started_at is None:
                    step.started_at = time.monotonic()
                step.status, step.cached = DONE, cached
                step.duration_ms = round((time.monotonic() - step.started_at) * 1000)
                self._bump()

    def finish(self, step_id: str, duration_ms: int, *, cached: bool = False) -> None:
        """Mark a step done with a duration measured elsewhere (a photo read while the user was still capturing)."""
        with self._lock:
            step = self._step(step_id)
            if step and step.status in (PENDING, RUNNING):
                step.status, step.cached, step.duration_ms = DONE, cached, duration_ms
                self._bump()

    def fail(self, step_id: str | None, message: str, stage: str | None = None) -> None:
        with self._lock:
            step = self._step(step_id) if step_id else None
            step = step or next((s for s in self.steps if s.status == RUNNING), None) or next((s for s in self.steps if s.status == PENDING), None)
            if step:
                step.status, step.message = FAILED, message
                if step.started_at is not None:
                    step.duration_ms = round((time.monotonic() - step.started_at) * 1000)
            self.state, self.finished = STATE_FAILED, time.monotonic()
            self.error = {"message": message, "step_id": step.id if step else None, "stage": stage}
            self._bump()

    def skip(self, step_id: str, message: str) -> None:
        """A step failed but the run goes on without it (one unreadable photo): shown as failed, the run is not."""
        with self._lock:
            step = self._step(step_id)
            if step and step.status != DONE:
                step.status, step.message = FAILED, message
                if step.started_at is not None:
                    step.duration_ms = round((time.monotonic() - step.started_at) * 1000)
                self._bump()

    def current_image(self, image_id: str | None) -> None:
        with self._lock:
            self.image_id = image_id
            self._bump()

    def complete(self) -> None:
        with self._lock:
            for step in self.steps:
                if step.status in (PENDING, RUNNING):
                    self.done(step.id)
            self.state, self.finished, self.image_id = STATE_COMPLETED, time.monotonic(), None
            self._bump()

    # ---------------------------------------------------------- snapshot
    def _stage(self) -> str:
        """The persisted inspection status this run corresponds to."""
        if self.state == STATE_COMPLETED:
            return "COMPLETED"
        if self.state == STATE_FAILED:
            return "FAILED"
        running = next((step for step in self.steps if step.status == RUNNING), None) or next((step for step in self.steps if step.status == PENDING), None)
        return {"prepare": "OCR_PROCESSING", "read": "OCR_PROCESSING", "link": "EXTRACTION_PROCESSING"}.get(running.kind if running else "", "COMPLIANCE_PROCESSING")

    @property
    def terminal(self) -> bool:
        return self.state in (STATE_COMPLETED, STATE_FAILED)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            total = len(self.steps)
            finished = sum(1 for step in self.steps if step.status == DONE)
            end = self.finished if self.finished is not None else time.monotonic()
            return {
                "inspection_id": str(self.inspection_id),
                "state": self.state,
                "stage": self._stage(),
                "percent": 100 if self.state == STATE_COMPLETED else (round(100 * finished / total) if total else 0),
                "completed_steps": finished,
                "total_steps": total,
                "started_at": self.started_wall.isoformat(),
                "elapsed_ms": round((end - self.started) * 1000),
                "image_id": self.image_id,
                "steps": [step.as_dict() for step in self.steps],
                "error": self.error,
                "version": self.version,
            }


class ProgressStore:
    """Per-inspection progress, with a guard so one inspection never runs twice at once."""

    def __init__(self) -> None:
        self._jobs: OrderedDict[UUID, JobProgress] = OrderedDict()
        self._lock = threading.Lock()

    def begin(self, inspection_id: UUID, steps: list[Step]) -> JobProgress:
        with self._lock:
            current = self._jobs.get(inspection_id)
            if current is not None and current.state == STATE_RUNNING:
                raise AlreadyRunning(str(inspection_id))
            job = JobProgress(inspection_id, steps)
            self._jobs[inspection_id] = job
            self._jobs.move_to_end(inspection_id)
            while len(self._jobs) > _KEEP:
                oldest = next(iter(self._jobs))
                if self._jobs[oldest].state == STATE_RUNNING:
                    break
                self._jobs.popitem(last=False)
            return job

    def get(self, inspection_id: UUID) -> JobProgress | None:
        with self._lock:
            return self._jobs.get(inspection_id)

    def is_running(self, inspection_id: UUID) -> bool:
        job = self.get(inspection_id)
        return job is not None and job.state == STATE_RUNNING

    def discard(self, inspection_id: UUID) -> None:
        """Forget a finished run (a new photo makes its outcome stale); a running one is kept."""
        with self._lock:
            job = self._jobs.get(inspection_id)
            if job is not None and job.state != STATE_RUNNING:
                del self._jobs[inspection_id]

    def clear(self) -> None:
        with self._lock:
            self._jobs.clear()


progress_store = ProgressStore()


def stored_snapshot(inspection_id: UUID, status: str, failure_message: str | None) -> dict[str, Any]:
    """Progress for an inspection with no in-memory run: derived only from the persisted stage."""
    if status == "COMPLETED":
        state, error, percent = STATE_COMPLETED, None, 100
    elif status == "FAILED":
        state, percent = STATE_FAILED, 0
        error = {"message": failure_message or "Processing failed. Try again.", "step_id": None, "stage": "unknown"}
    elif status.endswith("_PROCESSING"):
        # Marked as running in the database but nothing is running here: the server restarted mid-run.
        state, percent = STATE_FAILED, 0
        error = {"message": "Processing was interrupted. Try again to continue from where it stopped.", "step_id": None, "stage": "interrupted"}
    else:
        state, error, percent = STATE_IDLE, None, 0
    return {"inspection_id": str(inspection_id), "state": state, "stage": status, "percent": percent, "completed_steps": 0, "total_steps": 0,
            "started_at": None, "elapsed_ms": 0, "image_id": None, "steps": [], "error": error, "version": 0}
