"use strict";

import { appState, settingsKey, PREFILL_METRICS_KEY, PREFILL_PROGRESS_CAP, PREFILL_PROGRESS_TAIL_START, PREFILL_PROGRESS_FLOOR, PREFILL_TICK_MS, PREFILL_FINISH_DURATION_MS, PREFILL_FINISH_TICK_MS, PREFILL_FINISH_HOLD_MS, STATUS_CHIP_MIN_VISIBLE_MS, RUNTIME_RECONNECT_INTERVAL_MS, RUNTIME_RECONNECT_TIMEOUT_MS, RUNTIME_RECONNECT_MAX_ATTEMPTS, IMAGE_CANCEL_RECOVERY_DELAY_MS, IMAGE_CANCEL_RESTART_DELAY_MS, SESSIONS_DB_NAME, SESSIONS_DB_VERSION, SESSIONS_STORE, ACTIVE_SESSION_KEY, SESSION_TITLE_MAX_LENGTH, SESSION_LIST_MAX_VISIBLE, IMAGE_SAFE_MAX_BYTES, IMAGE_MAX_DIMENSION, IMAGE_MAX_PIXEL_COUNT, DEFAULT_MODEL_VISION_SETTINGS } from "./state.js";
import { formatBytes, formatPercent, formatClockMHz, normalizePercent, percentFromRatio, runtimeMetricSeverityClass, applyRuntimeMetricSeverity, formatCountdownSeconds, estimateDataUrlBytes, postJson } from "./utils.js";
import { registerAppendMessage, registerSetMessageMeta, saveActiveSession, clearChatState, startNewChat, deleteSession, deleteAllSessions, loadSessionIntoView, initSessionManager, renderSessionList } from "./session-manager.js";
import { isLocalModelConnected, findResumableFailedModel, renderDownloadPrompt } from "./status.js";
import { setModelUploadStatus, setLlamaRuntimeSwitchStatus, setLlamaRuntimeSwitchButtonState, setLlamaMemoryLoadingStatus, setLlamaMemoryLoadingButtonState, setLargeModelOverrideStatus, setLargeModelOverrideButtonState, setPowerCalibrationStatus, setPowerCalibrationButtonsState, setPowerCalibrationLiveStatus } from "./runtime-ui.js";
import { setUpdateCheckInFlight, setUpdateStartInFlight, renderUpdateCard, registerUpdateCallbacks, isUpdateExecutionActive } from "./update-ui.js";
import { registerOpenEditMessageModal, getMessagesBox, isMessagesPinned, setMessagesPinnedState, hasActiveMessageSelection, handleMessagesChanged, appendMessage, updateMessage, setMessageProcessingState, setMessageMeta, setMessageActionsVisible, removeMessage } from "./messages.js";
import { registerImageUiCallbacks, cancelPendingImageWork, clearPendingImage, handleImageSelected, buildUserMessageContent, buildUserBubblePayload, openImagePicker } from "./image-handler.js";
import { registerSettingsCallbacks, activeRuntimeVisionCapability, showTextOnlyImageBlockedState, resolveSelectedSettingsModel, selectedModelHasUnsavedChanges, blockModelSelectionChange, renderSettingsWorkspace, bindSettingsModal, setModelUrlStatus, formatModelUrlStatus } from "./settings-ui.js";
import { registerChatEngineCallbacks, setSendEnabled, setComposerActivity, setComposerStatusChip, hideComposerStatusChip, setCancelEnabled, sendChat, stopGeneration, cancelCurrentWork, extractApiErrorMessage } from "./chat-engine.js";

    // ── Session manager — extracted to session-manager.js ──────────────

    // Shell API references — populated by init()
    let _shell = {};

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


    // isMobileSidebarViewport, setSidebarOpen — extracted to shell.js

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
      if (appState.editModalOpen && _shell.setSidebarOpen) {
        _shell.setSidebarOpen(false);
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

    // bindMobileSidebar — extracted to shell.js

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

    // applyTheme, bindSettings — extracted to shell.js

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
        await _shell.pollStatus();
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
        await _shell.pollStatus();
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
        await _shell.pollStatus();
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
        await _shell.pollStatus();
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
        await _shell.pollStatus();
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
        await _shell.pollStatus();
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
      await _shell.pollStatus();
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
        await _shell.pollStatus();
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
        await _shell.pollStatus();
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
        await _shell.pollStatus();
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
        await _shell.pollStatus();
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
        await _shell.pollStatus();
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
        await _shell.pollStatus();
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
        await _shell.pollStatus();
      };
      xhr.onabort = async () => {
        appState.uploadRequest = null;
        if (cancelBtn) cancelBtn.hidden = true;
        setModelUploadStatus("Upload cancelled.");
        await postJson("/internal/models/cancel-upload", {});
        await _shell.pollStatus();
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
          if (body?.reason === "upload_too_large" && body?.max_upload_bytes) {
            setModelUploadStatus(`Upload too large — limit is ${formatBytes(body.max_upload_bytes)} (available storage).`);
          } else {
            setModelUploadStatus(`Upload failed (${body?.reason || xhr.status}).`);
          }
        } else if (body?.uploaded) {
          if (input) input.value = "";
          setModelUploadStatus("Upload completed.");
        } else {
          setModelUploadStatus(`Upload did not complete (${body?.reason || "unknown"}).`);
        }
        await _shell.pollStatus();
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
        await _shell.pollStatus();
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
      const statusPayload = await _shell.pollStatus({ timeoutMs: RUNTIME_RECONNECT_TIMEOUT_MS });
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
            _shell.pollStatus();
          }, 1000);
        }
      }
    }


    // ── Update UI actions ──────────────────────────────────────────────

    async function checkForUpdate() {
      if (appState.updateCheckInFlight) return;
      if (isUpdateExecutionActive()) return;
      appState.updateCheckInFlight = true;
      setUpdateCheckInFlight(true);
      try {
        const res = await fetch("/internal/update/check", {
          method: "POST",
          headers: { "content-type": "application/json" },
        });
        if (res.status === 409) {
          const body = await res.json().catch(() => ({}));
          appendMessage("assistant", `Updates are not available (${body?.reason || "orchestrator disabled"}).`);
          return;
        }
        if (!res.ok) {
          appendMessage("assistant", `Could not check for updates (HTTP ${res.status}).`);
          return;
        }
        await _shell.pollStatus();
      } catch (err) {
        appendMessage("assistant", `Could not check for updates: ${err}`);
      } finally {
        appState.updateCheckInFlight = false;
        setUpdateCheckInFlight(false);
      }
    }

    async function startUpdate() {
      if (appState.updateStartInFlight) return;
      appState.updateStartInFlight = true;
      setUpdateStartInFlight(true);
      try {
        const res = await fetch("/internal/update/start", {
          method: "POST",
          headers: { "content-type": "application/json" },
        });
        const body = await res.json().catch(() => ({}));
        if (res.status === 409 || !body?.started) {
          const reasons = {
            orchestrator_disabled: "Updates are not available (orchestrator disabled).",
            no_update_available: "No update available. Try checking again.",
            no_tarball_url: "No download URL found for this update. Try checking again.",
            download_active: "A model download is in progress. Wait for it to finish.",
            update_in_progress: "An update is already in progress.",
          };
          appendMessage("assistant", reasons[body?.reason] || `Could not start update (${body?.reason || "unknown"}).`);
          return;
        }
        await _shell.pollStatus();
      } catch (err) {
        appendMessage("assistant", `Could not start update: ${err}`);
      } finally {
        appState.updateStartInFlight = false;
        setUpdateStartInFlight(false);
      }
    }

    function showUpdateReleaseNotes() {
      const notes = appState.latestStatus?.update?.release_notes;
      if (notes) {
        appendMessage("assistant", notes);
      } else {
        appendMessage("assistant", "No release notes available.");
      }
    }

    function stopUpdateReconnectWatch() {
      if (appState.updateReconnectTimer) {
        window.clearTimeout(appState.updateReconnectTimer);
        appState.updateReconnectTimer = null;
      }
      appState.updateReconnectActive = false;
      appState.updateReconnectAttempts = 0;
    }

    async function stepUpdateReconnectWatch() {
      if (!appState.updateReconnectActive) return;
      appState.updateReconnectAttempts += 1;
      const statusPayload = await _shell.pollStatus({ timeoutMs: RUNTIME_RECONNECT_TIMEOUT_MS });
      const updateState = String(statusPayload?.update?.state || "idle");
      if (updateState === "idle") {
        stopUpdateReconnectWatch();
        const version = String(statusPayload?.update?.current_version || "");
        setComposerActivity(version ? `Update complete! Now running v${version}. Reloading...` : "Update complete! Reloading...");
        // Guard: skip reload if the user is mid-chat (submitted a prompt or
        // typing a draft). They'll get fresh assets on their next manual refresh.
        window.setTimeout(() => {
          const hasInput = document.querySelector("#userPrompt")?.value?.trim();
          if (!appState.requestInFlight && !hasInput) window.location.reload();
        }, 2000);
        return;
      }
      if (updateState === "failed") {
        stopUpdateReconnectWatch();
        setComposerActivity("");
        appendMessage("assistant", "Update may not have applied correctly. Check the version in the sidebar.");
        return;
      }
      if (appState.updateReconnectAttempts >= RUNTIME_RECONNECT_MAX_ATTEMPTS) {
        stopUpdateReconnectWatch();
        setComposerActivity("");
        appendMessage("assistant", "Reconnection is taking longer than expected. The update may still be completing.");
        return;
      }
      appState.updateReconnectTimer = window.setTimeout(stepUpdateReconnectWatch, RUNTIME_RECONNECT_INTERVAL_MS);
    }

    function startUpdateReconnectWatch() {
      stopUpdateReconnectWatch();
      appState.updateReconnectActive = true;
      appState.updateReconnectAttempts = 0;
      setComposerActivity("Potato OS is restarting after update. Reconnecting...");
      stepUpdateReconnectWatch();
    }


    // classifyPi5MemoryTier, setSidebarNote, setStatus, pollStatus, toggleTheme — extracted to shell.js

export function init(shellApi) {
    const { pollStatus, setSidebarOpen, isMobileSidebarViewport, registerEscapeHandler, bindModelSwitcher } = shellApi;
    _shell = { pollStatus, setSidebarOpen, isMobileSidebarViewport };

    // Register escape handler for edit modal
    if (registerEscapeHandler) {
      registerEscapeHandler(() => {
        if (appState.terminalModalOpen) {
          import("./terminal-ui.js").then((m) => m.closeTerminalModal());
          return true;
        }
        if (appState.editModalOpen) {
          closeEditMessageModal();
          return true;
        }
        return false;
      });
    }

    bindSettingsModal();
    bindEditModal();
    bindMessagesScroller();
    registerAppendMessage(appendMessage);
    registerSetMessageMeta(setMessageMeta);
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
    registerUpdateCallbacks({
      onRestartPending: startUpdateReconnectWatch,
    });
    initSessionManager().catch(() => {});

    // Model switcher — shell owns the DOM bindings, chat provides the activate callback
    bindModelSwitcher(activateSelectedModel);

    document.getElementById("newChatBtn").addEventListener("click", () => startNewChat());
    document.getElementById("deleteAllChatsBtn").addEventListener("click", () => {
      if (!window.confirm("Delete all chats? This cannot be undone.")) return;
      deleteAllSessions();
    });
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
      } else if (action === "delete") {
        deleteSelectedModel(modelId);
      }
    });
    document.getElementById("resetRuntimeBtn").addEventListener("click", resetRuntimeHeavy);
    document.getElementById("updateCheckBtn").addEventListener("click", checkForUpdate);
    {
      let terminalBound = false;
      document.getElementById("terminalOpenBtn").addEventListener("click", async () => {
        const mod = await import("./terminal-ui.js");
        if (!terminalBound) { mod.bindTerminalModal(); terminalBound = true; }
        mod.openTerminalModal();
      });
    }
    document.getElementById("updateStartBtn").addEventListener("click", startUpdate);
    document.getElementById("updateRetryBtn").addEventListener("click", startUpdate);
    document.getElementById("updateNotesBtn").addEventListener("click", showUpdateReleaseNotes);
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
}
