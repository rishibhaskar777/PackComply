import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import en from "../../locales/en.json";
import { I18nProvider } from "../../i18n";
import { TOAST_DURATION_MS, ToastProvider, useToast } from "./Toast";

function Trigger() {
  const toast = useToast();
  return <><button onClick={() => toast("Report downloaded")}>ok</button><button onClick={() => toast("Failed", "error")}>fail</button></>;
}
const renderToasts = () => render(<I18nProvider><ToastProvider><Trigger /></ToastProvider></I18nProvider>);

describe("toasts", () => {
  afterEach(() => { cleanup(); vi.useRealTimers(); });

  it("announces politely and dismisses itself after 4 s", () => {
    vi.useFakeTimers();
    renderToasts();
    fireEvent.click(screen.getByText("ok"));
    const region = screen.getByRole("region", { name: en.notifications });
    expect(region.querySelector("[aria-live='polite']")?.textContent).toContain("Report downloaded");
    act(() => { vi.advanceTimersByTime(TOAST_DURATION_MS + 50); });
    expect(screen.queryByText("Report downloaded")).toBeNull();
  });

  it("can be dismissed early and styles errors", () => {
    renderToasts();
    fireEvent.click(screen.getByText("fail"));
    expect(screen.getByText("Failed").closest(".toast")?.className).toContain("toast--error");
    fireEvent.click(screen.getByRole("button", { name: en.dismiss }));
    expect(screen.queryByText("Failed")).toBeNull();
  });

  it("is a harmless no-op without a provider", () => {
    render(<I18nProvider><Trigger /></I18nProvider>);
    expect(() => fireEvent.click(screen.getByText("ok"))).not.toThrow();
  });
});
