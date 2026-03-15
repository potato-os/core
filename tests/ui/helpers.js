const { expect } = require("@playwright/test");

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
      filename: "Qwen3-VL-4B-Instruct-Q4_K_M.gguf",
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
        filename: "Qwen3-VL-4B-Instruct-Q4_K_M.gguf",
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
  };
  return { ...base, ...overrides };
}

function makeMultiModelStatusPayload({ activeId = "default", extraModels = [] } = {}) {
  const defaultModel = {
    id: "default",
    filename: "Qwen3-VL-4B-Instruct-Q4_K_M.gguf",
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
