export type ApiError = Error & { detail?: string; status?: number; unreachable?: boolean };

// SameSite=Lax development cookies must remain same-site.  Deriving the
// fallback host from the page prevents a localhost UI from calling 127.0.0.1.
// A page opened by any other host (a phone on the laptop's LAN IP) uses the
// same-origin "/api" path, which the Vite dev server proxies to the backend.
const LOCAL_HOSTS = ["localhost", "127.0.0.1", "[::1]"];
export const API_URL = (import.meta.env.VITE_API_URL
  ?? (LOCAL_HOSTS.includes(window.location.hostname) ? `${window.location.protocol}//${window.location.hostname}:8000/api` : "/api")).replace(/\/$/, "");

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
