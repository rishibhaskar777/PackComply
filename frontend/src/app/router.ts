import { useEffect, useState } from "react";

// Minimal hash routing: #/overview, #/inspections, #/inspections/:id, #/new,
// #/capture (the mobile field app; ?add=<inspection id>&panel=DATE_STAMP adds a photo to a record),
// #/risk, #/patterns, #/rulesets, #/reports, #/about.  Each navigation is a history
// entry, so the browser back button works without a router library.
// Manufacturer portal: #/products (home), #/products/:id (self-check result), #/check (?previous=<id> uploads a corrected
// label for that product), #/fixes, #/rules-guide; #/reports and #/about are shared and render per role.
export type Route =
  | { name: "overview" } | { name: "inspections" } | { name: "inspection"; id: string } | { name: "new" } | { name: "capture"; add?: string; panel?: string }
  | { name: "risk" } | { name: "patterns" } | { name: "rulesets" } | { name: "reports" } | { name: "about" }
  | { name: "products" } | { name: "product"; id: string } | { name: "check"; previous?: string } | { name: "fixes" } | { name: "guide" }
  | { name: "notfound" };
export type RouteName = Route["name"];

const SIMPLE: RouteName[] = ["overview", "inspections", "new", "risk", "patterns", "rulesets", "reports", "about", "products", "fixes"];
export const DEFAULT_PATH = "/overview";
export type Role = "OFFICIAL" | "MANUFACTURER";

/** Where each role lands after signing in. */
export const homePath = (role?: string) => (role === "MANUFACTURER" ? "/products" : "/overview");

const INSPECTOR_ONLY: RouteName[] = ["overview", "inspections", "inspection", "new", "risk", "patterns", "rulesets"];
const MANUFACTURER_ONLY: RouteName[] = ["products", "product", "check", "fixes", "guide"];

/**
 * The route a signed-in user is sent to instead, or null when they may stay. A manufacturer opening an inspector page
 * lands on its manufacturer equivalent (a deep link to a record becomes the product result); an inspector opening a
 * manufacturer page lands on the overview.
 */
export function redirectFor(route: Route, role?: string): string | null {
  if (role === "MANUFACTURER" && INSPECTOR_ONLY.includes(route.name)) {
    switch (route.name) {
      case "inspection": return `/products/${encodeURIComponent(route.id)}`;
      case "new": return "/check";
      case "patterns": return "/fixes";
      case "rulesets": return "/rules-guide";
      default: return "/products";
    }
  }
  if (role !== "MANUFACTURER" && MANUFACTURER_ONLY.includes(route.name)) return "/overview";
  return null;
}

/** The hashes that mean "no particular page": the app opens the default one instead of showing Not found. */
const isBlankHash = (hash: string) => ["", "/", LOGIN_PATH, FORGOT_PASSWORD_PATH].includes(hash.replace(/^#/, "").split("?")[0].replace(/\/+$/, ""));

/** The route for a hash: a blank one is the default page, an unknown one is the Not found page. */
export function routeFor(hash: string): Route {
  return parseHash(hash) ?? (isBlankHash(hash) ? { name: "overview" } : { name: "notfound" });
}

export function parseHash(hash: string): Route | null {
  const [rawPath, query = ""] = hash.replace(/^#/, "").split("?");
  const path = rawPath.replace(/\/+$/, "");
  if (path === "/capture") {
    const params = new URLSearchParams(query);
    return { name: "capture", add: params.get("add") ?? undefined, panel: params.get("panel") ?? undefined };
  }
  if (path === "/check") return { name: "check", previous: new URLSearchParams(query).get("previous") ?? undefined };
  if (path === "/rules-guide") return { name: "guide" };
  const detail = /^\/inspections\/([^/]+)$/.exec(path);
  if (detail) return { name: "inspection", id: decodeURIComponent(detail[1]) };
  const product = /^\/products\/([^/]+)$/.exec(path);
  if (product) return { name: "product", id: decodeURIComponent(product[1]) };
  const name = path.slice(1) as RouteName;
  return SIMPLE.includes(name) ? { name } as Route : null;
}

export function href(route: Route): string {
  if (route.name === "capture") return `#/capture${route.add ? `?add=${encodeURIComponent(route.add)}${route.panel ? `&panel=${encodeURIComponent(route.panel)}` : ""}` : ""}`;
  if (route.name === "check") return `#/check${route.previous ? `?previous=${encodeURIComponent(route.previous)}` : ""}`;
  if (route.name === "guide") return "#/rules-guide";
  if (route.name === "product") return `#/products/${encodeURIComponent(route.id)}`;
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
/** The address the demo script opens: the sign-in page when signed out, the default page once signed in. */
export const LOGIN_PATH = "/login";

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

const sameRoute = (a: Route, b: Route) => JSON.stringify(a) === JSON.stringify(b);

export function useHashRoute(): Route {
  const [route, setRoute] = useState<Route>(() => routeFor(window.location.hash));
  useEffect(() => {
    // A hashchange to the page already shown (a late or duplicate event) keeps the same route object, so nothing that
    // depends on the route (closing the menu, moving focus) fires for it.
    const update = () => setRoute(current => { const next = routeFor(window.location.hash); return sameRoute(current, next) ? current : next; });
    if (!parseHash(window.location.hash) && isBlankHash(window.location.hash)) navigate(DEFAULT_PATH, { replace: true });
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return route;
}
