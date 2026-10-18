import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import basicSsl from "@vitejs/plugin-basic-ssl";
import type { Plugin } from "vite";

// Preload the two Latin font files every screen uses, so text is not first painted in the fallback face and then reflowed.
// The hashed file names only exist after bundling, so the links are added to the built index.html.
function preloadFonts(): Plugin {
  return {
    name: "packcomply-preload-fonts",
    apply: "build",
    transformIndexHtml: {
      order: "post",
      handler(_html, ctx) {
        const files = Object.keys(ctx.bundle ?? {}).filter(name => /(geist|geist-mono)-latin-wght-normal-.*\.woff2$/.test(name));
        return files.map(file => ({ tag: "link", attrs: { rel: "preload", as: "font", type: "font/woff2", href: `/${file}`, crossorigin: "" }, injectTo: "head" as const }));
      },
    },
  };
}

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
    plugins: [react(), preloadFonts(), ...(https ? [basicSsl()] : [])],
    server: { proxy: { "/api": { target: apiTarget, changeOrigin: false, xfwd: true } } },
    preview: { proxy: { "/api": { target: apiTarget, changeOrigin: false, xfwd: true } } },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      // Pages and languages are separate chunks that vite-node compiles on first use; in a full parallel run a cold worker
      // can need longer than the 5 s default for the first test that opens a route. Passing tests still finish immediately.
      testTimeout: 20_000,
      // Tests mock fetch by absolute URL; production/dev builds without VITE_API_URL use the relative "/api" proxy path.
      env: { VITE_API_URL: "http://localhost:8000/api" },
    },
  };
});
