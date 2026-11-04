import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: "http://localhost:8787", locale: "en-US", browserName: "chromium", viewport: { width: 1100, height: 760 } },
});
