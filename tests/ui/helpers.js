const { expect } = require("@playwright/test");

async function waitForStatusApplied(page) {
  await expect(page.locator("#statusText")).not.toHaveText("Checking status...", { timeout: 10000 });
  // Wait for the active app to finish mounting
  await expect(page.locator("#composerForm")).toBeVisible({ timeout: 5000 });
}

async function waitUntilReady(page) {
  await page.goto("/");
  await expect(page.locator("#statusText")).toContainText("State: READY");
  // Wait for the active app to load (chat injects #composerForm into #appContainer)
  await expect(page.locator("#composerForm")).toBeVisible({ timeout: 5000 });
  // Wait for chat to fully init (exposes window.appendMessage for tests)
  await page.waitForFunction(() => typeof window.appendMessage === "function", { timeout: 5000 });
}

async function openSettingsModal(page) {
  await page.locator("#settingsOpenBtn").click();
  await expect(page.locator("#settingsModal")).toBeVisible();
}

async function closeSettingsModal(page) {
  await page.locator("#settingsCloseBtn").click();
  await expect(page.locator("#settingsModal")).toBeHidden();
}

async function openAdvancedSettingsModal(page) {
  await page.locator("#settingsAdvancedBtn").click();
  await expect(page.locator("#legacySettingsModal")).toBeVisible();
}

async function closeAdvancedSettingsModal(page) {
  await page.locator("#legacySettingsCloseBtn").click();
  await expect(page.locator("#legacySettingsModal")).toBeHidden();
}

async function saveModelSettings(page) {
  await page.locator("#saveModelSettingsBtn").click();
  await expect(page.locator("#modelSettingsStatus")).toContainText(/saved|updated/i);
}

async function chooseModelSegment(page, fieldId, value) {
  await page.locator(`.settings-segmented[data-target="${fieldId}"] .settings-segment-btn[data-value="${value}"]`).click();
  await expect(page.locator(`#${fieldId}`)).toHaveValue(String(value));
}

async function fulfillStreamingChat(route, { content = "", timings = null, finishReason = "stop" } = {}) {
  const events = [];
  if (content) {
    events.push({
      choices: [{ delta: { content } }],
    });
  }
  events.push({
    choices: [{ delta: {}, finish_reason: finishReason }],
    ...(timings ? { timings } : {}),
  });
  await route.fulfill({
    status: 200,
    contentType: "text/event-stream",
    body: `${events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("")}data: [DONE]\n\n`,
  });
}

function makeStatusPayload(overrides = {}) {
  const base = {
    state: "READY",
    model_present: true,
    model: {
      filename: "Qwen3.5-2B-Q4_K_M.gguf",
      active_model_id: "default",
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
          cache_prompt: true,
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
    },
    models: [
      {
        id: "default",
        filename: "Qwen3.5-2B-Q4_K_M.gguf",
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
            max_tokens: 16384,
            cache_prompt: true,
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
    ],
    upload: { active: false, model_id: null, bytes_total: 0, bytes_received: 0, percent: 0, error: null },
    model_loading: { active: false, progress_percent: null, resident_bytes: null, model_size_bytes: null },
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
    update: {
      available: false,
      current_version: "0.4.0",
      latest_version: null,
      release_notes: null,
      checked_at_unix: null,
      state: "idle",
      deferred: false,
      defer_reason: null,
      progress: { phase: null, percent: 0, error: null },
      just_updated_to: null,
      just_updated_release_notes: null,
    },
    llama_runtime: {
      current: { family: "ik_llama", llama_cpp_commit: "abc12345", profile: "pi5-opt", has_server_binary: true, runtime_type: "llama_server" },
      available_runtimes: [
        { family: "ik_llama", commit: "abc12345", is_active: true, compatible: true },
        { family: "llama_cpp", commit: "def67890", is_active: false, compatible: true },
        { family: "litert", runtime_type: "litert_adapter", is_active: false, compatible: true },
      ],
      switch: { active: false, target_family: null, error: null },
      memory_loading: { mode: "auto", label: "Automatic", no_mmap_env: "0" },
      large_model_override: { enabled: false },
    },
  };
  return { ...base, ...overrides };
}

function makeMultiModelStatusPayload({ activeId = "default", extraModels = [] } = {}) {
  const defaultModel = {
    id: "default",
    filename: "Qwen3.5-2B-Q4_K_M.gguf",
    source_url: null,
    source_type: "local_file",
    status: "ready",
    is_active: activeId === "default",
    settings: {
      chat: { system_prompt: "", stream: true, generation_mode: "random", seed: 42, temperature: 0.7, top_p: 0.8, top_k: 20, repetition_penalty: 1.0, presence_penalty: 1.5, max_tokens: 16384, cache_prompt: true },
      vision: { enabled: true, projector_mode: "default", projector_filename: "" },
    },
    capabilities: { vision: true },
    projector: { present: false, filename: null, default_candidates: ["mmproj-F16.gguf"] },
    bytes_total: 0, bytes_downloaded: 0, percent: 0, error: null,
  };
  const secondModel = {
    id: "second-model",
    filename: "Qwen3-Coder-30B-Q3_K_M.gguf",
    source_url: null,
    source_type: "local_file",
    status: "ready",
    is_active: activeId === "second-model",
    settings: {
      chat: { system_prompt: "", stream: true, generation_mode: "random", seed: 42, temperature: 0.7, top_p: 0.8, top_k: 20, repetition_penalty: 1.0, presence_penalty: 1.5, max_tokens: 16384, cache_prompt: true },
      vision: { enabled: false, projector_mode: "default", projector_filename: "" },
    },
    capabilities: { vision: false },
    projector: { present: false, filename: null, default_candidates: [] },
    bytes_total: 0, bytes_downloaded: 0, percent: 0, error: null,
  };
  const models = [defaultModel, secondModel, ...extraModels];
  models.forEach((m) => { m.is_active = m.id === activeId; });
  const activeModel = models.find((m) => m.id === activeId) || defaultModel;
  return makeStatusPayload({
    model: {
      filename: activeModel.filename,
      active_model_id: activeId,
      settings: activeModel.settings,
      capabilities: activeModel.capabilities,
      projector: activeModel.projector,
    },
    models,
  });
}

async function sendAndWaitForReply(page, text) {
  await page.locator("#userPrompt").fill(text);
  await page.locator("#userPrompt").press("Enter");
  await expect(page.locator("#sendBtn")).toHaveText("Send", { timeout: 5000 });
}

module.exports = {
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
};
