import { resolveApiUrl } from "./apiUrl";

export const UNAUTHORIZED_EVENT = "packcomply:unauthorized";

export type ApiError = Error & { detail?: string; status?: number; unreachable?: boolean };

// Without VITE_API_URL the app calls the same-origin "/api" path, which the Vite dev/preview server proxies to the
// backend on 127.0.0.1:8000. That keeps cookies same-site and works over http and https (see apiUrl.ts).
export const API_URL = resolveApiUrl(import.meta.env.VITE_API_URL, window.location, import.meta.env.DEV);

export async function api(path: string, operation: string, options: RequestInit = {}, timeout = 30_000): Promise<Response> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(`${API_URL}${path}`, { ...options, credentials: "include", signal: controller.signal });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const error = new Error(operation) as ApiError;
      error.detail = `${response.status}${body.detail ? `: ${body.detail}` : ""}`;
      error.status = response.status;
      // A 401 anywhere (an expired session) is announced once, so the app can return to sign-in; sign-in itself is excluded.
      if (response.status === 401 && !path.startsWith("/auth/")) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
      throw error;
    }
    return response;
  } catch (error) {
    if (controller.signal.aborted) {
      const timeoutError = new Error(operation) as ApiError;
      timeoutError.detail = "The inspection took too long. You can safely retry.";
      timeoutError.unreachable = true;
      throw timeoutError;
    }
    if ((error as ApiError).status === undefined) (error as ApiError).unreachable = true;
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

export async function getJson<T>(path: string, operation: string): Promise<T> {
  return (await api(path, operation)).json();
}

// 503 (database down), a network failure or a timeout all mean the server cannot answer.
export function isUnavailable(error: unknown) { const e = error as ApiError; return e?.status === 503 || e?.unreachable === true; }
export function isExpired(error: unknown) { return (error as ApiError)?.detail?.startsWith("401") === true; }

/** Download an inspection's PDF report through the credentialed API helper. */
export async function downloadReport(id: string, inspectionNumber: string) {
  const blob = await (await api(`/inspections/${id}/report`, "report")).blob();
  const link = document.createElement("a");
  const objectUrl = URL.createObjectURL(blob);
  link.href = objectUrl;
  link.download = `inspection_${inspectionNumber}.pdf`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}
