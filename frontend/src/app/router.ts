import { useEffect, useState } from "react";

// Minimal hash routing: #/overview, #/inspections, #/inspections/:id, #/new,
// #/risk, #/patterns, #/rulesets, #/reports.  Each navigation is a history
// entry, so the browser back button works without a router library.
export type Route =
  | { name: "overview" } | { name: "inspections" } | { name: "inspection"; id: string } | { name: "new" }
  | { name: "risk" } | { name: "patterns" } | { name: "rulesets" } | { name: "reports" };
export type RouteName = Route["name"];

const SIMPLE: RouteName[] = ["overview", "inspections", "new", "risk", "patterns", "rulesets", "reports"];
export const DEFAULT_PATH = "/overview";

export function parseHash(hash: string): Route | null {
  const path = hash.replace(/^#/, "").replace(/\/+$/, "");
  const detail = /^\/inspections\/([^/]+)$/.exec(path);
  if (detail) return { name: "inspection", id: decodeURIComponent(detail[1]) };
  const name = path.slice(1) as RouteName;
  return SIMPLE.includes(name) ? { name } as Route : null;
}

export function href(route: Route): string {
  return route.name === "inspection" ? `#/inspections/${encodeURIComponent(route.id)}` : `#/${route.name}`;
}

export function navigate(path: string, { replace = false } = {}) {
  const target = `#${path}`;
  if (window.location.hash === target) return;
  if (replace) {
    window.history.replaceState(window.history.state, "", target);
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  } else {
    window.location.hash = path;
  }
}

export function useHashRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash) ?? { name: "overview" });
  useEffect(() => {
    const update = () => setRoute(parseHash(window.location.hash) ?? { name: "overview" });
    if (!parseHash(window.location.hash)) navigate(DEFAULT_PATH, { replace: true });
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return route;
}
