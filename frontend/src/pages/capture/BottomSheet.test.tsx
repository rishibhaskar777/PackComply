import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { I18nProvider } from "../../i18n";
import en from "../../locales/en.json";
import { BottomSheet, nearestSnap, SNAPS } from "./BottomSheet";

describe("nearestSnap", () => {
  it("rests on the nearest snap point when released slowly", () => {
    expect(nearestSnap(0.34, 0)).toBe(0.3);
    expect(nearestSnap(0.5, 0)).toBe(0.6);
    expect(nearestSnap(0.8, 0)).toBe(0.92);
  });
  it("goes to the next snap in the direction of a flick", () => {
    expect(nearestSnap(0.34, 2)).toBe(0.6);
    expect(nearestSnap(0.62, 2)).toBe(0.92);
    expect(nearestSnap(0.62, -2)).toBe(0.3);
    expect(nearestSnap(0.92, 3)).toBe(0.92);
    expect(nearestSnap(0.3, -3)).toBe(0.3);
  });
});

describe("bottom sheet", () => {
  afterEach(cleanup);
  const mount = () => render(<I18nProvider><BottomSheet label="Result" initial={1} footer={<button>Save</button>}><p>body</p></BottomSheet></I18nProvider>);

  it("opens at the middle snap and moves with the keyboard, within the snap points", () => {
    mount();
    const handle = screen.getByRole("slider", { name: en.resSheetResize });
    expect(handle.getAttribute("aria-valuenow")).toBe("1");
    expect(handle.getAttribute("aria-valuetext")).toBe(en.resSheetHalf);
    fireEvent.keyDown(handle, { key: "ArrowUp" });
    expect(handle.getAttribute("aria-valuenow")).toBe("2");
    expect(handle.getAttribute("aria-valuetext")).toBe(en.resSheetFull);
    fireEvent.keyDown(handle, { key: "ArrowUp" });
    expect(handle.getAttribute("aria-valuenow")).toBe("2");
    fireEvent.keyDown(handle, { key: "Home" });
    expect(handle.getAttribute("aria-valuenow")).toBe("0");
    expect(handle.getAttribute("aria-valuetext")).toBe(en.resSheetPeek);
    expect(SNAPS).toHaveLength(3);
  });

  it("cycles through the snaps on click, keeps the footer visible and scrolls its body only when full", () => {
    mount();
    const handle = screen.getByRole("slider");
    const body = document.querySelector(".bottom-sheet__body")!;
    expect(body.classList.contains("is-scrollable")).toBe(false);
    fireEvent.click(handle);
    expect(body.classList.contains("is-scrollable")).toBe(true);
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
    fireEvent.click(handle);
    expect(handle.getAttribute("aria-valuenow")).toBe("0");
  });
});
