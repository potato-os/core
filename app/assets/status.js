"use strict";

import { appState } from "./state.js";
import { formatBytes, formatCountdownSeconds } from "./utils.js";

    export function isLocalModelConnected(statusPayload) {
      const backendMode = String(
        statusPayload?.backend?.active
        || statusPayload?.backend?.mode
        || ""
      ).toLowerCase();
      const isReady = String(statusPayload?.state || "").toUpperCase() === "READY";
      const llamaHealthy = statusPayload?.llama_server?.healthy === true;
      return backendMode === "llama" && isReady && llamaHealthy;
    }

    export function updateLlamaIndicator(statusPayload) {
      const badge = document.getElementById("statusBadge");
      const dot = document.getElementById("statusDot");
      const spinner = document.getElementById("statusSpinner");
      const label = document.getElementById("statusLabel");
      if (!badge || !dot || !label) return;
      const backendMode = String(
        statusPayload?.backend?.active
        || statusPayload?.backend?.mode
        || ""
      ).toLowerCase();
      const modelFilename = String(statusPayload?.model?.filename || "").trim();
      const modelSuffix = modelFilename ? `:${modelFilename}` : "";
      const activeModelStorage = String(statusPayload?.model?.storage?.location || "").toLowerCase();
      const storageSuffix = activeModelStorage === "ssd" ? ":SSD" : "";
      const isReady = String(statusPayload?.state || "").toUpperCase() === "READY";
      const statusState = String(statusPayload?.state || "").toUpperCase();
      const hasModel = statusPayload?.model_present === true;
      const llamaHealthy = statusPayload?.llama_server?.healthy === true;
      const isHealthy = isLocalModelConnected(statusPayload) || (backendMode === "fake" && isReady);
      const isLoading = backendMode === "llama" && hasModel && !llamaHealthy && statusState === "BOOTING";
      const isFailed = backendMode === "llama" && statusState === "ERROR";
      badge.classList.remove("online", "loading", "failed", "offline");
      dot.classList.remove("online", "loading", "failed", "offline");
      dot.hidden = false;
      if (spinner) spinner.hidden = true;
      if (backendMode === "fake" && isReady) {
        badge.classList.add("online");
        dot.classList.add("online");
        label.textContent = "CONNECTED:Fake Backend";
      } else if (isHealthy) {
        badge.classList.add("online");
        dot.classList.add("online");
        label.textContent = `CONNECTED:llama.cpp${modelSuffix}${storageSuffix}`;
      } else if (isLoading) {
        badge.classList.add("loading");
        dot.classList.add("loading");
        dot.hidden = true;
        if (spinner) spinner.hidden = false;
        label.textContent = `LOADING:llama.cpp${modelSuffix}${storageSuffix}`;
      } else if (isFailed) {
        badge.classList.add("failed");
        dot.classList.add("failed");
        label.textContent = `FAILED:llama.cpp${modelSuffix}${storageSuffix}`;
      } else {
        badge.classList.add("offline");
        dot.classList.add("offline");
        label.textContent = "DISCONNECTED:llama.cpp";
      }
    }

    export function findResumableFailedModel(statusPayload) {
      const models = Array.isArray(statusPayload?.models) ? statusPayload.models : [];
      return models.find((item) => (
        String(item?.source_type || "") === "url"
        && String(item?.status || "").toLowerCase() === "failed"
      )) || null;
    }

    export function renderDownloadPrompt(statusPayload) {
      const prompt = document.getElementById("downloadPrompt");
      const hint = document.getElementById("downloadPromptHint");
      const startBtn = document.getElementById("startDownloadBtn");
      if (!prompt || !hint || !startBtn) return;

      const state = String(statusPayload?.state || "");
      const hasModel = statusPayload?.model_present === true;
      const downloadActive = statusPayload?.download?.active === true || state === "DOWNLOADING";
      if (hasModel || downloadActive || state === "READY") {
        prompt.hidden = true;
        startBtn.textContent = "Start download now";
        startBtn.disabled = false;
        return;
      }

      prompt.hidden = false;
      const countdownEnabled = statusPayload?.download?.countdown_enabled !== false;
      const autoStartRemaining = Number(statusPayload.download.auto_start_remaining_seconds);
      const freeBytes = Number(statusPayload?.system?.storage_free_bytes);
      const downloadError = String(statusPayload?.download?.error || "");
      const resumableFailedModel = findResumableFailedModel(statusPayload);
      if (
        downloadError === "insufficient_storage"
        || (Number.isFinite(freeBytes) && freeBytes < 512 * 1024 * 1024)
      ) {
        const free = formatBytes(statusPayload?.system?.storage_free_bytes);
        hint.textContent = `Not enough free storage for this model. Free space: ${free}. Delete model files and retry.`;
      } else if (downloadError === "download_failed" && resumableFailedModel) {
        hint.textContent =
          `Last download failed at ${formatBytes(resumableFailedModel?.bytes_downloaded)} ` +
          `of ${formatBytes(resumableFailedModel?.bytes_total)}. Resume when ready.`;
      } else if (!countdownEnabled) {
        hint.textContent = "Auto-download is paused. Start manually or re-enable it in settings.";
      } else if (Number.isFinite(autoStartRemaining) && autoStartRemaining > 0) {
        hint.textContent = `Auto-download starts in ${formatCountdownSeconds(autoStartRemaining)} if idle.`;
      } else {
        hint.textContent = "Auto-download starts soon if idle.";
      }

      if (appState.downloadStartInFlight) {
        startBtn.textContent = resumableFailedModel ? "Resuming..." : "Starting...";
        startBtn.disabled = true;
      } else {
        startBtn.textContent = resumableFailedModel ? "Resume download" : "Start download now";
        startBtn.disabled = false;
      }
    }

    export function renderStatusActions(statusPayload) {
      const actions = document.getElementById("statusActions");
      const resumeBtn = document.getElementById("statusResumeDownloadBtn");
      if (!actions || !resumeBtn) return;
      const hasModel = statusPayload?.model_present === true;
      const state = String(statusPayload?.state || "").toUpperCase();
      const downloadError = String(statusPayload?.download?.error || "");
      const resumableFailedModel = findResumableFailedModel(statusPayload);
      const showResume = hasModel && state === "READY" && downloadError === "download_failed" && !!resumableFailedModel;
      actions.hidden = !showResume;
      resumeBtn.disabled = appState.downloadStartInFlight;
      resumeBtn.textContent = appState.downloadStartInFlight ? "Resuming..." : "Resume";
    }

    export function renderCompatibilityWarnings(statusPayload) {
      const el = document.getElementById("compatibilityWarnings");
      const textEl = document.getElementById("compatibilityWarningsText");
      const overrideBtn = document.getElementById("compatibilityOverrideBtn");
      if (!el) return;
      const warnings = Array.isArray(statusPayload?.compatibility?.warnings)
        ? statusPayload.compatibility.warnings
        : [];
      const overrideEnabled = statusPayload?.compatibility?.override_enabled === true;
      if (!warnings.length) {
        el.hidden = true;
        if (textEl) textEl.textContent = "";
        else el.textContent = "";
        if (overrideBtn) {
          overrideBtn.hidden = true;
          overrideBtn.disabled = false;
          overrideBtn.textContent = "Try anyway";
        }
        return;
      }
      const text = warnings
        .map((item) => String(item?.message || "Compatibility warning"))
        .filter((item) => item.length > 0)
        .join(" | ");
      if (textEl) textEl.textContent = text || "Compatibility warning";
      else el.textContent = text || "Compatibility warning";
      if (overrideBtn) {
        overrideBtn.hidden = overrideEnabled;
      }
      el.hidden = false;
    }

    export function formatSidebarStatusDetail(statusPayload) {
      const download = statusPayload?.download || {};
      const downloaded = formatBytes(download.bytes_downloaded);
      const total = formatBytes(download.bytes_total);
      const state = String(statusPayload?.state || "").toUpperCase();
      const downloadActive = download.active === true || state === "DOWNLOADING";
      const downloadError = String(download.error || "");
      const resumableFailedModel = findResumableFailedModel(statusPayload);
      if (downloadActive) {
        return `Download: ${download.percent}% (${downloaded} / ${total})`;
      }
      if (downloadError === "download_failed" && resumableFailedModel) {
        return `Download failed (${downloaded} / ${total})`;
      }
      if (download.auto_download_paused === true) {
        return "Auto-download paused";
      }
      return "No active download";
    }

    export function formatModelStatusLabel(rawStatus) {
      const normalized = String(rawStatus || "unknown").trim().toLowerCase();
      if (!normalized) return "unknown";
      return normalized
        .replaceAll("_", " ")
        .split(" ")
        .filter(Boolean)
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(" ");
    }
