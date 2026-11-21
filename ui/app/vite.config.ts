import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vitest/config";

/**
 * The renderer is loaded by Electron from `file://`, so every asset URL must be
 * relative (`base: "./"`). `emptyOutDir: false` is essential: the Electron
 * entrypoints are compiled into `dist/electron` by `tsc` *before* Vite runs and
 * a default clean build would delete them.
 */
export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
  },
  build: {
    outDir: "dist",
    emptyOutDir: false,
    target: "chrome120",
    sourcemap: false,
    assetsDir: "assets",
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/__tests__/setup.ts"],
    css: false,
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
