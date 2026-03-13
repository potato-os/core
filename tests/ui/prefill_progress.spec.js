const { test, expect } = require("@playwright/test");

async function waitUntilReady(page) {
  await page.goto("/");
  await expect(page.locator("#statusText")).toContainText("State: READY");
}

async function openSettingsModal(page) {
  await page.locator("#settingsOpenBtn").click();
  await expect(page.locator("#settingsModal")).toBeVisible();
}

async function closeSettingsModal(page) {
  await page.locator("#settingsCloseBtn").click();
  await expect(page.locator("#settingsModal")).toBeHidden();
}

test("seed mode defaults to random, toggles deterministic, persists, and controls request payload", async ({ page }) => {
  await waitUntilReady(page);

  const generationMode = page.locator("#generationMode");
  const seedField = page.locator("#seed");
  const streamField = page.locator("#stream");
  const promptField = page.locator("#userPrompt");
  const sendBtn = page.locator("#sendBtn");
  await openSettingsModal(page);

  await expect(generationMode).toHaveValue("random");
  await expect(seedField).toHaveValue("42");
  await expect(seedField).toBeDisabled();

  await streamField.selectOption("false");
  await closeSettingsModal(page);
  await promptField.fill("Seed random request.");
  const randomRequestPromise = page.waitForRequest("**/v1/chat/completions");
  await promptField.press("Enter");
  const randomRequest = await randomRequestPromise;
  const randomPayload = JSON.parse(randomRequest.postData() || "{}");
  expect(randomPayload.seed).toBeUndefined();
  await expect(sendBtn).toHaveText("Send");

  await openSettingsModal(page);
  await generationMode.selectOption("deterministic");
  await expect(seedField).toBeEnabled();
  await expect(seedField).toHaveValue("42");
  await seedField.fill("1337");
  await closeSettingsModal(page);

  await promptField.fill("Seed deterministic request.");
  const deterministicRequestPromise = page.waitForRequest("**/v1/chat/completions");
  await promptField.press("Enter");
  const deterministicRequest = await deterministicRequestPromise;
  const deterministicPayload = JSON.parse(deterministicRequest.postData() || "{}");
  expect(deterministicPayload.seed).toBe(1337);
  await expect(sendBtn).toHaveText("Send");

  await openSettingsModal(page);
  await generationMode.selectOption("random");
  await expect(seedField).toBeDisabled();
  await expect(seedField).toHaveValue("1337");
  await closeSettingsModal(page);

  await promptField.fill("Seed random request after toggle.");
  const randomRequestAfterTogglePromise = page.waitForRequest("**/v1/chat/completions");
  await promptField.press("Enter");
  const randomRequestAfterToggle = await randomRequestAfterTogglePromise;
  const randomPayloadAfterToggle = JSON.parse(randomRequestAfterToggle.postData() || "{}");
  expect(randomPayloadAfterToggle.seed).toBeUndefined();
  await expect(sendBtn).toHaveText("Send");

  await openSettingsModal(page);
  await generationMode.selectOption("deterministic");
  await expect(seedField).toBeEnabled();
  await expect(seedField).toHaveValue("1337");
  await closeSettingsModal(page);

  await page.reload();
  await openSettingsModal(page);
  await expect(page.locator("#generationMode")).toHaveValue("deterministic");
  await expect(page.locator("#seed")).toHaveValue("1337");
  await expect(page.locator("#seed")).toBeEnabled();
});

test("shows staged prefill estimate before first token and clears after generation starts", async ({ page }) => {
  await page.addInitScript(() => {
    window.__POTATO_PREFILL_FINISH_DURATION_MS__ = 300;
    window.__POTATO_PREFILL_FINISH_HOLD_MS__ = 350;
  });
  await waitUntilReady(page);

  await page.locator("#userPrompt").fill("Give me one sentence about Potato OS.");
  await page.locator("#userPrompt").press("Enter");

  const assistantPending = page.locator(".message-row.assistant .message-bubble.processing").last();
  const chip = page.locator("#composerStatusChip");
  const chipText = page.locator("#composerStatusText");
  await expect(assistantPending).toBeVisible();
  await expect(assistantPending).toContainText("Prompt processing");
  await expect(chip).toBeVisible();
  await expect(chipText).toContainText(/Preparing prompt •/);

  const values = [];
  let sawHundred = false;
  for (let i = 0; i < 30; i += 1) {
    await page.waitForTimeout(120);
    if (!(await chip.isHidden())) {
      const label = await chipText.innerText();
      const match = label.match(/(\d+)%/);
      if (match) {
        const value = Number(match[1]);
        values.push(value);
        if (value === 100) {
          sawHundred = true;
        }
      }
    } else if (sawHundred) {
      break;
    }
  }

  if (values.length > 0) {
    expect(values.every((value, index) => index === 0 || value >= values[index - 1])).toBeTruthy();
    expect(Math.max(...values)).toBeLessThanOrEqual(100);
  }
  expect(sawHundred).toBeTruthy();

  await expect(page.locator(".message-row.assistant .message-bubble").last()).toContainText("[fake-llama.cpp]");
  await expect(page.locator(".message-row.assistant .message-bubble.processing").last()).toBeHidden();
  await expect(page.locator(".message-meta").last()).toContainText(/TTFT \d+\.\d{2}s/);
  await expect(chip).toBeHidden();
});

test("renders assistant markdown as formatted html", async ({ page }) => {
  await waitUntilReady(page);

  await page.route("**/v1/chat/completions", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 180));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "chatcmpl-md",
        object: "chat.completion",
        created: 1771778048,
        model: "qwen-local",
        choices: [
          {
            index: 0,
            message: {
              role: "assistant",
              content: "# Linus Torvalds\n\nHere are the key facts:\n\n- **Linux** kernel\n- Open source\n\n`uname -a`",
            },
            finish_reason: "stop",
          },
        ],
        timings: {
          prompt_ms: 1200,
          predicted_ms: 800,
          predicted_n: 12,
          predicted_per_second: 15,
        },
        usage: {
          prompt_tokens: 10,
          completion_tokens: 12,
          total_tokens: 22,
        },
      }),
    });
  });

  await openSettingsModal(page);
  await page.locator("#stream").selectOption("false");
  await closeSettingsModal(page);
  await page.locator("#userPrompt").fill("Format this nicely.");
  await page.locator("#userPrompt").press("Enter");

  const bubble = page.locator(".message-row.assistant .message-bubble").last();
  await expect(bubble.locator("h1")).toHaveText("Linus Torvalds");
  await expect(bubble.locator("li")).toHaveCount(2);
  await expect(bubble.locator("strong")).toHaveText("Linux");
  await expect(bubble.locator("code")).toHaveText("uname -a");
});

test("streaming cancel during finish animation does not render buffered assistant output", async ({ page }) => {
  await page.addInitScript(() => {
    window.__POTATO_PREFILL_FINISH_DURATION_MS__ = 1200;
    window.__POTATO_PREFILL_FINISH_HOLD_MS__ = 250;
  });
  await waitUntilReady(page);

  await page.locator("#userPrompt").fill("Give me a streamed response.");
  await page.locator("#userPrompt").press("Enter");

  const chipText = page.locator("#composerStatusText");
  await expect
    .poll(async () => {
      const label = await chipText.innerText();
      const match = label.match(/(\d+)%/);
      return match ? Number(match[1]) : 0;
    })
    .toBeGreaterThanOrEqual(95);

  await page.locator("#cancelBtn").click();

  await expect(page.locator(".message-row.assistant .message-bubble.processing")).toHaveCount(0);
  await expect(page.locator("#composerStatusChip")).toBeHidden();
  await expect(page.locator("#sendBtn")).toHaveText("Send");
  await page.waitForTimeout(1500);
  await expect(page.locator(".message-row.assistant .message-bubble")).toHaveCount(0);
});

test("non-stream cancel during finish animation does not render buffered assistant output", async ({ page }) => {
  await page.addInitScript(() => {
    window.__POTATO_PREFILL_FINISH_DURATION_MS__ = 1200;
    window.__POTATO_PREFILL_FINISH_HOLD_MS__ = 250;
  });
  await waitUntilReady(page);

  await page.route("**/v1/chat/completions", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "chatcmpl-cancel",
        object: "chat.completion",
        created: 1771778048,
        model: "qwen-local",
        choices: [
          {
            index: 0,
            message: {
              role: "assistant",
              content: "This should never appear after cancel.",
            },
            finish_reason: "stop",
          },
        ],
        timings: {
          prompt_ms: 1200,
          predicted_ms: 500,
          predicted_n: 8,
          predicted_per_second: 16,
        },
        usage: {
          prompt_tokens: 10,
          completion_tokens: 8,
          total_tokens: 18,
        },
      }),
    });
  });

  await openSettingsModal(page);
  await page.locator("#stream").selectOption("false");
  await closeSettingsModal(page);
  await page.locator("#userPrompt").fill("Give me a non-stream response.");
  await page.locator("#userPrompt").press("Enter");

  await page.waitForTimeout(350);

  await page.locator("#cancelBtn").click();

  await expect(page.locator(".message-row.assistant .message-bubble.processing")).toHaveCount(0);
  await expect(page.locator("#composerStatusChip")).toBeHidden();
  await expect(page.locator("#sendBtn")).toHaveText("Send");
  await page.waitForTimeout(1500);
  await expect(page.locator(".message-row.assistant .message-bubble")).toHaveCount(0);
});

test("assistant markdown strips remote resource tags while keeping safe formatting", async ({ page }) => {
  await waitUntilReady(page);

  const remoteRequests = [];
  page.on("request", (request) => {
    if (request.url().startsWith("https://example.com/")) {
      remoteRequests.push(request.url());
    }
  });

  await page.route("https://example.com/**", async (route) => {
    await route.abort();
  });

  await page.route("**/v1/chat/completions", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "chatcmpl-md-safe",
        object: "chat.completion",
        created: 1771778048,
        model: "qwen-local",
        choices: [
          {
            index: 0,
            message: {
              role: "assistant",
              content: "# Linus Torvalds\n\n- **Linux** kernel\n- Open source\n\n![tracker](https://example.com/tracker.png)\n<img src=\"https://example.com/raw.png\" alt=\"raw\">\n\n`uname -a`",
            },
            finish_reason: "stop",
          },
        ],
        timings: {
          prompt_ms: 1200,
          predicted_ms: 800,
          predicted_n: 12,
          predicted_per_second: 15,
        },
        usage: {
          prompt_tokens: 10,
          completion_tokens: 12,
          total_tokens: 22,
        },
      }),
    });
  });

  await openSettingsModal(page);
  await page.locator("#stream").selectOption("false");
  await closeSettingsModal(page);
  await page.locator("#userPrompt").fill("Format this safely.");
  await page.locator("#userPrompt").press("Enter");

  const bubble = page.locator(".message-row.assistant .message-bubble").last();
  await expect(bubble.locator("h1")).toHaveText("Linus Torvalds");
  await expect(bubble.locator("li")).toHaveCount(2);
  await expect(bubble.locator("strong")).toHaveText("Linux");
  await expect(bubble.locator("code")).toHaveText("uname -a");
  await expect(bubble.locator("img")).toHaveCount(0);
  expect(remoteRequests).toHaveLength(0);
});

test("cancel during prefill stops cleanly and shows stopped reason", async ({ page }) => {
  await waitUntilReady(page);

  await page.locator("#userPrompt").fill("Explain distributed systems in detail.");
  await page.locator("#userPrompt").press("Enter");

  await expect(page.locator(".message-row.assistant .message-bubble.processing").last()).toContainText("Prompt processing");
  await expect(page.locator("#composerStatusChip")).toBeVisible();
  await expect(page.locator("#composerStatusText")).toContainText(/Preparing prompt •/);
  await expect(page.locator("#sendBtn")).toHaveText("Stop");

  await page.locator("#cancelBtn").click();

  await expect(page.locator(".message-row.assistant .message-bubble.processing")).toHaveCount(0);
  await expect(page.locator("#composerStatusChip")).toBeHidden();
  await expect(page.locator("#sendBtn")).toHaveText("Send");
});

test("large image selection shows loading phases and optimization metadata", async ({ page }) => {
  await page.addInitScript(() => {
    window.__POTATO_PREFILL_FINISH_DURATION_MS__ = 300;
    window.__POTATO_PREFILL_FINISH_HOLD_MS__ = 350;
  });
  await waitUntilReady(page);

  await page.locator("#imageInput").setInputFiles("references/test-cat.jpg");

  await expect(page.locator("#imageMeta")).toBeVisible();
  await expect(page.locator("#imageMeta")).toContainText("optimized from");
  await expect(page.locator("#attachImageBtn")).toContainText("Change image");

  await page.locator("#userPrompt").fill("Describe this image.");
  await page.locator("#userPrompt").press("Enter");

  await expect(page.locator(".message-row.assistant .message-bubble.processing").last()).toContainText("Prompt processing");
  await expect(page.locator("#composerStatusChip")).toBeVisible();
  await expect(page.locator("#composerStatusText")).toContainText(/Preparing prompt •/);
  let sawHundred = false;
  for (let i = 0; i < 30; i += 1) {
    await page.waitForTimeout(120);
    const chip = page.locator("#composerStatusChip");
    if (await chip.isHidden()) {
      if (sawHundred) break;
      continue;
    }
    const label = await page.locator("#composerStatusText").innerText();
    if (/100%/.test(label)) {
      sawHundred = true;
    }
  }
  expect(sawHundred).toBeTruthy();
  await expect(page.locator(".message-row.assistant .message-bubble").last()).toContainText("[fake-llama.cpp]");
  await expect(page.locator(".message-meta").last()).toContainText(/TTFT \d+\.\d{2}s/);
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
  await expect(page.locator("#runtimeDetails")).toBeVisible();
  await expect(page.locator("#runtimeViewToggle")).toHaveText("Hide details");
  await expect(page.locator("#runtimeDetailCpuClockValue")).toHaveText("2400 MHz");
  await expect(page.locator("#runtimeDetails")).toContainText("Soft temp limit occurred");

  await page.locator("#runtimeViewToggle").dispatchEvent("click");
  await expect(page.locator("#runtimeCompact")).toContainText("CPU 21% @ 2400 MHz");
  await expect(page.locator("#runtimeCompact")).toContainText("GPU 910/960 MHz");
  await expect(page.locator("#runtimeCompact")).toBeVisible();
  await expect(page.locator("#runtimeDetails")).toBeHidden();
  await expect(page.locator("#runtimeViewToggle")).toHaveText("Show details");
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

  await expect(page.locator("#runtimeDetailCpuClockValue")).toHaveClass(/runtime-metric-critical/);
  await expect(page.locator("#runtimeDetailMemoryValue")).toHaveClass(/runtime-metric-critical/);
  await expect(page.locator("#runtimeDetailSwapValue")).toHaveClass(/runtime-metric-high/);
  await expect(page.locator("#runtimeDetailTempValue")).toHaveClass(/runtime-metric-high/);
});

test("fake backend ready state shows connected badge", async ({ page }) => {
  await waitUntilReady(page);
  await expect(page.locator("#statusLabel")).toHaveText("CONNECTED:Fake Backend");
  await expect(page.locator("#statusBadge")).toHaveClass(/online/);
});

test("llama ready state shows SSD marker in connected badge for SSD-backed active model", async ({ page }) => {
  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        state: "READY",
        model_present: true,
        model: {
          filename: "Qwen3.5-2B-Q4_0.gguf",
          active_model_id: "qwen3-5-2b-q4-0",
          storage: {
            location: "ssd",
            is_symlink: true,
            actual_path: "/mnt/potato-ssd/potato-models/Qwen3.5-2B-Q4_0.gguf",
          },
        },
        llama_server: { healthy: true, running: true, url: "http://127.0.0.1:8080" },
        backend: { mode: "llama", active: "llama", fallback_active: false },
        download: {
          bytes_total: 0,
          bytes_downloaded: 0,
          percent: 0,
          speed_bps: 0,
          eta_seconds: 0,
          error: null,
          active: false,
          auto_start_seconds: 300,
          auto_start_remaining_seconds: 0,
        },
      }),
    });
  });

  await page.goto("/");
  await expect(page.locator("#statusLabel")).toHaveText("CONNECTED:llama.cpp:Qwen3.5-2B-Q4_0.gguf:SSD");
  await expect(page.locator("#statusBadge")).toHaveClass(/online/);
});

test("llama booting with model present shows loading badge", async ({ page }) => {
  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        state: "BOOTING",
        model_present: true,
        model: { filename: "Qwen3-VL-4B-Instruct-Q4_K_M.gguf" },
        llama_server: { healthy: false, running: false, url: "http://127.0.0.1:8080" },
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
      }),
    });
  });

  await page.goto("/");
  await expect(page.locator("#statusLabel")).toHaveText("LOADING:llama.cpp:Qwen3-VL-4B-Instruct-Q4_K_M.gguf");
  await expect(page.locator("#statusBadge")).toHaveClass(/loading/);
});

test("llama error state shows failed badge", async ({ page }) => {
  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        state: "ERROR",
        model_present: true,
        model: { filename: "Qwen3-VL-4B-Instruct-Q4_K_M.gguf" },
        llama_server: { healthy: false, running: false, url: "http://127.0.0.1:8080" },
        backend: { mode: "llama", active: "llama", fallback_active: false },
        download: {
          bytes_total: 2497282336,
          bytes_downloaded: 2497282336,
          percent: 100,
          speed_bps: 0,
          eta_seconds: 0,
          error: "model_load_failed",
          active: false,
          auto_start_seconds: 300,
          auto_start_remaining_seconds: 0,
        },
      }),
    });
  });

  await page.goto("/");
  await expect(page.locator("#statusLabel")).toHaveText("FAILED:llama.cpp:Qwen3-VL-4B-Instruct-Q4_K_M.gguf");
  await expect(page.locator("#statusBadge")).toHaveClass(/failed/);
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

test("settings move into a modal, runtime monitor is expanded by default, and deep thinking is removed", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("potato_settings_v2", JSON.stringify({
      generation_mode: "deterministic",
      seed: 99,
      thinking_enabled: true,
    }));
  });
  await waitUntilReady(page);

  await expect(page.locator("#runtimeDetails")).toBeVisible();
  await expect(page.locator("#runtimeViewToggle")).toHaveText(/Hide details/i);
  await expect(page.locator("details.settings")).toHaveCount(0);
  await expect(page.locator("#thinkingToggleBtn")).toHaveCount(0);

  await page.locator("#settingsOpenBtn").click();
  await expect(page.locator("#settingsModal")).toBeVisible();
  await expect(page.locator("body")).toHaveClass(/settings-modal-open/);

  await page.keyboard.press("Escape");
  await expect(page.locator("#settingsModal")).toBeHidden();

  await page.locator("#settingsOpenBtn").click();
  await expect(page.locator("#settingsModal")).toBeVisible();
  await page.evaluate(() => {
    document.getElementById("settingsBackdrop").click();
  });
  await expect(page.locator("#settingsModal")).toBeHidden();

  await page.locator("#settingsOpenBtn").click();
  await expect(page.locator("#settingsModal")).toBeVisible();
  await page.locator("#settingsCloseBtn").click();
  await expect(page.locator("#settingsModal")).toBeHidden();
});

test("chat autoscroll stops following when user scrolls up and resumes only after scrolling back near the bottom", async ({ page }) => {
  await waitUntilReady(page);

  const messages = page.locator("#messages");
  await page.evaluate(() => {
    for (let index = 0; index < 24; index += 1) {
      window.appendMessage("assistant", `History block ${index}: ${"lorem ipsum ".repeat(28)}`);
    }
  });

  const baseline = await messages.evaluate((box) => {
    box.scrollTop = Math.max(0, box.scrollHeight - box.clientHeight - 260);
    return {
      scrollTop: box.scrollTop,
      maxScrollTop: box.scrollHeight - box.clientHeight,
    };
  });

  await page.evaluate(() => {
    window.appendMessage("assistant", `Newest block: ${"fresh update ".repeat(32)}`);
  });

  await expect(page.locator("#jumpToLatestBtn")).toHaveCount(0);
  const afterAppend = await messages.evaluate((box) => ({
    scrollTop: box.scrollTop,
    maxScrollTop: box.scrollHeight - box.clientHeight,
  }));
  expect(Math.abs(afterAppend.scrollTop - baseline.scrollTop)).toBeLessThan(24);
  expect(afterAppend.maxScrollTop - afterAppend.scrollTop).toBeGreaterThan(80);

  await messages.evaluate((box) => {
    box.scrollTop = box.scrollHeight;
    box.dispatchEvent(new Event("scroll", { bubbles: true }));
  });
  await page.evaluate(() => {
    window.appendMessage("assistant", `Newest block 2: ${"fresh update ".repeat(24)}`);
  });
  await expect
    .poll(async () => {
      return messages.evaluate((box) => Math.abs((box.scrollHeight - box.clientHeight) - box.scrollTop));
    })
    .toBeLessThan(8);
});

test("sending a new message forces the chat back to the latest turn", async ({ page }) => {
  await waitUntilReady(page);

  const messages = page.locator("#messages");
  await page.evaluate(() => {
    for (let index = 0; index < 24; index += 1) {
      window.appendMessage("assistant", `History block ${index}: ${"lorem ipsum ".repeat(28)}`);
    }
  });

  await messages.evaluate((box) => {
    box.scrollTop = Math.max(0, box.scrollHeight - box.clientHeight - 280);
    box.dispatchEvent(new Event("scroll", { bubbles: true }));
  });

  await page.route("**/v1/chat/completions", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "chatcmpl-force-follow",
        object: "chat.completion",
        created: 1771778048,
        model: "qwen-local",
        choices: [
          {
            index: 0,
            message: {
              role: "assistant",
              content: "Here is the newest reply.",
            },
            finish_reason: "stop",
          },
        ],
        timings: {
          prompt_ms: 420,
          predicted_ms: 180,
          predicted_n: 6,
          predicted_per_second: 32,
        },
        usage: {
          prompt_tokens: 8,
          completion_tokens: 6,
          total_tokens: 14,
        },
      }),
    });
  });

  await openSettingsModal(page);
  await page.locator("#stream").selectOption("false");
  await closeSettingsModal(page);

  await page.locator("#userPrompt").fill("Bring me back down.");
  await page.locator("#userPrompt").press("Enter");
  await expect(page.locator(".message-row.assistant .message-bubble").last()).toContainText("Here is the newest reply.");
  await expect
    .poll(async () => {
      return messages.evaluate((box) => Math.abs((box.scrollHeight - box.clientHeight) - box.scrollTop));
    })
    .toBeLessThan(8);
});

test("message bubbles allow text selection and do not force follow while selection is active", async ({ page }) => {
  await waitUntilReady(page);

  const messages = page.locator("#messages");
  await page.evaluate(() => {
    for (let index = 0; index < 18; index += 1) {
      window.appendMessage("assistant", `Copyable block ${index}: ${"select this text ".repeat(18)}`);
    }
  });

  await messages.evaluate((box) => {
    box.scrollTop = box.scrollHeight;
    box.dispatchEvent(new Event("scroll", { bubbles: true }));
  });

  const selectionState = await page.evaluate(() => {
    const bubbles = document.querySelectorAll(".message-row.assistant .message-bubble");
    const target = bubbles[bubbles.length - 1];
    const range = document.createRange();
    range.selectNodeContents(target);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    return {
      text: selection.toString(),
      bubbleUserSelect: getComputedStyle(target).userSelect,
      bubbleWebkitUserSelect: getComputedStyle(target).webkitUserSelect,
    };
  });

  expect(selectionState.text.length).toBeGreaterThan(8);
  expect(selectionState.bubbleUserSelect).not.toBe("none");
  expect(selectionState.bubbleWebkitUserSelect).not.toBe("none");

  const baseline = await messages.evaluate((box) => ({
    scrollTop: box.scrollTop,
    maxScrollTop: box.scrollHeight - box.clientHeight,
  }));

  await page.evaluate(() => {
    window.appendMessage("assistant", `Newest block while selecting: ${"new content ".repeat(26)}`);
  });

  const afterAppend = await messages.evaluate((box) => ({
    scrollTop: box.scrollTop,
    maxScrollTop: box.scrollHeight - box.clientHeight,
  }));
  expect(Math.abs(afterAppend.scrollTop - baseline.scrollTop)).toBeLessThan(24);
  expect(afterAppend.maxScrollTop - afterAppend.scrollTop).toBeGreaterThan(80);
  await expect
    .poll(async () => page.evaluate(() => window.getSelection().toString()))
    .toContain("Copyable block");
});

test("message actions copy assistant text and open the edit modal for user text", async ({ page }) => {
  await page.addInitScript(() => {
    window.__copiedMessage = "";
    const clipboard = {
      writeText: async (value) => {
        window.__copiedMessage = String(value || "");
      },
    };
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: clipboard,
    });
  });
  await waitUntilReady(page);
  await page.route("**/v1/chat/completions", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "chatcmpl-copy-edit",
        object: "chat.completion",
        created: 1771778048,
        model: "qwen-local",
        choices: [
          {
            index: 0,
            message: {
              role: "assistant",
              content: "Here is a cleaner version of that draft.",
            },
            finish_reason: "stop",
          },
        ],
        timings: {
          prompt_ms: 520,
          predicted_ms: 280,
          predicted_n: 8,
          predicted_per_second: 28,
        },
        usage: {
          prompt_tokens: 10,
          completion_tokens: 8,
          total_tokens: 18,
        },
      }),
    });
  });

  await openSettingsModal(page);
  await page.locator("#stream").selectOption("false");
  await closeSettingsModal(page);
  await page.locator("#userPrompt").fill("Please rewrite this draft.");
  await page.locator("#userPrompt").press("Enter");
  await expect(page.locator(".message-row.assistant .message-bubble").last()).toContainText("Here is a cleaner version of that draft.");

  await page.locator(".message-row.assistant .message-stack").last().hover();
  const assistantCopy = page.locator(".message-row.assistant .message-action-btn[data-action='copy']").last();
  await assistantCopy.click();
  await expect
    .poll(async () => page.evaluate(() => window.__copiedMessage))
    .toBe("Here is a cleaner version of that draft.");

  await page.locator(".message-row.user .message-stack").last().hover();
  const userEdit = page.locator(".message-row.user .message-action-btn[data-action='edit']").last();
  await userEdit.click();
  await expect(page.locator("#editModal")).toBeVisible();
  await expect(page.locator("#editMessageInput")).toHaveValue("Please rewrite this draft.");
});

test("assistant actions stay hidden until the response is finished", async ({ page }) => {
  await page.addInitScript(() => {
    window.__POTATO_PREFILL_FINISH_DURATION_MS__ = 300;
    window.__POTATO_PREFILL_FINISH_HOLD_MS__ = 250;
  });
  await waitUntilReady(page);
  await page.route("**/v1/chat/completions", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1200));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "chatcmpl-actions",
        object: "chat.completion",
        created: 1771778048,
        model: "qwen-local",
        choices: [
          {
            index: 0,
            message: {
              role: "assistant",
              content: "Here is one short fact.",
            },
            finish_reason: "stop",
          },
        ],
        timings: {
          prompt_ms: 950,
          predicted_ms: 250,
          predicted_n: 6,
          predicted_per_second: 24,
        },
        usage: {
          prompt_tokens: 8,
          completion_tokens: 6,
          total_tokens: 14,
        },
      }),
    });
  });

  await openSettingsModal(page);
  await page.locator("#stream").selectOption("false");
  await closeSettingsModal(page);

  await page.locator("#userPrompt").fill("Tell me one short fact.");
  await page.locator("#userPrompt").press("Enter");

  const assistantRow = page.locator(".message-row.assistant").filter({
    has: page.locator(".message-bubble.processing"),
  }).last();
  const assistantStack = assistantRow.locator(".message-stack");
  await assistantStack.hover();
  await expect(assistantRow.locator(".message-bubble.processing")).toBeVisible();
  await expect(assistantStack.locator(".message-action-btn[data-action='copy']")).toBeHidden();

  await expect(page.locator(".message-row.assistant .message-bubble.processing")).toHaveCount(0);
  const finishedAssistantStack = page.locator(".message-row.assistant .message-stack").last();
  await finishedAssistantStack.hover();
  await expect(finishedAssistantStack.locator(".message-action-btn[data-action='copy']")).toBeVisible();
});

test("editing a finished user turn resends from that point and removes later turns", async ({ page }) => {
  const requestPayloads = [];
  await waitUntilReady(page);
  await page.route("**/v1/chat/completions", async (route) => {
    const payload = JSON.parse(route.request().postData() || "{}");
    requestPayloads.push(payload);
    const lastMessage = payload.messages[payload.messages.length - 1];
    const content = typeof lastMessage?.content === "string"
      ? lastMessage.content
      : Array.isArray(lastMessage?.content)
        ? lastMessage.content.map((part) => part?.text || "").join(" ")
        : "";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: `chatcmpl-${requestPayloads.length}`,
        object: "chat.completion",
        created: 1771778048,
        model: "qwen-local",
        choices: [
          {
            index: 0,
            message: {
              role: "assistant",
              content: `Reply for: ${content}`,
            },
            finish_reason: "stop",
          },
        ],
        timings: {
          prompt_ms: 600,
          predicted_ms: 300,
          predicted_n: 8,
          predicted_per_second: 26,
        },
        usage: {
          prompt_tokens: 12,
          completion_tokens: 8,
          total_tokens: 20,
        },
      }),
    });
  });

  await openSettingsModal(page);
  await page.locator("#stream").selectOption("false");
  await closeSettingsModal(page);

  await page.locator("#userPrompt").fill("Original first question");
  await page.locator("#userPrompt").press("Enter");
  await expect(page.locator(".message-row.assistant .message-bubble").last()).toContainText("Reply for: Original first question");

  await page.locator("#userPrompt").fill("Second follow-up question");
  await page.locator("#userPrompt").press("Enter");
  await expect(page.locator(".message-row.assistant .message-bubble").last()).toContainText("Reply for: Second follow-up question");

  await page.locator(".message-row.user .message-stack").first().hover();
  await page.locator(".message-row.user .message-action-btn[data-action='edit']").first().click();
  await expect(page.locator("#editModal")).toBeVisible();
  await expect(page.locator("#editMessageInput")).toHaveValue("Original first question");
  await page.locator("#editMessageInput").fill("Edited first question");
  await page.locator("#editSendBtn").click();

  await expect(page.locator("#editModal")).toBeHidden();
  await expect(page.locator(".message-row.assistant .message-bubble").last()).toContainText("Reply for: Edited first question");
  await expect(page.locator(".message-row.user .message-bubble")).toHaveCount(1);
  await expect(page.locator("#messages")).not.toContainText("Second follow-up question");
  expect(requestPayloads).toHaveLength(3);
  const resentPayload = requestPayloads[2];
  const resentMessages = resentPayload.messages.map((message) => JSON.stringify(message));
  expect(resentMessages.join(" ")).toContain("Edited first question");
  expect(resentMessages.join(" ")).not.toContain("Second follow-up question");
});

test("editing while a reply is generating cancels it and restarts from that turn", async ({ page }) => {
  let requestCount = 0;
  await waitUntilReady(page);
  await page.route("**/v1/chat/completions", async (route) => {
    requestCount += 1;
    const payload = JSON.parse(route.request().postData() || "{}");
    const lastMessage = payload.messages[payload.messages.length - 1];
    const content = typeof lastMessage?.content === "string"
      ? lastMessage.content
      : Array.isArray(lastMessage?.content)
        ? lastMessage.content.map((part) => part?.text || "").join(" ")
        : "";
    if (requestCount === 2) {
      await new Promise((resolve) => setTimeout(resolve, 1200));
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: `chatcmpl-edit-${requestCount}`,
        object: "chat.completion",
        created: 1771778048,
        model: "qwen-local",
        choices: [
          {
            index: 0,
            message: {
              role: "assistant",
              content: `Reply for: ${content}`,
            },
            finish_reason: "stop",
          },
        ],
        timings: {
          prompt_ms: 700,
          predicted_ms: 320,
          predicted_n: 8,
          predicted_per_second: 25,
        },
        usage: {
          prompt_tokens: 12,
          completion_tokens: 8,
          total_tokens: 20,
        },
      }),
    });
  });

  await openSettingsModal(page);
  await page.locator("#stream").selectOption("false");
  await closeSettingsModal(page);

  await page.locator("#userPrompt").fill("Stable first turn");
  await page.locator("#userPrompt").press("Enter");
  await expect(page.locator(".message-row.assistant .message-bubble").last()).toContainText("Reply for: Stable first turn");

  await page.locator("#userPrompt").fill("Original second question");
  await page.locator("#userPrompt").press("Enter");
  await expect(page.locator(".message-row.assistant .message-bubble.processing").last()).toBeVisible();

  await page.locator(".message-row.user .message-stack").last().hover();
  await page.locator(".message-row.user .message-action-btn[data-action='edit']").last().click();
  await expect(page.locator("#editModal")).toBeVisible();
  await expect(page.locator("#editSendBtn")).toHaveText(/Cancel & send/i);
  await page.locator("#editMessageInput").fill("Edited second question");
  await page.locator("#editSendBtn").click();

  await expect(page.locator("#editModal")).toBeHidden();
  await expect(page.locator(".message-row.assistant .message-bubble.processing")).toHaveCount(0);
  await expect(page.locator(".message-row.assistant .message-bubble").last()).toContainText("Reply for: Edited second question");
  await expect(page.locator("#messages")).not.toContainText("Original second question");
});

test("model manager toggles countdown, registers URL model, and switches active model", async ({ page }) => {
  page.on("dialog", (dialog) => dialog.accept());
  let models = [
    {
      id: "default",
      filename: "Qwen3-VL-4B-Instruct-Q4_K_M.gguf",
      source_url: "https://example.com/default.gguf",
      source_type: "url",
      status: "ready",
      is_active: true,
      bytes_total: 0,
      bytes_downloaded: 0,
      percent: 0,
      error: null,
    },
    {
      id: "alt-model",
      filename: "Alt-Funny-Model.gguf",
      source_url: "https://example.com/alt.gguf",
      source_type: "url",
      status: "ready",
      is_active: false,
      bytes_total: 0,
      bytes_downloaded: 0,
      percent: 0,
      error: null,
    },
  ];
  let countdownEnabled = true;
  let activeModelId = "default";

  const statusPayload = () => ({
    state: "READY",
    model_present: true,
    model: {
      filename: models.find((m) => m.id === activeModelId)?.filename || "Qwen3-VL-4B-Instruct-Q4_K_M.gguf",
      active_model_id: activeModelId,
    },
    models: models.map((m) => ({ ...m, is_active: m.id === activeModelId })),
    upload: {
      active: false,
      model_id: null,
      bytes_total: 0,
      bytes_received: 0,
      percent: 0,
      error: null,
    },
    download: {
      bytes_total: 0,
      bytes_downloaded: 0,
      percent: 0,
      speed_bps: 0,
      eta_seconds: 0,
      error: null,
      active: models.some((m) => m.status === "downloading"),
      auto_start_seconds: 300,
      auto_start_remaining_seconds: countdownEnabled ? 120 : 0,
      countdown_enabled: countdownEnabled,
      current_model_id: models.find((m) => m.status === "downloading")?.id || null,
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

  await page.route("**/internal/download-countdown", async (route) => {
    const body = JSON.parse(route.request().postData() || "{}");
    countdownEnabled = body.enabled !== false;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ updated: true, countdown_enabled: countdownEnabled }),
    });
  });

  await page.route("**/internal/models/register", async (route) => {
    const body = JSON.parse(route.request().postData() || "{}");
    models.push({
      id: "new-url-model",
      filename: "new-url-model.gguf",
      source_url: body.source_url,
      source_type: "url",
      status: "not_downloaded",
      is_active: false,
      bytes_total: 0,
      bytes_downloaded: 0,
      percent: 0,
      error: null,
    });
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, reason: "registered", model: models[models.length - 1] }),
    });
  });

  await page.route("**/internal/models/download", async (route) => {
    const body = JSON.parse(route.request().postData() || "{}");
    models = models.map((m) => (m.id === body.model_id ? { ...m, status: "downloading", percent: 42 } : m));
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ started: true, reason: "started", model_id: body.model_id }),
    });
  });

  await page.route("**/internal/models/cancel-download", async (route) => {
    models = models.map((m) => (m.status === "downloading" ? { ...m, status: "not_downloaded", percent: 0 } : m));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ cancelled: true, reason: "cancelled" }),
    });
  });

  await page.route("**/internal/models/activate", async (route) => {
    const body = JSON.parse(route.request().postData() || "{}");
    activeModelId = body.model_id;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ switched: true, reason: "activated", restarted: true, model_id: body.model_id }),
    });
  });

  await page.route("**/internal/models/delete", async (route) => {
    const body = JSON.parse(route.request().postData() || "{}");
    models = models.filter((m) => m.id !== body.model_id);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ deleted: true, reason: "deleted", model_id: body.model_id, deleted_file: true }),
    });
  });

  await page.goto("/");
  await openSettingsModal(page);

  await page.locator("#downloadCountdownEnabled").selectOption("false");
  await expect(page.locator("#downloadCountdownEnabled")).toHaveValue("false");

  await page.locator("#modelUrlInput").fill("https://example.com/new-url-model.gguf");
  await page.locator("#registerModelBtn").click();
  await expect(page.locator("#modelsList")).toContainText("new-url-model.gguf");

  await page.locator('#modelsList .model-row[data-model-id="new-url-model"] button[data-action="download"]').click();
  await expect(page.locator('#modelsList .model-row[data-model-id="new-url-model"]')).toContainText("downloading");
  await expect(
    page.locator('#modelsList .model-row[data-model-id="new-url-model"] button[data-action="cancel-download"]')
  ).toHaveText("Stop download");
  await expect(
    page.locator('#modelsList .model-row[data-model-id="new-url-model"] button[data-action="delete"]')
  ).toHaveText("Cancel + delete");

  await page.locator('#modelsList .model-row[data-model-id="new-url-model"] button[data-action="cancel-download"]').click();
  await expect(page.locator('#modelsList .model-row[data-model-id="new-url-model"]')).toContainText("not downloaded");

  await page.locator('#modelsList .model-row[data-model-id="alt-model"] button[data-action="activate"]').click();
  await expect(page.locator("#modelName")).toHaveValue(/Alt-Funny-Model.gguf/);

  await page.locator('#modelsList .model-row[data-model-id="new-url-model"] button[data-action="delete"]').click();
  await expect(page.locator('#modelsList .model-row[data-model-id="new-url-model"]')).toHaveCount(0);
});

test("model upload sends file with filename header", async ({ page }) => {
  let sawUpload = false;
  let uploadName = "";

  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        state: "READY",
        model_present: true,
        model: { filename: "Qwen3-VL-4B-Instruct-Q4_K_M.gguf", active_model_id: "default" },
        models: [],
        upload: { active: false, model_id: null, bytes_total: 0, bytes_received: 0, percent: 0, error: null },
        download: {
          bytes_total: 0,
          bytes_downloaded: 0,
          percent: 0,
          speed_bps: 0,
          eta_seconds: 0,
          error: null,
          active: false,
          auto_start_seconds: 300,
          auto_start_remaining_seconds: 0,
          countdown_enabled: true,
          current_model_id: null,
        },
        llama_server: { healthy: true, running: true, url: "http://127.0.0.1:8080" },
        backend: { mode: "llama", active: "llama", fallback_active: false },
        system: { available: false, cpu_cores_percent: [] },
      }),
    });
  });

  await page.route("**/internal/models/upload", async (route) => {
    sawUpload = true;
    uploadName = route.request().headers()["x-potato-filename"] || "";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        uploaded: true,
        model: {
          id: "tiny-upload",
          filename: "tiny.gguf",
          source_url: null,
          source_type: "upload",
          status: "ready",
          error: null,
        },
      }),
    });
  });

  await page.goto("/");
  await openSettingsModal(page);

  await page.locator("#modelUploadInput").setInputFiles({
    name: "tiny.gguf",
    mimeType: "application/octet-stream",
    buffer: Buffer.from("tiny"),
  });
  await page.locator("#uploadModelBtn").click();
  await expect.poll(() => sawUpload).toBeTruthy();
  expect(uploadName).toBe("tiny.gguf");
});

test("model manager shows move-to-ssd action when SSD is available and posts the model id", async ({ page }) => {
  let movedModelId = "";
  let models = [
    {
      id: "local-model",
      filename: "vision-ready.gguf",
      source_url: null,
      source_type: "local_file",
      status: "ready",
      error: null,
      is_active: true,
      bytes_total: 0,
      bytes_downloaded: 0,
      percent: 0,
      storage: {
        location: "local",
        is_symlink: false,
        actual_path: "/opt/potato/models/vision-ready.gguf",
      },
    },
  ];

  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        state: "READY",
        model_present: true,
        model: { filename: "vision-ready.gguf", active_model_id: "local-model" },
        models,
        storage_targets: {
          ssd: {
            available: true,
            mount_point: "/media/pi/ssd",
            models_dir: "/media/pi/ssd/potato-models",
            free_bytes: 64000000000,
            label: "Mounted SSD",
          },
        },
        upload: { active: false, model_id: null, bytes_total: 0, bytes_received: 0, percent: 0, error: null },
        download: {
          bytes_total: 0,
          bytes_downloaded: 0,
          percent: 0,
          speed_bps: 0,
          eta_seconds: 0,
          error: null,
          active: false,
          auto_start_seconds: 300,
          auto_start_remaining_seconds: 0,
          countdown_enabled: true,
          current_model_id: null,
        },
        llama_server: { healthy: true, running: true, url: "http://127.0.0.1:8080" },
        backend: { mode: "llama", active: "llama", fallback_active: false },
        system: { available: false, cpu_cores_percent: [] },
      }),
    });
  });

  await page.route("**/internal/models/move-to-ssd", async (route) => {
    const body = JSON.parse(route.request().postData() || "{}");
    movedModelId = body.model_id;
    models = [
      {
        ...models[0],
        storage: {
          location: "ssd",
          is_symlink: true,
          actual_path: "/media/pi/ssd/potato-models/vision-ready.gguf",
        },
      },
    ];
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        moved: true,
        reason: "moved",
        model_id: body.model_id,
        storage: models[0].storage,
      }),
    });
  });

  await page.goto("/");
  await openSettingsModal(page);

  page.once("dialog", async (dialog) => {
    await dialog.accept();
  });
  await expect(
    page.locator('#modelsList .model-row[data-model-id="local-model"] button[data-action="move-to-ssd"]')
  ).toHaveText("Move to SSD");
  await page.locator('#modelsList .model-row[data-model-id="local-model"] button[data-action="move-to-ssd"]').click();

  await expect.poll(() => movedModelId).toBe("local-model");
  await expect(page.locator('#modelsList .model-row[data-model-id="local-model"]')).toContainText("On SSD");
});
