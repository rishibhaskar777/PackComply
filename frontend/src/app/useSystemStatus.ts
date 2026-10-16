import { useEffect, useState } from "react";
import { getJson } from "../api/client";

/** GET /api/system/status (public). */
export type SystemStatus = {
  status: "ok" | "degraded" | "down";
  database: boolean;
  ocr: "ready" | "warming_up" | "unavailable";
  ruleset_version: string;
  rule_count: number;
  languages: string[];
};

/** "loading" until the first answer; "offline" when the API cannot be reached. */
export type StatusState = { phase: "loading" } | { phase: "offline" } | { phase: "ready"; data: SystemStatus };

export const STATUS_POLL_MS = 30_000;
export const FALLBACK_RULE_COUNT = 14;

/** Polls the public status endpoint every 30 s while the tab is visible, and once more when it becomes visible. */
export function useSystemStatus(): StatusState {
  const [state, setState] = useState<StatusState>({ phase: "loading" });
  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    async function poll() {
      window.clearTimeout(timer);
      try {
        const data = await getJson<SystemStatus>("/system/status", "system status");
        if (active) setState({ phase: "ready", data });
      } catch {
        if (active) setState({ phase: "offline" });
      }
      if (active && !document.hidden) timer = window.setTimeout(poll, STATUS_POLL_MS);
    }
    function onVisibility() {
      if (document.hidden) window.clearTimeout(timer);
      else poll();
    }
    poll();
    document.addEventListener("visibilitychange", onVisibility);
    return () => { active = false; window.clearTimeout(timer); document.removeEventListener("visibilitychange", onVisibility); };
  }, []);
  return state;
}
