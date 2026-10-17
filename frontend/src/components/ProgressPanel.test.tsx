import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../i18n";
import en from "../locales/en.json";
import type { Progress, ProgressStep } from "../types";
import { formatElapsed, ProgressPanel } from "./ProgressPanel";

const step = (id: string, kind: ProgressStep["kind"], status: ProgressStep["status"], extra: Partial<ProgressStep> = {}): ProgressStep => ({ id, kind, label: id, status, ...extra });
const progress = (steps: ProgressStep[], extra: Partial<Progress> = {}): Progress => ({
  inspection_id: "i1", state: "running", stage: "OCR_PROCESSING", percent: 33, completed_steps: 2, total_steps: steps.length, started_at: null, elapsed_ms: 4200, steps, error: null, version: 1, ...extra,
});
const STEPS = [
  step("a:prepare", "prepare", "done", { panel: "BACK", duration_ms: 300 }),
  step("a:read:en", "read", "running", { panel: "BACK", language: "en" }),
  step("a:read:hi", "read", "pending", { panel: "BACK", language: "hi" }),
  step("link", "link", "pending"),
  step("rules", "rules", "pending", { count: 14 }),
  step("risk", "risk", "pending"),
];
const mount = (props: Partial<Parameters<typeof ProgressPanel>[0]> = {}) => render(<I18nProvider><ProgressPanel progress={progress(STEPS)} photos={[{ panel: "BACK", url: "blob:back" }]} {...props} /></I18nProvider>);

describe("progress panel", () => {
  afterEach(() => { cleanup(); vi.useRealTimers(); });

  it("lists every step in order with honest status, and the server percentage", () => {
    mount();
    const items = screen.getAllByRole("listitem");
    expect(items.map(item => item.getAttribute("data-status"))).toEqual(["done", "running", "pending", "pending", "pending", "pending"]);
    expect(items[0].textContent).toContain("Preparing photo (back panel)");
    expect(items[1].textContent).toContain("Reading English text");
    expect(items[2].textContent).toContain("Reading Hindi text");
    expect(items.slice(3).map(item => item.textContent?.split(" — ")[0])).toEqual(["Linking declarations", "Checking 14 rules", "Scoring risk"]);
    expect(items[0].textContent).toContain("0.3 s");
    const bar = screen.getByRole("progressbar", { name: en.progOverall });
    expect(bar.getAttribute("aria-valuenow")).toBe("33");
    expect((bar.firstElementChild as HTMLElement).style.width).toBe("33%");
  });

  it("shows the photo with a scan line while a reading step runs, and no scan line otherwise", () => {
    const { container } = mount();
    expect(screen.getByAltText(en.progPreviewAlt).getAttribute("src")).toBe("blob:back");
    expect(container.querySelector(".scan-frame__line")).toBeTruthy();
    cleanup();
    const idle = mount({ progress: progress(STEPS.map(item => ({ ...item, status: "done" as const })), { state: "completed", percent: 100 }) });
    expect(idle.container.querySelector(".scan-frame__line")).toBeNull();
    expect(screen.getAllByText(en.progAllDone).length).toBeGreaterThan(0);
  });

  it("shows a static scan line, with no animation class, under reduced motion", () => {
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("reduce"), addEventListener: () => undefined, removeEventListener: () => undefined }));
    const { container } = mount();
    expect(container.querySelector(".progress-panel")!.classList.contains("is-static")).toBe(true);
    vi.unstubAllGlobals();
  });

  it("announces the running step to screen readers", () => {
    mount();
    expect(screen.getByRole("status").textContent).toBe("Reading English text");
  });

  it("counts elapsed time up while running, from the server's elapsed time", () => {
    vi.useFakeTimers();
    mount();
    expect(screen.getByLabelText("Elapsed 0:04")).toBeTruthy();
    act(() => { vi.advanceTimersByTime(3000); });
    expect(screen.queryByLabelText("Elapsed 0:04")).toBeNull();
    expect(screen.getByLabelText(/Elapsed 0:0[67]/)).toBeTruthy();
  });

  it("shows a failed step with the error and a Try again button", () => {
    const onRetry = vi.fn();
    mount({
      progress: progress([step("a:prepare", "prepare", "done", { panel: "BACK" }), step("a:read:en", "read", "failed", { panel: "BACK", language: "en", message: "x" })], { state: "failed" }),
      error: { message: "raw server text", unreachable: false, stage: "OCR" }, onRetry,
    });
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain(en.progErrOcr);
    fireEvent.click(within(alert).getByRole("button", { name: en.tryAgain }));
    expect(onRetry).toHaveBeenCalled();
    expect(screen.getAllByRole("listitem")[1].getAttribute("data-status")).toBe("failed");
  });

  it("explains an unreachable server and keeps the photos", () => {
    mount({ progress: null, uploading: false, error: { message: "", unreachable: true }, onRetry: vi.fn() });
    expect(screen.getByRole("alert").textContent).toContain(en.progUnreachable);
  });

  it("shows an uploading step before the first server snapshot", () => {
    mount({ progress: null, uploading: true });
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(screen.getAllByText(en.progUploading).length).toBeGreaterThan(0);
  });

  it("formats elapsed time as m:ss", () => {
    expect(formatElapsed(0)).toBe("0:00");
    expect(formatElapsed(65_900)).toBe("1:05");
  });
});
