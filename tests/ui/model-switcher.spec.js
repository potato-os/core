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


test("quick model switcher opens on badge click and shows models", async ({ page }) => {
  const multiStatus = makeMultiModelStatusPayload();
  await page.route("**/status", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(multiStatus) }));
  await page.goto("/");
  await expect(page.locator("#statusText")).toContainText("State: READY");

  const badge = page.locator("#statusBadge");
  const switcher = page.locator("#modelSwitcher");
  await expect(switcher).toBeHidden();

  await badge.click();
  await expect(switcher).toBeVisible();

  const items = switcher.locator(".model-switcher-item");
  await expect(items).toHaveCount(2);
  await expect(items.nth(0)).toContainText("Qwen3.5-2B");
  await expect(items.nth(1)).toContainText("Qwen3-Coder-30B");
});

test("quick model switcher highlights active model", async ({ page }) => {
  const multiStatus = makeMultiModelStatusPayload({ activeId: "default" });
  await page.route("**/status", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(multiStatus) }));
  await page.goto("/");
  await expect(page.locator("#statusText")).toContainText("State: READY");

  await page.locator("#statusBadge").click();
  await expect(page.locator("#modelSwitcher")).toBeVisible();

  const activeItem = page.locator('.model-switcher-item[data-model-id="default"]');
  await expect(activeItem).toHaveClass(/active/);
  const inactiveItem = page.locator('.model-switcher-item[data-model-id="second-model"]');
  await expect(inactiveItem).not.toHaveClass(/active/);
});

test("quick model switcher activates a ready model", async ({ page }) => {
  const multiStatus = makeMultiModelStatusPayload({ activeId: "default" });
  await page.route("**/status", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(multiStatus) }));
  await page.goto("/");
  await expect(page.locator("#statusText")).toContainText("State: READY");

  let activatedModelId = null;
  await page.route("**/internal/models/activate", async (route) => {
    const body = JSON.parse(route.request().postData() || "{}");
    activatedModelId = body.model_id;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) });
  });

  // After activation, return status with the new model active
  const switchedStatus = makeMultiModelStatusPayload({ activeId: "second-model" });

  await page.locator("#statusBadge").click();
  await expect(page.locator("#modelSwitcher")).toBeVisible();

  // Override status route for the post-activation poll
  await page.route("**/status", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(switchedStatus) }));

  await page.locator('.model-switcher-item[data-model-id="second-model"]').click();

  // Dropdown should close after activation
  await expect(page.locator("#modelSwitcher")).toBeHidden();
  expect(activatedModelId).toBe("second-model");
});

test("quick model switcher disables non-ready models", async ({ page }) => {
  const failedModel = {
    id: "failed-model",
    filename: "BrokenModel-Q2_K.gguf",
    source_url: null,
    source_type: "local_file",
    status: "failed",
    is_active: false,
    settings: {
      chat: { system_prompt: "", stream: true, generation_mode: "random", seed: 42, temperature: 0.7, top_p: 0.8, top_k: 20, repetition_penalty: 1.0, presence_penalty: 1.5, max_tokens: 16384, cache_prompt: true },
      vision: { enabled: false, projector_mode: "default", projector_filename: "" },
    },
    capabilities: { vision: false },
    projector: { present: false, filename: null, default_candidates: [] },
    bytes_total: 0, bytes_downloaded: 0, percent: 0, error: "load_failed",
  };
  const downloadingModel = {
    id: "downloading-model",
    filename: "NewModel-Q4_K_M.gguf",
    source_url: "https://example.com/model.gguf",
    source_type: "url",
    status: "downloading",
    is_active: false,
    settings: {
      chat: { system_prompt: "", stream: true, generation_mode: "random", seed: 42, temperature: 0.7, top_p: 0.8, top_k: 20, repetition_penalty: 1.0, presence_penalty: 1.5, max_tokens: 16384, cache_prompt: true },
      vision: { enabled: false, projector_mode: "default", projector_filename: "" },
    },
    capabilities: { vision: false },
    projector: { present: false, filename: null, default_candidates: [] },
    bytes_total: 1000000, bytes_downloaded: 500000, percent: 50, error: null,
  };
  const multiStatus = makeMultiModelStatusPayload({ extraModels: [failedModel, downloadingModel] });
  await page.route("**/status", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(multiStatus) }));
  await page.goto("/");
  await expect(page.locator("#statusText")).toContainText("State: READY");

  await page.locator("#statusBadge").click();
  await expect(page.locator("#modelSwitcher")).toBeVisible();

  const failedItem = page.locator('.model-switcher-item[data-model-id="failed-model"]');
  await expect(failedItem).toHaveClass(/disabled/);
  await expect(failedItem).toContainText(/failed/i);

  const downloadingItem = page.locator('.model-switcher-item[data-model-id="downloading-model"]');
  await expect(downloadingItem).toHaveClass(/disabled/);
  await expect(downloadingItem).toContainText(/downloading/i);

  // Clicking disabled items should NOT trigger activation
  let activateCalled = false;
  await page.route("**/internal/models/activate", async (route) => {
    activateCalled = true;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) });
  });
  await failedItem.click();
  expect(activateCalled).toBe(false);
  // Dropdown should still be visible (not dismissed)
  await expect(page.locator("#modelSwitcher")).toBeVisible();
});

test("quick model switcher closes on escape and outside click", async ({ page }) => {
  const multiStatus = makeMultiModelStatusPayload();
  await page.route("**/status", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(multiStatus) }));
  await page.goto("/");
  await expect(page.locator("#statusText")).toContainText("State: READY");

  const badge = page.locator("#statusBadge");
  const switcher = page.locator("#modelSwitcher");

  // Test Escape dismissal
  await badge.click();
  await expect(switcher).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(switcher).toBeHidden();

  // Test outside click dismissal
  await badge.click();
  await expect(switcher).toBeVisible();
  await page.locator("#messages").click();
  await expect(switcher).toBeHidden();
});

test("quick model switcher supports keyboard navigation", async ({ page }) => {
  const multiStatus = makeMultiModelStatusPayload();
  await page.route("**/status", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(multiStatus) }));
  await page.goto("/");
  await expect(page.locator("#statusText")).toContainText("State: READY");

  const badge = page.locator("#statusBadge");
  const switcher = page.locator("#modelSwitcher");

  // Enter opens the switcher
  await badge.focus();
  await page.keyboard.press("Enter");
  await expect(switcher).toBeVisible();

  // Arrow down highlights first item
  await page.keyboard.press("ArrowDown");
  await expect(page.locator('.model-switcher-item.focused')).toHaveCount(1);

  // Arrow down again moves to second item
  await page.keyboard.press("ArrowDown");
  const secondItem = page.locator('.model-switcher-item[data-model-id="second-model"]');
  await expect(secondItem).toHaveClass(/focused/);

  // Escape closes
  await page.keyboard.press("Escape");
  await expect(switcher).toBeHidden();

  // Space also opens
  await badge.focus();
  await page.keyboard.press("Space");
  await expect(switcher).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(switcher).toBeHidden();
});

test("quick model switcher refreshes when status updates while open", async ({ page }) => {
  // Start with a single-model status
  const singleStatus = makeStatusPayload();
  let statusResponse = singleStatus;
  await page.route("**/status", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(statusResponse) }));
  await page.goto("/");
  await expect(page.locator("#statusText")).toContainText("State: READY");

  // Open switcher — shows 1 model
  await page.locator("#statusBadge").click();
  await expect(page.locator("#modelSwitcher")).toBeVisible();
  await expect(page.locator(".model-switcher-item")).toHaveCount(1);

  // Update the response to return 2 models — next poll will pick it up
  statusResponse = makeMultiModelStatusPayload();

  // Wait for auto-poll (every 2s) to refresh the open switcher
  await expect(page.locator(".model-switcher-item")).toHaveCount(2, { timeout: 5000 });
});

