import { vi } from "vitest";

export type MockResponse = { ok: boolean; status: number; json: () => Promise<unknown>; blob: () => Promise<Blob> };
type Reply = MockResponse | (() => Promise<MockResponse>);

export const response = (body: unknown, ok = true, status = 200): MockResponse => ({
  ok, status, json: async () => body, blob: async () => new Blob(["%PDF-test"], { type: "application/pdf" }),
});

export const API = "http://localhost:8000/api";
export const emptySummary = { period: "all", total_inspections: 0, verdict_counts: { NON_COMPLIANT: 0, NEEDS_REVIEW: 0, COMPLIANT: 0, PENDING: 0 }, top_risk: [], repeat_issues: [], median_processing_seconds: null, ruleset_version: "2" };
export const emptyRiskPage = { items: [], total: 0, page: 1, page_size: 20, total_pages: 0, summary: { CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0 } };

/**
 * Route fetch by "METHOD /path" (query string ignored). Each route replies with its
 * queued responses in order and keeps repeating the last one; unknown routes get a 404.
 */
export function mockApi(routes: Record<string, Reply | Reply[]>) {
  const queues = new Map(Object.entries(routes).map(([key, value]) => [key, Array.isArray(value) ? [...value] : [value]]));
  const defaults: Record<string, Reply> = { "GET /dashboard/summary": response(emptySummary), "GET /risk-targeting": response(emptyRiskPage) };
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url).pathname.replace(/^\/api/, "");
    const key = `${init?.method ?? "GET"} ${path}`;
    const queue = queues.get(key);
    const reply = queue ? (queue.length > 1 ? queue.shift()! : queue[0]) : defaults[key] ?? response({ detail: "Not found" }, false, 404);
    return typeof reply === "function" ? reply() : reply;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

export const calledPaths = (fetchMock: ReturnType<typeof mockApi>) => fetchMock.mock.calls.map(call => String(call[0]).replace(API, ""));
