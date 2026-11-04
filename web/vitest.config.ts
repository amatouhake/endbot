import { cloudflareTest } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [
    cloudflareTest({
      wrangler: { configPath: "./wrangler.jsonc" },
      miniflare: {
        bindings: {
          ENDBOT_ORIGIN: "https://endbot.example",
          // Each test installs its own key through this value.
          ENDBOTD_LINK_KEY: "{}",
        },
      },
    }),
  ],
  test: {
    include: ["test/**/*.test.ts"],
  },
});
