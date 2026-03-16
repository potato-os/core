const { test, expect } = require("@playwright/test");
const {
  waitForStatusApplied,
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


test("shows manual download prompt when model missing and starts download on click", async ({ page }) => {
  let statusCallCount = 0;
  let startDownloadCalls = 0;

  await page.route("**/status", async (route) => {
    statusCallCount += 1;
    const downloading = statusCallCount >= 2;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        state: downloading ? "DOWNLOADING" : "BOOTING",
        model_present: false,
        model: { filename: "Qwen3.5-2B-Q4_K_M.gguf" },
        llama_server: { healthy: false },
        backend: { mode: "llama", active: "llama", fallback_active: false },
        download: {
          bytes_total: 2497282336,
          bytes_downloaded: downloading ? 1200000 : 0,
          percent: downloading ? 1 : 0,
          speed_bps: downloading ? 510000 : 0,
          eta_seconds: downloading ? 4800 : 0,
          error: null,
          active: downloading,
          auto_start_seconds: 300,
          auto_start_remaining_seconds: downloading ? 0 : 287,
        },
      }),
    });
  });

  await page.route("**/internal/start-model-download", async (route) => {
    startDownloadCalls += 1;
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ started: true, reason: "started" }),
    });
  });

  await page.goto("/");
  await waitForStatusApplied(page);

  await expect(page.locator("#downloadPrompt")).toBeVisible();
  await expect(page.locator("#downloadPromptHint")).toContainText("Auto-download starts in");
  await page.locator("#startDownloadBtn").click();

  await expect.poll(() => startDownloadCalls).toBe(1);
  await expect(page.locator("#downloadPrompt")).toBeHidden();
});

test("surfaces failed downloads clearly and resumes them from the UI", async ({ page }) => {
  let downloadActive = false;
  let downloadError = "download_failed";
  let downloadCalls = [];
  let models = [
    {
      id: "failed-model",
      filename: "Qwen3-30B-A3B-Instruct-2507-Q3_K_S-3.25bpw.gguf",
      source_url: "https://example.com/qwen3-30b-a3b-q3ks.gguf",
      source_type: "url",
      status: "failed",
      is_active: false,
      bytes_total: 12424439872,
      bytes_downloaded: 1875986767,
      percent: 15,
      error: "download_failed",
    },
  ];

  const statusPayload = () => ({
    state: downloadActive ? "DOWNLOADING" : "ERROR",
    model_present: false,
    model: { filename: "Qwen3-30B-A3B-Instruct-2507-Q3_K_S-3.25bpw.gguf", active_model_id: null },
    models,
    upload: { active: false, model_id: null, bytes_total: 0, bytes_received: 0, percent: 0, error: null },
    download: {
      bytes_total: 12424439872,
      bytes_downloaded: 1875986767,
      percent: 15,
      speed_bps: downloadActive ? 510000 : 0,
      eta_seconds: downloadActive ? 18000 : 0,
      error: downloadError,
      active: downloadActive,
      auto_start_seconds: 300,
      auto_start_remaining_seconds: 0,
      countdown_enabled: true,
      current_model_id: downloadActive ? "failed-model" : null,
    },
    llama_server: { healthy: false, running: false, url: "http://127.0.0.1:8080" },
    backend: { mode: "llama", active: "llama", fallback_active: false },
    system: { available: false, cpu_cores_percent: [] },
  });

  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(statusPayload()),
    });
  });

  await page.route("**/internal/models/download", async (route) => {
    const body = JSON.parse(route.request().postData() || "{}");
    downloadCalls.push(body.model_id);
    downloadActive = true;
    downloadError = null;
    models = models.map((model) =>
      model.id === body.model_id
        ? { ...model, status: "downloading", error: null, percent: 15 }
        : model
    );
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ started: true, reason: "started", model_id: body.model_id }),
    });
  });

  await page.goto("/");
  await waitForStatusApplied(page);

  await expect(page.locator("#downloadPrompt")).toBeVisible();
  await expect(page.locator("#downloadPromptHint")).toContainText("Last download failed");
  await expect(page.locator("#startDownloadBtn")).toHaveText("Resume download");
  await expect(page.locator("#statusText")).toContainText("Download failed");

  await openSettingsModal(page);
  const failedRow = page.locator('#modelsList .model-row[data-model-id="failed-model"]');
  await expect(failedRow).toContainText("Failed");
  await expect(failedRow).toContainText("Failed at");
  await expect(failedRow.locator('button[data-action="download"]')).toHaveText("Resume download");
  await closeSettingsModal(page);

  await page.locator("#startDownloadBtn").click();

  await expect.poll(() => downloadCalls).toEqual(["failed-model"]);
  await expect(page.locator("#downloadPrompt")).toBeHidden();
  await expect(page.locator("#statusText")).toContainText("Download: 15%");
  await expect(failedRow).toContainText("Downloading");
  await expect(failedRow.locator('button[data-action="cancel-download"]')).toHaveText("Stop download");
});

test("shows sidebar resume button for failed download while another model is active", async ({ page }) => {
  let downloadCalls = [];
  let downloadActive = false;
  let models = [
    {
      id: "active-model",
      filename: "Qwen_Qwen3.5-2B-IQ4_NL.gguf",
      source_url: null,
      source_type: "local_file",
      status: "ready",
      is_active: true,
      bytes_total: 0,
      bytes_downloaded: 0,
      percent: 0,
      error: null,
    },
    {
      id: "failed-model",
      filename: "Qwen3-30B-A3B-Instruct-2507-Q3_K_S-3.25bpw.gguf",
      source_url: "https://example.com/qwen3-30b-a3b-q3ks.gguf",
      source_type: "url",
      status: "failed",
      is_active: false,
      bytes_total: 12424439872,
      bytes_downloaded: 1875986767,
      percent: 15,
      error: "download_failed",
    },
  ];

  const statusPayload = () => ({
    state: "READY",
    model_present: true,
    model: { filename: "Qwen_Qwen3.5-2B-IQ4_NL.gguf", active_model_id: "active-model" },
    models,
    upload: { active: false, model_id: null, bytes_total: 0, bytes_received: 0, percent: 0, error: null },
    download: {
      bytes_total: 12424439872,
      bytes_downloaded: 1875986767,
      percent: 15,
      speed_bps: downloadActive ? 510000 : 0,
      eta_seconds: downloadActive ? 18000 : 0,
      error: downloadActive ? null : "download_failed",
      active: downloadActive,
      auto_start_seconds: 300,
      auto_start_remaining_seconds: 0,
      countdown_enabled: true,
      current_model_id: downloadActive ? "failed-model" : null,
    },
    llama_server: { healthy: true, running: true, url: "http://127.0.0.1:8080" },
    backend: { mode: "llama", active: "llama", fallback_active: false },
    system: { available: false, cpu_cores_percent: [] },
  });

  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(statusPayload()),
    });
  });

  await page.route("**/internal/models/download", async (route) => {
    const body = JSON.parse(route.request().postData() || "{}");
    downloadCalls.push(body.model_id);
    downloadActive = true;
    models = models.map((model) =>
      model.id === body.model_id ? { ...model, status: "downloading", error: null } : model
    );
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ started: true, reason: "started", model_id: body.model_id }),
    });
  });

  await page.goto("/");
  await waitForStatusApplied(page);

  await expect(page.locator("#downloadPrompt")).toBeHidden();
  await expect(page.locator("#statusText")).toContainText("Download failed");
  await expect(page.locator("#statusResumeDownloadBtn")).toBeVisible();
  await expect(page.locator("#statusResumeDownloadBtn")).toHaveText("Resume");

  await page.locator("#statusResumeDownloadBtn").click();

  await expect.poll(() => downloadCalls).toEqual(["failed-model"]);
  await expect(page.locator("#statusResumeDownloadBtn")).toBeHidden();
  await expect(page.locator("#statusText")).toContainText("Download: 15%");
});


