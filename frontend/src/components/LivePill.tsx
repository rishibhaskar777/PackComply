import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useI18n } from "../i18n";

export const HEALTH_POLL_MS = 30_000;

/** Polls /api/health/ready every 30 s; skips while the tab is hidden and checks again as soon as it is visible. */
export function useBackendHealth(interval = HEALTH_POLL_MS): boolean {
  const [healthy, setHealthy] = useState(false);
  useEffect(() => {
    let active = true;
    async function check() {
      if (document.hidden) return;
      try { await api("/health/ready", "health", {}, 5_000); if (active) setHealthy(true); }
      catch { if (active) setHealthy(false); }
    }
    function onVisibility() { if (!document.hidden) check(); }
    check();
    const timer = window.setInterval(check, interval);
    document.addEventListener("visibilitychange", onVisibility);
    return () => { active = false; window.clearInterval(timer); document.removeEventListener("visibilitychange", onVisibility); };
  }, [interval]);
  return healthy;
}

/** "Live" pill with a breathing dot, shown only while the backend reports ready. */
export function LivePill() {
  const { t } = useI18n();
  if (!useBackendHealth()) return null;
  return <span className="live-pill" title={t("liveTitle")}><span className="live-pill__dot" aria-hidden="true" />{t("live")}</span>;
}
