import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API runs separately in dev (uvicorn on :8000; override with API_TARGET).
// In production the backend serves dist/ itself, so /api is same-origin.
const apiTarget = process.env.API_TARGET || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: apiTarget, changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
    rollupOptions: { output: { manualChunks: { graph: ["sigma", "graphology", "graphology-layout-forceatlas2"] } } },
  },
});
