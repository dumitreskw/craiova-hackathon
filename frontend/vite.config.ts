import { resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API runs separately in dev (uvicorn on :8000; override with API_TARGET).
// In production the backend serves dist/ itself, so /api is same-origin.
// Two pages: the public landing page (/) and the analyst app (/app/).
const apiTarget = process.env.API_TARGET || "http://127.0.0.1:8000";
const backend = { target: apiTarget, changeOrigin: false };

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Sign-in runs on the backend; the cookie it sets is for this origin.
    proxy: { "/api": backend, "/auth": backend, "/invite": backend, "/mcp": backend },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
    rollupOptions: {
      input: { landing: resolve(__dirname, "index.html"), app: resolve(__dirname, "app/index.html") },
      output: { manualChunks: { graph: ["sigma", "graphology", "graphology-layout-forceatlas2"] } },
    },
  },
});
