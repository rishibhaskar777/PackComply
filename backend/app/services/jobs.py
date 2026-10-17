"""Background processing inside the API process: a small thread pool, no broker, works on Windows.

Each run gets its own database session. The OCR engine, extraction service and
rule engine are the request's instances, so tests and alternate providers apply.
"""
from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from backend.app.services.progress import JobProgress, progress_store
from backend.app.services.workflow import InspectionWorkflow, WorkflowError, friendly_message

logger = logging.getLogger("uvicorn.error")

# OCR is CPU heavy: two runs at a time keeps one inspector's photos from starving another's.
_MAX_WORKERS = 2
_executor: ThreadPoolExecutor | None = None
_futures: dict[UUID, Future] = {}
_lock = Lock()


def _pool() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="inspection-run")
        return _executor


def shutdown() -> None:
    global _executor
    with _lock:
        pool, _executor = _executor, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


def wait_for(inspection_id: UUID, timeout: float = 30.0) -> None:
    """Block until the inspection's current run (if any) ends. Used by tests and scripts, never by requests."""
    future = _futures.get(inspection_id)
    if future is not None:
        future.result(timeout=timeout)


def _run(inspection_id: UUID, job: JobProgress, factory: sessionmaker, workflow_args: tuple) -> None:
    try:
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
