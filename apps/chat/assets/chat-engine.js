"use strict";

import { appState, PREFILL_METRICS_KEY, PREFILL_PROGRESS_CAP, PREFILL_PROGRESS_TAIL_START, PREFILL_PROGRESS_FLOOR, PREFILL_TICK_MS, PREFILL_FINISH_DURATION_MS, PREFILL_FINISH_TICK_MS, PREFILL_FINISH_HOLD_MS, STATUS_CHIP_MIN_VISIBLE_MS, IMAGE_CANCEL_RECOVERY_DELAY_MS, IMAGE_CANCEL_RESTART_DELAY_MS } from "/assets/state.js";
import { postJson } from "/assets/utils.js";
import { appendMessage, updateMessage, setMessageProcessingState, setMessageMeta, setMessageActionsVisible, removeMessage } from "./messages.js";
import { clearPendingImage, buildUserMessageContent, buildUserBubblePayload, cancelPendingImageWork } from "./image-handler.js";
import { collectSettings, resolveSeedForRequest, activeRuntimeVisionCapability, showTextOnlyImageBlockedState, renderComposerCapabilities, formatImageRejectedNotice } from "/assets/settings-ui.js";
import { saveActiveSession } from "./session-manager.js";

    let _ui = {};

    export function registerChatEngineCallbacks(callbacks) {
      _ui = callbacks;
    }

    export function extractApiErrorMessage(body) {
      if (!body || typeof body !== "object") return "";
      const candidate = body?.error?.message || body?.detail || body?.message || "";
      return typeof candidate === "string" ? candidate.trim() : "";
    }

    export function formatChatFailureMessage(statusCode, body, requestCtx = {}) {
      if (statusCode === 429) {
        return "Potato is busy with another request. Try again in a moment.";
      }
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

    export function setSendEnabled() {
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

    export function setComposerActivity(message) {
      const activity = document.getElementById("composerActivity");
      if (!activity) return;
      activity.textContent = String(message || "");
    }

    export function setComposerStatusChip(message, options = {}) {
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

    export function hideComposerStatusChip(options = {}) {
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

    export function setCancelEnabled(enabled) {
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

    // TODO(llama.cpp#21326): remove _stripChannelTokens once the upstream PEG
    // parser handles Gemma 4 <|channel>thought tokens natively.
    const _CHANNEL_RE = /<\|channel>[^<]*<channel\|>/g;
    function _stripChannelTokens(text) {
      return text.replace(_CHANNEL_RE, "");
    }

    export function consumeSseDeltas(state, chunkText) {
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
        case "disconnected":
          return "Connection lost";
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

    export async function sendChat() {
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
      let assistantText = "";
      let assistantReasoningText = "";
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
      if (_ui.focusPromptInput) _ui.focusPromptInput();
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
        reqBody.messages = reqBody.messages.concat(appState.chatHistory.map(({ meta, ...msg }) => msg));
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
          assistantText = "";
          assistantReasoningText = "";

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
              updateMessage(activeAssistantView, _stripChannelTokens(assistantText), { showActions: false });
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
          const finalAssistantText = _stripChannelTokens(assistantText).trim() || formatReasoningOnlyMessage(assistantReasoningText);
          updateMessage(activeAssistantView, finalAssistantText, { showActions: true });
          const elapsedSeconds = Math.max(0, (performance.now() - requestStartMs) / 1000);
          if (requestCtx.stoppedByUser) {
            streamStats.finish_reason = "cancelled";
          }
          const statsText = formatAssistantStats(streamStats, elapsedSeconds, requestCtx.firstTokenLatencyMs);
          appState.chatHistory.push({ role: "assistant", content: finalAssistantText, meta: statsText });
          setMessageMeta(activeAssistantView, statsText);
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
        const messageContent = typeof message?.content === "string" ? _stripChannelTokens(message.content).trim() : "";
        const msg = messageContent || formatReasoningOnlyMessage(message?.reasoning_content) || JSON.stringify(body);
        updateMessage(activeAssistantView, msg, { showActions: true });
        const elapsedSeconds = Math.max(0, (performance.now() - requestStartMs) / 1000);
        const statsText = formatAssistantStats(body, elapsedSeconds, requestCtx.firstTokenLatencyMs);
        appState.chatHistory.push({ role: "assistant", content: msg, meta: statsText });
        setMessageMeta(activeAssistantView, statsText);
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
            streamStats.finish_reason = "cancelled";
            if (!partial) {
              updateMessage(activeAssistantView, "(stopped)", { showActions: true });
            } else {
              const cancelStatsText = formatAssistantStats(streamStats, elapsedSeconds, requestCtx.firstTokenLatencyMs);
              appState.chatHistory.push({ role: "assistant", content: partial, meta: cancelStatsText });
            }
            setMessageMeta(activeAssistantView, formatAssistantStats(streamStats, elapsedSeconds, requestCtx.firstTokenLatencyMs));
          } else {
            const stoppedDiv = appendMessage("assistant", "(stopped)");
            setMessageMeta(stoppedDiv, formatAssistantStats({ finish_reason: "cancelled" }, elapsedSeconds, requestCtx.firstTokenLatencyMs));
          }
        } else {
          if (activeAssistantView) {
            const rawPartial = assistantText.trim() || formatReasoningOnlyMessage(assistantReasoningText);
            if (rawPartial && rawPartial !== "(empty response)") {
              activeAssistantView.bubble.classList.remove("processing");
              delete activeAssistantView.bubble.dataset.phase;
              activeAssistantView.copyText = rawPartial;
              setMessageActionsVisible(activeAssistantView, true);
              streamStats.finish_reason = "disconnected";
              const elapsedSeconds = Math.max(0, (performance.now() - requestStartMs) / 1000);
              setMessageMeta(activeAssistantView, formatAssistantStats(streamStats, elapsedSeconds, requestCtx.firstTokenLatencyMs));
            } else {
              updateMessage(activeAssistantView, `Request error: ${err}`, { showActions: true });
            }
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
        if (_ui.focusPromptInput) _ui.focusPromptInput();
      }
    }

    export function stopGeneration() {
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
        if (_ui.pollStatus) await _ui.pollStatus();
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
          if (_ui.pollStatus) await _ui.pollStatus();
          setComposerActivity("");
          hideComposerStatusChip();
          return;
        }
        setComposerActivity("Waiting for model to finish cancel...");
        setComposerStatusChip("Finalizing cancel...", { phase: "cancel" });
        scheduleImageCancelRestartFallback();
      }, IMAGE_CANCEL_RECOVERY_DELAY_MS);
    }

    export function cancelCurrentWork() {
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
