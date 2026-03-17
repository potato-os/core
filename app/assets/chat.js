"use strict";

import { appState, defaultSettings, settingsKey, PREFILL_METRICS_KEY, PREFILL_PROGRESS_CAP, PREFILL_PROGRESS_TAIL_START, PREFILL_PROGRESS_FLOOR, PREFILL_TICK_MS, PREFILL_FINISH_DURATION_MS, PREFILL_FINISH_TICK_MS, PREFILL_FINISH_HOLD_MS, STATUS_CHIP_MIN_VISIBLE_MS, STATUS_POLL_TIMEOUT_MS, RUNTIME_RECONNECT_INTERVAL_MS, RUNTIME_RECONNECT_TIMEOUT_MS, RUNTIME_RECONNECT_MAX_ATTEMPTS, IMAGE_CANCEL_RECOVERY_DELAY_MS, IMAGE_CANCEL_RESTART_DELAY_MS, SESSIONS_DB_NAME, SESSIONS_DB_VERSION, SESSIONS_STORE, ACTIVE_SESSION_KEY, SESSION_TITLE_MAX_LENGTH, SESSION_LIST_MAX_VISIBLE, IMAGE_SAFE_MAX_BYTES, IMAGE_MAX_DIMENSION, IMAGE_MAX_PIXEL_COUNT, CPU_CLOCK_MAX_HZ_PI5, GPU_CLOCK_MAX_HZ_PI5, RUNTIME_METRIC_SEVERITY_CLASSES, DEFAULT_MODEL_VISION_SETTINGS } from "./state.js";
import { formatBytes, formatPercent, formatClockMHz, normalizePercent, percentFromRatio, runtimeMetricSeverityClass, applyRuntimeMetricSeverity, formatCountdownSeconds, estimateDataUrlBytes, postJson } from "./utils.js";
import { registerAppendMessage, saveActiveSession, clearChatState, startNewChat, deleteSession, loadSessionIntoView, initSessionManager, renderSessionList } from "./session-manager.js";
import { populateModelSwitcher, openModelSwitcher, closeModelSwitcher, toggleModelSwitcher } from "./model-switcher.js";
import { isLocalModelConnected, updateLlamaIndicator, findResumableFailedModel, renderDownloadPrompt, renderStatusActions, renderCompatibilityWarnings, formatSidebarStatusDetail, formatModelStatusLabel } from "./status.js";
import { setRuntimeDetailsExpanded, renderSystemRuntime, renderLlamaRuntimeStatus, renderUploadState, setModelUploadStatus, setLlamaRuntimeSwitchStatus, setLlamaRuntimeSwitchButtonState, setLlamaMemoryLoadingStatus, setLlamaMemoryLoadingButtonState, setLargeModelOverrideStatus, setLargeModelOverrideButtonState, setPowerCalibrationStatus, setPowerCalibrationButtonsState, setPowerCalibrationLiveStatus } from "./runtime-ui.js";
import { registerOpenEditMessageModal, getMessagesBox, isMessagesPinned, setMessagesPinnedState, hasActiveMessageSelection, handleMessagesChanged, appendMessage, updateMessage, setMessageProcessingState, setMessageMeta, setMessageActionsVisible, removeMessage } from "./messages.js";
import { registerImageUiCallbacks, cancelPendingImageWork, clearPendingImage, handleImageSelected, buildUserMessageContent, buildUserBubblePayload, openImagePicker } from "./image-handler.js";
import { registerSettingsCallbacks, detectSystemTheme, normalizeTheme, loadSettings, saveSettings, normalizeGenerationMode, normalizeSeedValue, updateSeedFieldState, resolveSeedForRequest, normalizeChatSettings, activeRuntimeVisionCapability, formatImageRejectedNotice, showTextOnlyImageBlockedState, renderComposerCapabilities, getSettingsModels, resolveSelectedSettingsModel, getActiveChatSettings, collectSelectedModelSettings, markModelSettingsDraftDirty, clearModelSettingsDraftState, selectedModelHasUnsavedChanges, blockModelSelectionChange, discardSelectedModelSettings, collectSettings, setSettingsModalOpen, openSettingsModal, closeSettingsModal, closeLegacySettingsModal, openLegacySettingsModal, showSettingsWorkspaceTab, syncSegmentedControl, setSegmentedControlValue, renderSelectedModelSettings, renderSettingsWorkspace, loadSettingsDocument, applySettingsDocument, saveSelectedModelSettings, downloadProjectorForSelectedModel, bindSettingsModal, setModelUrlStatus, formatModelUrlStatus } from "./settings-ui.js";
import { registerChatEngineCallbacks, setSendEnabled, setComposerActivity, setComposerStatusChip, hideComposerStatusChip, setCancelEnabled, sendChat, stopGeneration, cancelCurrentWork, extractApiErrorMessage } from "./chat-engine.js";

    // ── Session manager — extracted to session-manager.js ──────────────


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
          const freeInfo = body?.free_bytes != null ? ` (${formatBytes(body.free_bytes)} free, ${formatBytes(body.required_bytes)} needed)` : "";
          appendMessage("assistant", `Not enough free storage to download this model${freeInfo}. Free up space or delete unused models and try again.`);
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
          const freeInfo = body?.free_bytes != null ? ` (${formatBytes(body.free_bytes)} free, ${formatBytes(body.required_bytes)} needed)` : "";
          appendMessage("assistant", `Not enough free storage to download this model${freeInfo}. Free up space or delete unused models and try again.`);
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

    function setSidebarNote(statusPayload) {
      const noteEl = document.getElementById("sidebarNote");
      if (!noteEl) return;
      const version = String(statusPayload?.version || "").trim();
      const systemPayload = statusPayload?.system;
      const piModelName = String(systemPayload?.pi_model_name || "").trim();
      const memoryTier = classifyPi5MemoryTier(systemPayload?.memory_total_bytes);
      const parts = [];
      if (version) parts.push(version);
      if (piModelName) parts.push(piModelName);
      if (memoryTier) parts.push(memoryTier);
      noteEl.textContent = parts.length > 0 ? parts.join(" · ") : "";
    }

    function setStatus(statusPayload) {
      appState.latestStatus = statusPayload;
      const downloadText = formatSidebarStatusDetail(statusPayload);
      const text = `State: ${statusPayload.state} | ${downloadText}`;
      document.getElementById("statusText").textContent = text;
      renderStatusActions(statusPayload);
      setSidebarNote(statusPayload);
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
    registerImageUiCallbacks({
      setComposerActivity, setComposerStatusChip, hideComposerStatusChip,
      setCancelEnabled, focusPromptInput, activeRuntimeVisionCapability,
      showTextOnlyImageBlockedState,
    });
    registerSettingsCallbacks({
      setComposerActivity, setComposerStatusChip, hideComposerStatusChip,
      setCancelEnabled, focusPromptInput, clearPendingImage,
      setSidebarOpen, pollStatus,
    });
    registerChatEngineCallbacks({
      focusPromptInput, pollStatus,
    });
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
