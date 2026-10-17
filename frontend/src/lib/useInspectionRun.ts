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

type Options = { onExpired: () => void };

/**
 * Upload photos (resized to what the backend's OCR reads), start background processing and follow
 * its live progress. One inspection is kept across retries: a failed or unreachable run resumes
 * from where it stopped instead of creating a second inspection or re-uploading finished photos.
 */
export function useInspectionRun({ onExpired }: Options) {
  const [phase, setPhase] = useState<RunPhase>("idle");
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState<RunError | null>(null);
  const [inspectionId, setInspectionId] = useState<string | null>(null);
  const created = useRef<string | null>(null);
  const uploaded = useRef(new Set<string>());
  const pending = useRef<CapturedPhoto[]>([]);
  const config = useRef<{ ocr_max_image_dimension: number } | null>(null);
  const controller = useRef<AbortController | null>(null);
  const active = useRef(true);

  useEffect(() => { active.current = true; return () => { active.current = false; controller.current?.abort(); }; }, []);

  const maxDimension = useCallback(async () => {
    if (!config.current) config.current = await getJson<{ ocr_max_image_dimension: number }>("/inspections/config", "config").catch(() => FALLBACK);
    return config.current.ocr_max_image_dimension || FALLBACK.ocr_max_image_dimension;
  }, []);

  /** Upload what is not yet uploaded, process, follow. `existing` adds photos to a finished inspection. */
  const run = useCallback(async (photos: CapturedPhoto[], existing?: string): Promise<Progress | null> => {
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    pending.current = photos;
    if (existing && created.current !== existing) { created.current = existing; uploaded.current = new Set(); }
    setError(null); setPhase("uploading"); setProgress(null);
    try {
      if (!created.current) {
        created.current = (await (await api("/inspections", "inspection", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })).json()).id as string;
      }
      const id = created.current;
      setInspectionId(id);
      const limit = await maxDimension();
      for (const photo of photos) {
        if (uploaded.current.has(photo.id)) continue;
        const prepared = await prepareForUpload(photo.blob, limit).catch(() => ({ blob: photo.blob }));
        const form = new FormData();
        form.append("image", prepared.blob, photo.name.replace(/\.\w+$/, prepared.blob.type === "image/png" ? ".png" : ".jpg"));
        form.append("panel", photo.panel);
        await api(`/inspections/${id}/image`, "upload", { method: "POST", body: form }, 120_000);
        uploaded.current.add(photo.id);
      }
      if (active.current) setPhase("processing");
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
      if (isExpired(caught)) { onExpired(); return null; }
      const detail = (caught as ApiError).detail;
      setError({ message: detail ?? "", unreachable: isUnavailable(caught) });
      setPhase("failed");
      return null;
    }
  }, [maxDimension, onExpired]);

  /** Try again with the same photos: finished uploads and finished OCR are kept. */
  const retry = useCallback(() => run(pending.current), [run]);

  const reset = useCallback(() => {
    controller.current?.abort();
    created.current = null; uploaded.current = new Set(); pending.current = [];
    setPhase("idle"); setProgress(null); setError(null); setInspectionId(null);
  }, []);

  return { phase, progress, error, inspectionId, run, retry, reset };
}
