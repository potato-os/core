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


