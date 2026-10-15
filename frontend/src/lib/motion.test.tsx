import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CountUp } from "../components/ui/CountUp";
import { COUNT_UP_MS, prefersReducedMotion } from "./motion";

function mockReducedMotion(reduce: boolean) {
  vi.stubGlobal("matchMedia", vi.fn((query: string) => ({
    matches: query.includes("prefers-reduced-motion") ? reduce : false, media: query,
    addEventListener: () => undefined, removeEventListener: () => undefined,
  })));
}
const count = () => screen.getByTestId("count").textContent;
const advance = (ms: number) => act(() => { vi.advanceTimersByTime(ms); });

describe("reduced motion", () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

  it("shows the final count-up value immediately when the user prefers reduced motion", () => {
    mockReducedMotion(true);
    render(<span data-testid="count"><CountUp value={42} /></span>);
    expect(prefersReducedMotion()).toBe(true);
    expect(count()).toBe("42");
  });

  it("counts up from 0 to the value otherwise, easing out over about 800 ms", () => {
    vi.useFakeTimers({ toFake: ["requestAnimationFrame", "cancelAnimationFrame", "performance"] });
    mockReducedMotion(false);
    render(<span data-testid="count"><CountUp value={42} /></span>);
    expect(count()).toBe("0");
    advance(COUNT_UP_MS / 2);
    // Ease-out: well past the linear midpoint at half time, not finished yet.
    expect(Number(count())).toBeGreaterThan(21);
    expect(Number(count())).toBeLessThan(42);
    advance(COUNT_UP_MS);
    expect(count()).toBe("42");
  });

  it("animates from the previous value when it changes", () => {
    vi.useFakeTimers({ toFake: ["requestAnimationFrame", "cancelAnimationFrame", "performance"] });
    mockReducedMotion(false);
    const { rerender } = render(<span data-testid="count"><CountUp value={10} /></span>);
    advance(COUNT_UP_MS + 50);
    expect(count()).toBe("10");
    rerender(<span data-testid="count"><CountUp value={20} /></span>);
    advance(50);
    expect(Number(count())).toBeGreaterThan(10);
    advance(COUNT_UP_MS);
    expect(count()).toBe("20");
  });

  it("treats an environment without matchMedia as reduced (instant is always safe)", () => {
    vi.stubGlobal("matchMedia", undefined);
    expect(prefersReducedMotion()).toBe(true);
  });
});
