/** Same-origin API path: the Vite dev/preview proxy forwards it to the backend (http://127.0.0.1:8000), so it works over http and https. */
export const PROXY_API_PATH = "/api";

type Page = { protocol: string };

/**
 * Where the browser should call the API. Without VITE_API_URL it is always the relative "/api" (localhost and LAN
 * hosts alike, http and https): uvicorn speaks plain HTTP, so building https://localhost:8000 from the page would fail.
 * In dev, an explicit http:// VITE_API_URL is ignored on an https page (mixed content / wrong protocol) with a warning.
 */
export function resolveApiUrl(configured: string | undefined, page: Page, dev: boolean, warn: (message: string) => void = console.warn): string {
  const value = configured?.trim();
  if (!value) return PROXY_API_PATH;
  if (dev && page.protocol === "https:" && /^http:\/\//i.test(value)) {
    warn(`VITE_API_URL (${value}) is http:// but this page is https://; ignoring it and using "${PROXY_API_PATH}" (proxied to the backend). Remove it from frontend/.env for dev:https.`);
    return PROXY_API_PATH;
  }
  return value.replace(/\/$/, "");
}
