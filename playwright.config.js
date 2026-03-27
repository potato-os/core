const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./tests/ui",
  timeout: 45_000,
  expect: {
    timeout: 10_000,
  },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  workers: "75%",
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:1983",
    browserName: "chromium",
    headless: true,
    trace: "on-first-retry",
  },
  webServer: {
    command: [
      "POTATO_ENABLE_ORCHESTRATOR=0",
      "POTATO_CHAT_BACKEND=fake",
      "POTATO_ALLOW_FAKE_FALLBACK=1",
      "POTATO_TEST_MODE=1",
      "POTATO_FAKE_PREFILL_DELAY_MS=1800",
      "POTATO_FAKE_STREAM_CHUNK_DELAY_MS=35",
      `${process.env.CI ? "python" : ".venv/bin/python"} -m uvicorn core.main:app --host 127.0.0.1 --port 1983`
    ].join(" "),
    url: "http://127.0.0.1:1983/",
    timeout: 120_000,
    reuseExistingServer: !process.env.CI,
  },
  projects: [
    {
      name: "chromium",
      use: { browserName: "chromium" },
    },
  ],
});
