import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: true,
    // In the dev container, file events don't propagate without polling.
    watch: process.env.VITE_API_PROXY ? { usePolling: true } : undefined,
    proxy: {
      // Defaults to localhost for host-based dev; the dev container sets
      // VITE_API_PROXY=http://backend:8000 to reach the backend service.
      "/api": process.env.VITE_API_PROXY ?? "http://localhost:8000",
    },
  },
});
