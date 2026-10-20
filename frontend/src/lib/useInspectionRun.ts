import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, getJson, isExpired, isUnavailable } from "../api/client";
import { followProgress } from "../api/progress";
import type { Panel, Progress } from "../types";
import { prepareForUpload } from "./imageResize";

/** A photo held in memory until it has been uploaded. */
export type CapturedPhoto = { id: string; panel: Panel; blob: Blob; name: string; url: string };
export type RunPhase = "idle" | "uploading" | "processing" | "failed" | "done";
export type RunError = { message: string; unreachable: boolean; stage?: string | null };

const FALLBACK = { ocr_max_image_dimension: 1280 };
let counter = 0;

export function newPhoto(panel: Panel, blob: Blob, name = `${panel.toLowerCase()}.jpg`): CapturedPhoto {
  return { id: `photo-${++counter}`, panel, blob, name, url: URL.createObjectURL(blob) };
}
export const releasePhoto = (photo: CapturedPhoto) => URL.revokeObjectURL(photo.url);

type Options = { onExpired: () => void; /** A corrected label: the earlier self-check of the same product, so the new check continues it. */ previousInspectionId?: string };

/** One inspection being built: its id once created, what has been uploaded, and the uploads still in line. */
type Session = { id: string | null; uploaded: Set<string>; queue: Promise<void>; started: boolean };
const newSession = (): Session => ({ id: null, uploaded: new Set(), queue: Promise.resolve(), started: false });

/** Ask the server to drop an inspection that was only ever a draft (photos staged, processing never started). */
function discardDraft(s: Session) {
  void s.queue.then(() => (s.id && !s.started ? api(`/inspections/${s.id}`, "discard", { method: "DELETE" }).catch(() => undefined) : undefined));
}

/**
 * Upload photos (resized to what the backend's OCR reads), start background processing and follow
 * its live progress.
 *
 * Pipelining: `stage(photo)` creates the inspection on the first photo and uploads each photo as soon as
 * it is accepted, asking the server to start reading its text right away. By the time `run()` is called
 * only the last photo is still being read, so processing only has to merge and check. One inspection is
 * kept across retries: a failed or unreachable run resumes from where it stopped instead of creating a
 * second inspection or re-uploading finished photos.
 */
export function useInspectionRun({ onExpired, previousInspectionId }: Options) {
  const [phase, setPhase] = useState<RunPhase>("idle");
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState<RunError | null>(null);
  const [inspectionId, setInspectionId] = useState<string | null>(null);
  const session = useRef<Session>(newSession());
  const pending = useRef<CapturedPhoto[]>([]);
  const config = useRef<{ ocr_max_image_dimension: number } | null>(null);
  const controller = useRef<AbortController | null>(null);
  const active = useRef(true);
  const expired = useRef(onExpired);
  expired.current = onExpired;
  const previous = useRef(previousInspectionId);
  previous.current = previousInspectionId;

  const maxDimension = useCallback(async () => {
    if (!config.current) config.current = await getJson<{ ocr_max_image_dimension: number }>("/inspections/config", "config").catch(() => FALLBACK);
    return config.current.ocr_max_image_dimension || FALLBACK.ocr_max_image_dimension;
  }, []);

  /** Create the inspection on first use. Always called one at a time (inside the queue, or after it has drained). */
  const ensureInspection = useCallback(async (s: Session): Promise<string> => {
    if (!s.id) {
      s.id = (await (await api("/inspections", "inspection", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(previous.current ? { previous_inspection_id: previous.current } : {}) })).json()).id as string;
      if (active.current && session.current === s) setInspectionId(s.id);
    }
    return s.id;
  }, []);

  const uploadOne = useCallback(async (s: Session, photo: CapturedPhoto, readNow: boolean, signal?: AbortSignal) => {
    if (s.uploaded.has(photo.id)) return;
    const id = await ensureInspection(s);
    const limit = await maxDimension();
    // Artwork PDFs go up untouched: the backend renders page 1 and keeps the PDF's SHA-256 in the audit trail.
    const isPdf = photo.blob.type === "application/pdf";
    const prepared = isPdf ? { blob: photo.blob } : await prepareForUpload(photo.blob, limit).catch(() => ({ blob: photo.blob }));
    const form = new FormData();
    form.append("image", prepared.blob, isPdf ? photo.name : photo.name.replace(/\.\w+$/, prepared.blob.type === "image/png" ? ".png" : ".jpg"));
    form.append("panel", photo.panel);
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
    await api(`/inspections/${id}/image${readNow ? "?process_now=true" : ""}`, "upload", { method: "POST", body: form }, 120_000);
    s.uploaded.add(photo.id);
  }, [ensureInspection, maxDimension]);

  /**
   * Upload a photo now, in the background, and start reading it. Failures are not shown: `run()` uploads
   * whatever did not get through, so a flaky connection costs time, never photos.
   */
  const stage = useCallback((photo: CapturedPhoto) => {
    const s = session.current;
    s.queue = s.queue.then(() => uploadOne(s, photo, true)).catch(caught => { if (isExpired(caught)) expired.current(); });
  }, [uploadOne]);

  /** Wait for staged uploads, upload the rest, process, follow. `existing` adds photos to a finished inspection. */
  const run = useCallback(async (photos: CapturedPhoto[], existing?: string): Promise<Progress | null> => {
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    pending.current = photos;
    if (existing && session.current.id !== existing) session.current = { ...newSession(), id: existing, started: true };
    const s = session.current;
    setError(null); setPhase("uploading"); setProgress(null);
    try {
      await s.queue;
      for (const photo of photos) await uploadOne(s, photo, false, abort.signal);
      const id = await ensureInspection(s);
      s.started = true;
      if (active.current) { setInspectionId(id); setPhase("processing"); }
      try {
        const started: Progress = await (await api(`/inspections/${id}/process`, "process", { method: "POST" })).json();
        if (active.current) setProgress(started);
      } catch (startError) {
        // Already running (a reload, a second tab): just follow it.
        if ((startError as ApiError).status !== 409) throw startError;
      }
      const final = await followProgress(id, { signal: abort.signal, onUpdate: next => { if (active.current) setProgress(next); } });
      if (!active.current) return final;
      if (final.state === "failed") {
        setError({ message: final.error?.message ?? "", unreachable: false, stage: final.error?.stage });
        setPhase("failed");
        return final;
      }
      setPhase("done");
      return final;
    } catch (caught) {
      if ((caught as Error).name === "AbortError" || !active.current) return null;
      if (isExpired(caught)) { expired.current(); return null; }
      const detail = (caught as ApiError).detail;
      setError({ message: detail ?? "", unreachable: isUnavailable(caught) });
      setPhase("failed");
      return null;
    }
  }, [ensureInspection, uploadOne]);

  /** Try again with the same photos: finished uploads and finished OCR are kept. */
  const retry = useCallback(() => run(pending.current), [run]);

  /**
   * Forget the current inspection. If it was only a draft (photos staged, never processed) the server
   * deletes it, so abandoning the camera leaves nothing in the history.
   */
  const reset = useCallback(() => {
    controller.current?.abort();
    discardDraft(session.current);
    session.current = newSession(); pending.current = [];
    setPhase("idle"); setProgress(null); setError(null); setInspectionId(null);
  }, []);

  useEffect(() => {
    active.current = true;
    return () => { active.current = false; controller.current?.abort(); discardDraft(session.current); };
  }, []);

  return { phase, progress, error, inspectionId, run, retry, reset, stage };
}
