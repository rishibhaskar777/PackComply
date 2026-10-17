import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Progress } from "../types";
import { API, mockApi, response } from "../test/mockApi";
import { followProgress } from "./progress";

const snapshot = (state: Progress["state"], percent: number, version: number): Progress => ({
  inspection_id: "i1", state, stage: null, percent, completed_steps: 0, total_steps: 3, started_at: null, elapsed_ms: version * 100,
  steps: [], error: state === "failed" ? { message: "boom", step_id: null } : null, version,
});

class FakeEventSource {
  static last: FakeEventSource | null = null;
  listeners: Record<string, ((event: MessageEvent) => void)[]> = {};
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string, public init?: EventSourceInit) { FakeEventSource.last = this; }
  addEventListener(name: string, listener: (event: MessageEvent) => void) { (this.listeners[name] ??= []).push(listener); }
  close() { this.closed = true; }
  emit(name: string, data: unknown) { this.listeners[name]?.forEach(listener => listener({ data: JSON.stringify(data) } as MessageEvent)); }
}
const Source = FakeEventSource as unknown as typeof EventSource;

describe("followProgress", () => {
  beforeEach(() => { FakeEventSource.last = null; });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("follows the server-sent events, resolves with the final snapshot and closes the stream", async () => {
    const updates: Progress[] = [];
    const done = followProgress("i1", { onUpdate: update => updates.push(update), eventSource: Source });
    const source = FakeEventSource.last!;
    expect(source.url).toBe(`${API}/inspections/i1/progress/stream`);
    expect(source.init).toEqual({ withCredentials: true });
    source.emit("progress", snapshot("running", 33, 1));
    source.emit("progress", snapshot("running", 66, 2));
    source.emit("progress", snapshot("completed", 100, 3));
    expect((await done).state).toBe("completed");
    expect(updates.map(update => update.percent)).toEqual([33, 66, 100]);
    expect(source.closed).toBe(true);
  });

  it("never lets an older snapshot move the display backwards", async () => {
    const updates: Progress[] = [];
    const done = followProgress("i1", { onUpdate: update => updates.push(update), eventSource: Source });
    const source = FakeEventSource.last!;
    source.emit("progress", snapshot("running", 66, 2));
    source.emit("progress", snapshot("running", 33, 1));
    source.emit("progress", snapshot("failed", 66, 3));
    expect((await done).state).toBe("failed");
    expect(updates.map(update => update.version)).toEqual([2, 3]);
  });

  it("polls every second when the browser has no EventSource", async () => {
    vi.useFakeTimers();
    const fetchMock = mockApi({ "GET /inspections/i1/progress": [response(snapshot("running", 33, 1)), response(snapshot("running", 66, 2)), response(snapshot("completed", 100, 3))] });
    const updates: Progress[] = [];
    const done = followProgress("i1", { onUpdate: update => updates.push(update), eventSource: null });
    await vi.advanceTimersByTimeAsync(0);
    expect(updates).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(999);
    expect(updates).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(updates).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(1000);
    expect((await done).percent).toBe(100);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("falls back to polling when the stream errors", async () => {
    vi.useFakeTimers();
    mockApi({ "GET /inspections/i1/progress": [response(snapshot("running", 50, 1)), response(snapshot("completed", 100, 2))] });
    const updates: Progress[] = [];
    const done = followProgress("i1", { onUpdate: update => updates.push(update), eventSource: Source });
    FakeEventSource.last!.onerror?.();
    await vi.advanceTimersByTimeAsync(0);
    expect(FakeEventSource.last!.closed).toBe(true);
    await vi.advanceTimersByTimeAsync(1000);
    expect((await done).state).toBe("completed");
    expect(updates.map(update => update.percent)).toEqual([50, 100]);
  });

  it("stops when aborted", async () => {
    const controller = new AbortController();
    const done = followProgress("i1", { onUpdate: () => undefined, eventSource: Source, signal: controller.signal });
    controller.abort();
    await expect(done).rejects.toMatchObject({ name: "AbortError" });
    expect(FakeEventSource.last!.closed).toBe(true);
  });
});
