import { defineConfig } from "@playwright/test";

const baseURL = "http://127.0.0.1:4180";

export default defineConfig({
  testDir: "./tests/e2e",
  workers: 1,
  use: {
    baseURL,
    channel: "chrome",
    launchOptions: { args: ["--enable-unsafe-swiftshader", "--use-angle=swiftshader"] },
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 4180 --strictPort",
    url: baseURL,
  },
});
