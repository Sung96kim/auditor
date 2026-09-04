import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";

export default defineConfig({
  plugins: [react(), viteSingleFile()],
  // emptyOutDir stays off: `dist/inputs.sha256` is committed and lives here, and vite's default
  // wipes the directory before writing, so a plain `pnpm build` deleted a tracked file
  build: {
    outDir: "dist",
    emptyOutDir: false,
    assetsInlineLimit: 100000000,
    cssCodeSplit: false,
  },
  test: { environment: "jsdom", globals: true, setupFiles: ["./vitest.setup.ts"] },
});
