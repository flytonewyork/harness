import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Served by the API (python -m backend.api --run ...), which also proxies in development.
// VITE_STATIC=1 builds a server-free copy into dist-static/ that reads ./run.json beside the page.
const isStatic = process.env.VITE_STATIC === "1";
export default defineConfig({
  plugins: [react()],
  base: isStatic ? "./" : "/",
  build: { outDir: isStatic ? "dist-static" : "dist" },
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
