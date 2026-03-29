"use strict";

// ── Platform Control Center ─────────────────────────────────────────
//
// Ownership boundary (ticket #144):
//
// PLATFORM (this module + platform-api.js + model-api.js):
//   Runtime switch, memory loading, power calibration, compatibility
//   override, runtime reset, model CRUD (register/download/cancel/
//   activate/delete/purge/upload), download countdown, updates.
//   Feedback: platform-notify.js bar + runtime-ui.js status fields.
//
// APP-SPECIFIC (chat.js + chat-engine.js):
//   Chat send/receive, message rendering, edit modal, session
//   management, image handling, composer activity/status chip.
//   Feedback: appendMessage() into chat stream.
//
// SHARED (settings-ui.js):
//   Model settings save, projector download, YAML.
//   Platform callbacks: setSidebarOpen, pollStatus.
//   Chat callbacks: setComposerActivity, focusPromptInput, etc.
//   Platform settings work without chat callbacks.

import { appState, RUNTIME_RECONNECT_INTERVAL_MS, RUNTIME_RECONNECT_TIMEOUT_MS, RUNTIME_RECONNECT_MAX_ATTEMPTS } from "./state.js";
import { formatBytes } from "./utils.js";
import { isLocalModelConnected, findResumableFailedModel, renderDownloadPrompt } from "./status.js";
import { setModelUploadStatus, setLlamaRuntimeSwitchStatus, setLlamaRuntimeSwitchButtonState, setLlamaMemoryLoadingStatus, setLlamaMemoryLoadingButtonState, setLargeModelOverrideStatus, setLargeModelOverrideButtonState, setPowerCalibrationStatus, setPowerCalibrationButtonsState } from "./runtime-ui.js";
import { setUpdateCheckInFlight, setUpdateStartInFlight, isUpdateExecutionActive, openChangelogModal } from "./update-ui.js";
import { setModelUrlStatus, formatModelUrlStatus, resolveSelectedSettingsModel, selectedModelHasUnsavedChanges, blockModelSelectionChange, renderSettingsWorkspace } from "./settings-ui.js";
import { showPlatformNotice } from "./platform-notify.js";
import * as platformApi from "./platform-api.js";
import * as modelApi from "./model-api.js";

// Composer activity is owned by the active app — no-op if no app loaded
let _setComposerActivity = () => {};
export function registerComposerActivity(fn) { _setComposerActivity = fn; }
export function resetComposerActivity() { _setComposerActivity = () => {}; }
function setComposerActivity(msg) { _setComposerActivity(msg); }

let _shell = {};

export function registerPlatformShell({ pollStatus }) {
  _shell = { pollStatus };
}

// ── Runtime controls ───────────────────────────────────────────────

export async function switchLlamaRuntimeBundle() {
  if (appState.llamaRuntimeSwitchInFlight) return;
  const select = document.getElementById("llamaRuntimeFamilySelect");
  const family = String(select?.value || "").trim();
  if (!family) {
    showPlatformNotice("No llama runtime selected.", { level: "warn" });
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
    const result = await platformApi.switchRuntime(family);
    if (!result.ok) {
      showPlatformNotice(`Could not switch llama runtime (${result.error}).`, { level: "error" });
      return;
    }
    showPlatformNotice(`Switched llama runtime to ${result.family}.`, { level: "success" });
    setComposerActivity("Llama runtime switched. Reconnecting...");
  } finally {
    appState.llamaRuntimeSwitchInFlight = false;
    setLlamaRuntimeSwitchButtonState(false);
    await _shell.pollStatus();
  }
}

export async function applyLlamaMemoryLoadingMode() {
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
    const result = await platformApi.setMemoryLoadingMode(mode);
    if (!result.ok) {
      showPlatformNotice(`Could not update model memory loading: ${result.error}.`, { level: "error" });
      setLlamaMemoryLoadingStatus(`Last memory loading update error: ${result.error}`);
      return;
    }
    showPlatformNotice(
      `Applied model memory loading: ${result.memoryLoading?.label || mode}. ` +
      `Runtime restart: ${result.restartReason}.`,
      { level: "success" },
    );
    await _shell.pollStatus();
  } finally {
    appState.llamaMemoryLoadingApplyInFlight = false;
    setLlamaMemoryLoadingButtonState(false);
  }
}

// ── Compatibility override ─────────────────────────────────────────

export async function applyLargeModelCompatibilityOverride(enabled) {
  if (appState.largeModelOverrideApplyInFlight) return;
  appState.largeModelOverrideApplyInFlight = true;
  setLargeModelOverrideButtonState(true);
  setLargeModelOverrideStatus(
    enabled
      ? "Applying compatibility override: try unsupported models..."
      : "Applying compatibility override: restore warnings..."
  );
  try {
    const result = await platformApi.setLargeModelOverride(enabled);
    if (!result.ok) {
      showPlatformNotice(`Could not update compatibility override (${result.error}).`, { level: "error" });
      setLargeModelOverrideStatus(`Last compatibility override error: ${result.error}`);
      return;
    }
    showPlatformNotice(
      result.override?.enabled
        ? "Enabled compatibility override. Potato will try unsupported large models."
        : "Disabled compatibility override. Default large-model warnings are active again.",
      { level: "success" },
    );
    setLargeModelOverrideStatus(
      result.override?.enabled
        ? "Compatibility override: trying unsupported large models is enabled"
        : "Compatibility override: default warnings"
    );
  } finally {
    appState.largeModelOverrideApplyInFlight = false;
    setLargeModelOverrideButtonState(false);
    await _shell.pollStatus();
  }
}

export async function applyLargeModelOverrideFromSettings() {
  const checkbox = document.getElementById("largeModelOverrideEnabled");
  await applyLargeModelCompatibilityOverride(checkbox?.checked === true);
}

export async function allowUnsupportedLargeModelFromWarning() {
  const confirmed = window.confirm(
    "Try loading unsupported large models anyway on this device? " +
    "This may fail or be unstable, but Potato will stop warning-blocking this attempt."
  );
  if (!confirmed) return;
  await applyLargeModelCompatibilityOverride(true);
}

// ── Power calibration ──────────────────────────────────────────────

export async function capturePowerCalibrationSample() {
  if (appState.powerCalibrationActionInFlight) return;
  const input = document.getElementById("powerCalibrationWallWatts");
  const wallWatts = Number(input?.value);
  if (!Number.isFinite(wallWatts) || wallWatts <= 0) {
    showPlatformNotice("Enter a valid wall meter reading in watts before capturing a sample.", { level: "warn" });
    setPowerCalibrationStatus("Power calibration error: invalid wall meter reading");
    return;
  }

  appState.powerCalibrationActionInFlight = true;
  setPowerCalibrationButtonsState(true);
  setPowerCalibrationStatus("Capturing power calibration sample...");
  try {
    const result = await platformApi.captureCalibrationSample(wallWatts);
    if (!result.ok) {
      showPlatformNotice(`Could not capture power sample (${result.error}).`, { level: "error" });
      setPowerCalibrationStatus(`Power calibration error: ${result.error}`);
      return;
    }
    showPlatformNotice(
      `Captured power calibration sample (wall ${Number(wallWatts).toFixed(2)} W vs raw ${Number(result.sample?.raw_pmic_watts || 0).toFixed(3)} W).`,
      { level: "success" },
    );
  } finally {
    appState.powerCalibrationActionInFlight = false;
    setPowerCalibrationButtonsState(false);
    await _shell.pollStatus();
  }
}

export async function fitPowerCalibrationModel() {
  if (appState.powerCalibrationActionInFlight) return;
  appState.powerCalibrationActionInFlight = true;
  setPowerCalibrationButtonsState(true);
  setPowerCalibrationStatus("Computing power calibration...");
  try {
    const result = await platformApi.fitCalibrationModel();
    if (!result.ok) {
      showPlatformNotice(`Could not compute power calibration (${result.error}).`, { level: "error" });
      setPowerCalibrationStatus(`Power calibration error: ${result.error}`);
      return;
    }
    const cal = result.calibration || {};
    showPlatformNotice(
      `Power calibration updated (a=${Number(cal?.a || 0).toFixed(4)}, b=${Number(cal?.b || 0).toFixed(4)}, samples=${Number(cal?.sample_count || 0)}).`,
      { level: "success" },
    );
  } finally {
    appState.powerCalibrationActionInFlight = false;
    setPowerCalibrationButtonsState(false);
    await _shell.pollStatus();
  }
}

export async function resetPowerCalibrationModel() {
  if (appState.powerCalibrationActionInFlight) return;
  const confirmed = window.confirm(
    "Reset power calibration to the default correction model? Saved wall-meter samples will be cleared."
  );
  if (!confirmed) return;

  appState.powerCalibrationActionInFlight = true;
  setPowerCalibrationButtonsState(true);
  setPowerCalibrationStatus("Resetting power calibration...");
  try {
    const result = await platformApi.resetCalibration();
    if (!result.ok) {
      showPlatformNotice(`Could not reset power calibration (${result.error}).`, { level: "error" });
      setPowerCalibrationStatus(`Power calibration error: ${result.error}`);
      return;
    }
    showPlatformNotice("Power calibration reset. Using default correction again.", { level: "success" });
  } finally {
    appState.powerCalibrationActionInFlight = false;
    setPowerCalibrationButtonsState(false);
    await _shell.pollStatus();
  }
}

// ── Download control ───────────────────────────────────────────────

export async function updateCountdownPreference(enabled) {
  const result = await platformApi.setDownloadCountdown(enabled);
  if (!result.ok) {
    showPlatformNotice(`Could not update auto-download: ${result.error}`, { level: "error" });
  }
  await _shell.pollStatus();
}

// ── Model operations ───────────────────────────────────────────────

function findModelInLatestStatus(modelId) {
  const models = Array.isArray(appState.latestStatus?.models) ? appState.latestStatus.models : [];
  return models.find((item) => String(item?.id || "") === String(modelId || "")) || null;
}

export async function registerModelFromUrl() {
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
    const result = await modelApi.registerModel(sourceUrl);
    if (!result.ok) {
      setModelUrlStatus(result.reason
        ? formatModelUrlStatus(result.reason, result.status)
        : `Could not add model URL: ${result.error}`);
      return;
    }
    setModelUrlStatus(
      result.reason === "already_exists"
        ? "That model URL is already registered."
        : "Model URL added."
    );
    if (input) input.value = "";
  } finally {
    appState.modelActionInFlight = false;
    await _shell.pollStatus();
  }
}

export async function startModelDownloadForModel(modelId) {
  if (!modelId) return;
  if (appState.modelActionInFlight) return;
  appState.modelActionInFlight = true;
  try {
    const result = await modelApi.downloadModel(modelId);
    if (!result.ok) {
      showPlatformNotice(`Could not start model download (${result.error}).`, { level: "error" });
      return;
    }
    if (!result.started && result.reason === "insufficient_storage") {
      const freeInfo = result.freeBytes != null ? ` (${formatBytes(result.freeBytes)} free, ${formatBytes(result.requiredBytes)} needed)` : "";
      showPlatformNotice(`Not enough free storage to download this model${freeInfo}. Free up space or delete unused models and try again.`, { level: "warn" });
      setComposerActivity("Model likely too large for free storage. Delete files and retry.");
    }
  } finally {
    appState.modelActionInFlight = false;
    await _shell.pollStatus();
  }
}

export async function cancelActiveModelDownload(modelId = null) {
  if (appState.modelActionInFlight) return;
  const targetModel = findModelInLatestStatus(modelId) || findModelInLatestStatus(appState.latestStatus?.download?.current_model_id);
  const targetName = String(targetModel?.filename || "this model");
  const confirmed = window.confirm(`Stop the current download for ${targetName}?`);
  if (!confirmed) return;
  appState.modelActionInFlight = true;
  try {
    const result = await modelApi.cancelDownload();
    if (!result.ok) {
      showPlatformNotice(`Could not cancel model download (${result.error}).`, { level: "error" });
    }
  } finally {
    appState.modelActionInFlight = false;
    await _shell.pollStatus();
  }
}

export async function activateSelectedModel(modelId) {
  if (!modelId) return;
  if (appState.modelActionInFlight) return;
  appState.modelActionInFlight = true;
  try {
    const result = await modelApi.activateModel(modelId);
    if (!result.ok) {
      showPlatformNotice(`Could not activate model (${result.error}).`, { level: "error" });
      return;
    }
    setComposerActivity("Switching active model...");
  } finally {
    appState.modelActionInFlight = false;
    await _shell.pollStatus();
  }
}

export async function deleteSelectedModel(modelId) {
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
    const result = await modelApi.deleteModel(modelId);
    if (!result.ok) {
      showPlatformNotice(`Could not delete model (${result.error}).`, { level: "error" });
      return;
    }
  } finally {
    appState.modelActionInFlight = false;
    await _shell.pollStatus();
  }
}

export async function purgeAllModels() {
  if (appState.modelActionInFlight) return;
  const confirmed = window.confirm(
    "Delete ALL model files and clear model/download metadata now?"
  );
  if (!confirmed) return;
  appState.modelActionInFlight = true;
  try {
    const result = await modelApi.purgeModels();
    if (!result.ok) {
      showPlatformNotice(`Could not purge models (${result.error}).`, { level: "error" });
      return;
    }
    setComposerActivity("All models and metadata were cleared.");
  } finally {
    appState.modelActionInFlight = false;
    await _shell.pollStatus();
  }
}

export async function uploadLocalModel() {
  if (appState.uploadRequest) return;
  const input = document.getElementById("modelUploadInput");
  const file = input?.files?.[0];
  if (!file) {
    showPlatformNotice("Pick a .gguf file to upload.", { level: "warn" });
    return;
  }
  if (!String(file.name || "").toLowerCase().endsWith(".gguf")) {
    showPlatformNotice("Only .gguf model files are supported.", { level: "warn" });
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
    await modelApi.cancelUpload();
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

export function cancelLocalModelUpload() {
  if (!appState.uploadRequest) return;
  appState.uploadRequest.abort();
}

export async function startModelDownload() {
  if (appState.downloadStartInFlight) return;
  appState.downloadStartInFlight = true;
  renderDownloadPrompt(appState.latestStatus || { download: { auto_start_remaining_seconds: 0 } });
  try {
    const resumableFailedModel = findResumableFailedModel(appState.latestStatus);
    const failedDownload = String(appState.latestStatus?.download?.error || "") === "download_failed";
    let result;
    if (resumableFailedModel && failedDownload) {
      result = await modelApi.downloadModel(resumableFailedModel.id);
    } else {
      result = await platformApi.startDefaultModelDownload();
    }
    if (!result.ok) {
      showPlatformNotice(
        `${resumableFailedModel && failedDownload ? "Could not resume model download" : "Could not start model download"} (${result.error}).`,
        { level: "error" },
      );
      return;
    }
    if (!result.started && result.reason === "already_running") {
      setComposerActivity("Model download already running.");
    } else if (!result.started && result.reason === "model_present") {
      setComposerActivity("Model already present.");
    } else if (!result.started && result.reason === "insufficient_storage") {
      const freeInfo = result.freeBytes != null ? ` (${formatBytes(result.freeBytes)} free, ${formatBytes(result.requiredBytes)} needed)` : "";
      showPlatformNotice(`Not enough free storage to download this model${freeInfo}. Free up space or delete unused models and try again.`, { level: "warn" });
      setComposerActivity("Model likely too large for free storage. Delete files and retry.");
    } else if (result.started) {
      setComposerActivity(resumableFailedModel && failedDownload ? "Model download resumed." : "Model download started.");
    }
  } finally {
    appState.downloadStartInFlight = false;
    await _shell.pollStatus();
  }
}

// ── Runtime reset ──────────────────────────────────────────────────

function setRuntimeResetButtonState(inFlight) {
  const btn = document.getElementById("resetRuntimeBtn");
  if (!btn) return;
  btn.disabled = Boolean(inFlight);
  btn.textContent = inFlight
    ? "Restarting runtime..."
    : "Unload model + clean memory + restart";
}

export function stopRuntimeReconnectWatch() {
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
    showPlatformNotice(
      "Runtime reset is taking longer than expected. It may still be loading the model. Check status in a few moments.",
      { level: "warn" },
    );
    return;
  }
  appState.runtimeReconnectWatchTimer = window.setTimeout(stepRuntimeReconnectWatch, RUNTIME_RECONNECT_INTERVAL_MS);
}

export function startRuntimeReconnectWatch() {
  stopRuntimeReconnectWatch();
  appState.runtimeReconnectWatchActive = true;
  appState.runtimeReconnectAttempts = 0;
  setComposerActivity("Runtime reset in progress. Reconnecting...");
  stepRuntimeReconnectWatch();
}

export async function resetRuntimeHeavy() {
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
    const result = await platformApi.resetRuntime();
    if (!result.ok) {
      showPlatformNotice(`Could not start runtime reset (${result.error}).`, { level: "error" });
      return;
    }
    if (result.started) {
      shouldTrackReconnect = true;
      showPlatformNotice(
        "Runtime reset started. Unloading model from memory and reclaiming RAM/swap. Model files on disk are unchanged.",
        { level: "info" },
      );
    } else {
      showPlatformNotice(`Runtime reset did not start (${result.reason || "unknown"}).`, { level: "warn" });
    }
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

// ── Update operations ──────────────────────────────────────────────

export async function checkForUpdate() {
  if (appState.updateCheckInFlight) return;
  if (isUpdateExecutionActive()) return;
  appState.updateCheckInFlight = true;
  setUpdateCheckInFlight(true);
  try {
    const result = await platformApi.checkForUpdate();
    if (!result.ok) {
      showPlatformNotice(`Could not check for updates (${result.error}).`, { level: "error" });
      return;
    }
    await _shell.pollStatus();
  } finally {
    appState.updateCheckInFlight = false;
    setUpdateCheckInFlight(false);
  }
}

export async function startUpdate() {
  if (appState.updateStartInFlight) return;
  appState.updateStartInFlight = true;
  setUpdateStartInFlight(true);
  try {
    const result = await platformApi.startUpdate();
    if (!result.ok) {
      const reasons = {
        orchestrator_disabled: "Updates are not available (orchestrator disabled).",
        no_update_available: "No update available. Try checking again.",
        no_tarball_url: "No download URL found for this update. Try checking again.",
        download_active: "A model download is in progress. Wait for it to finish.",
        update_in_progress: "An update is already in progress.",
      };
      showPlatformNotice(reasons[result.reason] || `Could not start update (${result.reason || result.error || "unknown"}).`, { level: "error" });
      return;
    }
    await _shell.pollStatus();
  } finally {
    appState.updateStartInFlight = false;
    setUpdateStartInFlight(false);
  }
}

export function showUpdateReleaseNotes() {
  const update = appState.latestStatus?.update;
  const notes = update?.release_notes;
  const latest = String(update?.latest_version || "");
  const current = String(update?.current_version || "");
  const subtitle = (current && latest) ? `v${current} \u2192 v${latest}` : "";
  openChangelogModal({ version: latest, notes: notes || null, subtitle });
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
    window.setTimeout(() => {
      const hasInput = document.querySelector("#userPrompt")?.value?.trim();
      if (!appState.requestInFlight && !hasInput) window.location.reload();
    }, 2000);
    return;
  }
  if (updateState === "failed") {
    stopUpdateReconnectWatch();
    setComposerActivity("");
    showPlatformNotice("Update may not have applied correctly. Check the version in the sidebar.", { level: "warn" });
    return;
  }
  if (appState.updateReconnectAttempts >= RUNTIME_RECONNECT_MAX_ATTEMPTS) {
    stopUpdateReconnectWatch();
    setComposerActivity("");
    showPlatformNotice("Reconnection is taking longer than expected. The update may still be completing.", { level: "warn" });
    return;
  }
  appState.updateReconnectTimer = window.setTimeout(stepUpdateReconnectWatch, RUNTIME_RECONNECT_INTERVAL_MS);
}

export function startUpdateReconnectWatch() {
  stopUpdateReconnectWatch();
  appState.updateReconnectActive = true;
  appState.updateReconnectAttempts = 0;
  setComposerActivity("Potato OS is restarting after update. Reconnecting...");
  stepUpdateReconnectWatch();
}

// ── Model list event handling ──────────────────────────────────────

export function handleModelsListClick(event) {
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
}
