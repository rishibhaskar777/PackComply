import { useEffect, useState } from "react";

// Minimal hash routing: #/overview, #/inspections, #/inspections/:id, #/new,
// #/capture (the mobile field app; ?add=<inspection id>&panel=DATE_STAMP adds a photo to a record),
// #/risk, #/patterns, #/rulesets, #/reports.  Each navigation is a history
// entry, so the browser back button works without a router library.
export type Route =
  | { name: "overview" } | { name: "inspections" } | { name: "inspection"; id: string } | { name: "new" } | { name: "capture"; add?: string; panel?: string }
  | { name: "risk" } | { name: "patterns" } | { name: "rulesets" } | { name: "reports" };
export type RouteName = Route["name"];

const SIMPLE: RouteName[] = ["overview", "inspections", "new", "risk", "patterns", "rulesets", "reports"];
export const DEFAULT_PATH = "/overview";

export function parseHash(hash: string): Route | null {
  const [rawPath, query = ""] = hash.replace(/^#/, "").split("?");
  const path = rawPath.replace(/\/+$/, "");
  if (path === "/capture") {
    const params = new URLSearchParams(query);
    return { name: "capture", add: params.get("add") ?? undefined, panel: params.get("panel") ?? undefined };
  }
  const detail = /^\/inspections\/([^/]+)$/.exec(path);
  if (detail) return { name: "inspection", id: decodeURIComponent(detail[1]) };
  const name = path.slice(1) as RouteName;
  return SIMPLE.includes(name) ? { name } as Route : null;
}

export function href(route: Route): string {
  if (route.name === "capture") return `#/capture${route.add ? `?add=${encodeURIComponent(route.add)}${route.panel ? `&panel=${encodeURIComponent(route.panel)}` : ""}` : ""}`;
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

// Signed-out routes, handled by the sign-in page rather than the workspace.
export const FORGOT_PASSWORD_PATH = "/forgot-password";

/** The current hash path (e.g. "/forgot-password"), updated on navigation. */
export function useHashPath(): string {
  const read = () => window.location.hash.replace(/^#/, "").replace(/\/+$/, "");
  const [path, setPath] = useState(read);
  useEffect(() => {
    const update = () => setPath(read());
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return path;
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
