import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [svelte()],
  server: { fs: { allow: [".."] } },
  build: {
    outDir: "dist/bundle/assets",
    emptyOutDir: true,
    // Everything stays in files: the Content-Security-Policy allows no inline script or style.
    assetsInlineLimit: 0,
    modulePreload: { polyfill: false },
  },
});
