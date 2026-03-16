from __future__ import annotations

from pathlib import Path

from app.main import CHAT_HTML, WEB_ASSETS_DIR

CHAT_CSS = (WEB_ASSETS_DIR / "chat.css").read_text(encoding="utf-8")
CHAT_JS = (WEB_ASSETS_DIR / "chat.js").read_text(encoding="utf-8")
CHAT_STATE_JS = (WEB_ASSETS_DIR / "state.js").read_text(encoding="utf-8") if (WEB_ASSETS_DIR / "state.js").exists() else ""
CHAT_UTILS_JS = (WEB_ASSETS_DIR / "utils.js").read_text(encoding="utf-8") if (WEB_ASSETS_DIR / "utils.js").exists() else ""
CHAT_SESSION_JS = (WEB_ASSETS_DIR / "session-manager.js").read_text(encoding="utf-8") if (WEB_ASSETS_DIR / "session-manager.js").exists() else ""
CHAT_STATUS_JS = (WEB_ASSETS_DIR / "status.js").read_text(encoding="utf-8") if (WEB_ASSETS_DIR / "status.js").exists() else ""
CHAT_RUNTIME_UI_JS = (WEB_ASSETS_DIR / "runtime-ui.js").read_text(encoding="utf-8") if (WEB_ASSETS_DIR / "runtime-ui.js").exists() else ""
CHAT_MESSAGES_JS = (WEB_ASSETS_DIR / "messages.js").read_text(encoding="utf-8") if (WEB_ASSETS_DIR / "messages.js").exists() else ""
CHAT_IMAGE_HANDLER_JS = (WEB_ASSETS_DIR / "image-handler.js").read_text(encoding="utf-8") if (WEB_ASSETS_DIR / "image-handler.js").exists() else ""
CHAT_SETTINGS_UI_JS = (WEB_ASSETS_DIR / "settings-ui.js").read_text(encoding="utf-8") if (WEB_ASSETS_DIR / "settings-ui.js").exists() else ""
CHAT_ENGINE_JS = (WEB_ASSETS_DIR / "chat-engine.js").read_text(encoding="utf-8") if (WEB_ASSETS_DIR / "chat-engine.js").exists() else ""
CHAT_UI = CHAT_HTML + CHAT_CSS + CHAT_JS + CHAT_STATE_JS + CHAT_UTILS_JS + CHAT_SESSION_JS + CHAT_STATUS_JS + CHAT_RUNTIME_UI_JS + CHAT_MESSAGES_JS + CHAT_IMAGE_HANDLER_JS + CHAT_SETTINGS_UI_JS + CHAT_ENGINE_JS


def test_chat_ui_streaming_parses_sse_and_ignores_done_marker():
    assert "function consumeSseDeltas" in CHAT_UI
    assert 'dataPayload === "[DONE]"' in CHAT_UI
    assert "event?.choices?.[0]?.delta?.content" in CHAT_UI
    assert "updateMessage(activeAssistantView, assistantText" in CHAT_UI
    assert 'renderMessage("assistant", output.trim())' not in CHAT_UI


def test_chat_ui_supports_theme_system_prompt_setting_and_enter_to_send():
    assert 'class="theme-toggle"' in CHAT_UI
    assert 'id="themeToggle"' in CHAT_UI
    assert "theme-icon--moon" in CHAT_UI
    assert "theme-icon--sun" in CHAT_UI
    assert "Switch to light theme" in CHAT_UI
    assert 'theme: "light"' in CHAT_UI
    assert "function detectSystemTheme(" in CHAT_UI
    assert 'window.matchMedia("(prefers-color-scheme: dark)")' in CHAT_UI
    assert 'return "dark";' in CHAT_UI
    assert 'return "light";' in CHAT_UI
    assert "theme: detectSystemTheme()" in CHAT_UI
    assert 'id="theme"' not in CHAT_UI
    assert "applyTheme(" in CHAT_UI
    assert 'id="systemPrompt"' in CHAT_UI
    assert "System Prompt (optional)" in CHAT_UI
    assert 'userPrompt.addEventListener("keydown"' in CHAT_UI
    assert 'event.key === "Enter"' in CHAT_UI
    assert "!event.shiftKey" in CHAT_UI


def test_chat_ui_seed_mode_settings_contract():
    assert 'id="generationMode"' in CHAT_UI
    assert 'class="settings-segmented" data-target="generationMode"' in CHAT_UI
    assert 'data-target="generationMode" data-value="random">Random</button>' in CHAT_UI
    assert 'data-target="generationMode" data-value="deterministic">Deterministic</button>' in CHAT_UI
    assert 'id="seed"' in CHAT_UI
    assert "generation_mode: \"random\"" in CHAT_UI
    assert "seed: 42" in CHAT_UI
    assert "function normalizeGenerationMode(" in CHAT_UI
    assert "function normalizeSeedValue(" in CHAT_UI
    assert "function updateSeedFieldState(" in CHAT_UI
    assert "function resolveSeedForRequest(" in CHAT_UI
    assert "seedField.disabled = generationMode !== \"deterministic\";" in CHAT_UI
    assert "#seed:disabled" in CHAT_UI
    assert "cursor: not-allowed;" in CHAT_UI
    assert "reqBody.seed = resolvedSeed;" in CHAT_UI


def test_chat_ui_keeps_theme_toggle_clear_of_status_badge():
    assert ".chat-header {" in CHAT_UI
    assert "padding: 2px 6px;" in CHAT_UI
    assert ".header-actions {" in CHAT_UI
    assert ".theme-toggle {" in CHAT_UI
    assert "position: static;" in CHAT_UI


def test_chat_ui_copy_and_stats_footnote_contract():
    assert 'id="sidebarNote"' in CHAT_UI
    assert "Pre-Alpha" in CHAT_UI
    assert "function classifyPi5MemoryTier(" in CHAT_UI
    assert "function setSidebarNote(" in CHAT_UI
    assert "statusPayload?.system" in CHAT_UI
    assert "Pre-Alpha · ${piModelName} · ${memoryTier}" in CHAT_UI
    assert "Potato OS is online. Ask anything to get started." not in CHAT_UI
    assert "Local-first chat frontend on your Pi." not in CHAT_UI
    assert "Local-first chat front end on your Pi." not in CHAT_UI
    assert "Press Enter to send. Shift+Enter adds a new line." not in CHAT_UI
    assert 'meta.className = "message-meta"' in CHAT_UI
    assert "function formatStopReason(" in CHAT_UI
    assert "function formatAssistantStats(" in CHAT_UI
    assert "tok/sec" in CHAT_UI
    assert "Stop reason:" in CHAT_UI
    assert "EOS Token found" in CHAT_UI


def test_chat_ui_runtime_details_hide_compact_and_apply_metric_threshold_classes():
    assert 'id="compatibilityWarnings"' in CHAT_UI
    assert 'id="compatibilityWarningsText"' in CHAT_UI
    assert 'id="compatibilityOverrideBtn"' in CHAT_UI
    assert "function renderCompatibilityWarnings(" in CHAT_UI
    assert "statusPayload?.compatibility?.warnings" in CHAT_UI
    assert "statusPayload?.compatibility?.override_enabled" in CHAT_UI
    assert 'id="runtimeCompact"' in CHAT_UI
    assert "compact.hidden = appState.runtimeDetailsExpanded;" in CHAT_UI
    assert 'toggle.textContent = appState.runtimeDetailsExpanded ? "Hide details" : "Show details";' in CHAT_UI
    assert "function runtimeMetricSeverityClass(" in CHAT_UI
    assert "runtime-metric-normal" in CHAT_UI
    assert "runtime-metric-warn" in CHAT_UI
    assert "runtime-metric-high" in CHAT_UI
    assert "runtime-metric-critical" in CHAT_UI
    assert "CPU_CLOCK_MAX_HZ_PI5" in CHAT_UI
    assert "GPU_CLOCK_MAX_HZ_PI5" in CHAT_UI
    assert "applyRuntimeMetricSeverity(memoryDetail, systemPayload?.memory_percent);" in CHAT_UI
    assert "applyRuntimeMetricSeverity(swapDetail, systemPayload?.swap_percent);" in CHAT_UI
    assert "applyRuntimeMetricSeverity(tempDetail, tempValue);" in CHAT_UI
    assert 'case "tool_calls"' in CHAT_UI
    assert "content_filter" not in CHAT_UI
    assert "function_call" not in CHAT_UI


def test_chat_ui_exposes_llama_runtime_family_switch_controls():
    assert "Llama Runtime" in CHAT_UI
    assert 'id="llamaRuntimeFamilySelect"' in CHAT_UI
    assert 'id="switchLlamaRuntimeBtn"' in CHAT_UI
    assert 'id="llamaRuntimeCurrent"' in CHAT_UI
    assert 'id="llamaRuntimeSwitchStatus"' in CHAT_UI
    assert "function renderLlamaRuntimeStatus(" in CHAT_UI
    assert "function switchLlamaRuntimeBundle(" in CHAT_UI
    assert "/internal/llama-runtime/switch" in CHAT_UI
    assert "statusPayload?.llama_runtime" in CHAT_UI


def test_chat_ui_exposes_llama_memory_loading_controls():
    assert "GGUF loading mode (requires runtime restart)" in CHAT_UI
    assert 'id="llamaMemoryLoadingMode"' in CHAT_UI
    assert 'id="applyLlamaMemoryLoadingBtn"' in CHAT_UI
    assert 'id="llamaMemoryLoadingStatus"' in CHAT_UI
    assert "function applyLlamaMemoryLoadingMode(" in CHAT_UI


def test_chat_ui_exposes_large_model_compatibility_override_controls():
    assert "Allow unsupported large models (try anyway)" in CHAT_UI
    assert 'id="largeModelOverrideEnabled"' in CHAT_UI
    assert 'id="applyLargeModelOverrideBtn"' in CHAT_UI
    assert 'id="largeModelOverrideStatus"' in CHAT_UI
    assert "function applyLargeModelCompatibilityOverride(" in CHAT_UI
    assert "/internal/compatibility/large-model-override" in CHAT_UI
    assert "/internal/llama-runtime/memory-loading" in CHAT_UI
    assert "memory_loading" in CHAT_UI


def test_chat_ui_has_potato_chat_brand_and_thinking_toggle():
    assert "🥔 Potato Chat" in CHAT_UI
    assert "Smart Search" not in CHAT_UI
    assert 'id="thinkingToggleBtn"' not in CHAT_UI
    assert "Deep thinking" not in CHAT_UI
    assert "function normalizeThinkingEnabled(" not in CHAT_UI
    assert "function setThinkingToggleState(" not in CHAT_UI
    assert "function toggleThinkingMode(" not in CHAT_UI


def test_chat_ui_supports_stop_generation_button_and_abort_controller():
    assert "activeRequest: null," in CHAT_UI
    assert "function stopGeneration()" in CHAT_UI
    assert "function requestLlamaCancelRecovery(" in CHAT_UI
    assert "function requestLlamaRestart(" in CHAT_UI
    assert "function scheduleImageCancelRestartFallback(" in CHAT_UI
    assert "IMAGE_CANCEL_RESTART_DELAY_MS" in CHAT_UI
    assert "function queueImageCancelRecovery(" in CHAT_UI
    assert 'sendBtn.textContent = "Stop"' in CHAT_UI
    assert 'sendBtn.classList.add("stop-mode")' in CHAT_UI
    assert "controller: new AbortController()" in CHAT_UI
    assert "signal: requestCtx.controller.signal" in CHAT_UI
    assert 'if (appState.requestInFlight) {' in CHAT_UI
    assert "stopGeneration();" in CHAT_UI
    assert "queueImageCancelRecovery(current);" in CHAT_UI
    assert "if (!requestCtx?.hasImageRequest)" in CHAT_UI
    assert "/internal/llama-healthz" in CHAT_UI
    assert "/internal/cancel-llama" in CHAT_UI
    assert "/internal/restart-llama" in CHAT_UI
    assert 'case "cancelled"' in CHAT_UI


def test_chat_ui_shows_llama_connection_indicator():
    assert 'id="llamaIndicator"' not in CHAT_UI
    assert 'id="llamaIndicatorLabel"' not in CHAT_UI
    assert 'id="statusDot"' in CHAT_UI
    assert 'id="statusSpinner"' in CHAT_UI
    assert 'id="statusLabel"' in CHAT_UI
    assert "indicator-dot" in CHAT_UI
    assert "chip-spinner" in CHAT_UI
    assert "function updateLlamaIndicator(" in CHAT_UI
    assert "statusPayload?.llama_server?.healthy" in CHAT_UI
    assert "const modelSuffix = modelFilename ? `:${modelFilename}` : \"\";" in CHAT_UI
    assert "const storageSuffix = activeModelStorage === \"ssd\" ? \":SSD\" : \"\";" in CHAT_UI
    assert "label.textContent = `CONNECTED:llama.cpp${modelSuffix}${storageSuffix}`" in CHAT_UI
    assert "label.textContent = `LOADING:llama.cpp${modelSuffix}${storageSuffix}`" in CHAT_UI
    assert "label.textContent = `FAILED:llama.cpp${modelSuffix}${storageSuffix}`" in CHAT_UI
    assert 'label.textContent = "DISCONNECTED:llama.cpp"' in CHAT_UI
    assert 'label.textContent = "CONNECTED:Fake Backend"' in CHAT_UI
    assert 'dot.classList.add("online")' in CHAT_UI
    assert 'dot.classList.add("loading")' in CHAT_UI
    assert 'dot.classList.add("failed")' in CHAT_UI
    assert 'dot.classList.add("offline")' in CHAT_UI
    assert "dot.hidden = true;" in CHAT_UI
    assert "spinner.hidden = false;" in CHAT_UI
    assert '.indicator-dot.loading {' in CHAT_UI
    assert '.indicator-dot.failed {' in CHAT_UI
    assert '.badge.loading {' in CHAT_UI
    assert '.badge.failed {' in CHAT_UI
    assert "statusPayload?.backend?.active" in CHAT_UI
    assert "backendMode === \"fake\"" in CHAT_UI
    assert "Llama server: connected" not in CHAT_UI


def test_chat_ui_mobile_layout_prioritizes_chat_area_before_sidebar():
    assert "@media (max-width: 900px)" in CHAT_UI
    assert ".app-shell {" in CHAT_UI
    assert 'id="sidebarPanel"' in CHAT_UI
    assert 'id="sidebarToggle"' in CHAT_UI
    assert 'id="sidebarCloseBtn"' in CHAT_UI
    assert 'id="sidebarBackdrop"' in CHAT_UI
    assert ".sidebar-backdrop {" in CHAT_UI
    assert "body.sidebar-open .sidebar {" in CHAT_UI
    assert "transform: translateX(-100%);" in CHAT_UI
    assert "body.sidebar-open {" in CHAT_UI
    assert "overflow: hidden;" in CHAT_UI
    assert "function setSidebarOpen(" in CHAT_UI
    assert "function bindMobileSidebar(" in CHAT_UI
    assert 'document.getElementById("sidebarToggle").addEventListener("click"' in CHAT_UI
    assert 'document.getElementById("sidebarCloseBtn").addEventListener("click"' in CHAT_UI
    assert 'document.getElementById("sidebarBackdrop").addEventListener("click"' in CHAT_UI
    assert 'id="settingsOpenBtn"' in CHAT_UI
    assert 'id="settingsModal"' in CHAT_UI
    assert 'id="settingsWorkspaceTabModel"' in CHAT_UI


def test_chat_ui_mobile_composer_keeps_actions_together():
    assert ".composer-bottom {" in CHAT_UI
    assert "display: grid;" in CHAT_UI
    assert "grid-template-columns: 1fr auto;" in CHAT_UI
    assert ".composer-right {" in CHAT_UI
    assert "justify-content: flex-end;" in CHAT_UI
    assert ".composer-left {" in CHAT_UI
    assert "@media (max-width: 900px)" in CHAT_UI
    assert ".composer-bottom { grid-template-columns: 1fr; }" in CHAT_UI
    assert ".composer-right {" in CHAT_UI
    assert "width: 100%;" in CHAT_UI
    assert "justify-content: flex-end;" in CHAT_UI


def test_chat_ui_uses_continuous_chat_history_in_openai_messages_format():
    assert "chatHistory: []," in CHAT_UI
    assert "reqBody.messages = reqBody.messages.concat(appState.chatHistory);" in CHAT_UI
    assert "const userMessage = { role: \"user\", content: buildUserMessageContent(content) };" in CHAT_UI
    assert "appState.chatHistory.push(userMessage);" in CHAT_UI
    assert CHAT_ENGINE_JS.index("reqBody.messages.push(userMessage);") < CHAT_ENGINE_JS.index("appState.chatHistory.push(userMessage);")
    assert "const finalAssistantText = assistantText.trim() || formatReasoningOnlyMessage(assistantReasoningText);" in CHAT_UI
    assert "appState.chatHistory.push({ role: \"assistant\", content: finalAssistantText });" in CHAT_UI
    assert "appState.chatHistory.push({ role: \"assistant\", content: msg });" in CHAT_UI


def test_chat_ui_formats_download_sizes_and_shows_model_filename_in_settings():
    assert 'id="modelName"' in CHAT_UI
    assert "Selected model" in CHAT_UI
    assert 'id="modelIdentityMeta"' in CHAT_UI
    assert "function formatBytes(" in CHAT_UI
    assert "units = [\"B\", \"KB\", \"MB\", \"GB\", \"TB\"]" in CHAT_UI
    assert "formatBytes(download.bytes_downloaded)" in CHAT_UI
    assert "formatBytes(download.bytes_total)" in CHAT_UI
    assert "statusPayload?.model?.filename" in CHAT_UI
    assert 'document.getElementById("modelName")' in CHAT_UI


def test_chat_ui_supports_manual_or_idle_model_download_prompt():
    assert 'id="downloadPrompt"' in CHAT_UI
    assert 'id="startDownloadBtn"' in CHAT_UI
    assert 'id="downloadPromptHint"' in CHAT_UI
    assert "function startModelDownload(" in CHAT_UI
    assert "function renderDownloadPrompt(" in CHAT_UI
    assert 'fetch("/internal/start-model-download"' in CHAT_UI
    assert "Auto-download starts in" in CHAT_UI
    assert "statusPayload.download.auto_start_remaining_seconds" in CHAT_UI
    assert "Not enough free storage for this model." in CHAT_UI
    assert "Model likely too large for free storage. Delete files and retry." in CHAT_UI
    assert "freeBytes < 512 * 1024 * 1024" in CHAT_UI


def test_chat_ui_supports_heavy_runtime_reset_action_with_confirmation():
    assert 'id="resetRuntimeBtn"' in CHAT_UI
    assert "Unload model + clean memory + restart" in CHAT_UI
    assert "function resetRuntimeHeavy(" in CHAT_UI
    assert "window.confirm(" in CHAT_UI
    assert 'fetch("/internal/reset-runtime"' in CHAT_UI
    assert 'document.getElementById("resetRuntimeBtn").addEventListener("click", resetRuntimeHeavy);' in CHAT_UI


def test_chat_ui_runtime_reset_has_active_reconnect_polling():
    assert "function startRuntimeReconnectWatch(" in CHAT_UI
    assert "function stopRuntimeReconnectWatch(" in CHAT_UI
    assert "RUNTIME_RECONNECT_MAX_ATTEMPTS" in CHAT_UI
    assert "Runtime reset in progress. Reconnecting..." in CHAT_UI
    assert "Runtime reconnected." in CHAT_UI
    assert "Model files on disk are unchanged." in CHAT_UI
    assert "const controller = new AbortController();" in CHAT_UI
    assert "cache: \"no-store\"" in CHAT_UI
    assert "controller.abort();" in CHAT_UI


def test_chat_ui_shows_pi_runtime_compact_with_details_toggle_above_settings():
    assert 'id="systemRuntimeCard"' in CHAT_UI
    assert 'id="runtimeCompact"' in CHAT_UI
    assert 'id="runtimeDetails"' in CHAT_UI
    assert 'id="runtimeViewToggle"' in CHAT_UI
    assert 'id="runtimeDetailsPowerGroup"' in CHAT_UI
    assert 'id="runtimeDetailsPerformanceGroup"' in CHAT_UI
    assert 'id="runtimeDetailsMemoryGroup"' in CHAT_UI
    assert 'id="runtimeDetailsPlatformGroup"' in CHAT_UI
    assert 'id="runtimeDetailCpuClockValue"' in CHAT_UI
    assert "Show details" in CHAT_UI
    assert "function setRuntimeDetailsExpanded(" in CHAT_UI
    assert "function renderSystemRuntime(" in CHAT_UI
    assert 'id="runtimeDetailStorageValue"' in CHAT_UI
    assert 'id="runtimeDetailSwapValue"' in CHAT_UI
    assert 'id="runtimeDetailPiModelValue"' in CHAT_UI
    assert 'id="runtimeDetailOsValue"' in CHAT_UI
    assert 'id="runtimeDetailKernelValue"' in CHAT_UI
    assert 'id="runtimeDetailBootloaderValue"' in CHAT_UI
    assert 'id="runtimeDetailFirmwareValue"' in CHAT_UI
    assert 'id="runtimeDetailPower"' in CHAT_UI
    assert 'id="runtimeDetailPowerRaw"' in CHAT_UI
    assert 'class="runtime-detail-prominent"' in CHAT_UI
    assert "Power (estimated total):" in CHAT_UI
    assert "Power (PMIC raw):" in CHAT_UI
    assert '>Bootloader</span>' in CHAT_UI
    assert '>Firmware</span>' in CHAT_UI
    assert "Performance" in CHAT_UI
    assert "Memory &amp; storage" in CHAT_UI
    assert "Platform" in CHAT_UI
    assert '>zram</span>' in CHAT_UI
    assert "Power note:" not in CHAT_UI
    assert CHAT_HTML.index('id="runtimeDetailPower"') < CHAT_HTML.index('id="runtimeDetailCpuValue"')
    assert "renderSystemRuntime(statusPayload?.system)" in CHAT_UI
    assert CHAT_HTML.index('id="systemRuntimeCard"') < CHAT_HTML.index('id="settingsModal"')
    assert 'id="settingsModelWorkspace"' in CHAT_UI
    assert 'id="settingsYamlPanel"' in CHAT_UI
    assert 'id="legacySettingsRuntimeSection"' in CHAT_UI
    assert 'id="settingsPowerCalibration"' in CHAT_UI
    assert 'id="powerCalibrationWallWatts"' in CHAT_UI
    assert 'id="capturePowerCalibrationSampleBtn"' in CHAT_UI
    assert 'id="fitPowerCalibrationBtn"' in CHAT_UI
    assert 'id="resetPowerCalibrationBtn"' in CHAT_UI
    assert "/internal/power-calibration/sample" in CHAT_UI
    assert "/internal/power-calibration/fit" in CHAT_UI
    assert "/internal/power-calibration/reset" in CHAT_UI


def test_chat_ui_supports_image_upload_for_vision_messages():
    assert 'id="imageInput"' in CHAT_UI
    assert 'accept="image/*"' in CHAT_UI
    assert 'id="attachImageBtn"' in CHAT_UI
    assert 'for="imageInput"' not in CHAT_UI
    assert 'id="attachImageBtn" class="attach-btn" type="button">Attach image</button>' in CHAT_UI
    assert "function handleImageSelected(" in CHAT_UI
    assert "FileReader()" in CHAT_UI
    assert "reader.readAsDataURL(file);" in CHAT_UI
    assert "appState.pendingImage = {" in CHAT_UI
    assert "type: \"image_url\"" in CHAT_UI
    assert "image_url: { url: appState.pendingImage.dataUrl }" in CHAT_UI
    assert "function openImagePicker(" in CHAT_UI
    assert "input.showPicker()" not in CHAT_UI
    assert "input.click();" in CHAT_UI
    assert 'document.getElementById("attachImageBtn").addEventListener("click", openImagePicker);' in CHAT_UI
    assert 'document.getElementById("attachImageBtn").addEventListener("click", (event) => {' not in CHAT_UI
    assert 'document.getElementById("attachImageBtn").addEventListener("keydown"' not in CHAT_UI


def test_chat_ui_renders_image_thumbnail_in_user_bubble():
    assert "function buildUserBubblePayload(" in CHAT_UI
    assert "imageDataUrl: appState.pendingImage.dataUrl" in CHAT_UI
    assert 'thumbnail.className = "message-image-thumb"' in CHAT_UI
    assert 'thumbnail.src = imageDataUrl;' in CHAT_UI
    assert "bubble.replaceChildren();" in CHAT_UI
    assert 'caption.className = "message-text"' in CHAT_UI


def test_chat_ui_compresses_large_images_before_send():
    assert "const IMAGE_SAFE_MAX_BYTES = 140 * 1024;" in CHAT_UI
    assert "const IMAGE_MAX_DIMENSION = 896;" in CHAT_UI
    assert "const IMAGE_MAX_PIXEL_COUNT = IMAGE_MAX_DIMENSION * IMAGE_MAX_DIMENSION;" in CHAT_UI
    assert "function estimateDataUrlBytes(" in CHAT_UI
    assert "function inspectImageDataUrl(" in CHAT_UI
    assert "function compressImageDataUrl(" in CHAT_UI
    assert "function maybeCompressImage(" in CHAT_UI
    assert "const needsResize =" in CHAT_UI
    assert "metadata.maxDim > IMAGE_MAX_DIMENSION" in CHAT_UI
    assert "metadata.pixelCount > IMAGE_MAX_PIXEL_COUNT" in CHAT_UI
    assert "setComposerActivity(\"Optimizing image...\")" in CHAT_UI
    assert "await maybeCompressImage(result, file);" in CHAT_UI
    assert "optimized from" in CHAT_UI


def test_chat_ui_model_manager_supports_model_delete_action():
    assert "async function deleteSelectedModel(" in CHAT_UI
    assert "async function moveModelToSsd(" in CHAT_UI
    assert "async function cancelActiveModelDownload(modelId = null)" in CHAT_UI
    assert "/internal/models/delete" in CHAT_UI
    assert "/internal/models/move-to-ssd" in CHAT_UI
    assert "Move to SSD" in CHAT_UI
    assert "On SSD" in CHAT_UI
    assert "statusPayload?.storage_targets?.ssd?.available" in CHAT_UI
    assert 'deleteBtn.dataset.action = "delete"' in CHAT_UI
    assert "Delete model" in CHAT_UI
    assert "Cancel + delete" in CHAT_UI
    assert "Stop download" in CHAT_UI
    assert "formatModelStatusLabel" in CHAT_UI
    assert "function startModelDownloadForModel(" in CHAT_UI
    assert "/internal/models/download" in CHAT_UI
    assert "insufficient_storage" in CHAT_UI
    assert 'id="purgeModelsBtn"' in CHAT_UI
    assert "async function purgeAllModels(" in CHAT_UI
    assert "/internal/models/purge" in CHAT_UI
    assert "reset_bootstrap_flag: false" in CHAT_UI


def test_chat_ui_shows_processing_indicator_while_generating():
    assert 'id="composerActivity"' in CHAT_UI
    assert 'id="composerStatusChip"' in CHAT_UI
    assert 'id="composerStatusText"' in CHAT_UI
    assert 'class="composer-status-chip"' in CHAT_UI
    assert 'id="cancelBtn"' in CHAT_UI
    assert "function setComposerActivity(" in CHAT_UI
    assert "function setComposerStatusChip(" in CHAT_UI
    assert "function hideComposerStatusChip(" in CHAT_UI
    assert "function setCancelEnabled(" in CHAT_UI
    assert "function cancelCurrentWork(" in CHAT_UI
    assert "const PREFILL_PROGRESS_CAP = 99;" in CHAT_UI
    assert "function estimatePrefillEtaMs(" in CHAT_UI
    assert "function beginPrefillProgress(" in CHAT_UI
    assert "function markPrefillGenerationStarted(" in CHAT_UI
    assert "function stopPrefillProgress(" in CHAT_UI
    assert "return Promise.resolve({ cancelled: false });" in CHAT_UI
    assert "resolve({ cancelled: true });" in CHAT_UI
    assert "resolve({ cancelled: false });" in CHAT_UI
    assert "function throwIfRequestStoppedAfterPrefill(" in CHAT_UI
    assert "if (finishResult?.cancelled || requestCtx?.stoppedByUser)" in CHAT_UI
    assert "const PREFILL_FINISH_DURATION_MS =" in CHAT_UI
    assert "const PREFILL_FINISH_HOLD_MS =" in CHAT_UI
    assert "function setMessageProcessingState(" in CHAT_UI
    assert "className = \"message-processing-shell\"" in CHAT_UI
    assert "const PREFILL_PROGRESS_TAIL_START = 89;" in CHAT_UI
    assert "Prompt processing" in CHAT_UI
    assert "Generating reply" not in CHAT_UI
    assert "potato_prefill_metrics_v1" in CHAT_UI
    assert "Preparing prompt..." in CHAT_UI
    assert "Preparing prompt • " in CHAT_UI
    assert "Preparing prompt: " not in CHAT_UI
    assert "1 - Math.exp(-3.2 * Math.min(1.4, normalized))" in CHAT_UI
    assert "Math.log1p(overtimeSeconds) * 2.6" in CHAT_UI
    assert "Math.min(PREFILL_PROGRESS_CAP" in CHAT_UI
    assert 'applyPrefillProgressState(requestCtx, 100);' in CHAT_UI
    assert 'window.__POTATO_PREFILL_FINISH_DURATION_MS__' in CHAT_UI
    assert 'window.__POTATO_PREFILL_FINISH_HOLD_MS__' in CHAT_UI
    assert 'setComposerActivity("Reading image...")' in CHAT_UI
    assert "reader.onprogress" in CHAT_UI
    assert "pendingImageReader.abort();" in CHAT_UI
    assert 'document.getElementById("cancelBtn").addEventListener("click", cancelCurrentWork);' in CHAT_UI
    assert "setComposerActivity(\"\")" in CHAT_UI
    assert "TTFT " in CHAT_UI


def test_chat_assets_are_loaded_from_external_files():
    for name in ("chat.html", "chat.css", "chat.js", "state.js"):
        path = WEB_ASSETS_DIR / name
        assert path.exists(), f"Expected {name} at {path}"

    assert (WEB_ASSETS_DIR / "chat.html").read_text(encoding="utf-8") == CHAT_HTML
    assert (WEB_ASSETS_DIR / "chat.css").read_text(encoding="utf-8") == CHAT_CSS
    assert (WEB_ASSETS_DIR / "chat.js").read_text(encoding="utf-8") == CHAT_JS
    assert '<link rel="stylesheet" href="/assets/chat.css">' in CHAT_HTML
    assert '<script type="module" src="/assets/chat.js"></script>' in CHAT_HTML
    assert '<link rel="modulepreload" href="/assets/state.js">' in CHAT_HTML


def test_root_endpoint_serves_chat_html(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "<!doctype html>" in response.text.lower()
    assert "Potato Chat" in response.text
