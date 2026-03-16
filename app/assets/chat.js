"use strict";

import { appState, defaultSettings, settingsKey, PREFILL_METRICS_KEY, PREFILL_PROGRESS_CAP, PREFILL_PROGRESS_TAIL_START, PREFILL_PROGRESS_FLOOR, PREFILL_TICK_MS, PREFILL_FINISH_DURATION_MS, PREFILL_FINISH_TICK_MS, PREFILL_FINISH_HOLD_MS, STATUS_CHIP_MIN_VISIBLE_MS, STATUS_POLL_TIMEOUT_MS, RUNTIME_RECONNECT_INTERVAL_MS, RUNTIME_RECONNECT_TIMEOUT_MS, RUNTIME_RECONNECT_MAX_ATTEMPTS, IMAGE_CANCEL_RECOVERY_DELAY_MS, IMAGE_CANCEL_RESTART_DELAY_MS, SESSIONS_DB_NAME, SESSIONS_DB_VERSION, SESSIONS_STORE, ACTIVE_SESSION_KEY, SESSION_TITLE_MAX_LENGTH, SESSION_LIST_MAX_VISIBLE, IMAGE_SAFE_MAX_BYTES, IMAGE_MAX_DIMENSION, IMAGE_MAX_PIXEL_COUNT, CPU_CLOCK_MAX_HZ_PI5, GPU_CLOCK_MAX_HZ_PI5, RUNTIME_METRIC_SEVERITY_CLASSES, DEFAULT_MODEL_VISION_SETTINGS } from "./state.js";
import { formatBytes, formatPercent, formatClockMHz, normalizePercent, percentFromRatio, runtimeMetricSeverityClass, applyRuntimeMetricSeverity, formatCountdownSeconds, estimateDataUrlBytes, postJson } from "./utils.js";
import { registerAppendMessage, saveActiveSession, clearChatState, startNewChat, deleteSession, loadSessionIntoView, initSessionManager, renderSessionList } from "./session-manager.js";
import { populateModelSwitcher, openModelSwitcher, closeModelSwitcher, toggleModelSwitcher } from "./model-switcher.js";
import { isLocalModelConnected, updateLlamaIndicator, findResumableFailedModel, renderDownloadPrompt, renderStatusActions, renderCompatibilityWarnings, formatSidebarStatusDetail, formatModelStatusLabel } from "./status.js";
import { setRuntimeDetailsExpanded, renderSystemRuntime, renderLlamaRuntimeStatus, renderUploadState, setModelUploadStatus, setLlamaRuntimeSwitchStatus, setLlamaRuntimeSwitchButtonState, setLlamaMemoryLoadingStatus, setLlamaMemoryLoadingButtonState, setLargeModelOverrideStatus, setLargeModelOverrideButtonState, setPowerCalibrationStatus, setPowerCalibrationButtonsState, setPowerCalibrationLiveStatus } from "./runtime-ui.js";
import { registerOpenEditMessageModal, getMessagesBox, isMessagesPinned, setMessagesPinnedState, hasActiveMessageSelection, handleMessagesChanged, appendMessage, updateMessage, setMessageProcessingState, setMessageMeta, setMessageActionsVisible, removeMessage } from "./messages.js";

    // ── Session manager — extracted to session-manager.js ──────────────

    function detectSystemTheme() {
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

    function normalizeTheme(rawTheme, fallback = defaultSettings.theme) {
      if (rawTheme === "dark") return "dark";
      if (rawTheme === "light") return "light";
      return fallback;
    }

    function loadSettings() {
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

    function saveSettings(settings) {
      const theme = normalizeTheme(settings?.theme, detectSystemTheme());
      localStorage.setItem(settingsKey, JSON.stringify({ theme }));
    }

    function parseNumber(id, fallback) {
      const parsed = Number(document.getElementById(id).value);
      return Number.isFinite(parsed) ? parsed : fallback;
    }

    function normalizeGenerationMode(rawMode) {
      return rawMode === "deterministic" ? "deterministic" : "random";
    }

    function normalizeSeedValue(rawSeed, fallback = defaultSettings.seed) {
      const parsed = Number(rawSeed);
      if (!Number.isFinite(parsed)) return fallback;
      return Math.trunc(parsed);
    }

    function updateSeedFieldState(generationMode) {
      const seedField = document.getElementById("seed");
      if (!seedField) return;
      seedField.disabled = generationMode !== "deterministic";
      seedField.title = seedField.disabled ? "Seed is only used in deterministic mode" : "";
    }

    function resolveSeedForRequest(settings) {
      const mode = normalizeGenerationMode(settings?.generation_mode);
      if (mode !== "deterministic") {
        return null;
      }
      return normalizeSeedValue(settings?.seed, defaultSettings.seed);
    }

    // formatBytes, formatPercent, formatClockMHz, normalizePercent, percentFromRatio,
    // runtimeMetricSeverityClass, applyRuntimeMetricSeverity, formatCountdownSeconds,
    // estimateDataUrlBytes — extracted to utils.js

    function dataUrlToImage(dataUrl) {
      return new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => reject(new Error("image_decode_failed"));
        img.src = dataUrl;
      });
    }

    async function inspectImageDataUrl(dataUrl) {
      const image = await dataUrlToImage(dataUrl);
      const width = Math.max(1, Number(image.naturalWidth) || 1);
      const height = Math.max(1, Number(image.naturalHeight) || 1);
      return {
        width,
        height,
        maxDim: Math.max(width, height),
        pixelCount: width * height,
      };
    }

    function canvasToDataUrl(canvas, mimeType, quality) {
      return new Promise((resolve, reject) => {
        canvas.toBlob(
          (blob) => {
            if (!blob) {
              reject(new Error("canvas_blob_failed"));
              return;
            }
            const fr = new FileReader();
            fr.onload = () => resolve({ dataUrl: String(fr.result || ""), size: blob.size });
            fr.onerror = () => reject(new Error("canvas_read_failed"));
            fr.readAsDataURL(blob);
          },
          mimeType,
          quality
        );
      });
    }

    async function compressImageDataUrl(originalDataUrl) {
      const image = await dataUrlToImage(originalDataUrl);
      const maxDim = Math.max(image.naturalWidth || 1, image.naturalHeight || 1);
      const scale = maxDim > IMAGE_MAX_DIMENSION ? IMAGE_MAX_DIMENSION / maxDim : 1;
      const width = Math.max(1, Math.round((image.naturalWidth || 1) * scale));
      const height = Math.max(1, Math.round((image.naturalHeight || 1) * scale));
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        throw new Error("canvas_context_failed");
      }
      ctx.drawImage(image, 0, 0, width, height);

      const qualities = [0.82, 0.74, 0.66, 0.58, 0.5, 0.42];
      let best = null;
      for (const quality of qualities) {
        const candidate = await canvasToDataUrl(canvas, "image/jpeg", quality);
        if (!best || candidate.size < best.size) {
          best = candidate;
        }
        if (candidate.size <= IMAGE_SAFE_MAX_BYTES) {
          break;
        }
      }

      if (!best) {
        throw new Error("compress_failed");
      }

      return {
        dataUrl: best.dataUrl,
        size: best.size,
        type: "image/jpeg",
      };
    }

    async function maybeCompressImage(dataUrl, file) {
      const inputSize = Number(file?.size) || estimateDataUrlBytes(dataUrl);
      let metadata = null;
      try {
        metadata = await inspectImageDataUrl(dataUrl);
      } catch (_err) {
        metadata = null;
      }
      const needsResize = Boolean(
        metadata && (
          metadata.maxDim > IMAGE_MAX_DIMENSION
          || metadata.pixelCount > IMAGE_MAX_PIXEL_COUNT
        )
      );

      if (inputSize <= IMAGE_SAFE_MAX_BYTES && !needsResize) {
        return {
          dataUrl,
          size: inputSize,
          type: file?.type || "image/*",
          optimized: false,
          originalSize: inputSize,
        };
      }

      setComposerActivity("Optimizing image...");
      setComposerStatusChip("Optimizing image...", { phase: "image" });
      const compressed = await compressImageDataUrl(dataUrl);
      return {
        dataUrl: compressed.dataUrl,
        size: compressed.size,
        type: compressed.type,
        optimized: true,
        originalSize: inputSize,
      };
    }

    function normalizeChatSettings(rawSettings) {
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

    function activeRuntimeVisionCapability(statusPayload = appState.latestStatus) {
      const activeModel = resolveActiveRuntimeModel(statusPayload);
      const supportsVision = activeModel?.capabilities?.vision;
      return typeof supportsVision === "boolean" ? supportsVision : null;
    }

    function formatTextOnlyImageNotice(statusPayload = appState.latestStatus) {
      const activeModel = resolveActiveRuntimeModel(statusPayload);
      const modelName = String(activeModel?.filename || "The current model").trim();
      return `${modelName} is text-only. Switch to a vision-capable model in Settings to send images.`;
    }

    function formatImageRejectedNotice(statusPayload = appState.latestStatus) {
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

    function showTextOnlyImageBlockedState(statusPayload = appState.latestStatus) {
      const notice = formatTextOnlyImageNotice(statusPayload);
      setComposerVisionNotice(notice);
      setComposerActivity(notice);
      setComposerStatusChip("Current model is text-only.", { phase: "image" });
      hideComposerStatusChip();
      setCancelEnabled(false);
      focusPromptInput();
    }

    function renderComposerCapabilities(statusPayload = appState.latestStatus) {
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
        clearPendingImage();
        setComposerActivity("Image removed.");
      }
    }

    function getSettingsModels(statusPayload = appState.latestStatus) {
      return Array.isArray(statusPayload?.models) ? statusPayload.models : [];
    }

    function resolveSelectedSettingsModel(statusPayload = appState.latestStatus) {
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

    function getActiveChatSettings(statusPayload = appState.latestStatus) {
      const activeChat = statusPayload?.model?.settings?.chat;
      return normalizeChatSettings(activeChat);
    }

    function collectSelectedModelSettings() {
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
        vision: {
          enabled: supportsVision && Boolean(document.getElementById("visionEnabled")?.checked),
          projector_mode: "default",
          projector_filename: supportsVision
            ? String(document.getElementById("downloadProjectorBtn")?.dataset?.projectorFilename || "")
            : "",
        },
      };
    }

    function markModelSettingsDraftDirty() {
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

    function clearModelSettingsDraftState() {
      appState.modelSettingsDraftDirty = false;
      appState.modelSettingsDraftModelId = "";
      const discardBtn = document.getElementById("discardModelSettingsBtn");
      if (discardBtn) {
        discardBtn.hidden = true;
        discardBtn.disabled = true;
      }
    }

    function setModelUrlStatus(message) {
      const statusEl = document.getElementById("modelUrlStatus");
      if (statusEl) {
        statusEl.textContent = String(message || "");
      }
    }

    function formatModelUrlStatus(reason, fallbackStatus) {
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
        || (visionEnabledEl ? Boolean(visionEnabledEl.checked) !== Boolean(vision.enabled) : false)
      );
    }

    function selectedModelHasUnsavedChanges(statusPayload = appState.latestStatus) {
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

    function blockModelSelectionChange() {
      const statusEl = document.getElementById("modelSettingsStatus");
      if (statusEl) {
        statusEl.textContent = "Save or discard changes before switching models.";
      }
    }

    function discardSelectedModelSettings() {
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

    function collectSettings() {
      return {
        ...getActiveChatSettings(),
        theme: document.documentElement.getAttribute("data-theme") || defaultSettings.theme,
      };
    }

    function focusPromptInput(options = {}) {
      const prompt = document.getElementById("userPrompt");
      if (!prompt) return;
      const preventScroll = options.preventScroll !== false;
      prompt.focus({ preventScroll });
      if (options.moveCaretToEnd === false) return;
      const cursor = prompt.value.length;
      if (typeof prompt.setSelectionRange === "function") {
        prompt.setSelectionRange(cursor, cursor);
      }
    }

    function cancelPendingImageWork() {
      appState.pendingImageToken += 1;
      if (appState.pendingImageReader) {
        appState.pendingImageReader.abort();
      }
      appState.pendingImageReader = null;
    }

    function clearPendingImage() {
      appState.pendingImage = null;
      const fileInput = document.getElementById("imageInput");
      const attachBtn = document.getElementById("attachImageBtn");
      const preview = document.getElementById("imagePreview");
      const previewWrap = document.getElementById("imagePreviewWrap");
      const imageMeta = document.getElementById("imageMeta");
      const clearBtn = document.getElementById("clearImageBtn");
      if (fileInput) {
        fileInput.value = "";
      }
      if (preview) {
        preview.removeAttribute("src");
      }
      if (previewWrap) {
        previewWrap.hidden = true;
      }
      if (imageMeta) {
        imageMeta.textContent = "";
        imageMeta.hidden = true;
      }
      if (clearBtn) {
        clearBtn.hidden = true;
      }
      if (attachBtn) {
        attachBtn.textContent = "Attach image";
        attachBtn.classList.remove("selected");
      }
    }

    function handleImageSelected(file) {
      const selectionToken = appState.pendingImageToken + 1;
      appState.pendingImageToken = selectionToken;

      if (!file) {
        clearPendingImage();
        setComposerActivity("");
        hideComposerStatusChip();
        setCancelEnabled(false);
        focusPromptInput();
        return;
      }
      if (activeRuntimeVisionCapability(appState.latestStatus) === false) {
        clearPendingImage();
        showTextOnlyImageBlockedState(appState.latestStatus);
        return;
      }
      if (!String(file.type || "").startsWith("image/")) {
        appendMessage("assistant", "Only image files are supported.");
        clearPendingImage();
        setComposerActivity("");
        hideComposerStatusChip();
        setCancelEnabled(false);
        focusPromptInput();
        return;
      }

      if (appState.pendingImageReader) {
        appState.pendingImageReader.abort();
      }
      const reader = new FileReader();
      appState.pendingImageReader = reader;
      setComposerActivity("Reading image...");
      setComposerStatusChip("Reading image • 0%", { phase: "image" });
      setCancelEnabled(true);
      reader.onprogress = (event) => {
        if (event.lengthComputable && event.total > 0) {
          const percent = Math.round((event.loaded * 100) / event.total);
          setComposerActivity(`Reading image... ${percent}%`);
          setComposerStatusChip(`Reading image • ${percent}%`, { phase: "image" });
          return;
        }
        setComposerActivity("Reading image...");
        setComposerStatusChip("Reading image...", { phase: "image" });
      };
      reader.onload = async () => {
        if (selectionToken !== appState.pendingImageToken) {
          return;
        }
        const result = typeof reader.result === "string" ? reader.result : "";
        if (!result.startsWith("data:image/")) {
          appendMessage("assistant", "Invalid image encoding.");
          clearPendingImage();
          appState.pendingImageReader = null;
          setComposerActivity("");
          hideComposerStatusChip();
          setCancelEnabled(false);
          focusPromptInput();
          return;
        }

        let processedImage;
        try {
          processedImage = await maybeCompressImage(result, file);
        } catch (_err) {
          appendMessage("assistant", "Could not optimize the selected image.");
          clearPendingImage();
          appState.pendingImageReader = null;
          setComposerActivity("");
          hideComposerStatusChip();
          setCancelEnabled(false);
          focusPromptInput();
          return;
        }

        if (selectionToken !== appState.pendingImageToken) {
          return;
        }

        appState.pendingImage = {
          name: file.name || "image",
          type: processedImage.type || file.type || "image/*",
          size: Number(processedImage.size) || 0,
          originalSize: Number(processedImage.originalSize) || Number(file.size) || 0,
          optimized: Boolean(processedImage.optimized),
          dataUrl: processedImage.dataUrl || result,
        };

        const preview = document.getElementById("imagePreview");
        const previewWrap = document.getElementById("imagePreviewWrap");
        const imageMeta = document.getElementById("imageMeta");
        const clearBtn = document.getElementById("clearImageBtn");
        const attachBtn = document.getElementById("attachImageBtn");
        if (preview) {
          preview.src = appState.pendingImage.dataUrl;
        }
        if (previewWrap) {
          previewWrap.hidden = false;
        }
        if (imageMeta) {
          if (appState.pendingImage.optimized && appState.pendingImage.originalSize > appState.pendingImage.size) {
            imageMeta.textContent = `${appState.pendingImage.name} (${formatBytes(appState.pendingImage.size)}, optimized from ${formatBytes(appState.pendingImage.originalSize)})`;
          } else {
            imageMeta.textContent = `${appState.pendingImage.name} (${formatBytes(appState.pendingImage.size)})`;
          }
          imageMeta.hidden = false;
        }
        if (clearBtn) {
          clearBtn.hidden = false;
        }
        if (attachBtn) {
          attachBtn.textContent = "Change image";
          attachBtn.classList.add("selected");
        }
        appState.pendingImageReader = null;
        setComposerActivity("");
        hideComposerStatusChip();
        setCancelEnabled(false);
        focusPromptInput();
      };
      reader.onerror = () => {
        if (selectionToken !== appState.pendingImageToken) {
          return;
        }
        appendMessage("assistant", "Could not read the selected image.");
        clearPendingImage();
        appState.pendingImageReader = null;
        setComposerActivity("");
        hideComposerStatusChip();
        setCancelEnabled(false);
        focusPromptInput();
      };
      reader.onabort = () => {
        if (selectionToken !== appState.pendingImageToken) {
          return;
        }
        clearPendingImage();
        appState.pendingImageReader = null;
        setComposerActivity("Image load cancelled.");
        hideComposerStatusChip();
        setCancelEnabled(false);
        focusPromptInput();
      };
      reader.readAsDataURL(file);
    }

    function buildUserMessageContent(content) {
      if (!appState.pendingImage) {
        return content;
      }
      const textPart = content || "Describe this image.";
      return [
        { type: "text", text: textPart },
        { type: "image_url", image_url: { url: appState.pendingImage.dataUrl } },
      ];
    }

    function buildUserBubblePayload(content) {
      const text = String(content || "");
      if (!appState.pendingImage) {
        return {
          text,
          imageDataUrl: "",
          imageName: "",
        };
      }
      return {
        text,
        imageDataUrl: appState.pendingImage.dataUrl,
        imageName: appState.pendingImage.name || "image",
      };
    }

    function extractApiErrorMessage(body) {
      if (!body || typeof body !== "object") return "";
      const candidate = body?.error?.message || body?.detail || body?.message || "";
      return typeof candidate === "string" ? candidate.trim() : "";
    }

    function formatChatFailureMessage(statusCode, body, requestCtx = {}) {
      const apiMessage = extractApiErrorMessage(body);
      const normalized = apiMessage.toLowerCase();
      if (
        requestCtx?.hasImageRequest
        && (
          normalized.includes("image input is not supported")
          || normalized.includes("mmproj")
        )
      ) {
        return formatImageRejectedNotice(appState.latestStatus);
      }
      if (apiMessage) {
        return `Request failed (${statusCode}): ${apiMessage}`;
      }
      return `Request failed (${statusCode}).`;
    }

    function openImagePicker() {
      if (appState.requestInFlight) return;
      if (activeRuntimeVisionCapability(appState.latestStatus) === false) {
        clearPendingImage();
        showTextOnlyImageBlockedState(appState.latestStatus);
        return;
      }
      const input = document.getElementById("imageInput");
      if (!input) return;
      input.value = "";
      input.click();
    }

    function isMobileSidebarViewport() {
      if (!appState.mobileSidebarMql) {
        appState.mobileSidebarMql = window.matchMedia("(max-width: 900px)");
      }
      return appState.mobileSidebarMql.matches;
    }

    function setSidebarOpen(open) {
      const sidebar = document.getElementById("sidebarPanel");
      const backdrop = document.getElementById("sidebarBackdrop");
      const toggle = document.getElementById("sidebarToggle");
      const closeBtn = document.getElementById("sidebarCloseBtn");
      const mobile = isMobileSidebarViewport();
      const shouldOpen = Boolean(open) && mobile;

      document.body.classList.toggle("sidebar-open", shouldOpen);

      if (sidebar) {
        sidebar.setAttribute("aria-hidden", mobile ? (shouldOpen ? "false" : "true") : "false");
      }
      if (backdrop) {
        backdrop.hidden = !shouldOpen;
      }
      if (toggle) {
        toggle.hidden = !mobile;
        toggle.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
      }
      if (closeBtn) {
        closeBtn.hidden = !shouldOpen;
      }
    }

    function setSettingsModalOpen(open) {
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
        setSidebarOpen(false);
      } else {
        closeLegacySettingsModal();
      }
    }

    function setLegacySettingsModalOpen(open) {
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
        setSidebarOpen(false);
      }
    }

    function showSettingsWorkspaceTab(tabName) {
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

    function openSettingsModal() {
      showSettingsWorkspaceTab(appState.settingsWorkspaceTab);
      renderSettingsWorkspace(appState.latestStatus);
      setSettingsModalOpen(true);
    }

    function closeSettingsModal() {
      setSettingsModalOpen(false);
    }

    function openLegacySettingsModal() {
      setLegacySettingsModalOpen(true);
    }

    function closeLegacySettingsModal() {
      setLegacySettingsModalOpen(false);
    }

    function syncSegmentedControl(targetId) {
      const currentValue = String(document.getElementById(targetId)?.value || "");
      document.querySelectorAll(`.settings-segmented[data-target="${targetId}"] .settings-segment-btn`).forEach((button) => {
        const active = String(button.dataset.value || "") === currentValue;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", active ? "true" : "false");
      });
    }

    function setSegmentedControlValue(targetId, value) {
      const input = document.getElementById(targetId);
      if (!input) return;
      input.value = String(value || "");
      syncSegmentedControl(targetId);
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function renderSelectedModelSettings(statusPayload) {
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
        if (selectedModel?.storage?.location === "ssd") metaBits.push("Stored on SSD");
        else if (selectedModel?.storage?.location) metaBits.push(`Stored on ${String(selectedModel.storage.location).toUpperCase()}`);
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

    function renderModelsList(statusPayload) {
      const container = document.getElementById("modelsList");
      if (!container) return;
      const models = Array.isArray(statusPayload?.models) ? statusPayload.models : [];
      const ssdAvailable = statusPayload?.storage_targets?.ssd?.available === true;
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
        status.textContent = formatModelStatusLabel(model?.status);
        head.appendChild(name);
        head.appendChild(status);

        const meta = document.createElement("div");
        meta.className = "model-row-meta";
        const metaBits = [];
        if (model?.is_active === true) metaBits.push("Active");
        if (model?.capabilities?.vision) metaBits.push("Vision");
        if (model?.storage?.location === "ssd") metaBits.push("SSD");
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
        if (ssdAvailable && model?.status === "ready" && model?.storage?.location !== "ssd") {
          const ssdBtn = document.createElement("button");
          ssdBtn.type = "button";
          ssdBtn.className = "ghost-btn";
          ssdBtn.dataset.action = "move-to-ssd";
          ssdBtn.textContent = "Move to SSD";
          ssdBtn.title = "Copy this model to the attached SSD and keep using it from there";
          actions.appendChild(ssdBtn);
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
        if (model?.storage?.location === "ssd") {
          const storageLabel = document.createElement("span");
          storageLabel.className = "runtime-compact";
          storageLabel.textContent = "On SSD";
          actions.appendChild(storageLabel);
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

    function renderSettingsWorkspace(statusPayload) {
      renderModelsList(statusPayload);
      if (!shouldPauseSelectedModelSettingsRender()) {
        renderSelectedModelSettings(statusPayload);
      }
      showSettingsWorkspaceTab(appState.settingsWorkspaceTab);
    }

    async function loadSettingsDocument() {
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

    async function applySettingsDocument() {
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
        await pollStatus();
      } catch (err) {
        if (statusEl) statusEl.textContent = `Could not apply YAML: ${err}`;
      } finally {
        appState.settingsYamlRequestInFlight = false;
      }
    }

    async function saveSelectedModelSettings() {
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
        await pollStatus();
      } catch (err) {
        if (statusEl) statusEl.textContent = `Could not save model settings: ${err}`;
      } finally {
        appState.modelSettingsSaveInFlight = false;
        if (saveBtn) saveBtn.disabled = false;
        if (discardBtn) discardBtn.disabled = false;
      }
    }

    async function downloadProjectorForSelectedModel() {
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
        await pollStatus();
      } catch (err) {
        if (statusEl) statusEl.textContent = `Could not download encoder: ${err}`;
      } finally {
        appState.projectorDownloadInFlight = false;
        if (button) button.disabled = false;
      }
    }

    function setEditModalOpen(open) {
      appState.editModalOpen = Boolean(open);
      const modal = document.getElementById("editModal");
      const backdrop = document.getElementById("editBackdrop");
      document.body.classList.toggle("edit-modal-open", appState.editModalOpen);
      if (modal) {
        modal.hidden = !appState.editModalOpen;
      }
      if (backdrop) {
        backdrop.hidden = !appState.editModalOpen;
      }
      if (appState.editModalOpen) {
        setSidebarOpen(false);
      }
    }

    function closeEditMessageModal(options = {}) {
      appState.activeEditState = null;
      setEditModalOpen(false);
      if (options.restoreFocus !== false) {
        focusPromptInput();
      }
    }

    function setEditModalBusy(busy) {
      const input = document.getElementById("editMessageInput");
      const sendBtn = document.getElementById("editSendBtn");
      const cancelBtn = document.getElementById("editCancelBtn");
      const closeBtn = document.getElementById("editCloseBtn");
      if (input) input.disabled = Boolean(busy);
      if (sendBtn) sendBtn.disabled = Boolean(busy);
      if (cancelBtn) cancelBtn.disabled = Boolean(busy);
      if (closeBtn) closeBtn.disabled = Boolean(busy);
    }

    function updateEditModalCopy(state) {
      const note = document.getElementById("editModalNote");
      const hint = document.getElementById("editModalHint");
      const sendBtn = document.getElementById("editSendBtn");
      const isGenerating = Boolean(state?.wasGenerating);
      if (note) {
        note.textContent = isGenerating
          ? "Update the message, stop the current reply, and restart from this point."
          : "Update the message and resend from this point in the conversation.";
      }
      if (hint) {
        hint.textContent = isGenerating
          ? "Sending will cancel the in-progress response and replace everything from this turn onward."
          : "Everything after this turn will be replaced by the new run.";
      }
      if (sendBtn) {
        sendBtn.textContent = isGenerating ? "Cancel & send" : "Send";
      }
    }

    function openEditMessageModal(messageView) {
      const turn = messageView?.turnRef;
      if (!turn || messageView?.role !== "user") return;
      const input = document.getElementById("editMessageInput");
      appState.activeEditState = {
        turn,
        wasGenerating: Boolean(appState.requestInFlight),
      };
      updateEditModalCopy(appState.activeEditState);
      if (input) {
        input.value = String(turn.userText || messageView.editText || "");
      }
      setEditModalBusy(false);
      setEditModalOpen(true);
      window.setTimeout(() => {
        if (!input) return;
        input.focus({ preventScroll: true });
        if (typeof input.setSelectionRange === "function") {
          input.setSelectionRange(input.value.length, input.value.length);
        }
      }, 0);
    }


    function bindMessagesScroller() {
      const box = getMessagesBox();
      if (!box) return;
      box.addEventListener("pointerdown", (event) => {
        if (event.target instanceof Element && event.target.closest(".message-bubble, .message-meta")) {
          appState.messagePointerSelectionActive = true;
        }
      });
      const clearPointerSelection = () => {
        appState.messagePointerSelectionActive = false;
      };
      box.addEventListener("pointerup", clearPointerSelection);
      box.addEventListener("pointercancel", clearPointerSelection);
      document.addEventListener("pointerup", clearPointerSelection);
      document.addEventListener("selectionchange", () => {
        if (!hasActiveMessageSelection(box) && !document.activeElement?.closest?.(".message-bubble, .message-meta")) {
          appState.messagePointerSelectionActive = false;
        }
      });
      box.addEventListener("scroll", () => {
        setMessagesPinnedState(isMessagesPinned(box));
      });
      setMessagesPinnedState(isMessagesPinned(box));
    }

    function bindMobileSidebar() {
      appState.mobileSidebarMql = window.matchMedia("(max-width: 900px)");
      const sync = () => {
        if (!appState.mobileSidebarMql.matches) {
          setSidebarOpen(false);
        } else {
          setSidebarOpen(document.body.classList.contains("sidebar-open"));
        }
      };

      const onViewportChange = () => {
        sync();
      };
      if (typeof appState.mobileSidebarMql.addEventListener === "function") {
        appState.mobileSidebarMql.addEventListener("change", onViewportChange);
      } else if (typeof appState.mobileSidebarMql.addListener === "function") {
        appState.mobileSidebarMql.addListener(onViewportChange);
      }

      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          if (appState.modelSwitcherOpen) {
            closeModelSwitcher();
            return;
          }
          if (appState.editModalOpen) {
            closeEditMessageModal();
            return;
          }
          if (appState.legacySettingsModalOpen) {
            closeLegacySettingsModal();
            return;
          }
          if (appState.settingsModalOpen) {
            closeSettingsModal();
            return;
          }
          setSidebarOpen(false);
        }
      });

      sync();
    }

    function bindSettingsModal() {
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

    function bindEditModal() {
      document.getElementById("editCloseBtn").addEventListener("click", () => closeEditMessageModal());
      document.getElementById("editCancelBtn").addEventListener("click", () => closeEditMessageModal());
      document.getElementById("editBackdrop").addEventListener("click", () => closeEditMessageModal());
      document.getElementById("editModal").addEventListener("click", (event) => {
        if (event.target === event.currentTarget) {
          closeEditMessageModal();
        }
      });
      document.getElementById("editSendBtn").addEventListener("click", submitEditMessageModal);
      document.getElementById("editMessageInput").addEventListener("keydown", (event) => {
        if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
          event.preventDefault();
          submitEditMessageModal();
        }
      });
    }

    function applyTheme(theme) {
      const resolved = theme === "light" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", resolved);
      const toggle = document.getElementById("themeToggle");
      const target = resolved === "dark" ? "light" : "dark";
      toggle.setAttribute("aria-label", `Switch to ${target} theme`);
      toggle.setAttribute("title", `Switch to ${target} theme`);
    }

    function bindSettings() {
      const settings = loadSettings();
      applyTheme(settings.theme);
    }

    function setSendEnabled() {
      const sendBtn = document.getElementById("sendBtn");
      const ready = appState.latestStatus && appState.latestStatus.state === "READY";
      if (appState.requestInFlight) {
        sendBtn.disabled = false;
        sendBtn.textContent = "Stop";
        sendBtn.classList.add("stop-mode");
        renderComposerCapabilities(appState.latestStatus);
        return;
      }
      sendBtn.textContent = "Send";
      sendBtn.classList.remove("stop-mode");
      sendBtn.disabled = !ready;
      renderComposerCapabilities(appState.latestStatus);
    }

    function setComposerActivity(message) {
      const activity = document.getElementById("composerActivity");
      if (!activity) return;
      activity.textContent = String(message || "");
    }

    function setComposerStatusChip(message, options = {}) {
      const chip = document.getElementById("composerStatusChip");
      const text = document.getElementById("composerStatusText");
      if (!chip || !text) return;
      if (appState.statusChipHideTimer) {
        window.clearTimeout(appState.statusChipHideTimer);
        appState.statusChipHideTimer = null;
      }

      const label = String(message || "").trim();
      if (!label) {
        chip.hidden = true;
        text.textContent = "";
        chip.dataset.phase = "idle";
        appState.statusChipVisibleAtMs = 0;
        return;
      }

      if (chip.hidden) {
        appState.statusChipVisibleAtMs = performance.now();
      }
      chip.hidden = false;
      text.textContent = label;
      chip.dataset.phase = String(options.phase || "prefill");
    }

    function hideComposerStatusChip(options = {}) {
      const chip = document.getElementById("composerStatusChip");
      if (!chip) return;
      const immediate = options.immediate === true;
      const elapsedMs = appState.statusChipVisibleAtMs > 0 ? (performance.now() - appState.statusChipVisibleAtMs) : STATUS_CHIP_MIN_VISIBLE_MS;
      const delayMs = immediate ? 0 : Math.max(0, STATUS_CHIP_MIN_VISIBLE_MS - elapsedMs);
      if (appState.statusChipHideTimer) {
        window.clearTimeout(appState.statusChipHideTimer);
      }
      appState.statusChipHideTimer = window.setTimeout(() => {
        appState.statusChipHideTimer = null;
        setComposerStatusChip("");
      }, delayMs);
    }

    function estimateContentChars(content) {
      if (typeof content === "string") {
        return content.length;
      }
      if (!Array.isArray(content)) {
        return 0;
      }
      let chars = 0;
      for (const part of content) {
        if (!part || typeof part !== "object") continue;
        if (part.type === "text" && typeof part.text === "string") {
          chars += part.text.length;
        } else if (part.type === "image_url") {
          chars += 1200;
        }
      }
      return chars;
    }

    function estimatePromptTokens(messages) {
      if (!Array.isArray(messages)) return 0;
      let chars = 0;
      for (const message of messages) {
        if (!message || typeof message !== "object") continue;
        chars += estimateContentChars(message.content);
      }
      return Math.max(1, Math.round(chars / 4));
    }

    function loadPrefillMetrics() {
      const raw = localStorage.getItem(PREFILL_METRICS_KEY);
      if (!raw) return {};
      try {
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed === "object" ? parsed : {};
      } catch (_err) {
        return {};
      }
    }

    function savePrefillMetrics(metrics) {
      localStorage.setItem(PREFILL_METRICS_KEY, JSON.stringify(metrics));
    }

    function choosePrefillBucket(hasImage, promptTokens, imageBytes) {
      const largeText = promptTokens > 1300;
      const largeImage = imageBytes > 120 * 1024;
      const size = largeText || largeImage ? "large" : "small";
      return `${hasImage ? "vision" : "text"}_${size}`;
    }

    function estimatePrefillEtaMs(hasImage, promptTokens, imageBytes, bucket) {
      const isBigImage = hasImage && imageBytes >= (120 * 1024);
      const baseMs = hasImage ? (isBigImage ? 8500 : 5400) : 1800;
      const promptMs = Math.round(Math.max(0, promptTokens) * 7);
      const imageMs = hasImage
        ? Math.round((Math.max(0, imageBytes) / 1024) * (isBigImage ? 80 : 28))
        : 0;
      let estimateMs = baseMs + promptMs + imageMs;

      const metrics = loadPrefillMetrics();
      const sample = metrics[bucket];
      const learnedMs = Number(sample?.ewma_ms);
      const learnedCount = Number(sample?.count);
      if (Number.isFinite(learnedMs) && learnedMs > 0 && Number.isFinite(learnedCount) && learnedCount >= 1) {
        estimateMs = Math.round((estimateMs * 0.55) + (learnedMs * 0.45));
      }

      const etaMs = Math.max(1500, Math.min(120000, estimateMs));
      return { etaMs };
    }

    function beginPrefillProgress(requestCtx, options) {
      stopPrefillProgress({ resetUi: false });
      const hasImage = Boolean(options?.hasImage);
      const promptTokens = Number(options?.promptTokens) || 0;
      const imageBytes = Number(options?.imageBytes) || 0;
      const bucket = String(options?.bucket || choosePrefillBucket(hasImage, promptTokens, imageBytes));
      const estimate = estimatePrefillEtaMs(hasImage, promptTokens, imageBytes, bucket);
      const initialProgress = hasImage ? 14 : PREFILL_PROGRESS_FLOOR;

      appState.activePrefillProgress = {
        requestCtx,
        bucket,
        startedAtMs: performance.now(),
        etaMs: estimate.etaMs,
        progress: initialProgress,
        timerId: null,
        finishTimerId: null,
        finishPromise: null,
        finishResolve: null,
      };

      setComposerActivity("Preparing prompt...");
      applyPrefillProgressState(requestCtx, initialProgress);

      appState.activePrefillProgress.timerId = window.setInterval(() => {
        const active = appState.activePrefillProgress;
        if (!active || active.requestCtx !== requestCtx) return;
        const elapsedMs = Math.max(0, performance.now() - active.startedAtMs);
        const normalized = Math.max(0, elapsedMs / Math.max(active.etaMs, 1));
        const eased = 1 - Math.exp(-3.2 * Math.min(1.4, normalized));
        let target = PREFILL_PROGRESS_FLOOR + ((95 - PREFILL_PROGRESS_FLOOR) * eased);
        if (normalized > 0.75) {
          target -= Math.min(2.8, (normalized - 0.75) * 7.5);
        }
        if (elapsedMs > active.etaMs) {
          const overtimeSeconds = (elapsedMs - active.etaMs) / 1000;
          const tail = Math.min(
            PREFILL_PROGRESS_CAP - PREFILL_PROGRESS_TAIL_START,
            Math.log1p(overtimeSeconds) * 2.6
          );
          target = Math.max(target, PREFILL_PROGRESS_TAIL_START + tail);
        }
        active.progress = Math.max(active.progress, Math.min(PREFILL_PROGRESS_CAP, target));
        const percent = Math.round(Math.min(PREFILL_PROGRESS_CAP, active.progress));
        applyPrefillProgressState(requestCtx, percent);
      }, PREFILL_TICK_MS);
    }

    function markPrefillGenerationStarted(requestCtx) {
      const active = appState.activePrefillProgress;
      if (!active || active.requestCtx !== requestCtx) return Promise.resolve({ cancelled: false });
      if (active.finishPromise) {
        return active.finishPromise;
      }
      if (active.timerId !== null) {
        window.clearInterval(active.timerId);
      }
      active.timerId = null;
      const startPercent = Math.max(
        PREFILL_PROGRESS_FLOOR,
        Math.min(PREFILL_PROGRESS_CAP, Math.round(Number(active.progress) || PREFILL_PROGRESS_FLOOR)),
      );
      active.finishPromise = new Promise((resolve) => {
        active.finishResolve = resolve;
        const startedAtMs = performance.now();

        const finalize = (cancelled = false) => {
          if (active.finishTimerId !== null) {
            window.clearTimeout(active.finishTimerId);
            active.finishTimerId = null;
          }
          active.finishResolve = null;
          active.finishPromise = null;
          if (appState.activePrefillProgress && appState.activePrefillProgress.requestCtx === requestCtx) {
            appState.activePrefillProgress = null;
          }
          if (cancelled) {
            resolve({ cancelled: true });
            return;
          }
          applyPrefillProgressState(requestCtx, 100);
          active.finishTimerId = window.setTimeout(() => {
            if (active.finishTimerId !== null) {
              window.clearTimeout(active.finishTimerId);
              active.finishTimerId = null;
            }
            hideComposerStatusChip({ immediate: true });
            resolve({ cancelled: false });
          }, PREFILL_FINISH_HOLD_MS);
        };

        const step = () => {
          if (requestCtx?.stoppedByUser === true) {
            finalize(true);
            return;
          }
          const elapsedMs = Math.max(0, performance.now() - startedAtMs);
          const progress = Math.min(1, elapsedMs / PREFILL_FINISH_DURATION_MS);
          const eased = 1 - Math.pow(1 - progress, 2);
          const nextPercent = startPercent + ((100 - startPercent) * eased);
          active.progress = nextPercent;
          applyPrefillProgressState(requestCtx, nextPercent);
          if (progress >= 1) {
            finalize(false);
            return;
          }
          active.finishTimerId = window.setTimeout(step, PREFILL_FINISH_TICK_MS);
        };

        step();
      });
      return active.finishPromise;
    }

    function stopPrefillProgress(options = {}) {
      const active = appState.activePrefillProgress;
      if (active && active.timerId !== null) {
        window.clearInterval(active.timerId);
      }
      if (active && active.finishTimerId !== null) {
        window.clearTimeout(active.finishTimerId);
      }
      if (active && typeof active.finishResolve === "function") {
        active.finishResolve({ cancelled: true });
      }
      appState.activePrefillProgress = null;
      if (options.resetUi !== false) {
        hideComposerStatusChip();
      }
    }

    function resolvePromptPrefillMs(source, fallbackMs = 0) {
      const direct = Number(source?.timings?.prompt_ms);
      if (Number.isFinite(direct) && direct > 0) {
        return direct;
      }
      const fallback = Number(fallbackMs);
      if (Number.isFinite(fallback) && fallback > 0) {
        return fallback;
      }
      return 0;
    }

    function recordPrefillMetric(bucket, promptMs) {
      if (!bucket) return;
      const sampleMs = Number(promptMs);
      if (!Number.isFinite(sampleMs) || sampleMs <= 0) return;
      const metrics = loadPrefillMetrics();
      const current = metrics[bucket] && typeof metrics[bucket] === "object" ? metrics[bucket] : {};
      const priorCount = Number(current.count);
      const priorEwma = Number(current.ewma_ms);
      const hasPrior = Number.isFinite(priorCount) && priorCount > 0 && Number.isFinite(priorEwma) && priorEwma > 0;
      const ewmaMs = hasPrior ? ((priorEwma * 0.65) + (sampleMs * 0.35)) : sampleMs;
      metrics[bucket] = {
        count: Math.max(1, Math.min(64, Math.floor((hasPrior ? priorCount : 0) + 1))),
        ewma_ms: Math.round(ewmaMs),
      };
      savePrefillMetrics(metrics);
    }

    function applyPrefillProgressState(requestCtx, percent) {
      const safePercent = Math.max(0, Math.min(100, Math.round(Number(percent) || 0)));
      setComposerStatusChip(`Preparing prompt • ${safePercent}%`, { phase: "prefill" });
      setMessageProcessingState(requestCtx?.assistantView, {
        phase: "prefill",
        label: "Prompt processing",
        percent: safePercent,
      });
    }

    function setCancelEnabled(enabled) {
      const cancelBtn = document.getElementById("cancelBtn");
      if (!cancelBtn) return;
      const show = Boolean(enabled);
      cancelBtn.hidden = !show;
      cancelBtn.disabled = !show;
    }


    function throwIfRequestStoppedAfterPrefill(requestCtx, finishResult) {
      if (finishResult?.cancelled || requestCtx?.stoppedByUser) {
        const error = new Error("Request cancelled");
        error.name = "AbortError";
        throw error;
      }
    }

    function waitForRequestIdle(timeoutMs = 6000) {
      const deadline = performance.now() + Math.max(250, Number(timeoutMs) || 6000);
      return new Promise((resolve) => {
        const tick = () => {
          if (!appState.requestInFlight || performance.now() >= deadline) {
            resolve(!appState.requestInFlight);
            return;
          }
          window.setTimeout(tick, 40);
        };
        tick();
      });
    }

    function rollbackConversationFromTurn(targetTurn) {
      if (!targetTurn) return;
      const startIndex = appState.conversationTurns.indexOf(targetTurn);
      if (startIndex < 0) return;
      appState.chatHistory.length = Math.max(0, Number(targetTurn.baseHistoryLength) || 0);
      for (let index = appState.conversationTurns.length - 1; index >= startIndex; index -= 1) {
        const turn = appState.conversationTurns[index];
        if (turn?.assistantView) {
          removeMessage(turn.assistantView);
        }
        if (turn?.userView) {
          removeMessage(turn.userView);
        }
      }
      appState.conversationTurns.splice(startIndex);
    }

    async function submitEditMessageModal() {
      if (!appState.activeEditState?.turn) {
        closeEditMessageModal();
        return;
      }
      const input = document.getElementById("editMessageInput");
      const nextText = String(input?.value || "").trim();
      if (!nextText) {
        if (input) {
          input.focus({ preventScroll: true });
        }
        return;
      }

      const { turn } = appState.activeEditState;
      setEditModalBusy(true);

      if (appState.requestInFlight && appState.activeRequest) {
        const current = appState.activeRequest;
        current.hideProcessingBubbleOnCancel = true;
        if (current.assistantView) {
          removeMessage(current.assistantView);
        }
        stopGeneration();
        await waitForRequestIdle();
      }

      rollbackConversationFromTurn(turn);
      closeEditMessageModal({ restoreFocus: false });
      clearPendingImage();
      const prompt = document.getElementById("userPrompt");
      if (prompt) {
        prompt.value = nextText;
      }
      focusPromptInput();
      sendChat();
    }

    // isLocalModelConnected, updateLlamaIndicator — extracted to status.js

    // model switcher — extracted to model-switcher.js

    // renderDownloadPrompt, renderStatusActions — extracted to status.js

    // setRuntimeDetailsExpanded, renderSystemRuntime, renderLlamaRuntimeStatus,
    // renderUploadState, set*Status helpers — extracted to runtime-ui.js


    function findModelInLatestStatus(modelId) {
      const models = Array.isArray(appState.latestStatus?.models) ? appState.latestStatus.models : [];
      return models.find((item) => String(item?.id || "") === String(modelId || "")) || null;
    }

    // postJson — extracted to utils.js
    // formatModelStatusLabel, formatSidebarStatusDetail, findResumableFailedModel,
    // isLocalModelConnected, updateLlamaIndicator, renderDownloadPrompt,
    // renderStatusActions, renderCompatibilityWarnings — extracted to status.js

    async function switchLlamaRuntimeBundle() {
      if (appState.llamaRuntimeSwitchInFlight) return;
      const select = document.getElementById("llamaRuntimeFamilySelect");
      const family = String(select?.value || "").trim();
      if (!family) {
        appendMessage("assistant", "No llama runtime selected.");
        return;
      }
      const selectedLabel = select?.selectedOptions?.[0]?.textContent || family;
      const confirmed = window.confirm(
        `Switch llama runtime to ${selectedLabel}?\n\nThis will restart the local llama runtime process.`
      );
      if (!confirmed) return;

      appState.llamaRuntimeSwitchInFlight = true;
      setLlamaRuntimeSwitchButtonState(true);
      setLlamaRuntimeSwitchStatus("Switching runtime...");
      setComposerActivity("Switching llama runtime...");
      try {
        const { res, body } = await postJson("/internal/llama-runtime/switch", { family });
        if (!res.ok || body?.switched !== true) {
          appendMessage("assistant", `Could not switch llama runtime (${body?.reason || res.status}).`);
          return;
        }
        appendMessage("assistant", `Switched llama runtime to ${body?.family || "selected runtime"}.`);
        setComposerActivity("Llama runtime switched. Reconnecting...");
      } catch (err) {
        appendMessage("assistant", `Could not switch llama runtime: ${err}`);
      } finally {
        appState.llamaRuntimeSwitchInFlight = false;
        setLlamaRuntimeSwitchButtonState(false);
        await pollStatus();
      }
    }

    async function applyLlamaMemoryLoadingMode() {
      if (appState.llamaMemoryLoadingApplyInFlight) return;
      const select = document.getElementById("llamaMemoryLoadingMode");
      const mode = String(select?.value || "auto").trim() || "auto";
      const label = select?.selectedOptions?.[0]?.textContent || mode;
      const confirmed = window.confirm(
        `Apply "${label}" and restart the llama runtime now? ` +
        "The model will reload and chat will disconnect briefly."
      );
      if (!confirmed) return;

      appState.llamaMemoryLoadingApplyInFlight = true;
      setLlamaMemoryLoadingButtonState(true);
      setLlamaMemoryLoadingStatus(`Applying memory loading mode: ${label}...`);
      try {
        const { res, body } = await postJson("/internal/llama-runtime/memory-loading", { mode });
        if (!res.ok) {
          appendMessage(
            "assistant",
            `Could not update model memory loading (${res.status}): ${body?.reason || "unknown"}.`
          );
          setLlamaMemoryLoadingStatus(`Last memory loading update error: ${body?.reason || res.status}`);
          return;
        }
        appendMessage(
          "assistant",
          `Applied model memory loading: ${body?.memory_loading?.label || mode}. ` +
          `Runtime restart: ${body?.restart_reason || "requested"}.`
        );
        await pollStatus();
      } catch (err) {
        appendMessage("assistant", `Could not update model memory loading: ${err}`);
        setLlamaMemoryLoadingStatus(`Last memory loading update error: ${err}`);
      } finally {
        appState.llamaMemoryLoadingApplyInFlight = false;
        setLlamaMemoryLoadingButtonState(false);
      }
    }

    async function applyLargeModelCompatibilityOverride(enabled) {
      if (appState.largeModelOverrideApplyInFlight) return;
      appState.largeModelOverrideApplyInFlight = true;
      setLargeModelOverrideButtonState(true);
      setLargeModelOverrideStatus(
        enabled
          ? "Applying compatibility override: try unsupported models..."
          : "Applying compatibility override: restore warnings..."
      );
      try {
        const { res, body } = await postJson("/internal/compatibility/large-model-override", { enabled: Boolean(enabled) });
        if (!res.ok || body?.updated !== true) {
          appendMessage("assistant", `Could not update compatibility override (${body?.reason || res.status}).`);
          setLargeModelOverrideStatus(`Last compatibility override error: ${body?.reason || res.status}`);
          return;
        }
        appendMessage(
          "assistant",
          body?.override?.enabled
            ? "Enabled compatibility override. Potato will try unsupported large models."
            : "Disabled compatibility override. Default large-model warnings are active again."
        );
        setLargeModelOverrideStatus(
          body?.override?.enabled
            ? "Compatibility override: trying unsupported large models is enabled"
            : "Compatibility override: default warnings"
        );
      } catch (err) {
        appendMessage("assistant", `Could not update compatibility override: ${err}`);
        setLargeModelOverrideStatus(`Last compatibility override error: ${err}`);
      } finally {
        appState.largeModelOverrideApplyInFlight = false;
        setLargeModelOverrideButtonState(false);
        await pollStatus();
      }
    }

    async function applyLargeModelOverrideFromSettings() {
      const checkbox = document.getElementById("largeModelOverrideEnabled");
      await applyLargeModelCompatibilityOverride(checkbox?.checked === true);
    }

    async function capturePowerCalibrationSample() {
      if (appState.powerCalibrationActionInFlight) return;
      const input = document.getElementById("powerCalibrationWallWatts");
      const wallWatts = Number(input?.value);
      if (!Number.isFinite(wallWatts) || wallWatts <= 0) {
        appendMessage("assistant", "Enter a valid wall meter reading in watts before capturing a sample.");
        setPowerCalibrationStatus("Power calibration error: invalid wall meter reading");
        return;
      }

      appState.powerCalibrationActionInFlight = true;
      setPowerCalibrationButtonsState(true);
      setPowerCalibrationStatus("Capturing power calibration sample...");
      try {
        const { res, body } = await postJson("/internal/power-calibration/sample", { wall_watts: wallWatts });
        if (!res.ok || body?.captured !== true) {
          appendMessage("assistant", `Could not capture power sample (${body?.reason || res.status}).`);
          setPowerCalibrationStatus(`Power calibration error: ${body?.reason || res.status}`);
          return;
        }
        appendMessage(
          "assistant",
          `Captured power calibration sample (wall ${Number(wallWatts).toFixed(2)} W vs raw ${Number(body?.sample?.raw_pmic_watts || 0).toFixed(3)} W).`
        );
      } catch (err) {
        appendMessage("assistant", `Could not capture power calibration sample: ${err}`);
        setPowerCalibrationStatus(`Power calibration error: ${err}`);
      } finally {
        appState.powerCalibrationActionInFlight = false;
        setPowerCalibrationButtonsState(false);
        await pollStatus();
      }
    }

    async function fitPowerCalibrationModel() {
      if (appState.powerCalibrationActionInFlight) return;
      appState.powerCalibrationActionInFlight = true;
      setPowerCalibrationButtonsState(true);
      setPowerCalibrationStatus("Computing power calibration...");
      try {
        const { res, body } = await postJson("/internal/power-calibration/fit", {});
        if (!res.ok || body?.updated !== true) {
          appendMessage("assistant", `Could not compute power calibration (${body?.reason || res.status}).`);
          setPowerCalibrationStatus(`Power calibration error: ${body?.reason || res.status}`);
          return;
        }
        const cal = body?.calibration || {};
        appendMessage(
          "assistant",
          `Power calibration updated (a=${Number(cal?.a || 0).toFixed(4)}, b=${Number(cal?.b || 0).toFixed(4)}, samples=${Number(cal?.sample_count || 0)}).`
        );
      } catch (err) {
        appendMessage("assistant", `Could not compute power calibration: ${err}`);
        setPowerCalibrationStatus(`Power calibration error: ${err}`);
      } finally {
        appState.powerCalibrationActionInFlight = false;
        setPowerCalibrationButtonsState(false);
        await pollStatus();
      }
    }

    async function resetPowerCalibrationModel() {
      if (appState.powerCalibrationActionInFlight) return;
      const confirmed = window.confirm(
        "Reset power calibration to the default correction model? Saved wall-meter samples will be cleared."
      );
      if (!confirmed) return;

      appState.powerCalibrationActionInFlight = true;
      setPowerCalibrationButtonsState(true);
      setPowerCalibrationStatus("Resetting power calibration...");
      try {
        const { res, body } = await postJson("/internal/power-calibration/reset", {});
        if (!res.ok || body?.updated !== true) {
          appendMessage("assistant", `Could not reset power calibration (${body?.reason || res.status}).`);
          setPowerCalibrationStatus(`Power calibration error: ${body?.reason || res.status}`);
          return;
        }
        appendMessage("assistant", "Power calibration reset. Using default correction again.");
      } catch (err) {
        appendMessage("assistant", `Could not reset power calibration: ${err}`);
        setPowerCalibrationStatus(`Power calibration error: ${err}`);
      } finally {
        appState.powerCalibrationActionInFlight = false;
        setPowerCalibrationButtonsState(false);
        await pollStatus();
      }
    }

    async function allowUnsupportedLargeModelFromWarning() {
      const confirmed = window.confirm(
        "Try loading unsupported large models anyway on this device? " +
        "This may fail or be unstable, but Potato will stop warning-blocking this attempt."
      );
      if (!confirmed) return;
      await applyLargeModelCompatibilityOverride(true);
    }

    // renderUploadState — extracted to runtime-ui.js

    async function updateCountdownPreference(enabled) {
      const { res, body } = await postJson("/internal/download-countdown", { enabled });
      if (!res.ok) {
        appendMessage("assistant", `Could not update auto-download: ${body?.reason || res.status}`);
      }
      await pollStatus();
    }

    async function registerModelFromUrl() {
      if (appState.modelActionInFlight) return;
      const input = document.getElementById("modelUrlInput");
      const sourceUrl = String(input?.value || "").trim();
      if (!sourceUrl) {
        setModelUrlStatus("Enter an HTTPS model URL ending with .gguf.");
        return;
      }
      appState.modelActionInFlight = true;
      setModelUrlStatus("Adding model URL...");
      try {
        const { res, body } = await postJson("/internal/models/register", { source_url: sourceUrl });
        if (!res.ok) {
          setModelUrlStatus(formatModelUrlStatus(body?.reason, res.status));
          return;
        }
        setModelUrlStatus(
          body?.reason === "already_exists"
            ? "That model URL is already registered."
            : "Model URL added."
        );
        if (input) input.value = "";
      } catch (err) {
        setModelUrlStatus(`Could not add model URL: ${err}`);
      } finally {
        appState.modelActionInFlight = false;
        await pollStatus();
      }
    }

    async function startModelDownloadForModel(modelId) {
      if (!modelId) return;
      if (appState.modelActionInFlight) return;
      appState.modelActionInFlight = true;
      try {
        const { res, body } = await postJson("/internal/models/download", { model_id: modelId });
        if (!res.ok) {
          appendMessage("assistant", `Could not start model download (${body?.reason || res.status}).`);
          return;
        }
        if (!body?.started && body?.reason === "insufficient_storage") {
          setComposerActivity("Model likely too large for free storage. Delete files and retry.");
        }
      } catch (err) {
        appendMessage("assistant", `Could not start model download: ${err}`);
      } finally {
        appState.modelActionInFlight = false;
        await pollStatus();
      }
    }

    async function cancelActiveModelDownload(modelId = null) {
      if (appState.modelActionInFlight) return;
      const targetModel = findModelInLatestStatus(modelId) || findModelInLatestStatus(appState.latestStatus?.download?.current_model_id);
      const targetName = String(targetModel?.filename || "this model");
      const confirmed = window.confirm(`Stop the current download for ${targetName}?`);
      if (!confirmed) return;
      appState.modelActionInFlight = true;
      try {
        const { res, body } = await postJson("/internal/models/cancel-download", {});
        if (!res.ok) {
          appendMessage("assistant", `Could not cancel model download (${body?.reason || res.status}).`);
        }
      } catch (err) {
        appendMessage("assistant", `Could not cancel model download: ${err}`);
      } finally {
        appState.modelActionInFlight = false;
        await pollStatus();
      }
    }

    async function activateSelectedModel(modelId) {
      if (!modelId) return;
      if (appState.modelActionInFlight) return;
      appState.modelActionInFlight = true;
      try {
        const { res, body } = await postJson("/internal/models/activate", { model_id: modelId });
        if (!res.ok) {
          appendMessage("assistant", `Could not activate model (${body?.reason || res.status}).`);
          return;
        }
        setComposerActivity("Switching active model...");
      } catch (err) {
        appendMessage("assistant", `Could not activate model: ${err}`);
      } finally {
        appState.modelActionInFlight = false;
        await pollStatus();
      }
    }

    async function moveModelToSsd(modelId) {
      if (!modelId) return;
      if (appState.modelActionInFlight) return;
      const targetModel = findModelInLatestStatus(modelId);
      const targetName = String(targetModel?.filename || "this model");
      const targetLabel = String(appState.latestStatus?.storage_targets?.ssd?.label || "attached SSD");
      const confirmed = window.confirm(`Move ${targetName} onto ${targetLabel} now?`);
      if (!confirmed) return;
      appState.modelActionInFlight = true;
      try {
        const { res, body } = await postJson("/internal/models/move-to-ssd", { model_id: modelId });
        if (!res.ok) {
          appendMessage("assistant", `Could not move model to SSD (${body?.reason || res.status}).`);
          return;
        }
        setComposerActivity(`${targetName} moved to SSD.`);
      } catch (err) {
        appendMessage("assistant", `Could not move model to SSD: ${err}`);
      } finally {
        appState.modelActionInFlight = false;
        await pollStatus();
      }
    }

    async function deleteSelectedModel(modelId) {
      if (!modelId) return;
      if (appState.modelActionInFlight) return;
      const targetModel = findModelInLatestStatus(modelId);
      const targetName = String(targetModel?.filename || "this model");
      const isDownloading = targetModel?.status === "downloading";
      const confirmMessage = isDownloading
        ? `Cancel the download for ${targetName} and delete any partially downloaded data?`
        : `Delete ${targetName} and remove it from the model list?`;
      const confirmed = window.confirm(confirmMessage);
      if (!confirmed) return;
      appState.modelActionInFlight = true;
      try {
        const { res, body } = await postJson("/internal/models/delete", { model_id: modelId });
        if (!res.ok) {
          appendMessage("assistant", `Could not delete model (${body?.reason || res.status}).`);
          return;
        }
      } catch (err) {
        appendMessage("assistant", `Could not delete model: ${err}`);
      } finally {
        appState.modelActionInFlight = false;
        await pollStatus();
      }
    }

    async function purgeAllModels() {
      if (appState.modelActionInFlight) return;
      const confirmed = window.confirm(
        "Delete ALL model files and clear model/download metadata now?"
      );
      if (!confirmed) return;
      appState.modelActionInFlight = true;
      try {
        const { res, body } = await postJson("/internal/models/purge", { reset_bootstrap_flag: false });
        if (!res.ok || body?.purged !== true) {
          appendMessage("assistant", `Could not purge models (${body?.reason || res.status}).`);
          return;
        }
        setComposerActivity("All models and metadata were cleared.");
      } catch (err) {
        appendMessage("assistant", `Could not purge models: ${err}`);
      } finally {
        appState.modelActionInFlight = false;
        await pollStatus();
      }
    }

    async function uploadLocalModel() {
      if (appState.uploadRequest) return;
      const input = document.getElementById("modelUploadInput");
      const file = input?.files?.[0];
      if (!file) {
        appendMessage("assistant", "Pick a .gguf file to upload.");
        return;
      }
      if (!String(file.name || "").toLowerCase().endsWith(".gguf")) {
        appendMessage("assistant", "Only .gguf model files are supported.");
        return;
      }

      const xhr = new XMLHttpRequest();
      appState.uploadRequest = xhr;
      const cancelBtn = document.getElementById("cancelUploadBtn");
      if (cancelBtn) cancelBtn.hidden = false;
      setModelUploadStatus("Uploading model... 0%");

      xhr.open("POST", "/internal/models/upload");
      xhr.setRequestHeader("x-potato-filename", file.name);
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && event.total > 0) {
          const percent = Math.round((event.loaded * 100) / event.total);
          setModelUploadStatus(`Uploading model... ${percent}% (${formatBytes(event.loaded)} / ${formatBytes(event.total)})`);
        } else {
          setModelUploadStatus("Uploading model...");
        }
      };
      xhr.onerror = async () => {
        appState.uploadRequest = null;
        if (cancelBtn) cancelBtn.hidden = true;
        setModelUploadStatus("Upload failed.");
        await pollStatus();
      };
      xhr.onabort = async () => {
        appState.uploadRequest = null;
        if (cancelBtn) cancelBtn.hidden = true;
        setModelUploadStatus("Upload cancelled.");
        await postJson("/internal/models/cancel-upload", {});
        await pollStatus();
      };
      xhr.onload = async () => {
        appState.uploadRequest = null;
        if (cancelBtn) cancelBtn.hidden = true;
        const body = (() => {
          try {
            return JSON.parse(xhr.responseText || "{}");
          } catch (_err) {
            return {};
          }
        })();
        if (xhr.status < 200 || xhr.status >= 300) {
          setModelUploadStatus(`Upload failed (${body?.reason || xhr.status}).`);
        } else if (body?.uploaded) {
          if (input) input.value = "";
          setModelUploadStatus("Upload completed.");
        } else {
          setModelUploadStatus(`Upload did not complete (${body?.reason || "unknown"}).`);
        }
        await pollStatus();
      };
      xhr.send(file);
    }

    function cancelLocalModelUpload() {
      if (!appState.uploadRequest) return;
      appState.uploadRequest.abort();
    }

    async function startModelDownload() {
      if (appState.downloadStartInFlight) return;
      appState.downloadStartInFlight = true;
      renderDownloadPrompt(appState.latestStatus || { download: { auto_start_remaining_seconds: 0 } });
      try {
        const resumableFailedModel = findResumableFailedModel(appState.latestStatus);
        const failedDownload = String(appState.latestStatus?.download?.error || "") === "download_failed";
        let res;
        let body;
        if (resumableFailedModel && failedDownload) {
          ({ res, body } = await postJson("/internal/models/download", { model_id: resumableFailedModel.id }));
        } else {
          res = await fetch("/internal/start-model-download", {
            method: "POST",
            headers: { "content-type": "application/json" },
          });
          body = await res.json().catch(() => ({}));
        }
        if (!res.ok) {
          const reason = body?.reason ? ` (${body.reason})` : "";
          appendMessage(
            "assistant",
            `${resumableFailedModel && failedDownload ? "Could not resume model download" : "Could not start model download"}${reason}.`
          );
          return;
        }
        if (!body?.started && body?.reason === "already_running") {
          setComposerActivity("Model download already running.");
        } else if (!body?.started && body?.reason === "model_present") {
          setComposerActivity("Model already present.");
        } else if (!body?.started && body?.reason === "insufficient_storage") {
          setComposerActivity("Model likely too large for free storage. Delete files and retry.");
        } else if (body?.started) {
          setComposerActivity(resumableFailedModel && failedDownload ? "Model download resumed." : "Model download started.");
        }
      } catch (err) {
        appendMessage(
          "assistant",
          `Could not ${String(appState.latestStatus?.download?.error || "") === "download_failed" ? "resume" : "start"} model download: ${err}`
        );
      } finally {
        appState.downloadStartInFlight = false;
        await pollStatus();
      }
    }

    function setRuntimeResetButtonState(inFlight) {
      const btn = document.getElementById("resetRuntimeBtn");
      if (!btn) return;
      btn.disabled = Boolean(inFlight);
      btn.textContent = inFlight
        ? "Restarting runtime..."
        : "Unload model + clean memory + restart";
    }

    function stopRuntimeReconnectWatch() {
      if (appState.runtimeReconnectWatchTimer) {
        window.clearTimeout(appState.runtimeReconnectWatchTimer);
        appState.runtimeReconnectWatchTimer = null;
      }
      appState.runtimeReconnectWatchActive = false;
      appState.runtimeReconnectAttempts = 0;
    }

    async function stepRuntimeReconnectWatch() {
      if (!appState.runtimeReconnectWatchActive) return;
      appState.runtimeReconnectAttempts += 1;
      const statusPayload = await pollStatus({ timeoutMs: RUNTIME_RECONNECT_TIMEOUT_MS });
      if (isLocalModelConnected(statusPayload)) {
        stopRuntimeReconnectWatch();
        setComposerActivity("Runtime reconnected.");
        window.setTimeout(() => {
          if (!appState.runtimeReconnectWatchActive && !appState.requestInFlight) {
            setComposerActivity("");
          }
        }, 1500);
        return;
      }
      if (appState.runtimeReconnectAttempts >= RUNTIME_RECONNECT_MAX_ATTEMPTS) {
        stopRuntimeReconnectWatch();
        setComposerActivity("");
        appendMessage(
          "assistant",
          "Runtime reset is taking longer than expected. It may still be loading the model. " +
          "Check status in a few moments."
        );
        return;
      }
      appState.runtimeReconnectWatchTimer = window.setTimeout(stepRuntimeReconnectWatch, RUNTIME_RECONNECT_INTERVAL_MS);
    }

    function startRuntimeReconnectWatch() {
      stopRuntimeReconnectWatch();
      appState.runtimeReconnectWatchActive = true;
      appState.runtimeReconnectAttempts = 0;
      setComposerActivity("Runtime reset in progress. Reconnecting...");
      stepRuntimeReconnectWatch();
    }

    async function resetRuntimeHeavy() {
      if (appState.runtimeResetInFlight) return;
      const confirmed = window.confirm(
        "Unload the model, reclaim memory/swap, and restart Potato runtime now? " +
        "The chat will disconnect briefly."
      );
      if (!confirmed) return;

      appState.runtimeResetInFlight = true;
      let shouldTrackReconnect = false;
      setRuntimeResetButtonState(true);
      setComposerActivity("Scheduling runtime reset...");
      try {
        const res = await fetch("/internal/reset-runtime", {
          method: "POST",
          headers: { "content-type": "application/json" },
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          const reason = body?.reason ? ` (${body.reason})` : "";
          appendMessage("assistant", `Could not start runtime reset${reason}.`);
          return;
        }
        if (body?.started) {
          shouldTrackReconnect = true;
          appendMessage(
            "assistant",
            "Runtime reset started. Unloading model from memory and reclaiming RAM/swap. " +
            "Model files on disk are unchanged."
          );
        } else {
          appendMessage("assistant", `Runtime reset did not start (${body?.reason || "unknown"}).`);
        }
      } catch (err) {
        appendMessage("assistant", `Could not start runtime reset: ${err}`);
      } finally {
        appState.runtimeResetInFlight = false;
        setRuntimeResetButtonState(false);
        if (shouldTrackReconnect) {
          startRuntimeReconnectWatch();
        } else {
          setComposerActivity("");
          window.setTimeout(() => {
            pollStatus();
          }, 1000);
        }
      }
    }

    function consumeSseDeltas(state, chunkText) {
      if (!chunkText) return { deltas: [], reasoningDeltas: [], events: [] };
      state.buffer += chunkText.replace(/\r\n/g, "\n");
      const deltas = [];
      const reasoningDeltas = [];
      const events = [];

      while (true) {
        const boundary = state.buffer.indexOf("\n\n");
        if (boundary === -1) break;

        const eventBlock = state.buffer.slice(0, boundary);
        state.buffer = state.buffer.slice(boundary + 2);

        const dataPayload = eventBlock
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n")
          .trim();

        if (!dataPayload || dataPayload === "[DONE]") continue;

        try {
          const event = JSON.parse(dataPayload);
          events.push(event);
          const delta = event?.choices?.[0]?.delta?.content;
          if (typeof delta === "string") {
            deltas.push(delta);
          }
          const reasoningDelta = event?.choices?.[0]?.delta?.reasoning_content;
          if (typeof reasoningDelta === "string") {
            reasoningDeltas.push(reasoningDelta);
          }
        } catch (_err) {
          // Ignore partial/non-JSON events and continue.
        }
      }

      return { deltas, reasoningDeltas, events };
    }

    function formatReasoningOnlyMessage(reasoningText) {
      const text = String(reasoningText || "").trim();
      if (!text) return "(empty response)";
      return `Thinking...\n\n${text}`;
    }

    function formatStopReason(reason) {
      switch (reason) {
        case "stop":
          return "EOS Token found";
        case "length":
          return "Max tokens reached";
        case "tool_calls":
          return "Tool calls emitted";
        case "cancelled":
          return "Stopped by user";
        case null:
        case undefined:
        case "":
          return "Unknown";
        default:
          return String(reason);
      }
    }

    function resolveTimeToFirstTokenMs(source, fallbackMs = 0) {
      const direct = Number(
        source?.timings?.ttft_ms
        ?? source?.timings?.first_token_ms
        ?? source?.timings?.prompt_ms
      );
      if (Number.isFinite(direct) && direct > 0) {
        return direct;
      }
      const fallback = Number(fallbackMs);
      if (Number.isFinite(fallback) && fallback > 0) {
        return fallback;
      }
      return 0;
    }

    function formatAssistantStats(source, elapsedSeconds = 0, firstTokenLatencyMs = 0) {
      const timings = source?.timings && typeof source.timings === "object" ? source.timings : {};
      const usage = source?.usage && typeof source.usage === "object" ? source.usage : {};
      const rawTokens = Number(timings.predicted_n ?? usage.completion_tokens ?? 0);
      const tokens = Number.isFinite(rawTokens) && rawTokens > 0 ? Math.round(rawTokens) : 0;

      const predictedMs = Number(timings.predicted_ms);
      const seconds = Number.isFinite(predictedMs) && predictedMs > 0
        ? predictedMs / 1000
        : Math.max(0, Number(elapsedSeconds) || 0);

      let tokPerSecond = Number(timings.predicted_per_second);
      if (!Number.isFinite(tokPerSecond) || tokPerSecond < 0) {
        tokPerSecond = seconds > 0 ? tokens / seconds : 0;
      }

      const finishReason = source?.finish_reason ?? source?.choices?.[0]?.finish_reason ?? null;
      const ttftMs = resolveTimeToFirstTokenMs(source, firstTokenLatencyMs);
      const ttftText = ttftMs > 0 ? `${(ttftMs / 1000).toFixed(2)}s` : "--";
      return `TTFT ${ttftText} · ${tokPerSecond.toFixed(2)} tok/sec · ${tokens} tokens · ${seconds.toFixed(2)}s · Stop reason: ${formatStopReason(finishReason)}`;
    }

    function classifyPi5MemoryTier(totalBytes) {
      const value = Number(totalBytes);
      if (!Number.isFinite(value) || value <= 0) return null;
      const gib = value / (1024 ** 3);
      const supportedTiers = [1, 2, 4, 8, 16];
      let bestTier = supportedTiers[0];
      let bestDistance = Math.abs(gib - bestTier);
      for (const tier of supportedTiers.slice(1)) {
        const distance = Math.abs(gib - tier);
        if (distance < bestDistance) {
          bestTier = tier;
          bestDistance = distance;
        }
      }
      return `${bestTier}GB`;
    }

    function setSidebarNote(systemPayload) {
      const noteEl = document.getElementById("sidebarNote");
      if (!noteEl) return;
      const piModelName = String(systemPayload?.pi_model_name || "").trim();
      const memoryTier = classifyPi5MemoryTier(systemPayload?.memory_total_bytes);
      if (piModelName && memoryTier) {
        noteEl.textContent = `V0.3 Pre-Alpha · ${piModelName} · ${memoryTier}`;
        return;
      }
      noteEl.textContent = piModelName
        ? `V0.3 Pre-Alpha · ${piModelName}`
        : "V0.3 Pre-Alpha";
    }

    function setStatus(statusPayload) {
      appState.latestStatus = statusPayload;
      const downloadText = formatSidebarStatusDetail(statusPayload);
      const text = `State: ${statusPayload.state} | ${downloadText}`;
      document.getElementById("statusText").textContent = text;
      renderStatusActions(statusPayload);
      setSidebarNote(statusPayload?.system);
      const modelNameField = document.getElementById("modelName");
      if (modelNameField) {
        const modelName = statusPayload?.model?.filename || "Unknown model";
        modelNameField.textContent = statusPayload?.model_present ? modelName : `${modelName} (not loaded)`;
      }
      const countdownSelect = document.getElementById("downloadCountdownEnabled");
      if (countdownSelect) {
        countdownSelect.value = statusPayload?.download?.countdown_enabled === false ? "false" : "true";
      }
      updateLlamaIndicator(statusPayload);
      if (appState.modelSwitcherOpen) populateModelSwitcher();
      renderDownloadPrompt(statusPayload);
      renderCompatibilityWarnings(statusPayload);
      renderLlamaRuntimeStatus(statusPayload);
      renderSystemRuntime(statusPayload?.system);
      renderSettingsWorkspace(statusPayload);
      renderUploadState(statusPayload);
      setSendEnabled();
    }

    async function pollStatus(options = {}) {
      const timeoutMs = Math.max(500, Number(options?.timeoutMs || STATUS_POLL_TIMEOUT_MS));
      const seq = ++appState.statusPollSeq;
      const controller = new AbortController();
      const timeoutHandle = window.setTimeout(() => {
        controller.abort();
      }, timeoutMs);
      try {
        const res = await fetch("/status", { cache: "no-store", signal: controller.signal });
        const body = await res.json();
        if (seq < appState.statusPollAppliedSeq) {
          return appState.latestStatus;
        }
        appState.statusPollAppliedSeq = seq;
        setStatus(body);
        return body;
      } catch (err) {
        if (seq < appState.statusPollAppliedSeq) {
          return appState.latestStatus;
        }
        appState.statusPollAppliedSeq = seq;
        const statusErrText = err?.name === "AbortError" ? "request timeout" : String(err);
        if (appState.latestStatus && typeof appState.latestStatus === "object" && appState.latestStatus.state && appState.latestStatus.state !== "DOWN") {
          document.getElementById("statusText").textContent = `Status warning: ${statusErrText}`;
          renderStatusActions({});
          return appState.latestStatus;
        }
        appState.latestStatus = {
          state: "DOWN",
          model_present: false,
          model: { filename: "Unknown model", active_model_id: null },
          models: [],
          download: {
            percent: 0,
            bytes_downloaded: 0,
            bytes_total: 0,
            active: false,
            auto_start_seconds: 0,
            auto_start_remaining_seconds: 0,
            countdown_enabled: true,
            current_model_id: null,
          },
          upload: {
            active: false,
            model_id: null,
            bytes_total: 0,
            bytes_received: 0,
            percent: 0,
            error: null,
          },
          compatibility: {
            device_class: "unknown",
            large_model_warn_threshold_bytes: 0,
            warnings: [],
          },
          llama_runtime: {
            current: {
              install_dir: "",
              exists: false,
              has_server_binary: false,
              source_bundle_path: null,
              source_bundle_name: null,
              profile: null,
            },
            available_bundles: [],
            switch: {
              active: false,
              target_bundle_path: null,
              error: null,
            },
          },
          system: {
            available: false,
            cpu_percent: null,
            cpu_cores_percent: [],
            cpu_clock_arm_hz: null,
            memory_total_bytes: 0,
            memory_used_bytes: 0,
            memory_percent: null,
            swap_total_bytes: 0,
            swap_used_bytes: 0,
            swap_percent: null,
            temperature_c: null,
            gpu_clock_core_hz: null,
            gpu_clock_v3d_hz: null,
            updated_at_unix: null,
            throttling: { any_current: false, current_flags: [], history_flags: [] },
          },
        };
        document.getElementById("statusText").textContent = `Status error: ${statusErrText}`;
        renderStatusActions({});
        const modelNameField = document.getElementById("modelName");
        if (modelNameField) {
          modelNameField.textContent = "Unknown model (status unavailable)";
        }
        updateLlamaIndicator(appState.latestStatus);
        renderDownloadPrompt(appState.latestStatus);
        renderCompatibilityWarnings(appState.latestStatus);
        renderSystemRuntime(appState.latestStatus.system);
        renderSettingsWorkspace(appState.latestStatus);
        renderUploadState(appState.latestStatus);
        setSendEnabled();
        return appState.latestStatus;
      } finally {
        window.clearTimeout(timeoutHandle);
      }
    }

    async function sendChat() {
      if (appState.requestInFlight) return;
      if (appState.imageCancelRecoveryTimer) {
        window.clearTimeout(appState.imageCancelRecoveryTimer);
        appState.imageCancelRecoveryTimer = null;
      }
      if (appState.imageCancelRestartTimer) {
        window.clearTimeout(appState.imageCancelRestartTimer);
        appState.imageCancelRestartTimer = null;
      }
      const userPrompt = document.getElementById("userPrompt");
      if (appState.pendingImage && activeRuntimeVisionCapability(appState.latestStatus) === false) {
        clearPendingImage();
        showTextOnlyImageBlockedState(appState.latestStatus);
        return;
      }
      const content = userPrompt.value.trim();
      if (!content && !appState.pendingImage) return;
      const hasImageRequest = Boolean(appState.pendingImage);
      const selectedImageSize = appState.pendingImage ? (Number(appState.pendingImage.size) || 0) : 0;
      const userMessage = { role: "user", content: buildUserMessageContent(content) };
      const userBubblePayload = buildUserBubblePayload(content);
      const requestStartMs = performance.now();
      const requestCtx = {
        controller: new AbortController(),
        stoppedByUser: false,
        hasImageRequest,
        prefillBucket: "",
        firstTokenLatencyMs: 0,
        generationStarted: false,
      };
      const streamStats = { timings: null, finish_reason: null };
      let activeAssistantView = null;
      appState.activeRequest = requestCtx;

      const settings = collectSettings();

      const baseHistoryLength = appState.chatHistory.length;
      const userView = appendMessage("user", userBubblePayload.text, {
        imageDataUrl: userBubblePayload.imageDataUrl,
        imageName: userBubblePayload.imageName,
        forceFollow: true,
      });
      activeAssistantView = appendMessage("assistant", "", { actionsHidden: true, forceFollow: true });
      const turn = {
        baseHistoryLength,
        userText: content,
        userView,
        assistantView: activeAssistantView,
      };
      userView.turnRef = turn;
      activeAssistantView.turnRef = turn;
      appState.conversationTurns.push(turn);
      requestCtx.turn = turn;
      requestCtx.assistantView = activeAssistantView;
      userPrompt.value = "";
      clearPendingImage();
      focusPromptInput();
      appState.requestInFlight = true;
      setSendEnabled();
      setCancelEnabled(true);

      try {
        const reqBody = {
          model: "qwen-local",
          messages: [],
          temperature: settings.temperature,
          top_p: settings.top_p,
          top_k: settings.top_k,
          repetition_penalty: settings.repetition_penalty,
          presence_penalty: settings.presence_penalty,
          max_tokens: settings.max_tokens,
          stream: settings.stream,
        };
        const resolvedSeed = resolveSeedForRequest(settings);
        if (resolvedSeed !== null) {
          reqBody.seed = resolvedSeed;
        }

        if (settings.system_prompt) {
          reqBody.messages.push({ role: "system", content: settings.system_prompt });
        }
        reqBody.messages = reqBody.messages.concat(appState.chatHistory);
        reqBody.messages.push(userMessage);
        appState.chatHistory.push(userMessage);

        const promptTokens = estimatePromptTokens(reqBody.messages);
        requestCtx.prefillBucket = choosePrefillBucket(hasImageRequest, promptTokens, selectedImageSize);
        beginPrefillProgress(requestCtx, {
          hasImage: hasImageRequest,
          promptTokens,
          imageBytes: selectedImageSize,
          bucket: requestCtx.prefillBucket,
        });

        const res = await fetch("/v1/chat/completions", {
          method: "POST",
          headers: { "content-type": "application/json" },
          signal: requestCtx.controller.signal,
          body: JSON.stringify(reqBody),
        });

        if (!res.ok) {
          stopPrefillProgress();
          const body = await res.json().catch(() => ({}));
          updateMessage(activeAssistantView, formatChatFailureMessage(res.status, body, requestCtx), { showActions: true });
          return;
        }

        if (settings.stream) {
          const reader = res.body.getReader();
          const decoder = new TextDecoder();
          const state = { buffer: "" };
          let assistantText = "";
          let assistantReasoningText = "";

          while (true) {
            const { done, value } = await reader.read();
            if (done) {
              const decoded = consumeSseDeltas(state, decoder.decode());
              for (const event of decoded.events) {
                const stop = event?.choices?.[0]?.finish_reason;
                if (stop !== null && stop !== undefined) {
                  streamStats.finish_reason = stop;
                }
                if (event?.timings && typeof event.timings === "object") {
                  streamStats.timings = event.timings;
                }
              }
              break;
            }

            const textChunk = decoder.decode(value, { stream: true });
            const parsed = consumeSseDeltas(state, textChunk);
            for (const delta of parsed.deltas) {
              if (!requestCtx.generationStarted) {
                requestCtx.generationStarted = true;
                requestCtx.firstTokenLatencyMs = Math.max(0, performance.now() - requestStartMs);
                const finishResult = await markPrefillGenerationStarted(requestCtx);
                throwIfRequestStoppedAfterPrefill(requestCtx, finishResult);
              }
              assistantText += delta;
              updateMessage(activeAssistantView, assistantText, { showActions: false });
            }
            for (const reasoningDelta of parsed.reasoningDeltas) {
              assistantReasoningText += reasoningDelta;
              if (!assistantText.trim()) {
                updateMessage(activeAssistantView, formatReasoningOnlyMessage(assistantReasoningText), { showActions: false });
              }
            }
            for (const event of parsed.events) {
              const stop = event?.choices?.[0]?.finish_reason;
              if (stop !== null && stop !== undefined) {
                streamStats.finish_reason = stop;
              }
              if (event?.timings && typeof event.timings === "object") {
                streamStats.timings = event.timings;
              }
            }
          }

          const tailParsed = consumeSseDeltas(state, "\n\n");
          for (const delta of tailParsed.deltas) {
            assistantText += delta;
          }
          for (const reasoningDelta of tailParsed.reasoningDeltas) {
            assistantReasoningText += reasoningDelta;
          }
          for (const event of tailParsed.events) {
            const stop = event?.choices?.[0]?.finish_reason;
            if (stop !== null && stop !== undefined) {
              streamStats.finish_reason = stop;
            }
            if (event?.timings && typeof event.timings === "object") {
              streamStats.timings = event.timings;
            }
          }
          if (!requestCtx.generationStarted) {
            requestCtx.generationStarted = true;
            requestCtx.firstTokenLatencyMs = Math.max(0, performance.now() - requestStartMs);
            const finishResult = await markPrefillGenerationStarted(requestCtx);
            throwIfRequestStoppedAfterPrefill(requestCtx, finishResult);
          }
          const finalAssistantText = assistantText.trim() || formatReasoningOnlyMessage(assistantReasoningText);
          updateMessage(activeAssistantView, finalAssistantText, { showActions: true });
          appState.chatHistory.push({ role: "assistant", content: finalAssistantText });
          const elapsedSeconds = Math.max(0, (performance.now() - requestStartMs) / 1000);
          if (requestCtx.stoppedByUser) {
            streamStats.finish_reason = "cancelled";
          }
          setMessageMeta(activeAssistantView, formatAssistantStats(streamStats, elapsedSeconds, requestCtx.firstTokenLatencyMs));
          recordPrefillMetric(
            requestCtx.prefillBucket,
            resolvePromptPrefillMs(streamStats, requestCtx.firstTokenLatencyMs),
          );
          return;
        }

        const body = await res.json();
        if (!requestCtx.generationStarted) {
          requestCtx.generationStarted = true;
          requestCtx.firstTokenLatencyMs = Math.max(0, performance.now() - requestStartMs);
          const finishResult = await markPrefillGenerationStarted(requestCtx);
          throwIfRequestStoppedAfterPrefill(requestCtx, finishResult);
        }
        const message = body?.choices?.[0]?.message || {};
        const messageContent = typeof message?.content === "string" ? message.content.trim() : "";
        const msg = messageContent || formatReasoningOnlyMessage(message?.reasoning_content) || JSON.stringify(body);
        appState.chatHistory.push({ role: "assistant", content: msg });
        updateMessage(activeAssistantView, msg, { showActions: true });
        const elapsedSeconds = Math.max(0, (performance.now() - requestStartMs) / 1000);
        setMessageMeta(activeAssistantView, formatAssistantStats(body, elapsedSeconds, requestCtx.firstTokenLatencyMs));
        recordPrefillMetric(
          requestCtx.prefillBucket,
          resolvePromptPrefillMs(body, requestCtx.firstTokenLatencyMs),
        );
      } catch (err) {
          if (requestCtx.stoppedByUser) {
            const elapsedSeconds = Math.max(0, (performance.now() - requestStartMs) / 1000);
          if (requestCtx.hideProcessingBubbleOnCancel === true) {
            return;
          }
          if (activeAssistantView) {
            const partial = activeAssistantView.bubble.textContent.trim();
            if (!partial) {
              updateMessage(activeAssistantView, "(stopped)", { showActions: true });
            } else {
              appState.chatHistory.push({ role: "assistant", content: partial });
            }
            streamStats.finish_reason = "cancelled";
            setMessageMeta(activeAssistantView, formatAssistantStats(streamStats, elapsedSeconds, requestCtx.firstTokenLatencyMs));
          } else {
            const stoppedDiv = appendMessage("assistant", "(stopped)");
            setMessageMeta(stoppedDiv, formatAssistantStats({ finish_reason: "cancelled" }, elapsedSeconds, requestCtx.firstTokenLatencyMs));
          }
        } else {
          if (activeAssistantView) {
            updateMessage(activeAssistantView, `Request error: ${err}`, { showActions: true });
          } else {
            appendMessage("assistant", `Request error: ${err}`);
          }
        }
      } finally {
        if (appState.chatHistory.length > 0) {
          try { await saveActiveSession(); } catch (_e) { /* IndexedDB write failed — degrade gracefully */ }
        }
        appState.requestInFlight = false;
        appState.activeRequest = null;
        setSendEnabled();
        stopPrefillProgress();
        setComposerActivity("");
        setCancelEnabled(false);
        focusPromptInput();
      }
    }

    function stopGeneration() {
      if (!appState.requestInFlight || !appState.activeRequest) return;
      appState.activeRequest.stoppedByUser = true;
      appState.activeRequest.controller.abort();
    }

    async function requestLlamaCancelRecovery(reason = "cancelled") {
      try {
        const res = await fetch("/internal/cancel-llama", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ reason }),
        });
        if (!res.ok) return { cancelled: false, restarted: false };
        return await res.json().catch(() => ({ cancelled: false, restarted: false }));
      } catch (_err) {
        // Best-effort recovery only.
        return { cancelled: false, restarted: false };
      }
    }

    async function requestLlamaRestart(reason = "cancelled") {
      try {
        const res = await fetch("/internal/restart-llama", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ reason }),
        });
        if (!res.ok) return { restarted: false };
        return await res.json().catch(() => ({ restarted: false }));
      } catch (_err) {
        return { restarted: false };
      }
    }

    async function checkLlamaHealthStrict() {
      try {
        const res = await fetch("/internal/llama-healthz", { cache: "no-store" });
        if (!res.ok) return false;
        const payload = await res.json().catch(() => ({}));
        return payload?.healthy === true;
      } catch (_err) {
        return false;
      }
    }

    function scheduleImageCancelRestartFallback() {
      if (appState.imageCancelRestartTimer) {
        window.clearTimeout(appState.imageCancelRestartTimer);
      }
      appState.imageCancelRestartTimer = window.setTimeout(async () => {
        appState.imageCancelRestartTimer = null;
        if (appState.requestInFlight) {
          return;
        }
        const healthy = await checkLlamaHealthStrict();
        if (healthy) {
          return;
        }
        setComposerActivity("Restarting model after stalled cancel...");
        setComposerStatusChip("Restarting model...", { phase: "cancel" });
        await requestLlamaRestart("image_cancel_stalled");
        await pollStatus();
        setComposerActivity("");
        hideComposerStatusChip();
      }, IMAGE_CANCEL_RESTART_DELAY_MS);
    }

    function queueImageCancelRecovery(requestCtx) {
      if (!requestCtx?.hasImageRequest) {
        return;
      }
      if (appState.imageCancelRecoveryTimer) {
        window.clearTimeout(appState.imageCancelRecoveryTimer);
      }
      appState.imageCancelRecoveryTimer = window.setTimeout(async () => {
        appState.imageCancelRecoveryTimer = null;
        if (appState.requestInFlight) {
          return;
        }
        const healthy = await checkLlamaHealthStrict();
        if (healthy) {
          return;
        }
        setComposerActivity("Recovering model after image cancel...");
        setComposerStatusChip("Recovering model...", { phase: "cancel" });
        const recovery = await requestLlamaCancelRecovery("image_cancel_timeout");
        if (recovery?.cancelled) {
          setComposerActivity("");
          hideComposerStatusChip();
          return;
        }
        if (recovery?.restarted) {
          setComposerActivity("Restarting model...");
          await pollStatus();
          setComposerActivity("");
          hideComposerStatusChip();
          return;
        }
        setComposerActivity("Waiting for model to finish cancel...");
        setComposerStatusChip("Finalizing cancel...", { phase: "cancel" });
        scheduleImageCancelRestartFallback();
      }, IMAGE_CANCEL_RECOVERY_DELAY_MS);
    }

    function cancelCurrentWork() {
      if (appState.pendingImageReader) {
        cancelPendingImageWork();
        clearPendingImage();
        setComposerActivity("Image load cancelled.");
        hideComposerStatusChip();
        setCancelEnabled(false);
        return;
      }
      if (appState.requestInFlight) {
        const current = appState.activeRequest;
        stopPrefillProgress({ resetUi: false });
        setComposerActivity("Cancelling...");
        setComposerStatusChip("Cancelling...", { phase: "cancel" });
        setCancelEnabled(false);
        if (current && current.assistantView?.bubble?.classList?.contains("processing")) {
          current.hideProcessingBubbleOnCancel = true;
          removeMessage(current.assistantView);
        }
        stopGeneration();
        queueImageCancelRecovery(current);
      }
    }

    function toggleTheme() {
      const current = document.documentElement.getAttribute("data-theme") || defaultSettings.theme;
      const next = current === "dark" ? "light" : "dark";
      applyTheme(next);
      saveSettings({ theme: next });
    }

    bindSettings();
    bindSettingsModal();
    bindEditModal();
    bindMobileSidebar();
    bindMessagesScroller();
    setRuntimeDetailsExpanded(true);
    registerAppendMessage(appendMessage);
    registerOpenEditMessageModal(openEditMessageModal);
    initSessionManager().catch(() => {});
    setInterval(() => {
      if (appState.settingsModalOpen) return;
      pollStatus();
    }, 2000);
    pollStatus();

    document.getElementById("statusBadge").addEventListener("click", (event) => {
      event.stopPropagation();
      toggleModelSwitcher();
    });
    document.getElementById("statusBadge").addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        event.stopPropagation();
        toggleModelSwitcher();
      }
    });
    function activateSwitcherItem(item) {
      if (!item) return;
      if (item.classList.contains("disabled")) return;
      if (item.classList.contains("active")) {
        closeModelSwitcher();
        return;
      }
      const modelId = item.dataset.modelId;
      if (modelId) {
        closeModelSwitcher();
        activateSelectedModel(modelId);
      }
    }
    document.getElementById("modelSwitcherList").addEventListener("click", (event) => {
      activateSwitcherItem(event.target.closest(".model-switcher-item"));
    });
    document.getElementById("modelSwitcher").addEventListener("keydown", (event) => {
      const list = document.getElementById("modelSwitcherList");
      if (!list) return;
      const items = Array.from(list.querySelectorAll(".model-switcher-item"));
      if (items.length === 0) return;
      const focused = list.querySelector(".model-switcher-item.focused");
      const idx = focused ? items.indexOf(focused) : -1;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const next = event.key === "ArrowDown"
          ? (idx + 1) % items.length
          : (idx - 1 + items.length) % items.length;
        if (focused) focused.classList.remove("focused");
        items[next].classList.add("focused");
        items[next].scrollIntoView({ block: "nearest" });
      } else if (event.key === "Enter") {
        event.preventDefault();
        if (focused) activateSwitcherItem(focused);
      }
    });
    document.addEventListener("click", (event) => {
      if (!appState.modelSwitcherOpen) return;
      const anchor = document.querySelector(".model-switcher-anchor");
      if (anchor && !anchor.contains(event.target)) {
        closeModelSwitcher();
      }
    });
    document.getElementById("newChatBtn").addEventListener("click", () => startNewChat());
    document.getElementById("chatSessionList").addEventListener("click", (event) => {
      const del = event.target.closest(".chat-session-delete");
      if (del) {
        const item = del.closest(".chat-session-item");
        if (item?.dataset?.sessionId) deleteSession(item.dataset.sessionId);
        return;
      }
      const item = event.target.closest(".chat-session-item");
      if (item?.dataset?.sessionId && item.dataset.sessionId !== appState.activeSessionId) {
        loadSessionIntoView(item.dataset.sessionId);
        if (isMobileSidebarViewport()) setSidebarOpen(false);
      }
    });
    document.getElementById("themeToggle").addEventListener("click", toggleTheme);
    document.getElementById("sidebarToggle").addEventListener("click", () => {
      setSidebarOpen(!document.body.classList.contains("sidebar-open"));
    });
    document.getElementById("sidebarCloseBtn").addEventListener("click", () => {
      setSidebarOpen(false);
    });
    document.getElementById("sidebarBackdrop").addEventListener("click", () => {
      setSidebarOpen(false);
    });
    document.getElementById("runtimeViewToggle").addEventListener("click", () => {
      setRuntimeDetailsExpanded(!appState.runtimeDetailsExpanded);
    });
    document.getElementById("startDownloadBtn").addEventListener("click", startModelDownload);
    document.getElementById("statusResumeDownloadBtn").addEventListener("click", startModelDownload);
    document.getElementById("registerModelBtn").addEventListener("click", registerModelFromUrl);
    document.getElementById("uploadModelBtn").addEventListener("click", uploadLocalModel);
    document.getElementById("cancelUploadBtn").addEventListener("click", cancelLocalModelUpload);
    document.getElementById("purgeModelsBtn").addEventListener("click", purgeAllModels);
    document.getElementById("applyLargeModelOverrideBtn").addEventListener("click", applyLargeModelOverrideFromSettings);
    document.getElementById("compatibilityOverrideBtn").addEventListener("click", allowUnsupportedLargeModelFromWarning);
    document.getElementById("applyLlamaMemoryLoadingBtn").addEventListener("click", applyLlamaMemoryLoadingMode);
    document.getElementById("switchLlamaRuntimeBtn").addEventListener("click", switchLlamaRuntimeBundle);
    document.getElementById("capturePowerCalibrationSampleBtn").addEventListener("click", capturePowerCalibrationSample);
    document.getElementById("fitPowerCalibrationBtn").addEventListener("click", fitPowerCalibrationModel);
    document.getElementById("resetPowerCalibrationBtn").addEventListener("click", resetPowerCalibrationModel);
    document.getElementById("modelsList").addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const action = target.dataset?.action;
      const row = target.closest(".model-row");
      const modelId = row?.dataset?.modelId;
      const selectedModel = resolveSelectedSettingsModel(appState.latestStatus);
      const selectedModelId = String(selectedModel?.id || "");
      const targetDiffers = Boolean(modelId) && String(modelId) !== selectedModelId;
      if (targetDiffers && selectedModelHasUnsavedChanges()) {
        blockModelSelectionChange();
        return;
      }
      if (!action) {
        if (modelId) {
          appState.selectedSettingsModelId = String(modelId);
          renderSettingsWorkspace(appState.latestStatus);
        }
        return;
      }
      if (action === "download") {
        startModelDownloadForModel(modelId);
      } else if (action === "cancel-download") {
        cancelActiveModelDownload(modelId);
      } else if (action === "activate") {
        activateSelectedModel(modelId);
      } else if (action === "move-to-ssd") {
        moveModelToSsd(modelId);
      } else if (action === "delete") {
        deleteSelectedModel(modelId);
      }
    });
    document.getElementById("resetRuntimeBtn").addEventListener("click", resetRuntimeHeavy);
    document.getElementById("attachImageBtn").addEventListener("click", openImagePicker);
    document.getElementById("cancelBtn").addEventListener("click", cancelCurrentWork);
    document.getElementById("clearImageBtn").addEventListener("click", (event) => {
      event.preventDefault();
      clearPendingImage();
    });
    document.getElementById("imageInput").addEventListener("change", (event) => {
      const file = event.target?.files?.[0] || null;
      handleImageSelected(file);
    });
    document.getElementById("sendBtn").addEventListener("click", (event) => {
      event.preventDefault();
      if (appState.requestInFlight) {
        cancelCurrentWork();
        return;
      }
      sendChat();
    });
    document.getElementById("composerForm").addEventListener("submit", (event) => {
      event.preventDefault();
      sendChat();
    });
    const userPrompt = document.getElementById("userPrompt");
    userPrompt.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendChat();
      }
    });

    // Expose select functions for Playwright test access
    window.appendMessage = appendMessage;
