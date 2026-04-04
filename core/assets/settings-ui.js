"use strict";

import { appState, defaultSettings, settingsKey, DEFAULT_MODEL_VISION_SETTINGS } from "./state.js";
import { formatBytes, postJson } from "./utils.js";
import { formatModelStatusLabel } from "./status.js";
import { flushPendingNoticeDismissal } from "./platform-notify.js";

    let _platform = {};
    let _chat = {};

    export function registerSettingsPlatformCallbacks(callbacks) {
      _platform = callbacks;
    }

    export function registerSettingsChatCallbacks(callbacks) {
      _chat = callbacks;
    }

    export function detectSystemTheme() {
      try {
        if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
          if (window.matchMedia("(prefers-color-scheme: dark)").matches) {
            return "dark";
          }
        }
      } catch (_err) {
        // Fall through to light theme fallback.
      }
      return "light";
    }

    export function normalizeTheme(rawTheme, fallback = defaultSettings.theme) {
      if (rawTheme === "dark") return "dark";
      if (rawTheme === "light") return "light";
      return fallback;
    }

    export function loadSettings() {
      const raw = localStorage.getItem(settingsKey);
      if (!raw) {
        return { theme: detectSystemTheme() };
      }
      try {
        const parsedRaw = JSON.parse(raw);
        return {
          theme: normalizeTheme(parsedRaw?.theme, detectSystemTheme()),
        };
      } catch (_err) {
        return { theme: detectSystemTheme() };
      }
    }

    export function saveSettings(settings) {
      const theme = normalizeTheme(settings?.theme, detectSystemTheme());
      localStorage.setItem(settingsKey, JSON.stringify({ theme }));
    }

    function parseNumber(id, fallback) {
      const parsed = Number(document.getElementById(id).value);
      return Number.isFinite(parsed) ? parsed : fallback;
    }

    export function normalizeGenerationMode(rawMode) {
      return rawMode === "deterministic" ? "deterministic" : "random";
    }

    export function normalizeSeedValue(rawSeed, fallback = defaultSettings.seed) {
      const parsed = Number(rawSeed);
      if (!Number.isFinite(parsed)) return fallback;
      return Math.trunc(parsed);
    }

    export function updateSeedFieldState(generationMode) {
      const seedField = document.getElementById("seed");
      if (!seedField) return;
      seedField.disabled = generationMode !== "deterministic";
      seedField.title = seedField.disabled ? "Seed is only used in deterministic mode" : "";
    }

    export function resolveSeedForRequest(settings) {
      const mode = normalizeGenerationMode(settings?.generation_mode);
      if (mode !== "deterministic") {
        return null;
      }
      return normalizeSeedValue(settings?.seed, defaultSettings.seed);
    }

    export function normalizeChatSettings(rawSettings) {
      const chat = rawSettings && typeof rawSettings === "object" ? rawSettings : {};
      const generationMode = normalizeGenerationMode(chat.generation_mode);
      return {
        temperature: Number.isFinite(Number(chat.temperature)) ? Number(chat.temperature) : defaultSettings.temperature,
        top_p: Number.isFinite(Number(chat.top_p)) ? Number(chat.top_p) : defaultSettings.top_p,
        top_k: Number.isFinite(Number(chat.top_k)) ? Number(chat.top_k) : defaultSettings.top_k,
        repetition_penalty: Number.isFinite(Number(chat.repetition_penalty)) ? Number(chat.repetition_penalty) : defaultSettings.repetition_penalty,
        presence_penalty: Number.isFinite(Number(chat.presence_penalty)) ? Number(chat.presence_penalty) : defaultSettings.presence_penalty,
        max_tokens: Number.isFinite(Number(chat.max_tokens)) ? Number(chat.max_tokens) : defaultSettings.max_tokens,
        stream: true,
        generation_mode: generationMode,
        seed: normalizeSeedValue(chat.seed, defaultSettings.seed),
        system_prompt: String(chat.system_prompt || "").trim(),
      };
    }

    function normalizeVisionSettings(rawSettings) {
      const vision = rawSettings && typeof rawSettings === "object" ? rawSettings : {};
      return {
        enabled: Boolean(vision.enabled),
        projector_mode: String(vision.projector_mode || "default"),
        projector_filename: String(vision.projector_filename || ""),
      };
    }

    function normalizeProjectorStatus(rawProjector, visionSettings = {}) {
      const projector = rawProjector && typeof rawProjector === "object" ? rawProjector : {};
      const defaultCandidates = Array.isArray(projector.default_candidates)
        ? projector.default_candidates.map((item) => String(item || "").trim()).filter(Boolean)
        : [];
      const legacyDefaultFilename = String(projector.default_filename || "").trim();
      if (legacyDefaultFilename && !defaultCandidates.includes(legacyDefaultFilename)) {
        defaultCandidates.unshift(legacyDefaultFilename);
      }
      const resolvedFilename = String(
        projector.filename
        || projector.selected_filename
        || visionSettings.projector_filename
        || ""
      ).trim();
      return {
        present: projector.present === true || projector.available === true,
        filename: resolvedFilename,
        defaultFilename: defaultCandidates[0] || "",
        defaultCandidates,
      };
    }

    function resolveActiveRuntimeModel(statusPayload = appState.latestStatus) {
      const topModel = statusPayload?.model && typeof statusPayload.model === "object"
        ? statusPayload.model
        : null;
      if (typeof topModel?.capabilities?.vision === "boolean") {
        return topModel;
      }
      const models = getSettingsModels(statusPayload);
      const activeModelId = String(topModel?.active_model_id || "");
      if (activeModelId) {
        const exact = models.find((item) => String(item?.id || "") === activeModelId);
        if (exact) return exact;
      }
      const activeFilename = String(topModel?.filename || "").trim();
      if (activeFilename) {
        const exactFilename = models.find((item) => String(item?.filename || "").trim() === activeFilename);
        if (exactFilename) return exactFilename;
      }
      const active = models.find((item) => item?.is_active === true);
      return active || topModel || null;
    }

    export function activeRuntimeVisionCapability(statusPayload = appState.latestStatus) {
      const activeModel = resolveActiveRuntimeModel(statusPayload);
      const supportsVision = activeModel?.capabilities?.vision;
      return typeof supportsVision === "boolean" ? supportsVision : null;
    }

    function formatTextOnlyImageNotice(statusPayload = appState.latestStatus) {
      const activeModel = resolveActiveRuntimeModel(statusPayload);
      const modelName = String(activeModel?.filename || "The current model").trim();
      return `${modelName} is text-only. Switch to a vision-capable model in Settings to send images.`;
    }

    export function formatImageRejectedNotice(statusPayload = appState.latestStatus) {
      if (activeRuntimeVisionCapability(statusPayload) === false) {
        return formatTextOnlyImageNotice(statusPayload);
      }
      return "This model can't process images right now. Switch to a vision-capable model or configure its vision encoder in Settings.";
    }

    function setComposerVisionNotice(message) {
      const notice = document.getElementById("composerVisionNotice");
      if (!notice) return;
      const text = String(message || "").trim();
      notice.textContent = text;
      notice.hidden = text.length === 0;
    }

    export function showTextOnlyImageBlockedState(statusPayload = appState.latestStatus) {
      const notice = formatTextOnlyImageNotice(statusPayload);
      setComposerVisionNotice(notice);
      if (_chat.setComposerActivity) _chat.setComposerActivity(notice);
      if (_chat.setComposerStatusChip) _chat.setComposerStatusChip("Current model is text-only.", { phase: "image" });
      if (_chat.hideComposerStatusChip) _chat.hideComposerStatusChip();
      if (_chat.setCancelEnabled) _chat.setCancelEnabled(false);
      if (_chat.focusPromptInput) _chat.focusPromptInput();
    }

    export function renderComposerCapabilities(statusPayload = appState.latestStatus) {
      const attachBtn = document.getElementById("attachImageBtn");
      const clearBtn = document.getElementById("clearImageBtn");
      if (!attachBtn) return;
      const visionCapability = activeRuntimeVisionCapability(statusPayload);
      const explicitTextOnly = visionCapability === false;
      const blockedMessage = explicitTextOnly ? formatTextOnlyImageNotice(statusPayload) : "";
      setComposerVisionNotice(blockedMessage);
      attachBtn.disabled = appState.requestInFlight || explicitTextOnly;
      attachBtn.setAttribute("aria-disabled", attachBtn.disabled ? "true" : "false");
      attachBtn.setAttribute("title", explicitTextOnly ? blockedMessage : "Attach image");
      attachBtn.setAttribute("aria-label", explicitTextOnly ? blockedMessage : "Attach image");
      if (clearBtn) {
        clearBtn.disabled = appState.requestInFlight;
      }
      if (explicitTextOnly && appState.pendingImage) {
        if (_chat.clearPendingImage) _chat.clearPendingImage();
        if (_chat.setComposerActivity) _chat.setComposerActivity("Image removed.");
      }
    }

    export function getSettingsModels(statusPayload = appState.latestStatus) {
      return Array.isArray(statusPayload?.models) ? statusPayload.models : [];
    }

    export function resolveSelectedSettingsModel(statusPayload = appState.latestStatus) {
      const models = getSettingsModels(statusPayload);
      if (models.length === 0) return null;
      if (appState.selectedSettingsModelId) {
        const exact = models.find((item) => String(item?.id || "") === appState.selectedSettingsModelId);
        if (exact) return exact;
      }
      const activeModelId = String(statusPayload?.model?.active_model_id || "");
      const active = models.find((item) => String(item?.id || "") === activeModelId || item?.is_active === true);
      if (active) {
        appState.selectedSettingsModelId = String(active.id || "");
        return active;
      }
      appState.selectedSettingsModelId = String(models[0]?.id || "");
      return models[0];
    }

    export function getActiveChatSettings(statusPayload = appState.latestStatus) {
      const activeChat = statusPayload?.model?.settings?.chat;
      return normalizeChatSettings(activeChat);
    }

    export function collectSelectedModelSettings() {
      const selectedModel = resolveSelectedSettingsModel(appState.latestStatus);
      const supportsVision = Boolean(selectedModel?.capabilities?.vision);
      const generationMode = normalizeGenerationMode(document.getElementById("generationMode").value);
      const seed = normalizeSeedValue(document.getElementById("seed").value, defaultSettings.seed);
      const persistedStream = selectedModel?.settings?.chat?.stream !== false;
      return {
        chat: {
          temperature: parseNumber("temperature", defaultSettings.temperature),
          top_p: parseNumber("top_p", defaultSettings.top_p),
          top_k: parseNumber("top_k", defaultSettings.top_k),
          repetition_penalty: parseNumber("repetition_penalty", defaultSettings.repetition_penalty),
          presence_penalty: parseNumber("presence_penalty", defaultSettings.presence_penalty),
          max_tokens: parseNumber("max_tokens", defaultSettings.max_tokens),
          stream: persistedStream,
          generation_mode: generationMode,
          seed,
          system_prompt: document.getElementById("systemPrompt").value.trim(),
        },
        vision: supportsVision
          ? {
              enabled: Boolean(document.getElementById("visionEnabled")?.checked),
              projector_mode: "default",
              projector_filename: String(document.getElementById("downloadProjectorBtn")?.dataset?.projectorFilename || ""),
            }
          : (selectedModel?.settings?.vision || { enabled: false, projector_mode: "default", projector_filename: "" }),
      };
    }

    export function markModelSettingsDraftDirty() {
      const selectedModel = resolveSelectedSettingsModel(appState.latestStatus);
      appState.modelSettingsDraftDirty = true;
      appState.modelSettingsDraftModelId = String(selectedModel?.id || "");
      const statusEl = document.getElementById("modelSettingsStatus");
      const discardBtn = document.getElementById("discardModelSettingsBtn");
      if (statusEl) {
        statusEl.textContent = "Unsaved changes.";
      }
      if (discardBtn) {
        discardBtn.hidden = false;
        discardBtn.disabled = appState.modelSettingsSaveInFlight;
      }
    }

    export function clearModelSettingsDraftState() {
      appState.modelSettingsDraftDirty = false;
      appState.modelSettingsDraftModelId = "";
      const discardBtn = document.getElementById("discardModelSettingsBtn");
      if (discardBtn) {
        discardBtn.hidden = true;
        discardBtn.disabled = true;
      }
    }

    export function setModelUrlStatus(message) {
      const statusEl = document.getElementById("modelUrlStatus");
      if (statusEl) {
        statusEl.textContent = String(message || "");
      }
    }

    export function formatModelUrlStatus(reason, fallbackStatus) {
      const normalized = String(reason || "").trim().toLowerCase();
      if (normalized === "https_required") {
        return "Use an HTTPS model URL that ends with .gguf.";
      }
      if (normalized === "gguf_required") {
        return "Model URL must point to a .gguf file.";
      }
      if (normalized === "filename_missing") {
        return "Model URL must include a model filename.";
      }
      if (normalized === "already_exists") {
        return "That model URL is already registered.";
      }
      return `Could not add model URL (${reason || fallbackStatus}).`;
    }

    function isEditingModelSettingsField() {
      const active = document.activeElement;
      if (!(active instanceof HTMLElement)) return false;
      if (!active.id) return false;
      return [
        "systemPrompt",
        "seed",
        "temperature",
        "top_p",
        "top_k",
        "repetition_penalty",
        "presence_penalty",
        "max_tokens",
        "visionEnabled",
      ].includes(String(active.id));
    }

    function shouldPauseSelectedModelSettingsRender() {
      return appState.settingsModalOpen
        && appState.settingsWorkspaceTab === "model"
        && (appState.modelSettingsDraftDirty || isEditingModelSettingsField());
    }

    function modelSettingsFormHasUnsavedValues(chat, vision) {
      const systemPromptEl = document.getElementById("systemPrompt");
      const generationModeEl = document.getElementById("generationMode");
      const seedEl = document.getElementById("seed");
      const temperatureEl = document.getElementById("temperature");
      const topPEl = document.getElementById("top_p");
      const topKEl = document.getElementById("top_k");
      const repetitionPenaltyEl = document.getElementById("repetition_penalty");
      const presencePenaltyEl = document.getElementById("presence_penalty");
      const maxTokensEl = document.getElementById("max_tokens");
      const visionEnabledEl = document.getElementById("visionEnabled");
      if (
        !systemPromptEl || !generationModeEl || !seedEl || !temperatureEl
        || !topPEl || !topKEl || !repetitionPenaltyEl || !presencePenaltyEl || !maxTokensEl
      ) {
        return false;
      }
      return (
        String(systemPromptEl.value || "") !== String(chat.system_prompt || "")
        || String(generationModeEl.value || "") !== String(chat.generation_mode)
        || String(seedEl.value || "") !== String(chat.seed)
        || String(temperatureEl.value || "") !== String(chat.temperature)
        || String(topPEl.value || "") !== String(chat.top_p)
        || String(topKEl.value || "") !== String(chat.top_k)
        || String(repetitionPenaltyEl.value || "") !== String(chat.repetition_penalty)
        || String(presencePenaltyEl.value || "") !== String(chat.presence_penalty)
        || String(maxTokensEl.value || "") !== String(chat.max_tokens)
        || (visionEnabledEl && !visionEnabledEl.disabled ? Boolean(visionEnabledEl.checked) !== Boolean(vision.enabled) : false)
      );
    }

    export function selectedModelHasUnsavedChanges(statusPayload = appState.latestStatus) {
      const selectedModel = resolveSelectedSettingsModel(statusPayload);
      const selectedModelId = String(selectedModel?.id || "");
      if (!selectedModelId) return false;
      const chat = normalizeChatSettings(selectedModel?.settings?.chat);
      const vision = normalizeVisionSettings(selectedModel?.settings?.vision);
      return (
        (appState.modelSettingsDraftDirty && appState.modelSettingsDraftModelId === selectedModelId)
        || (
          appState.displayedSettingsModelId === selectedModelId
          && modelSettingsFormHasUnsavedValues(chat, vision)
        )
      );
    }

    export function blockModelSelectionChange() {
      const statusEl = document.getElementById("modelSettingsStatus");
      if (statusEl) {
        statusEl.textContent = "Save or discard changes before switching models.";
      }
    }

    export function discardSelectedModelSettings() {
      const selectedModel = resolveSelectedSettingsModel(appState.latestStatus);
      if (!selectedModel) return;
      clearModelSettingsDraftState();
      appState.displayedSettingsModelId = "";
      appState.modelSettingsStatusModelId = "";
      renderSelectedModelSettings(appState.latestStatus);
      const statusEl = document.getElementById("modelSettingsStatus");
      if (statusEl) {
        statusEl.textContent = "Changes discarded.";
      }
    }

    export function collectSettings() {
      return {
        ...getActiveChatSettings(),
        theme: document.documentElement.getAttribute("data-theme") || defaultSettings.theme,
      };
    }

    export function setSettingsModalOpen(open) {
      appState.settingsModalOpen = Boolean(open);
      const modal = document.getElementById("settingsModal");
      const backdrop = document.getElementById("settingsBackdrop");
      document.body.classList.toggle("settings-modal-open", appState.settingsModalOpen);
      if (modal) {
        modal.hidden = !appState.settingsModalOpen;
      }
      if (backdrop) {
        backdrop.hidden = !appState.settingsModalOpen;
      }
      if (appState.settingsModalOpen) {
        appState.settingsModalOpenedAtMs = performance.now();
        if (_platform.setSidebarOpen) _platform.setSidebarOpen(false);
      } else {
        closeLegacySettingsModal();
        flushPendingNoticeDismissal();
      }
    }

    export function setLegacySettingsModalOpen(open) {
      appState.legacySettingsModalOpen = Boolean(open);
      const modal = document.getElementById("legacySettingsModal");
      const backdrop = document.getElementById("legacySettingsBackdrop");
      document.body.classList.toggle("legacy-settings-modal-open", appState.legacySettingsModalOpen);
      if (modal) {
        modal.hidden = !appState.legacySettingsModalOpen;
      }
      if (backdrop) {
        backdrop.hidden = !appState.legacySettingsModalOpen;
      }
      if (appState.legacySettingsModalOpen) {
        if (_platform.setSidebarOpen) _platform.setSidebarOpen(false);
      } else {
        flushPendingNoticeDismissal();
      }
    }

    export function showSettingsWorkspaceTab(tabName) {
      appState.settingsWorkspaceTab = tabName === "yaml" ? "yaml" : "model";
      const modelPanel = document.getElementById("settingsModelWorkspace");
      const yamlPanel = document.getElementById("settingsYamlPanel");
      const modelTabBtn = document.getElementById("settingsWorkspaceTabModel");
      const yamlTabBtn = document.getElementById("settingsWorkspaceTabYaml");
      const isYaml = appState.settingsWorkspaceTab === "yaml";
      if (modelPanel) modelPanel.hidden = isYaml;
      if (yamlPanel) yamlPanel.hidden = !isYaml;
      if (modelTabBtn) {
        modelTabBtn.classList.toggle("active", !isYaml);
        modelTabBtn.setAttribute("aria-selected", !isYaml ? "true" : "false");
      }
      if (yamlTabBtn) {
        yamlTabBtn.classList.toggle("active", isYaml);
        yamlTabBtn.setAttribute("aria-selected", isYaml ? "true" : "false");
      }
      if (isYaml && !appState.settingsYamlLoaded) {
        loadSettingsDocument();
      }
    }

    export function openSettingsModal() {
      showSettingsWorkspaceTab(appState.settingsWorkspaceTab);
      renderSettingsWorkspace(appState.latestStatus);
      setSettingsModalOpen(true);
    }

    export function closeSettingsModal() {
      setSettingsModalOpen(false);
    }

    export function openLegacySettingsModal() {
      setLegacySettingsModalOpen(true);
    }

    export function closeLegacySettingsModal() {
      setLegacySettingsModalOpen(false);
    }

    export function syncSegmentedControl(targetId) {
      const currentValue = String(document.getElementById(targetId)?.value || "");
      document.querySelectorAll(`.settings-segmented[data-target="${targetId}"] .settings-segment-btn`).forEach((button) => {
        const active = String(button.dataset.value || "") === currentValue;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", active ? "true" : "false");
      });
    }

    export function setSegmentedControlValue(targetId, value) {
      const input = document.getElementById(targetId);
      if (!input) return;
      input.value = String(value || "");
      syncSegmentedControl(targetId);
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }

    export function renderSelectedModelSettings(statusPayload) {
      const selectedModel = resolveSelectedSettingsModel(statusPayload);
      const selectedModelId = String(selectedModel?.id || "");
      const modelNameField = document.getElementById("modelName");
      const modelIdentityMeta = document.getElementById("modelIdentityMeta");
      const capabilitiesChips = document.getElementById("modelCapabilitiesChips");
      const capabilitiesText = document.getElementById("modelCapabilitiesText");
      const statusEl = document.getElementById("modelSettingsStatus");
      const saveBtn = document.getElementById("saveModelSettingsBtn");
      const discardBtn = document.getElementById("discardModelSettingsBtn");
      const projectorBtn = document.getElementById("downloadProjectorBtn");
      const projectorStatus = document.getElementById("projectorStatusText");
      const visionSection = document.getElementById("settingsVisionSection");
      const visionEnabled = document.getElementById("visionEnabled");
      if (!selectedModel) {
        clearModelSettingsDraftState();
        appState.displayedSettingsModelId = "";
        if (modelNameField) modelNameField.textContent = "No model selected";
        if (modelIdentityMeta) modelIdentityMeta.replaceChildren();
        if (capabilitiesText) capabilitiesText.textContent = "Register or upload a model to configure it.";
        if (capabilitiesChips) capabilitiesChips.replaceChildren();
        if (statusEl) statusEl.textContent = "No models registered yet.";
        if (saveBtn) saveBtn.disabled = true;
        if (discardBtn) {
          discardBtn.hidden = true;
          discardBtn.disabled = true;
        }
        if (projectorBtn) projectorBtn.disabled = true;
        if (visionSection) visionSection.hidden = true;
        return;
      }

      const chat = normalizeChatSettings(selectedModel?.settings?.chat);
      const vision = normalizeVisionSettings(selectedModel?.settings?.vision);
      const supportsVision = Boolean(selectedModel?.capabilities?.vision);
      const projector = normalizeProjectorStatus(selectedModel?.projector, vision);
      const preserveDraft = (
        (appState.modelSettingsDraftDirty && appState.modelSettingsDraftModelId === selectedModelId)
        || (
          appState.displayedSettingsModelId === selectedModelId
          && modelSettingsFormHasUnsavedValues(chat, vision)
        )
        || isEditingModelSettingsField()
      );

      if (modelNameField) {
        modelNameField.textContent = String(selectedModel?.filename || "");
      }
      if (modelIdentityMeta) {
        modelIdentityMeta.replaceChildren();
        const metaBits = [];
        if (selectedModel?.storage?.location) metaBits.push(`Stored on ${String(selectedModel.storage.location).toUpperCase()}`);
        if (selectedModel?.source_type === "url") metaBits.push("Added from URL");
        else if (selectedModel?.source_type === "upload") metaBits.push("Uploaded locally");
        if (selectedModel?.status === "failed" && selectedModel?.error) {
          metaBits.push(String(selectedModel.error));
        }
        for (const bit of metaBits) {
          const item = document.createElement("span");
          item.className = "selected-model-meta-item";
          item.textContent = String(bit);
          modelIdentityMeta.appendChild(item);
        }
      }
      if (capabilitiesText) {
        const bits = [
          selectedModel?.is_active ? "Active" : "Inactive",
          selectedModel?.status ? `Status: ${formatModelStatusLabel(selectedModel.status)}` : "",
          supportsVision ? "Vision capable" : "Text only",
        ].filter(Boolean);
        capabilitiesText.textContent = bits.join(" · ");
      }
      if (capabilitiesChips) {
        const chipSpecs = [
          {
            kind: "active",
            text: selectedModel?.is_active ? "Active" : "Inactive",
          },
          {
            kind: "status",
            text: formatModelStatusLabel(selectedModel?.status),
          },
          {
            kind: "vision",
            text: supportsVision ? "Vision" : "Text only",
          },
        ];
        capabilitiesChips.replaceChildren(...chipSpecs.map((chip) => {
          const node = document.createElement("span");
          node.className = "settings-chip";
          node.dataset.kind = String(chip.kind || "");
          node.textContent = String(chip.text || "");
          return node;
        }));
      }
      if (statusEl) {
        if (preserveDraft) {
          statusEl.textContent = "Unsaved changes.";
        } else {
          const currentText = String(statusEl.textContent || "");
          const keepRecentSuccess = (
            appState.modelSettingsStatusModelId === selectedModelId
            && /updated|saved/i.test(currentText)
          );
          if (!keepRecentSuccess) {
            statusEl.textContent = selectedModel?.status === "failed"
              ? `Model state: ${String(selectedModel?.error || "failed")}`
              : "Update the selected model profile and save to persist it.";
          }
        }
      }

      if (!preserveDraft) {
        document.getElementById("systemPrompt").value = chat.system_prompt;
        document.getElementById("generationMode").value = chat.generation_mode;
        document.getElementById("seed").value = String(chat.seed);
        document.getElementById("temperature").value = String(chat.temperature);
        document.getElementById("top_p").value = String(chat.top_p);
        document.getElementById("top_k").value = String(chat.top_k);
        document.getElementById("repetition_penalty").value = String(chat.repetition_penalty);
        document.getElementById("presence_penalty").value = String(chat.presence_penalty);
        document.getElementById("max_tokens").value = String(chat.max_tokens);
        syncSegmentedControl("generationMode");
        updateSeedFieldState(chat.generation_mode);
        appState.displayedSettingsModelId = selectedModelId;
      }

      if (visionSection) {
        visionSection.hidden = !supportsVision;
      }
      if (visionEnabled) {
        visionEnabled.checked = supportsVision ? vision.enabled : false;
        visionEnabled.disabled = !supportsVision;
      }
      if (projectorStatus) {
        if (!supportsVision) {
          projectorStatus.textContent = "This model does not use a vision encoder.";
        } else if (projector.present) {
          projectorStatus.textContent = `Vision encoder ready: ${projector.filename || projector.defaultFilename || "available"}`;
        } else {
          projectorStatus.textContent = `No encoder installed. Default: ${projector.defaultFilename || "unknown"}`;
        }
      }
      if (projectorBtn) {
        projectorBtn.hidden = !supportsVision;
        projectorBtn.disabled = !supportsVision || appState.projectorDownloadInFlight;
        projectorBtn.dataset.modelId = String(selectedModel?.id || "");
        projectorBtn.dataset.projectorFilename = supportsVision
          ? String(projector.filename || vision.projector_filename || "")
          : "";
        projectorBtn.textContent = projector.present ? "Re-download vision encoder" : "Download vision encoder";
      }
      if (saveBtn) {
        saveBtn.disabled = appState.modelSettingsSaveInFlight;
      }
      if (discardBtn) {
        const hasUnsavedChanges = selectedModelHasUnsavedChanges(statusPayload);
        discardBtn.hidden = !hasUnsavedChanges;
        discardBtn.disabled = appState.modelSettingsSaveInFlight || !hasUnsavedChanges;
      }
    }

    export function renderModelsList(statusPayload) {
      const container = document.getElementById("modelsList");
      if (!container) return;
      const models = Array.isArray(statusPayload?.models) ? statusPayload.models : [];
      const selectedModel = resolveSelectedSettingsModel(statusPayload);
      container.replaceChildren();
      if (models.length === 0) {
        const empty = document.createElement("div");
        empty.className = "runtime-compact";
        empty.textContent = "No models registered yet.";
        container.appendChild(empty);
        return;
      }

      for (const model of models) {
        const row = document.createElement("div");
        row.className = "model-row";
        row.dataset.modelId = String(model?.id || "");
        if (String(model?.id || "") === String(selectedModel?.id || "")) {
          row.classList.add("selected");
        }

        const head = document.createElement("div");
        head.className = "model-row-head";
        const name = document.createElement("span");
        name.className = "model-row-name";
        name.textContent = String(model?.filename || "unknown.gguf");
        const status = document.createElement("span");
        status.className = "model-status-pill";
        status.textContent = model?.status === "failed" && model?.error === "insufficient_storage"
          ? "Insufficient storage"
          : formatModelStatusLabel(model?.status);
        head.appendChild(name);
        head.appendChild(status);

        const meta = document.createElement("div");
        meta.className = "model-row-meta";
        const metaBits = [];
        if (model?.is_active === true) metaBits.push("Active");
        if (model?.capabilities?.vision) metaBits.push("Vision");
        if (String(model?.source_type || "") === "url") metaBits.push("URL");
        for (const bit of metaBits) {
          const chip = document.createElement("span");
          chip.className = "model-mini-chip";
          chip.textContent = String(bit);
          meta.appendChild(chip);
        }

        const actions = document.createElement("div");
        actions.className = "model-row-actions";
        if (model?.status === "downloading") {
          const cancelBtn = document.createElement("button");
          cancelBtn.type = "button";
          cancelBtn.className = "ghost-btn";
          cancelBtn.dataset.action = "cancel-download";
          cancelBtn.textContent = "Stop download";
          cancelBtn.title = "Stop the active download for this model";
          actions.appendChild(cancelBtn);
        } else if (model?.status !== "ready" && model?.source_type === "url") {
          const downloadBtn = document.createElement("button");
          downloadBtn.type = "button";
          downloadBtn.className = "ghost-btn";
          downloadBtn.dataset.action = "download";
          downloadBtn.textContent = model?.status === "failed" ? "Resume download" : "Download";
          actions.appendChild(downloadBtn);
        }
        if (model?.is_active !== true && model?.status === "ready") {
          const activeBtn = document.createElement("button");
          activeBtn.type = "button";
          activeBtn.className = "ghost-btn";
          activeBtn.dataset.action = "activate";
          activeBtn.textContent = "Set active";
          actions.appendChild(activeBtn);
        }
        if (String(model?.id || "").length > 0) {
          const deleteBtn = document.createElement("button");
          deleteBtn.type = "button";
          deleteBtn.className = "ghost-btn danger-btn";
          deleteBtn.dataset.action = "delete";
          deleteBtn.textContent = model?.status === "downloading" ? "Cancel + delete" : "Delete model";
          actions.appendChild(deleteBtn);
        }
        if (model?.is_active === true) {
          const activeLabel = document.createElement("span");
          activeLabel.className = "runtime-compact";
          activeLabel.textContent = "Active model";
          actions.appendChild(activeLabel);
        }
        if (model?.status === "downloading") {
          const progress = document.createElement("span");
          progress.className = "runtime-compact";
          progress.textContent = `Downloading ${Number(model?.percent || 0)}% (${formatBytes(model?.bytes_downloaded)} / ${formatBytes(model?.bytes_total)})`;
          actions.appendChild(progress);
        } else if (model?.status === "failed" && Number(model?.bytes_total || 0) > 0) {
          const progress = document.createElement("span");
          progress.className = "runtime-compact";
          progress.textContent = `Failed at ${formatBytes(model?.bytes_downloaded)} / ${formatBytes(model?.bytes_total)}`;
          actions.appendChild(progress);
        }
        row.appendChild(head);
        if (meta.childElementCount > 0) {
          row.appendChild(meta);
        }
        row.appendChild(actions);
        container.appendChild(row);
      }
    }

    export function renderSettingsWorkspace(statusPayload) {
      renderModelsList(statusPayload);
      if (!shouldPauseSelectedModelSettingsRender()) {
        renderSelectedModelSettings(statusPayload);
      }
      showSettingsWorkspaceTab(appState.settingsWorkspaceTab);
    }

    export async function loadSettingsDocument() {
      if (appState.settingsYamlRequestInFlight) return;
      appState.settingsYamlRequestInFlight = true;
      const statusEl = document.getElementById("settingsYamlStatus");
      if (statusEl) statusEl.textContent = "Loading YAML...";
      try {
        const res = await fetch("/internal/settings-document", { cache: "no-store" });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          if (statusEl) statusEl.textContent = `Could not load YAML (${body?.reason || res.status}).`;
          return;
        }
        document.getElementById("settingsYamlInput").value = String(body?.document || "");
        appState.settingsYamlLoaded = true;
        if (statusEl) statusEl.textContent = "YAML loaded.";
      } catch (err) {
        if (statusEl) statusEl.textContent = `Could not load YAML: ${err}`;
      } finally {
        appState.settingsYamlRequestInFlight = false;
      }
    }

    export async function applySettingsDocument() {
      if (appState.settingsYamlRequestInFlight) return;
      appState.settingsYamlRequestInFlight = true;
      const statusEl = document.getElementById("settingsYamlStatus");
      if (statusEl) statusEl.textContent = "Applying YAML...";
      try {
        const documentText = String(document.getElementById("settingsYamlInput").value || "");
        const { res, body } = await postJson("/internal/settings-document", { document: documentText });
        if (!res.ok) {
          if (statusEl) statusEl.textContent = `Could not apply YAML (${body?.reason || res.status}).`;
          return;
        }
        clearModelSettingsDraftState();
        appState.displayedSettingsModelId = "";
        appState.modelSettingsStatusModelId = "";
        if (body?.active_model_id) {
          appState.selectedSettingsModelId = String(body.active_model_id);
        }
        if (statusEl) statusEl.textContent = "YAML applied.";
        appState.settingsYamlLoaded = true;
        if (_platform.pollStatus) await _platform.pollStatus();
      } catch (err) {
        if (statusEl) statusEl.textContent = `Could not apply YAML: ${err}`;
      } finally {
        appState.settingsYamlRequestInFlight = false;
      }
    }

    export async function saveSelectedModelSettings() {
      const selectedModel = resolveSelectedSettingsModel(appState.latestStatus);
      if (!selectedModel || appState.modelSettingsSaveInFlight) return;
      appState.modelSettingsSaveInFlight = true;
      const statusEl = document.getElementById("modelSettingsStatus");
      const saveBtn = document.getElementById("saveModelSettingsBtn");
      const discardBtn = document.getElementById("discardModelSettingsBtn");
      if (saveBtn) saveBtn.disabled = true;
      if (discardBtn) discardBtn.disabled = true;
      if (statusEl) statusEl.textContent = "Saving model settings...";
      try {
        const settings = collectSelectedModelSettings();
        const { res, body } = await postJson("/internal/models/settings", {
          model_id: selectedModel.id,
          settings,
        });
        if (!res.ok) {
          if (statusEl) statusEl.textContent = `Could not save model settings (${body?.reason || res.status}).`;
          return;
        }
        clearModelSettingsDraftState();
        appState.displayedSettingsModelId = "";
        appState.modelSettingsStatusModelId = String(selectedModel?.id || "");
        if (statusEl) statusEl.textContent = "Model settings updated.";
        if (_platform.pollStatus) await _platform.pollStatus();
      } catch (err) {
        if (statusEl) statusEl.textContent = `Could not save model settings: ${err}`;
      } finally {
        appState.modelSettingsSaveInFlight = false;
        if (saveBtn) saveBtn.disabled = false;
        if (discardBtn) discardBtn.disabled = false;
      }
    }

    export async function downloadProjectorForSelectedModel() {
      const selectedModel = resolveSelectedSettingsModel(appState.latestStatus);
      if (!selectedModel || appState.projectorDownloadInFlight) return;
      appState.projectorDownloadInFlight = true;
      const statusEl = document.getElementById("projectorStatusText");
      const button = document.getElementById("downloadProjectorBtn");
      if (button) button.disabled = true;
      if (statusEl) statusEl.textContent = "Downloading vision encoder...";
      try {
        const { res, body } = await postJson("/internal/models/download-projector", { model_id: selectedModel.id });
        if (!res.ok) {
          if (statusEl) statusEl.textContent = `Could not download encoder (${body?.reason || res.status}).`;
          return;
        }
        if (button) {
          button.dataset.projectorFilename = String(body?.projector_filename || "");
        }
        if (statusEl) statusEl.textContent = `Vision encoder ready: ${body?.projector_filename || "downloaded"}`;
        if (_platform.pollStatus) await _platform.pollStatus();
      } catch (err) {
        if (statusEl) statusEl.textContent = `Could not download encoder: ${err}`;
      } finally {
        appState.projectorDownloadInFlight = false;
        if (button) button.disabled = false;
      }
    }

    export function bindSettingsModal() {
      document.getElementById("settingsOpenBtn").addEventListener("click", openSettingsModal);
      document.getElementById("settingsCloseBtn").addEventListener("click", closeSettingsModal);
      document.getElementById("settingsBackdrop").addEventListener("click", closeSettingsModal);
      document.getElementById("settingsModal").addEventListener("click", (event) => {
        if (event.target === event.currentTarget) {
          closeSettingsModal();
        }
      });
      document.getElementById("settingsModal").addEventListener("input", (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;
        if (
          target.closest("#systemPrompt, #seed, #temperature, #top_p, #top_k, #repetition_penalty, #presence_penalty, #max_tokens, #visionEnabled")
        ) {
          markModelSettingsDraftDirty();
        }
      });
      document.getElementById("settingsModal").addEventListener("change", (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;
        if (
          target.closest("#systemPrompt, #seed, #temperature, #top_p, #top_k, #repetition_penalty, #presence_penalty, #max_tokens, #visionEnabled")
        ) {
          markModelSettingsDraftDirty();
        }
      });
      document.getElementById("settingsModal").addEventListener("keydown", (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;
        if (
          target.closest("#systemPrompt, #seed, #temperature, #top_p, #top_k, #repetition_penalty, #presence_penalty, #max_tokens")
        ) {
          markModelSettingsDraftDirty();
        }
      });
      document.getElementById("settingsAdvancedBtn").addEventListener("click", openLegacySettingsModal);
      document.getElementById("settingsWorkspaceTabModel").addEventListener("click", () => {
        showSettingsWorkspaceTab("model");
      });
      document.getElementById("settingsWorkspaceTabYaml").addEventListener("click", () => {
        showSettingsWorkspaceTab("yaml");
      });
      document.getElementById("saveModelSettingsBtn").addEventListener("click", saveSelectedModelSettings);
      document.getElementById("discardModelSettingsBtn").addEventListener("click", discardSelectedModelSettings);
      document.getElementById("downloadProjectorBtn").addEventListener("click", downloadProjectorForSelectedModel);
      document.getElementById("settingsYamlReloadBtn").addEventListener("click", loadSettingsDocument);
      document.getElementById("settingsYamlApplyBtn").addEventListener("click", applySettingsDocument);
      document.querySelectorAll(".settings-segment-btn").forEach((button) => {
        button.addEventListener("click", () => {
          markModelSettingsDraftDirty();
          setSegmentedControlValue(String(button.dataset.target || ""), String(button.dataset.value || ""));
        });
      });
      document.getElementById("generationMode").addEventListener("change", (event) => {
        updateSeedFieldState(normalizeGenerationMode(event.target?.value));
      });
      syncSegmentedControl("generationMode");
      document.getElementById("legacySettingsCloseBtn").addEventListener("click", closeLegacySettingsModal);
      document.getElementById("legacySettingsBackdrop").addEventListener("click", closeLegacySettingsModal);
      document.getElementById("legacySettingsModal").addEventListener("click", (event) => {
        if (event.target === event.currentTarget) {
          closeLegacySettingsModal();
        }
      });
    }
