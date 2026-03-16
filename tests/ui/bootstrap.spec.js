const { test, expect } = require("@playwright/test");
const {
  waitUntilReady,
  openSettingsModal,
  closeSettingsModal,
  openAdvancedSettingsModal,
  closeAdvancedSettingsModal,
  saveModelSettings,
  chooseModelSegment,
  fulfillStreamingChat,
  makeStatusPayload,
  makeMultiModelStatusPayload,
  sendAndWaitForReply,
} = require("./helpers");


test("download prompt shows countdown when no model is present", async ({ page }) => {
  const noModelStatus = makeStatusPayload({
    state: "BOOTING",
    model_present: false,
    download: {
      bytes_total: 0, bytes_downloaded: 0, percent: 0, speed_bps: 0,
      eta_seconds: 0, error: null, active: false,
      auto_start_seconds: 300,
      auto_start_remaining_seconds: 120,
      countdown_enabled: true,
      auto_download_paused: false,
      auto_download_completed_once: false,
      current_model_id: null,
    },
    llama_server: { healthy: false, running: false, url: "http://127.0.0.1:8080" },
  });
  await page.route("**/status", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(noModelStatus) }));
  await page.goto("/");
  await expect(page.locator("#downloadPrompt")).toBeVisible();
  await expect(page.locator("#downloadPromptHint")).toContainText(/auto-download starts in/i);
});

test("start download button sends request to backend", async ({ page }) => {
  const noModelStatus = makeStatusPayload({
    state: "BOOTING",
    model_present: false,
    download: {
      bytes_total: 0, bytes_downloaded: 0, percent: 0, speed_bps: 0,
      eta_seconds: 0, error: null, active: false,
      auto_start_seconds: 300,
      auto_start_remaining_seconds: 60,
      countdown_enabled: true,
      auto_download_paused: false,
      auto_download_completed_once: false,
      current_model_id: null,
    },
    llama_server: { healthy: false, running: false, url: "http://127.0.0.1:8080" },
  });
  await page.route("**/status", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(noModelStatus) }));
  await page.goto("/");
  await expect(page.locator("#downloadPrompt")).toBeVisible();

  let downloadRequested = false;
  await page.route("**/internal/start-model-download", async (route) => {
    downloadRequested = true;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ started: true }) });
  });

  await page.locator("#startDownloadBtn").click();
  expect(downloadRequested).toBe(true);
});

test("download prompt is hidden when model is present", async ({ page }) => {
  await waitUntilReady(page);
  await expect(page.locator("#downloadPrompt")).toBeHidden();
});

