import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const backendTarget = process.env.STATION_BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/health": backendTarget,
      "/api": backendTarget,
      "/ws": { target: backendTarget, ws: true },
      "/docs": backendTarget,
      "/openapi.json": backendTarget,
      "/static/docs": backendTarget,
    },
  },
  test: {
    include: ["src/**/*.test.ts"],
    environment: "node",
  },
});
