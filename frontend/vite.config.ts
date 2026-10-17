import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The API runs on 127.0.0.1:8000. Proxying /api through the dev server gives a phone on the same
// Wi-Fi a single origin (http://<laptop-ip>:5173): same-site cookies, no CORS changes, no second port.
const apiTarget = process.env.VITE_PROXY_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": { target: apiTarget, changeOrigin: false } } },
  preview: { proxy: { "/api": { target: apiTarget, changeOrigin: false } } },
  test: {
    environment: "jsdom",
  },
});
