"""Background processing inside the API process: a small thread pool, no broker, works on Windows.

Each run gets its own database session. The OCR engine, extraction service and
rule engine are the request's instances, so tests and alternate providers apply.
"""
from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import RLock
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from backend.app.services.progress import JobProgress, progress_store
from backend.app.services.workflow import InspectionWorkflow, WorkflowError, friendly_message

logger = logging.getLogger("uvicorn.error")

# OCR is CPU heavy: two runs at a time keeps one inspector's photos from starving another's.
_MAX_WORKERS = 2
_executor: ThreadPoolExecutor | None = None
_futures: dict[UUID, Future] = {}
_lock = RLock()

# Photos read ahead of the final process call (while the inspector captures the next panel). They run on
# their own pool so a process run waiting on them can never starve them of a worker.
_ocr_executor: ThreadPoolExecutor | None = None
_image_futures: dict[UUID, tuple[UUID, Future]] = {}  # image id -> (inspection id, future)


def _pool() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="inspection-run")
        return _executor


def _ocr_pool() -> ThreadPoolExecutor:
    global _ocr_executor
    with _lock:
        if _ocr_executor is None:
            _ocr_executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="image-ocr")
        return _ocr_executor


def shutdown() -> None:
    global _executor, _ocr_executor
    with _lock:
        pools, (_executor, _ocr_executor) = (_executor, _ocr_executor), (None, None)
    for pool in pools:
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)


def _read_ahead(inspection_id: UUID, image_id: UUID, factory: sessionmaker, workflow_args: tuple) -> None:
    try:
        with factory() as db:
            try:
                InspectionWorkflow(db, *workflow_args).read_ahead(inspection_id, image_id)
            except Exception:  # noqa: BLE001 - best effort: the process call reads the photo again if this failed
                db.rollback()
                logger.warning("Read-ahead OCR failed inspection_id=%s image_id=%s", inspection_id, image_id, exc_info=True)
    finally:
        with _lock:
            _image_futures.pop(image_id, None)


def start_image_ocr(workflow: InspectionWorkflow, inspection_id: UUID, image_id: UUID) -> Future:
    """Queue OCR for one photo. Asking again for a photo that is already queued or running returns the same future."""
    factory = sessionmaker(bind=workflow.db.get_bind(), expire_on_commit=False)
    args = (workflow.ocr, workflow.extraction, workflow.compliance, Path(workflow.upload_dir), workflow.max_upload_bytes, workflow.pattern_min_occurrences)
    with _lock:
        existing = _image_futures.get(image_id)
        if existing is not None:
            return existing[1]
        future = _ocr_pool().submit(_read_ahead, inspection_id, image_id, factory, args)
        _image_futures[image_id] = (inspection_id, future)
        return future


def image_ocr_in_flight(inspection_id: UUID) -> dict[UUID, Future]:
    with _lock:
        return {image_id: future for image_id, (owner, future) in _image_futures.items() if owner == inspection_id and not future.done()}


def await_image_ocr(inspection_id: UUID, job: JobProgress, factory: sessionmaker, timeout: float = 600.0) -> None:
    """Wait for photos still being read ahead, then record their real read time on this run's steps.

    A read step still pending was planned before its photo finished (or while it was queued); it is closed
    here with the stored read time. A photo whose read-ahead failed keeps its step and is read by the run.
    """
    from sqlalchemy import select
    from backend.app.models import OcrResult

    waiting = image_ocr_in_flight(inspection_id)
    for step in [step for step in job.steps if step.kind == "read" and step.status == "pending"]:
        image_id = UUID(step.image_id)
        future = waiting.get(image_id)
        if future is not None:
            job.start(step.id)
            try:
                future.result(timeout=timeout)
            except Exception:  # noqa: BLE001 - the process run reads a photo that failed again
                logger.warning("Read-ahead OCR did not finish inspection_id=%s image_id=%s", inspection_id, image_id, exc_info=True)
        with factory() as db:
            result = db.scalar(select(OcrResult).where(OcrResult.image_id == image_id).order_by(OcrResult.created_at.desc()))
        if result is not None:
            job.finish(f"{image_id}:prepare", 0)
            job.finish(step.id, result.duration_ms or 0, cached=not result.duration_ms)


def wait_for(inspection_id: UUID, timeout: float = 30.0) -> None:
    """Block until the inspection's current run (if any) ends. Used by tests and scripts, never by requests."""
    future = _futures.get(inspection_id)
    if future is not None:
        future.result(timeout=timeout)


def _run(inspection_id: UUID, job: JobProgress, factory: sessionmaker, workflow_args: tuple) -> None:
    try:
        await_image_ocr(inspection_id, job, factory)
        with factory() as db:
            workflow = InspectionWorkflow(db, *workflow_args)
            try:
                workflow.process(inspection_id, job)
            except WorkflowError as error:
                db.rollback()
                job.fail(None, friendly_message(error), error.stage)
                return
            except Exception:  # noqa: BLE001 - the run must always end in a terminal state
                db.rollback()
                logger.exception("Inspection run crashed inspection_id=%s", inspection_id)
                job.fail(None, "Something went wrong while processing. Try again.", "unknown")
                return
        job.complete()
    finally:
        _futures.pop(inspection_id, None)


def start(workflow: InspectionWorkflow, inspection_id: UUID) -> JobProgress:
    """Plan the run, register its progress (raising AlreadyRunning if one is in flight) and queue it."""
    steps = workflow.plan(inspection_id)
    job = progress_store.begin(inspection_id, steps)
    factory = sessionmaker(bind=workflow.db.get_bind(), expire_on_commit=False)
    args = (workflow.ocr, workflow.extraction, workflow.compliance, Path(workflow.upload_dir), workflow.max_upload_bytes, workflow.pattern_min_occurrences)
    try:
        _futures[inspection_id] = _pool().submit(_run, inspection_id, job, factory, args)
    except Exception:
        job.fail(None, "Processing could not be started. Try again.", "unknown")
        raise
    return job
