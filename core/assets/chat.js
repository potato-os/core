"use strict";

import { appState } from "./state.js";
import { registerAppendMessage, registerSetMessageMeta, startNewChat, deleteSession, deleteAllSessions, loadSessionIntoView, initSessionManager } from "./session-manager.js";
import { registerUpdateCallbacks } from "./update-ui.js";
import { registerOpenEditMessageModal, getMessagesBox, isMessagesPinned, setMessagesPinnedState, hasActiveMessageSelection, appendMessage, setMessageMeta, removeMessage } from "./messages.js";
import { registerImageUiCallbacks, clearPendingImage, handleImageSelected, openImagePicker } from "./image-handler.js";
import { registerSettingsChatCallbacks, activeRuntimeVisionCapability, showTextOnlyImageBlockedState, bindSettingsModal } from "./settings-ui.js";
import { registerChatEngineCallbacks, setComposerActivity, setComposerStatusChip, hideComposerStatusChip, setCancelEnabled, sendChat, stopGeneration, cancelCurrentWork } from "./chat-engine.js";
import { switchLlamaRuntimeBundle, applyLlamaMemoryLoadingMode, applyLargeModelOverrideFromSettings, allowUnsupportedLargeModelFromWarning, capturePowerCalibrationSample, fitPowerCalibrationModel, resetPowerCalibrationModel, registerModelFromUrl, activateSelectedModel, purgeAllModels, uploadLocalModel, cancelLocalModelUpload, startModelDownload, resetRuntimeHeavy, checkForUpdate, startUpdate, showUpdateReleaseNotes, startUpdateReconnectWatch, handleModelsListClick } from "./platform-controls.js";

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
    registerSettingsChatCallbacks({
      setComposerActivity, setComposerStatusChip, hideComposerStatusChip,
      setCancelEnabled, focusPromptInput, clearPendingImage,
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
    document.getElementById("modelsList").addEventListener("click", handleModelsListClick);
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
