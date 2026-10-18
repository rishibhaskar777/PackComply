import { describe, expect, it, vi } from "vitest";
import { resolveApiUrl } from "./apiUrl";

describe("API URL resolution", () => {
  it("uses the relative /api proxy path when VITE_API_URL is not set, on http and https, localhost or LAN", () => {
    for (const protocol of ["http:", "https:"]) {
      expect(resolveApiUrl(undefined, { protocol }, true)).toBe("/api");
      expect(resolveApiUrl("", { protocol }, false)).toBe("/api");
      expect(resolveApiUrl("  ", { protocol }, true)).toBe("/api");
    }
  });

  it("honours an explicit VITE_API_URL (trailing slash trimmed)", () => {
    expect(resolveApiUrl("http://localhost:8000/api/", { protocol: "http:" }, true)).toBe("http://localhost:8000/api");
    expect(resolveApiUrl("https://api.example.com/api", { protocol: "https:" }, false)).toBe("https://api.example.com/api");
  });

  it("ignores an http:// VITE_API_URL on an https page in dev, with a console warning", () => {
    const warn = vi.fn();
    expect(resolveApiUrl("http://localhost:8000/api", { protocol: "https:" }, true, warn)).toBe("/api");
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn.mock.calls[0][0]).toContain("http://localhost:8000/api");
  });

  it("does not override an http:// URL in a production build", () => {
    expect(resolveApiUrl("http://api.internal/api", { protocol: "https:" }, false, vi.fn())).toBe("http://api.internal/api");
  });
});
