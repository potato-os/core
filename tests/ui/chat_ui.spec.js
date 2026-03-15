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
} = require("./helpers");

test("seed mode defaults to random, toggles deterministic, persists, and controls request payload", async ({ page }) => {
  await waitUntilReady(page);

  const generationMode = page.locator("#generationMode");
  const seedField = page.locator("#seed");
  const promptField = page.locator("#userPrompt");
  const sendBtn = page.locator("#sendBtn");
  await openSettingsModal(page);

  await expect(page.locator("#stream")).toHaveCount(0);
  await expect(page.locator("text=Streaming")).toHaveCount(0);
  await chooseModelSegment(page, "generationMode", "random");
  await expect(seedField).toBeDisabled();
  await saveModelSettings(page);
  await closeSettingsModal(page);
  await promptField.fill("Seed random request.");
  const randomRequestPromise = page.waitForRequest("**/v1/chat/completions");
  await promptField.press("Enter");
  const randomRequest = await randomRequestPromise;
  const randomPayload = JSON.parse(randomRequest.postData() || "{}");
  expect(randomPayload.stream).toBe(true);
  expect(randomPayload.seed).toBeUndefined();
  await expect(sendBtn).toHaveText("Send");

  await openSettingsModal(page);
  await chooseModelSegment(page, "generationMode", "deterministic");
  await expect(seedField).toBeEnabled();
  await seedField.fill("1337");
  await saveModelSettings(page);
  await closeSettingsModal(page);

  await promptField.fill("Seed deterministic request.");
  const deterministicRequestPromise = page.waitForRequest("**/v1/chat/completions");
  await promptField.press("Enter");
  const deterministicRequest = await deterministicRequestPromise;
  const deterministicPayload = JSON.parse(deterministicRequest.postData() || "{}");
  expect(deterministicPayload.stream).toBe(true);
  expect(deterministicPayload.seed).toBe(1337);
  await expect(sendBtn).toHaveText("Send");

  await openSettingsModal(page);
  await chooseModelSegment(page, "generationMode", "random");
  await expect(seedField).toBeDisabled();
  await expect(seedField).toHaveValue("1337");
  await saveModelSettings(page);
  await closeSettingsModal(page);

  await promptField.fill("Seed random request after toggle.");
  const randomRequestAfterTogglePromise = page.waitForRequest("**/v1/chat/completions");
  await promptField.press("Enter");
  const randomRequestAfterToggle = await randomRequestAfterTogglePromise;
  const randomPayloadAfterToggle = JSON.parse(randomRequestAfterToggle.postData() || "{}");
  expect(randomPayloadAfterToggle.stream).toBe(true);
  expect(randomPayloadAfterToggle.seed).toBeUndefined();
  await expect(sendBtn).toHaveText("Send");

  await openSettingsModal(page);
  await chooseModelSegment(page, "generationMode", "deterministic");
  await expect(seedField).toBeEnabled();
  await expect(seedField).toHaveValue("1337");
  await saveModelSettings(page);
  await closeSettingsModal(page);

  await page.reload();
  await openSettingsModal(page);
  await expect(page.locator("#generationMode")).toHaveValue("deterministic");
  await expect(page.locator("#seed")).toHaveValue("1337");
  await expect(page.locator("#seed")).toBeEnabled();
});

test("product chat requests still stream when saved model settings say stream false", async ({ page }) => {
  const promptField = page.locator("#userPrompt");
  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        state: "READY",
        model_present: true,
        model: {
          filename: "stream-false.gguf",
          active_model_id: "stream-false-model",
          settings: {
            chat: {
              system_prompt: "",
              stream: false,
              generation_mode: "random",
              seed: 42,
              temperature: 0.7,
              top_p: 0.8,
              top_k: 20,
              repetition_penalty: 1.0,
              presence_penalty: 1.5,
              max_tokens: 4096,
            },
            vision: {
              enabled: false,
              projector_mode: "default",
              projector_filename: "",
            },
          },
          capabilities: { vision: false },
          projector: {
            present: false,
            filename: "",
            default_candidates: [],
          },
        },
        models: [
          {
            id: "stream-false-model",
            filename: "stream-false.gguf",
            source_url: null,
            source_type: "local_file",
            status: "ready",
            is_active: true,
            settings: {
              chat: {
                system_prompt: "",
                stream: false,
                generation_mode: "random",
                seed: 42,
                temperature: 0.7,
                top_p: 0.8,
                top_k: 20,
                repetition_penalty: 1.0,
                presence_penalty: 1.5,
                max_tokens: 4096,
              },
              vision: {
                enabled: false,
                projector_mode: "default",
                projector_filename: "",
              },
            },
            capabilities: { vision: false },
            projector: {
              present: false,
              filename: "",
              default_candidates: [],
            },
            bytes_total: 0,
            bytes_downloaded: 0,
            percent: 0,
            error: null,
          },
        ],
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
          countdown_enabled: false,
          auto_download_paused: true,
          current_model_id: null,
        },
        llama_server: { healthy: true, running: true, url: "http://127.0.0.1:8080" },
        backend: { mode: "llama", active: "llama", fallback_active: false },
        system: { available: false, cpu_cores_percent: [] },
      }),
    });
  });

  await waitUntilReady(page);
  await openSettingsModal(page);
  await expect(page.locator("#stream")).toHaveCount(0);
  await closeSettingsModal(page);

  await promptField.fill("Always stream this.");
  const requestPromise = page.waitForRequest("**/v1/chat/completions");
  await promptField.press("Enter");
  const request = await requestPromise;
  const payload = JSON.parse(request.postData() || "{}");
  expect(payload.stream).toBe(true);
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
    await fulfillStreamingChat(route, {
      content: "# Linus Torvalds\n\nHere are the key facts:\n\n- **Linux** kernel\n- Open source\n\n`uname -a`",
      timings: {
        prompt_ms: 1200,
        predicted_ms: 800,
        predicted_n: 12,
        predicted_per_second: 15,
      },
    });
  });

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
    await fulfillStreamingChat(route, {
      content: "# Linus Torvalds\n\n- **Linux** kernel\n- Open source\n\n![tracker](https://example.com/tracker.png)\n<img src=\"https://example.com/raw.png\" alt=\"raw\">\n\n`uname -a`",
      timings: {
        prompt_ms: 1200,
        predicted_ms: 800,
        predicted_n: 12,
        predicted_per_second: 15,
      },
    });
  });

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

  await page.locator("#imageInput").setInputFiles("tests/ui/fixtures/test-cat.jpg");

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

  await page.locator("#imageInput").setInputFiles("tests/ui/fixtures/test-cat.jpg");
  await expect(page.locator("#imageMeta")).toBeVisible();
  await expect(page.locator("#userPrompt")).toBeFocused();

  await page.locator("#userPrompt").fill("Please describe this image.");
  await page.locator("#userPrompt").press("Enter");

  await expect(page.locator(".message-row.assistant .message-bubble").last()).toContainText("[fake-llama.cpp]");
  await expect(page.locator("#userPrompt")).toBeFocused();
});

test("text-only active model disables image attach and explains why", async ({ page }) => {
  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        state: "READY",
        model_present: true,
        model: {
          filename: "text-only.gguf",
          active_model_id: "text-only-model",
          settings: {
            chat: {
              system_prompt: "",
              stream: true,
              generation_mode: "random",
              seed: 42,
              temperature: 0.7,
              top_p: 0.8,
              top_k: 20,
              repetition_penalty: 1.0,
              presence_penalty: 1.5,
              max_tokens: 4096,
            },
            vision: {
              enabled: false,
              projector_mode: "default",
              projector_filename: "",
            },
          },
          capabilities: { vision: false },
          projector: {
            present: false,
            filename: "",
            default_candidates: [],
          },
        },
        models: [
          {
            id: "text-only-model",
            filename: "text-only.gguf",
            source_url: null,
            source_type: "local_file",
            status: "ready",
            is_active: true,
            settings: {
              chat: {
                system_prompt: "",
                stream: true,
                generation_mode: "random",
                seed: 42,
                temperature: 0.7,
                top_p: 0.8,
                top_k: 20,
                repetition_penalty: 1.0,
                presence_penalty: 1.5,
                max_tokens: 4096,
              },
              vision: {
                enabled: false,
                projector_mode: "default",
                projector_filename: "",
              },
            },
            capabilities: { vision: false },
            projector: {
              present: false,
              filename: "",
              default_candidates: [],
            },
            bytes_total: 0,
            bytes_downloaded: 0,
            percent: 0,
            error: null,
          },
        ],
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

  await waitUntilReady(page);

  await expect(page.locator("#attachImageBtn")).toBeDisabled();
  await expect(page.locator("#composerVisionNotice")).toContainText("text-only");
  await expect(page.locator("#composerVisionNotice")).toContainText("vision-capable");

  await page.locator("#imageInput").setInputFiles("tests/ui/fixtures/test-cat.jpg");
  await expect(page.locator("#imageMeta")).toBeHidden();
  await expect(page.locator("#imagePreviewWrap")).toBeHidden();
  await expect(page.locator("#clearImageBtn")).toBeHidden();
  await expect(page.locator("#userPrompt")).toBeFocused();
});

test("image-send failures show friendly guidance and leave the composer ready for text retry", async ({ page }) => {
  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        state: "READY",
        model_present: true,
        model: {
          filename: "vision-ready.gguf",
          active_model_id: "vision-model",
          settings: {
            chat: {
              system_prompt: "",
              stream: false,
              generation_mode: "random",
              seed: 42,
              temperature: 0.7,
              top_p: 0.8,
              top_k: 20,
              repetition_penalty: 1.0,
              presence_penalty: 1.5,
              max_tokens: 4096,
            },
            vision: {
              enabled: true,
              projector_mode: "default",
              projector_filename: "mmproj-F16.gguf",
            },
          },
          capabilities: { vision: true },
          projector: {
            present: true,
            filename: "mmproj-F16.gguf",
            default_candidates: ["mmproj-F16.gguf"],
          },
        },
        models: [
          {
            id: "vision-model",
            filename: "vision-ready.gguf",
            source_url: null,
            source_type: "local_file",
            status: "ready",
            is_active: true,
            settings: {
              chat: {
                system_prompt: "",
                stream: false,
                generation_mode: "random",
                seed: 42,
                temperature: 0.7,
                top_p: 0.8,
                top_k: 20,
                repetition_penalty: 1.0,
                presence_penalty: 1.5,
                max_tokens: 4096,
              },
              vision: {
                enabled: true,
                projector_mode: "default",
                projector_filename: "mmproj-F16.gguf",
              },
            },
            capabilities: { vision: true },
            projector: {
              present: true,
              filename: "mmproj-F16.gguf",
              default_candidates: ["mmproj-F16.gguf"],
            },
            bytes_total: 0,
            bytes_downloaded: 0,
            percent: 0,
            error: null,
          },
        ],
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

  await page.route("**/v1/chat/completions", async (route) => {
    const payload = JSON.parse(route.request().postData() || "{}");
    expect(payload.stream).toBe(true);
    const lastMessage = payload.messages[payload.messages.length - 1];
    const hasImage = Array.isArray(lastMessage?.content)
      && lastMessage.content.some((part) => part?.type === "image_url");
    if (hasImage) {
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({
          error: {
            code: 500,
            message: "image input is not supported - hint: if this is unexpected, you may need to provide the mmproj",
            type: "server_error",
          },
        }),
      });
      return;
    }

    await route.continue();
  });

  await waitUntilReady(page);

  await page.locator("#imageInput").setInputFiles("tests/ui/fixtures/test-cat.jpg");
  await expect(page.locator("#imageMeta")).toBeVisible();
  await expect(page.locator("#clearImageBtn")).toBeVisible();
  await expect(page.locator("#imagePreviewWrap")).toBeVisible();

  await page.locator("#userPrompt").fill("What animal is this?");
  await page.locator("#userPrompt").press("Enter");

  await expect(page.locator(".message-row.assistant .message-bubble").last()).toContainText("can't process images");
  await expect(page.locator(".message-row.assistant .message-bubble").last()).not.toContainText("Request failed (500)");
  await expect(page.locator(".message-row.assistant .message-bubble").last()).not.toContainText("mmproj");
  await expect(page.locator("#attachImageBtn")).toContainText("Attach image");
  await expect(page.locator("#clearImageBtn")).toBeHidden();
  await expect(page.locator("#imageMeta")).toBeHidden();
  await expect(page.locator("#imagePreviewWrap")).toBeHidden();
  await expect(page.locator("#composerStatusChip")).toBeHidden();
  await expect(page.locator("#userPrompt")).toBeFocused();

  await page.locator("#userPrompt").fill("Plain text follow-up.");
  await page.locator("#userPrompt").press("Enter");
  await expect(page.locator(".message-row.assistant .message-bubble").last()).toContainText("[fake-llama.cpp]");
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

  await page.locator("#imageInput").setInputFiles("tests/ui/fixtures/test-cat.jpg");
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

  await expect(page.locator("#downloadPrompt")).toBeHidden();
  await expect(page.locator("#statusText")).toContainText("Download failed");
  await expect(page.locator("#statusResumeDownloadBtn")).toBeVisible();
  await expect(page.locator("#statusResumeDownloadBtn")).toHaveText("Resume");

  await page.locator("#statusResumeDownloadBtn").click();

  await expect.poll(() => downloadCalls).toEqual(["failed-model"]);
  await expect(page.locator("#statusResumeDownloadBtn")).toBeHidden();
  await expect(page.locator("#statusText")).toContainText("Download: 15%");
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
      theme: "dark",
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
  await expect(page.locator("#settingsModelWorkspace")).toBeVisible();
  await expect(page.locator("#settingsModal #downloadCountdownEnabled")).toHaveCount(0);

  await openAdvancedSettingsModal(page);
  await expect(page.locator("#legacySettingsRuntimeSection")).toBeVisible();
  await closeAdvancedSettingsModal(page);

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
    await fulfillStreamingChat(route, {
      content: "Here is the newest reply.",
      timings: {
        prompt_ms: 420,
        predicted_ms: 180,
        predicted_n: 6,
        predicted_per_second: 32,
      },
    });
  });

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
    await fulfillStreamingChat(route, {
      content: "Here is a cleaner version of that draft.",
      timings: {
        prompt_ms: 520,
        predicted_ms: 280,
        predicted_n: 8,
        predicted_per_second: 28,
      },
    });
  });

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
    await fulfillStreamingChat(route, {
      content: "Here is one short fact.",
      timings: {
        prompt_ms: 950,
        predicted_ms: 250,
        predicted_n: 6,
        predicted_per_second: 24,
      },
    });
  });

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
    await fulfillStreamingChat(route, {
      content: `Reply for: ${content}`,
      timings: {
        prompt_ms: 600,
        predicted_ms: 300,
        predicted_n: 8,
        predicted_per_second: 26,
      },
    });
  });

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
    await fulfillStreamingChat(route, {
      content: `Reply for: ${content}`,
      timings: {
        prompt_ms: 700,
        predicted_ms: 320,
        predicted_n: 8,
        predicted_per_second: 25,
      },
    });
  });

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

test("model-first settings save per model, yaml can be applied, and projector download is exposed for vision models", async ({ page }) => {
  page.on("dialog", (dialog) => dialog.accept());
  let models = [
    {
      id: "default",
      filename: "Qwen3-VL-4B-Instruct-Q4_K_M.gguf",
      source_url: "https://example.com/default.gguf",
      source_type: "url",
      status: "ready",
      is_active: true,
      settings: {
        chat: {
          system_prompt: "",
          stream: true,
          generation_mode: "random",
          seed: 42,
          temperature: 0.7,
          top_p: 0.8,
          top_k: 20,
          repetition_penalty: 1.0,
          presence_penalty: 1.5,
          max_tokens: 16384,
        },
        vision: {
          enabled: true,
          projector_mode: "default",
          projector_filename: "",
        },
      },
      capabilities: { vision: true },
      projector: {
        present: false,
        filename: null,
        default_candidates: ["mmproj-F16.gguf"],
      },
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
      settings: {
        chat: {
          system_prompt: "Alt instructions",
          stream: false,
          generation_mode: "deterministic",
          seed: 99,
          temperature: 0.2,
          top_p: 0.5,
          top_k: 8,
          repetition_penalty: 1.1,
          presence_penalty: 0.0,
          max_tokens: 512,
        },
        vision: {
          enabled: false,
          projector_mode: "default",
          projector_filename: "",
        },
      },
      capabilities: { vision: false },
      projector: {
        present: false,
        filename: null,
        default_candidates: [],
      },
      bytes_total: 0,
      bytes_downloaded: 0,
      percent: 0,
      error: null,
    },
  ];
  let activeModelId = "default";
  let lastProjectorDownloadModelId = "";
  let lastSettingsDocument = "";
  const savedPayloads = [];
  let statusHits = 0;

  const statusPayload = () => ({
    state: "READY",
    model_present: true,
    model: {
      filename: models.find((m) => m.id === activeModelId)?.filename || "Qwen3-VL-4B-Instruct-Q4_K_M.gguf",
      active_model_id: activeModelId,
      settings: models.find((m) => m.id === activeModelId)?.settings,
      capabilities: models.find((m) => m.id === activeModelId)?.capabilities,
      projector: models.find((m) => m.id === activeModelId)?.projector,
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
      auto_start_remaining_seconds: 0,
      countdown_enabled: false,
      auto_download_paused: true,
      current_model_id: models.find((m) => m.status === "downloading")?.id || null,
    },
    llama_server: { healthy: true, running: true, url: "http://127.0.0.1:8080" },
    backend: { mode: "llama", active: "llama", fallback_active: false },
    system: { available: false, cpu_cores_percent: [] },
  });

  await page.route("**/status", async (route) => {
    statusHits += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(statusPayload()),
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
      settings: {
        chat: {
          system_prompt: "",
          stream: true,
          generation_mode: "random",
          seed: 42,
          temperature: 0.7,
          top_p: 0.8,
          top_k: 20,
          repetition_penalty: 1.0,
          presence_penalty: 1.5,
          max_tokens: 16384,
        },
        vision: {
          enabled: false,
          projector_mode: "default",
          projector_filename: "",
        },
      },
      capabilities: { vision: false },
      projector: {
        present: false,
        filename: null,
        default_candidates: [],
      },
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

  await page.route("**/internal/models/settings", async (route) => {
    const body = JSON.parse(route.request().postData() || "{}");
    savedPayloads.push(body);
    models = models.map((model) => (
      model.id === body.model_id
        ? { ...model, settings: body.settings }
        : model
    ));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ updated: true, reason: "updated", model_id: body.model_id, model: models.find((m) => m.id === body.model_id) }),
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

  await page.route("**/internal/settings-document", async (route) => {
    if (route.request().method() === "GET") {
      const document = [
        "version: 1",
        `active_model_id: ${activeModelId}`,
        "runtime:",
        "  memory_loading_mode: auto",
        "  allow_unsupported_large_models: false",
        "models:",
        ...models.flatMap((model) => [
          `  - id: ${model.id}`,
          "    settings:",
          "      chat:",
          `        system_prompt: ${JSON.stringify(model.settings.chat.system_prompt)}`,
          `        stream: ${model.settings.chat.stream ? "true" : "false"}`,
          `        generation_mode: ${model.settings.chat.generation_mode}`,
          `        seed: ${model.settings.chat.seed}`,
          `        temperature: ${model.settings.chat.temperature}`,
          `        top_p: ${model.settings.chat.top_p}`,
          `        top_k: ${model.settings.chat.top_k}`,
          `        repetition_penalty: ${model.settings.chat.repetition_penalty}`,
          `        presence_penalty: ${model.settings.chat.presence_penalty}`,
          `        max_tokens: ${model.settings.chat.max_tokens}`,
          "      vision:",
          `        enabled: ${model.settings.vision.enabled ? "true" : "false"}`,
          `        projector_mode: ${model.settings.vision.projector_mode}`,
          `        projector_filename: ${JSON.stringify(model.settings.vision.projector_filename)}`,
        ]),
        "",
      ].join("\\n");
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ format: "yaml", document }),
      });
      return;
    }
    const body = JSON.parse(route.request().postData() || "{}");
    lastSettingsDocument = body.document;
    activeModelId = "alt-model";
    models = models.map((model) => (
      model.id === "alt-model"
        ? {
            ...model,
            settings: {
              ...model.settings,
              chat: {
                ...model.settings.chat,
                system_prompt: "Applied from yaml",
                max_tokens: 1024,
              },
            },
          }
        : model
    ));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ updated: true, reason: "updated", active_model_id: activeModelId, document: body.document, restarted: true }),
    });
  });

  await page.route("**/internal/models/download-projector", async (route) => {
    const body = JSON.parse(route.request().postData() || "{}");
    lastProjectorDownloadModelId = body.model_id;
    models = models.map((model) => (
      model.id === body.model_id
        ? {
            ...model,
            projector: {
              present: true,
              filename: "mmproj-F16.gguf",
              default_candidates: ["mmproj-F16.gguf"],
            },
            settings: {
              ...model.settings,
              vision: {
                ...model.settings.vision,
                projector_filename: "mmproj-F16.gguf",
              },
            },
          }
        : model
    ));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ downloaded: true, reason: "downloaded", model_id: body.model_id, projector_filename: "mmproj-F16.gguf" }),
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

  await expect(page.locator("#settingsModelWorkspace")).toBeVisible();
  await expect(page.locator("#settingsWorkspaceTabYaml")).toBeVisible();
  await expect(page.locator("#settingsAdvancedBtn")).toBeVisible();
  await expect(page.locator("#settingsAdvancedBtn")).toBeEnabled();
  await expect(page.locator("#settingsModal #downloadCountdownEnabled")).toHaveCount(0);
  await expect(page.locator("#purgeModelsBtn")).toBeHidden();

  await page.locator('#modelsList .model-row[data-model-id="default"]').click();
  await expect(page.locator("#modelName")).toHaveText(/Qwen3-VL-4B-Instruct-Q4_K_M.gguf/);
  await expect(page.locator("#modelCapabilitiesChips .settings-chip")).toHaveCount(3);
  await expect(page.locator("#modelCapabilitiesChips")).toContainText("Active");
  await expect(page.locator("#modelCapabilitiesChips")).toContainText("Ready");
  await expect(page.locator("#modelCapabilitiesChips")).toContainText("Vision");
  await expect(page.locator("#downloadProjectorBtn")).toBeVisible();
  await page.locator("#downloadProjectorBtn").click();
  await expect.poll(() => lastProjectorDownloadModelId).toBe("default");
  await expect(page.locator("#projectorStatusText")).toContainText("mmproj-F16.gguf");

  await page.locator("#modelUrlInput").fill("https://example.com/new-url-model.gguf");
  await page.locator("#registerModelBtn").click();
  await expect(page.locator("#modelsList")).toContainText("new-url-model.gguf");

  await page.locator('#modelsList .model-row[data-model-id="new-url-model"] button[data-action="download"]').click();
  await expect(page.locator('#modelsList .model-row[data-model-id="new-url-model"]')).toContainText("Downloading");
  await expect(
    page.locator('#modelsList .model-row[data-model-id="new-url-model"] button[data-action="cancel-download"]')
  ).toHaveText("Stop download");
  await expect(
    page.locator('#modelsList .model-row[data-model-id="new-url-model"] button[data-action="delete"]')
  ).toHaveText("Cancel + delete");

  await page.locator('#modelsList .model-row[data-model-id="new-url-model"] button[data-action="cancel-download"]').click();
  await expect(page.locator('#modelsList .model-row[data-model-id="new-url-model"]')).toContainText("Not Downloaded");

  await page.locator('#modelsList .model-row[data-model-id="alt-model"]').click();
  await expect(page.locator("#systemPrompt")).toHaveValue("Alt instructions");
  await expect(page.locator("#modelCapabilitiesChips")).toContainText("Inactive");
  await expect(page.locator("#modelCapabilitiesChips")).toContainText("Text only");
  await expect(page.locator("#stream")).toHaveCount(0);
  await expect(page.locator("#generationMode")).toHaveValue("deterministic");
  await page.locator("#systemPrompt").evaluate((node) => {
    node.value = "Saved per-model";
    node.dispatchEvent(new Event("input", { bubbles: true }));
    node.dispatchEvent(new Event("change", { bubbles: true }));
  });
  const statusHitsBeforeWait = statusHits;
  await page.waitForTimeout(2600);
  expect(statusHits - statusHitsBeforeWait).toBeLessThanOrEqual(1);
  await page.locator("#temperature").fill("0.4");
  await saveModelSettings(page);
  expect(savedPayloads.at(-1)?.settings?.chat?.stream).toBe(false);

  await page.locator('#modelsList .model-row[data-model-id="alt-model"] button[data-action="activate"]').click();
  await expect(page.locator("#modelName")).toHaveText(/Alt-Funny-Model.gguf/);

  await page.locator("#settingsWorkspaceTabYaml").click();
  await expect(page.locator("#settingsYamlPanel")).toBeVisible();
  await expect(page.locator("#settingsYamlInput")).toHaveValue(/active_model_id/);
  await page.locator("#settingsYamlInput").fill([
    "version: 1",
    "active_model_id: alt-model",
    "runtime:",
    "  memory_loading_mode: auto",
    "  allow_unsupported_large_models: false",
    "models:",
    "  - id: alt-model",
    "    settings:",
    "      chat:",
    "        system_prompt: Applied from yaml",
    "        stream: false",
    "        generation_mode: deterministic",
    "        seed: 99",
    "        temperature: 0.4",
    "        top_p: 0.5",
    "        top_k: 8",
    "        repetition_penalty: 1.1",
    "        presence_penalty: 0.0",
    "        max_tokens: 1024",
    "      vision:",
    "        enabled: false",
    "        projector_mode: default",
    "        projector_filename: \"\"",
  ].join("\n"));
  await page.locator("#settingsYamlApplyBtn").click();
  await expect(page.locator("#settingsYamlStatus")).toContainText(/applied/i);
  expect(lastSettingsDocument).toContain("Applied from yaml");
  expect(lastSettingsDocument).toContain("stream: false");

  await page.locator("#settingsWorkspaceTabModel").click();
  await expect(page.locator("#systemPrompt")).toHaveValue("Applied from yaml");
  await expect(page.locator("#max_tokens")).toHaveValue("1024");

  await openAdvancedSettingsModal(page);
  await expect(page.locator("#legacySettingsRuntimeSection")).toBeVisible();
  await closeAdvancedSettingsModal(page);

  await page.locator('#modelsList .model-row[data-model-id="new-url-model"] button[data-action="delete"]').click();
  await expect(page.locator('#modelsList .model-row[data-model-id="new-url-model"]')).toHaveCount(0);
});

test("model settings block cross-model actions until edits are saved or discarded", async ({ page }) => {
  const savedPayloads = [];
  const activateCalls = [];
  let models = [
    {
      id: "default",
      filename: "Qwen3-VL-4B-Instruct-Q4_K_M.gguf",
      source_url: "https://example.com/default.gguf",
      source_type: "url",
      status: "ready",
      is_active: true,
      settings: {
        chat: {
          system_prompt: "Default prompt",
          stream: true,
          generation_mode: "random",
          seed: 42,
          temperature: 0.7,
          top_p: 0.8,
          top_k: 20,
          repetition_penalty: 1.0,
          presence_penalty: 1.5,
          max_tokens: 16384,
        },
        vision: {
          enabled: true,
          projector_mode: "default",
          projector_filename: "",
        },
      },
      capabilities: { vision: true },
      projector: {
        present: false,
        filename: null,
        default_candidates: ["mmproj-F16.gguf"],
      },
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
      settings: {
        chat: {
          system_prompt: "Alt instructions",
          stream: false,
          generation_mode: "deterministic",
          seed: 99,
          temperature: 0.2,
          top_p: 0.5,
          top_k: 8,
          repetition_penalty: 1.1,
          presence_penalty: 0.0,
          max_tokens: 512,
        },
        vision: {
          enabled: false,
          projector_mode: "default",
          projector_filename: "",
        },
      },
      capabilities: { vision: false },
      projector: {
        present: false,
        filename: null,
        default_candidates: [],
      },
      bytes_total: 0,
      bytes_downloaded: 0,
      percent: 0,
      error: null,
    },
  ];
  let activeModelId = "default";

  const statusPayload = () => ({
    state: "READY",
    model_present: true,
    model: {
      filename: models.find((m) => m.id === activeModelId)?.filename || "Qwen3-VL-4B-Instruct-Q4_K_M.gguf",
      active_model_id: activeModelId,
      settings: models.find((m) => m.id === activeModelId)?.settings,
      capabilities: models.find((m) => m.id === activeModelId)?.capabilities,
      projector: models.find((m) => m.id === activeModelId)?.projector,
    },
    models: models.map((m) => ({ ...m, is_active: m.id === activeModelId })),
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
      countdown_enabled: false,
      auto_download_paused: true,
      current_model_id: null,
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

  await page.route("**/internal/models/settings", async (route) => {
    const body = JSON.parse(route.request().postData() || "{}");
    savedPayloads.push(body);
    models = models.map((model) => (
      model.id === body.model_id
        ? { ...model, settings: body.settings }
        : model
    ));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ updated: true, reason: "updated", model_id: body.model_id, model: models.find((m) => m.id === body.model_id) }),
    });
  });

  await page.route("**/internal/models/activate", async (route) => {
    const body = JSON.parse(route.request().postData() || "{}");
    activateCalls.push(body.model_id);
    activeModelId = body.model_id;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ switched: true, reason: "activated", restarted: true, model_id: body.model_id }),
    });
  });

  await page.goto("/");
  await openSettingsModal(page);

  await page.locator('#modelsList .model-row[data-model-id="default"]').click();
  await expect(page.locator("#modelName")).toHaveText(/Qwen3-VL-4B-Instruct-Q4_K_M.gguf/);
  await page.locator("#systemPrompt").fill("Unsaved default draft");

  await page.locator('#modelsList .model-row[data-model-id="alt-model"]').click();
  await expect(page.locator("#modelName")).toHaveText(/Qwen3-VL-4B-Instruct-Q4_K_M.gguf/);
  await expect(page.locator("#systemPrompt")).toHaveValue("Unsaved default draft");
  await expect(page.locator("#modelSettingsStatus")).toContainText(/save or discard/i);

  await page.locator('#modelsList .model-row[data-model-id="alt-model"] button[data-action="activate"]').click();
  await expect(page.locator("#modelSettingsStatus")).toContainText(/save or discard/i);
  expect(activateCalls).toEqual([]);

  await page.locator("#discardModelSettingsBtn").click();
  await expect(page.locator("#modelSettingsStatus")).toContainText(/discarded|reverted/i);

  await page.locator('#modelsList .model-row[data-model-id="alt-model"]').click();
  await expect(page.locator("#modelName")).toHaveText(/Alt-Funny-Model.gguf/);
  await expect(page.locator("#systemPrompt")).toHaveValue("Alt instructions");

  await page.locator("#systemPrompt").fill("Saved alt draft");
  await saveModelSettings(page);
  expect(savedPayloads.at(-1)?.model_id).toBe("alt-model");
  expect(savedPayloads.at(-1)?.settings?.chat?.system_prompt).toBe("Saved alt draft");
});

test("settings show installed projector state from canonical backend payload", async ({ page }) => {
  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        state: "READY",
        model_present: true,
        model: {
          filename: "Qwen_Qwen3.5-2B-IQ4_NL.gguf",
          active_model_id: "vision-model",
          settings: {
            chat: {
              system_prompt: "",
              stream: true,
              generation_mode: "random",
              seed: 42,
              temperature: 0.7,
              top_p: 0.8,
              top_k: 20,
              repetition_penalty: 1.0,
              presence_penalty: 1.5,
              max_tokens: 16384,
            },
            vision: {
              enabled: true,
              projector_mode: "default",
              projector_filename: "",
            },
          },
          capabilities: { vision: true },
          projector: {
            present: true,
            filename: "mmproj-F16.gguf",
            default_candidates: ["mmproj-F16.gguf", "mmproj-BF16.gguf"],
          },
        },
        models: [
          {
            id: "vision-model",
            filename: "Qwen_Qwen3.5-2B-IQ4_NL.gguf",
            source_url: "https://example.com/qwen35.gguf",
            source_type: "url",
            status: "ready",
            is_active: true,
            settings: {
              chat: {
                system_prompt: "",
                stream: true,
                generation_mode: "random",
                seed: 42,
                temperature: 0.7,
                top_p: 0.8,
                top_k: 20,
                repetition_penalty: 1.0,
                presence_penalty: 1.5,
                max_tokens: 16384,
              },
              vision: {
                enabled: true,
                projector_mode: "default",
                projector_filename: "",
              },
            },
            capabilities: { vision: true },
            projector: {
              present: true,
              filename: "mmproj-F16.gguf",
              default_candidates: ["mmproj-F16.gguf", "mmproj-BF16.gguf"],
            },
            bytes_total: 0,
            bytes_downloaded: 0,
            percent: 0,
            error: null,
          },
        ],
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
          countdown_enabled: false,
          auto_download_paused: true,
          current_model_id: null,
        },
        llama_server: { healthy: true, running: true, url: "http://127.0.0.1:8080" },
        backend: { mode: "llama", active: "llama", fallback_active: false },
        system: { available: false, cpu_cores_percent: [] },
      }),
    });
  });

  await page.goto("/");
  await openSettingsModal(page);

  await expect(page.locator("#projectorStatusText")).toContainText("mmproj-F16.gguf");
  await expect(page.locator("#downloadProjectorBtn")).toHaveText("Re-download vision encoder");
});

test("add model by URL shows inline validation feedback", async ({ page }) => {
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
          countdown_enabled: false,
          auto_download_paused: true,
          current_model_id: null,
        },
        llama_server: { healthy: true, running: true, url: "http://127.0.0.1:8080" },
        backend: { mode: "llama", active: "llama", fallback_active: false },
        system: { available: false, cpu_cores_percent: [] },
      }),
    });
  });

  await page.route("**/internal/models/register", async (route) => {
    await route.fulfill({
      status: 400,
      contentType: "application/json",
      body: JSON.stringify({ ok: false, reason: "https_required" }),
    });
  });

  await page.goto("/");
  await openSettingsModal(page);

  await page.locator("#modelUrlInput").fill("http://example.com/bad-model.gguf");
  await page.locator("#registerModelBtn").click();

  await expect(page.locator("#modelUrlStatus")).toContainText(/https/i);
  await expect(page.locator("#modelUrlInput")).toHaveValue("http://example.com/bad-model.gguf");
});

test("sidebar status avoids stale completed download text when downloads are idle", async ({ page }) => {
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
          bytes_total: 12_400_000_000,
          bytes_downloaded: 12_400_000_000,
          percent: 100,
          speed_bps: 0,
          eta_seconds: 0,
          error: null,
          active: false,
          auto_start_seconds: 300,
          auto_start_remaining_seconds: 0,
          countdown_enabled: false,
          auto_download_paused: true,
          current_model_id: null,
        },
        llama_server: { healthy: true, running: true, url: "http://127.0.0.1:8080" },
        backend: { mode: "llama", active: "llama", fallback_active: false },
        system: { available: false, cpu_cores_percent: [] },
      }),
    });
  });

  await page.goto("/");

  await expect(page.locator("#statusText")).toContainText("Auto-download paused");
  await expect(page.locator("#statusText")).not.toContainText("Download: 100%");
  await expect(page.locator("#statusResumeDownloadBtn")).toBeHidden();
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

test("chat request sends stream true and messages array to the backend", async ({ page }) => {
  await waitUntilReady(page);

  const requestPromise = page.waitForRequest("**/v1/chat/completions");
  await page.locator("#userPrompt").fill("Verify chat payload.");
  await page.locator("#userPrompt").press("Enter");
  const request = await requestPromise;
  const payload = JSON.parse(request.postData() || "{}");

  expect(payload.stream).toBe(true);
  expect(payload.messages).toBeDefined();
  expect(payload.messages.length).toBeGreaterThan(0);
  expect(payload.messages[payload.messages.length - 1].content).toContain("Verify chat payload.");
});

// ── Quick model switcher (#52) ──────────────────────────────────────

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
  await expect(items.nth(0)).toContainText("Qwen3-VL-4B");
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
