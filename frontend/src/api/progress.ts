import { API_URL, api } from "./client";
import type { Progress } from "../types";

export const POLL_INTERVAL_MS = 1000;

export type FollowOptions = {
  onUpdate: (progress: Progress) => void;
  signal?: AbortSignal;
  /** Test seam: the EventSource constructor to use; `null` forces polling. */
  eventSource?: typeof EventSource | null;
  pollMs?: number;
};

const TERMINAL = (progress: Progress) => progress.state === "completed" || progress.state === "failed";

/**
 * Follow an inspection run until it ends, resolving with the final progress.
 *
 * Uses Server-Sent Events when the browser has them; if the stream cannot be opened or drops
 * before the run ends, it falls back to polling GET /progress every second. Either way every
 * update is the server's own snapshot: nothing is estimated here.
 */
export function followProgress(inspectionId: string, { onUpdate, signal, eventSource, pollMs = POLL_INTERVAL_MS }: FollowOptions): Promise<Progress> {
  const path = `/inspections/${encodeURIComponent(inspectionId)}/progress`;
  const Source = eventSource === undefined ? (typeof EventSource === "undefined" ? null : EventSource) : eventSource;

  return new Promise<Progress>((resolve, reject) => {
    let settled = false;
    let source: EventSource | null = null;
    let timer: number | undefined;
    let lastVersion = -1;

    const cleanup = () => {
      settled = true;
      source?.close(); source = null;
      window.clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    };
    const finish = (progress: Progress) => { if (settled) return; cleanup(); resolve(progress); };
    const fail = (error: unknown) => { if (settled) return; cleanup(); reject(error); };
    function onAbort() { fail(new DOMException("Aborted", "AbortError")); }
    if (signal?.aborted) return onAbort();
    signal?.addEventListener("abort", onAbort);

    const accept = (progress: Progress) => {
      if (settled) return;
      // A stale snapshot (reconnect after a newer one) must never move the display backwards.
      if (progress.version !== undefined && progress.version < lastVersion) return;
      lastVersion = progress.version ?? lastVersion;
      onUpdate(progress);
      if (TERMINAL(progress)) finish(progress);
    };

    const poll = async () => {
      if (settled) return;
      try {
        accept(await (await api(path, "progress", { signal: undefined }, 15_000)).json());
      } catch (error) {
        // 401/404 will not fix themselves; network blips are retried on the next tick.
        const status = (error as { status?: number }).status;
        if (status === 401 || status === 404) return fail(error);
      }
      if (!settled) timer = window.setTimeout(poll, pollMs);
    };

    if (!Source) { void poll(); return; }
    try {
      source = new Source(`${API_URL}${path}/stream`, { withCredentials: true });
    } catch {
      void poll(); return;
    }
    source.addEventListener("progress", event => {
      try { accept(JSON.parse((event as MessageEvent).data)); } catch { /* ignore a malformed frame; the next one replaces it */ }
    });
    source.addEventListener("done", () => {
      // The server closes the stream after the last snapshot; if that one was lost, ask once.
      if (!settled) { source?.close(); source = null; void poll(); }
    });
    source.onerror = () => {
      // EventSource would retry on its own, but a hidden proxy buffering SSE would stall us: poll instead.
      if (settled) return;
      source?.close(); source = null;
      void poll();
    };
  });
}
