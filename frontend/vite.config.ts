import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(
      ["/api", "/docs", "/openapi.json", "/redoc", "/llms.txt"].map((path) => [
        path,
        { target: process.env.AXIOM_API_TARGET || "http://127.0.0.1:8000", changeOrigin: true },
      ]),
    ),
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return;
          if (
            id.includes("three") ||
            id.includes("camera-controls") ||
            id.includes("maath") ||
            id.includes("meshline")
          )
            return "viewport-vendor";
          if (id.includes("recharts") || id.includes("/d3-"))
            return "charts-vendor";
        },
      },
    },
    chunkSizeWarningLimit: 1200,
  },
});
