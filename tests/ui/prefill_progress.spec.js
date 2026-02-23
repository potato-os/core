const { test, expect } = require("@playwright/test");

async function waitUntilReady(page) {
  await page.goto("/");
  await expect(page.locator("#statusText")).toContainText("State: READY");
}

test("shows staged prefill estimate before first token and clears after generation starts", async ({ page }) => {
  await waitUntilReady(page);

  await page.locator("#userPrompt").fill("Give me one sentence about Potato OS.");
  await page.locator("#userPrompt").press("Enter");

  const chip = page.locator("#composerStatusChip");
  const chipText = page.locator("#composerStatusText");
  await expect(chip).toBeVisible();
  await expect(chipText).toContainText(/Preparing prompt •|Generating\.\.\./);

  const values = [];
  for (let i = 0; i < 6; i += 1) {
    await page.waitForTimeout(180);
    if (await chip.isHidden()) {
      break;
    }
    const label = await chipText.innerText();
    const match = label.match(/(\d+)%/);
    if (match) {
      values.push(Number(match[1]));
    }
  }

  if (values.length > 0) {
    expect(values.every((value, index) => index === 0 || value >= values[index - 1])).toBeTruthy();
    expect(Math.max(...values)).toBeLessThanOrEqual(95);
  }

  await expect(page.locator(".message-row.assistant .message-bubble").last()).toContainText("[fake-llama.cpp]");
  await expect(chip).toBeHidden();
});

test("cancel during prefill stops cleanly and shows stopped reason", async ({ page }) => {
  await waitUntilReady(page);

  await page.locator("#userPrompt").fill("Explain distributed systems in detail.");
  await page.locator("#userPrompt").press("Enter");

  await expect(page.locator("#composerStatusChip")).toBeVisible();
  await expect(page.locator("#composerStatusText")).toContainText(/Preparing prompt •|Generating\.\.\./);
  await expect(page.locator("#sendBtn")).toHaveText("Stop");

  await page.locator("#cancelBtn").click();

  await expect(page.locator(".message-meta").last()).toContainText("Stopped by user");
  await expect(page.locator("#composerStatusChip")).toBeHidden();
  await expect(page.locator("#sendBtn")).toHaveText("Send");
});

test("large image selection shows loading phases and optimization metadata", async ({ page }) => {
  await waitUntilReady(page);

  await page.locator("#imageInput").setInputFiles("references/test-cat.jpg");

  await expect(page.locator("#imageMeta")).toBeVisible();
  await expect(page.locator("#imageMeta")).toContainText("optimized from");
  await expect(page.locator("#attachImageBtn")).toContainText("Change image");

  await page.locator("#userPrompt").fill("Describe this image.");
  await page.locator("#userPrompt").press("Enter");

  await expect(page.locator("#composerStatusChip")).toBeVisible();
  await expect(page.locator("#composerStatusText")).toContainText(/Preparing prompt •|Generating\.\.\./);
  await expect(page.locator(".message-row.assistant .message-bubble").last()).toContainText("[fake-llama.cpp]");
  await expect(page.locator("#composerStatusChip")).toBeHidden();
});

test("image upload returns typing focus to prompt and keeps it after enter-send", async ({ page }) => {
  await waitUntilReady(page);

  await page.locator("#imageInput").setInputFiles("references/test-cat.jpg");
  await expect(page.locator("#imageMeta")).toBeVisible();
  await expect(page.locator("#userPrompt")).toBeFocused();

  await page.locator("#userPrompt").fill("Please describe this image.");
  await page.locator("#userPrompt").press("Enter");

  await expect(page.locator(".message-row.assistant .message-bubble").last()).toContainText("[fake-llama.cpp]");
  await expect(page.locator("#userPrompt")).toBeFocused();
});

test("cancel image generation uses cancel endpoint and avoids restart endpoint", async ({ page }) => {
  await page.addInitScript(() => {
    window.__POTATO_CANCEL_RECOVERY_DELAY_MS__ = 250;
  });
  await waitUntilReady(page);

  const cancelCalls = [];
  const restartCalls = [];
  page.on("request", (request) => {
    if (request.url().includes("/internal/cancel-llama")) {
      cancelCalls.push(request.url());
    }
    if (request.url().includes("/internal/restart-llama")) {
      restartCalls.push(request.url());
    }
  });

  await page.locator("#imageInput").setInputFiles("references/test-cat.jpg");
  await expect(page.locator("#imageMeta")).toBeVisible();

  await page.locator("#userPrompt").fill("Describe this image briefly.");
  await page.locator("#userPrompt").press("Enter");

  await expect(page.locator("#sendBtn")).toHaveText("Stop");
  await expect(page.locator("#composerStatusChip")).toBeVisible();
  await page.locator("#cancelBtn").click();

  await expect(page.locator("#sendBtn")).toHaveText("Send");
  await page.waitForTimeout(1400);
  expect(cancelCalls.length).toBeGreaterThan(0);
  expect(restartCalls).toHaveLength(0);
});

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
        model: { filename: "Qwen3-VL-4B-Instruct-Q4_K_M.gguf" },
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

  await expect(page.locator("#downloadPrompt")).toBeVisible();
  await expect(page.locator("#downloadPromptHint")).toContainText("Auto-download starts in");
  await page.locator("#startDownloadBtn").click();

  await expect.poll(() => startDownloadCalls).toBe(1);
  await expect(page.locator("#downloadPrompt")).toBeHidden();
});

test("renders compact Pi runtime info and toggles details view", async ({ page }) => {
  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        state: "READY",
        model_present: true,
        model: { filename: "Qwen3-VL-4B-Instruct-Q4_K_M.gguf" },
        llama_server: { healthy: true },
        backend: { mode: "llama", active: "llama", fallback_active: false },
        download: {
          bytes_total: 2497282336,
          bytes_downloaded: 2497282336,
          percent: 100,
          speed_bps: 0,
          eta_seconds: 0,
          error: null,
          active: false,
          auto_start_seconds: 300,
          auto_start_remaining_seconds: 0,
        },
        system: {
          available: true,
          updated_at_unix: 1771778048,
          cpu_percent: 21.4,
          cpu_cores_percent: [18, 24, 19, 22],
          cpu_clock_arm_hz: 2400023808,
          memory_total_bytes: 7900000000,
          memory_used_bytes: 4800000000,
          memory_percent: 61,
          swap_total_bytes: 2000000000,
          swap_used_bytes: 7000000,
          swap_percent: 0.35,
          temperature_c: 67.5,
          gpu_clock_core_hz: 910007424,
          gpu_clock_v3d_hz: 960012800,
          throttling: {
            raw: "0x80000",
            any_current: false,
            any_history: true,
            current_flags: [],
            history_flags: ["Soft temp limit occurred"],
          },
        },
      }),
    });
  });

  await page.goto("/");
  await expect(page.locator("#runtimeCompact")).toContainText("CPU 21% @ 2400 MHz");
  await expect(page.locator("#runtimeCompact")).toContainText("GPU 910/960 MHz");
  await expect(page.locator("#runtimeDetails")).toBeHidden();

  await page.locator("#runtimeViewToggle").click();
  await expect(page.locator("#runtimeCompact")).toBeHidden();
  await expect(page.locator("#runtimeDetails")).toBeVisible();
  await expect(page.locator("#runtimeDetails")).toContainText("CPU clock: 2400 MHz");
  await expect(page.locator("#runtimeDetails")).toContainText("Soft temp limit occurred");

  await page.locator("#runtimeViewToggle").click();
  await expect(page.locator("#runtimeCompact")).toBeVisible();
  await expect(page.locator("#runtimeDetails")).toBeHidden();
});

test("runtime details apply threshold colors for clock, memory, swap, and temperature", async ({ page }) => {
  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        state: "READY",
        model_present: true,
        model: { filename: "Qwen3-VL-4B-Instruct-Q4_K_M.gguf" },
        llama_server: { healthy: true },
        backend: { mode: "llama", active: "llama", fallback_active: false },
        download: {
          bytes_total: 2497282336,
          bytes_downloaded: 2497282336,
          percent: 100,
          speed_bps: 0,
          eta_seconds: 0,
          error: null,
          active: false,
          auto_start_seconds: 300,
          auto_start_remaining_seconds: 0,
        },
        system: {
          available: true,
          updated_at_unix: 1771778048,
          cpu_percent: 12,
          cpu_cores_percent: [10, 14, 11, 13],
          cpu_clock_arm_hz: 2300000000,
          memory_total_bytes: 8000000000,
          memory_used_bytes: 7500000000,
          memory_percent: 93.75,
          swap_total_bytes: 1000000000,
          swap_used_bytes: 790000000,
          swap_percent: 79,
          temperature_c: 88,
          gpu_clock_core_hz: 500000000,
          gpu_clock_v3d_hz: 500000000,
          throttling: {
            raw: "0x0",
            any_current: false,
            any_history: false,
            current_flags: [],
            history_flags: [],
          },
        },
      }),
    });
  });

  await page.goto("/");
  await page.locator("#runtimeViewToggle").click();

  await expect(page.locator("#runtimeDetailCpuClock")).toHaveClass(/runtime-metric-critical/);
  await expect(page.locator("#runtimeDetailMemory")).toHaveClass(/runtime-metric-critical/);
  await expect(page.locator("#runtimeDetailSwap")).toHaveClass(/runtime-metric-high/);
  await expect(page.locator("#runtimeDetailTemp")).toHaveClass(/runtime-metric-high/);
});

test("fake backend ready state shows connected badge", async ({ page }) => {
  await waitUntilReady(page);
  await expect(page.locator("#statusLabel")).toHaveText("CONNECTED:Local Model");
  await expect(page.locator("#statusBadge")).toHaveClass(/online/);
});

test("mobile hamburger controls sidebar drawer and keeps composer actions aligned", async ({ page }) => {
  await page.setViewportSize({ width: 500, height: 844 });
  await waitUntilReady(page);

  await expect(page.locator("#sidebarBackdrop")).toBeHidden();
  await expect(page.locator("#sidebarToggle")).toBeVisible();

  const sidebarClosed = await page.locator(".sidebar").evaluate((el) => {
    const rect = el.getBoundingClientRect();
    return rect.right <= 0 || rect.left < 0;
  });
  expect(sidebarClosed).toBeTruthy();

  await page.locator("#sidebarToggle").click();
  await expect(page.locator("#sidebarBackdrop")).toBeVisible();
  await expect(page.locator("body")).toHaveClass(/sidebar-open/);

  await page.locator("#sidebarBackdrop").click();
  await expect(page.locator("#sidebarBackdrop")).toBeHidden();
  await expect(page.locator("body")).not.toHaveClass(/sidebar-open/);

  const composer = page.locator(".composer");
  await expect(composer).toBeVisible();
  await expect(page.locator("#attachImageBtn")).toBeVisible();
  await expect(page.locator("#sendBtn")).toBeVisible();

  const [attachBox, sendBox, composerBox] = await Promise.all([
    page.locator("#attachImageBtn").boundingBox(),
    page.locator("#sendBtn").boundingBox(),
    composer.boundingBox(),
  ]);

  expect(attachBox).not.toBeNull();
  expect(sendBox).not.toBeNull();
  expect(composerBox).not.toBeNull();

  const attach = attachBox;
  const send = sendBox;
  const comp = composerBox;
  expect(attach.y).toBeGreaterThanOrEqual(comp.y);
  expect(send.y).toBeGreaterThanOrEqual(comp.y);
  expect(send.y + send.height).toBeLessThanOrEqual(comp.y + comp.height + 1);
});
