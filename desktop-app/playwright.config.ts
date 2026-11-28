import { defineConfig } from "@playwright/test";

const port = Number(process.env.PROXIGO_PLAYWRIGHT_PORT ?? 4180);
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("Choose a valid Playwright port.");
const baseURL = "http://127.0.0.1:" + port;

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
    command: "npm run dev -- --host 127.0.0.1 --port " + port + " --strictPort",
    url: baseURL,
  },
});
