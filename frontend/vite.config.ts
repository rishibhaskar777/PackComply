import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import basicSsl from "@vitejs/plugin-basic-ssl";

// The API runs on 127.0.0.1:8000. Proxying /api through the dev server gives a phone on the same
// Wi-Fi a single origin (http://<laptop-ip>:5173): same-site cookies, no CORS changes, no second port.
const apiTarget = process.env.VITE_PROXY_TARGET ?? "http://127.0.0.1:8000";

// `npm run dev:https` serves the dev server over HTTPS with a local self-signed certificate (no network
// involved). Browsers only allow the live camera preview on a secure page, and a phone opening
// http://<laptop-ip>:5173 is not one. The /api proxy is unchanged: the browser talks HTTPS to Vite and Vite
// talks plain HTTP to the API on the same machine. `npm run dev` stays plain HTTP.
export default defineConfig(({ mode }) => {
  const https = mode === "https" || process.env.VITE_DEV_HTTPS === "1";
  return {
    plugins: [react(), ...(https ? [basicSsl()] : [])],
    server: { proxy: { "/api": { target: apiTarget, changeOrigin: false } } },
    preview: { proxy: { "/api": { target: apiTarget, changeOrigin: false } } },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      // Tests mock fetch by absolute URL; production/dev builds without VITE_API_URL use the relative "/api" proxy path.
      env: { VITE_API_URL: "http://localhost:8000/api" },
    },
  };
});
