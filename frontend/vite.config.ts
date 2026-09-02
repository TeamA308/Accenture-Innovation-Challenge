import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The built app is served by the FastAPI backend from frontend/dist, so the
// whole prototype is one process on one port. In dev, `npm run dev` runs on
// 5173 and proxies API calls to the backend so hot reload still works.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/v1": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/health": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 1200 },
});
