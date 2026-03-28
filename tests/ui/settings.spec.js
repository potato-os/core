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


test("seed mode defaults to random, toggles deterministic, persists, and controls request payload", async ({ page }) => {
  await page.route("**/v1/chat/completions", (route) => fulfillStreamingChat(route));
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
  await page.route("**/v1/chat/completions", (route) => fulfillStreamingChat(route));
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


test("model-first settings save per model, yaml can be applied, and projector download is exposed for vision models", async ({ page }) => {
  page.on("dialog", (dialog) => dialog.accept());
  let models = [
    {
      id: "default",
      filename: "Qwen3.5-2B-Q4_K_M.gguf",
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
      filename: models.find((m) => m.id === activeModelId)?.filename || "Qwen3.5-2B-Q4_K_M.gguf",
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

  await waitUntilReady(page);
  await openSettingsModal(page);

  await expect(page.locator("#settingsModelWorkspace")).toBeVisible();
  await expect(page.locator("#settingsWorkspaceTabYaml")).toBeVisible();
  await expect(page.locator("#settingsAdvancedBtn")).toBeVisible();
  await expect(page.locator("#settingsAdvancedBtn")).toBeEnabled();
  await expect(page.locator("#settingsModal #downloadCountdownEnabled")).toHaveCount(0);
  await expect(page.locator("#purgeModelsBtn")).toBeHidden();

  await page.locator('#modelsList .model-row[data-model-id="default"]').click();
  await expect(page.locator("#modelName")).toHaveText(/Qwen3.5-2B-Q4_K_M.gguf/);
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
      filename: "Qwen3.5-2B-Q4_K_M.gguf",
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
      filename: models.find((m) => m.id === activeModelId)?.filename || "Qwen3.5-2B-Q4_K_M.gguf",
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

  await waitUntilReady(page);
  await openSettingsModal(page);

  await page.locator('#modelsList .model-row[data-model-id="default"]').click();
  await expect(page.locator("#modelName")).toHaveText(/Qwen3.5-2B-Q4_K_M.gguf/);
  await page.locator("#systemPrompt").fill("Unsaved default draft");

  await page.locator('#modelsList .model-row[data-model-id="alt-model"]').click();
  await expect(page.locator("#modelName")).toHaveText(/Qwen3.5-2B-Q4_K_M.gguf/);
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

  await waitUntilReady(page);
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
        model: { filename: "Qwen3.5-2B-Q4_K_M.gguf", active_model_id: "default" },
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

  await waitUntilReady(page);
  await openSettingsModal(page);

  await page.locator("#modelUrlInput").fill("http://example.com/bad-model.gguf");
  await page.locator("#registerModelBtn").click();

  await expect(page.locator("#modelUrlStatus")).toContainText(/https/i);
  await expect(page.locator("#modelUrlInput")).toHaveValue("http://example.com/bad-model.gguf");
});

