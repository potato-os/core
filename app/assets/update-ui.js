"use strict";

import { appState } from "./state.js";

const ACTIVE_STATES = new Set(["downloading", "staging", "applying", "restart_pending"]);

let _onRestartPending = null;

export function registerUpdateCallbacks({ onRestartPending }) {
  _onRestartPending = onRestartPending || null;
}

export function renderUpdateCard(updatePayload) {
  const card = document.getElementById("updateCard");
  const title = document.getElementById("updateCardTitle");
  const hint = document.getElementById("updateCardHint");
  const progressWrap = document.getElementById("updateCardProgress");
  const progressBar = document.getElementById("updateCardProgressBar");
  const startBtn = document.getElementById("updateStartBtn");
  const notesBtn = document.getElementById("updateNotesBtn");
  const retryBtn = document.getElementById("updateRetryBtn");
  if (!card || !title || !hint) return;

  const state = String(updatePayload?.state || "idle");
  const available = updatePayload?.available === true;
  const deferred = updatePayload?.deferred === true;
  const latest = String(updatePayload?.latest_version || "");
  const current = String(updatePayload?.current_version || "");
  const phase = String(updatePayload?.progress?.phase || "");
  const percent = Number(updatePayload?.progress?.percent || 0);
  const error = String(updatePayload?.progress?.error || "");
  const hasNotes = Boolean(updatePayload?.release_notes);
  const isActive = ACTIVE_STATES.has(state);

  // Default: hide everything
  if (startBtn) startBtn.hidden = true;
  if (notesBtn) notesBtn.hidden = true;
  if (retryBtn) retryBtn.hidden = true;
  if (progressWrap) progressWrap.hidden = true;

  // Block check button during active execution to prevent update.json overwrite
  const checkBtn = document.getElementById("updateCheckBtn");
  if (checkBtn) {
    checkBtn.disabled = appState.updateCheckInFlight || isActive;
  }

  // No update, idle — hide card unless there's a check error to surface
  if (state === "idle" && !available) {
    if (error) {
      // Check failed (rate_limited, network_error, etc.) — show feedback
      card.hidden = false;
      const errorLabels = {
        rate_limited: "GitHub rate limit reached. Try again later.",
        network_error: "Could not reach GitHub. Check network connection.",
        parse_error: "Received an unexpected response from GitHub.",
        unknown_error: "An unexpected error occurred while checking for updates.",
      };
      title.textContent = "Update check failed";
      hint.textContent = errorLabels[error] || `Check failed: ${error}`;
      return;
    }
    card.hidden = true;
    return;
  }

  card.hidden = false;

  if (state === "failed") {
    title.textContent = "Update failed";
    hint.textContent = error || "An unknown error occurred during the update.";
    if (retryBtn) {
      retryBtn.hidden = false;
      retryBtn.disabled = appState.updateStartInFlight;
      retryBtn.textContent = appState.updateStartInFlight ? "Retrying..." : "Retry";
    }
    return;
  }

  if (state === "restart_pending") {
    title.textContent = "Restarting...";
    hint.textContent = "Update installed. Potato OS is restarting. This page will reconnect automatically.";
    if (_onRestartPending && !appState.updateReconnectActive) {
      _onRestartPending();
    }
    return;
  }

  if (state === "applying") {
    title.textContent = "Installing update...";
    hint.textContent = latest
      ? `Applying v${latest}. Do not power off.`
      : "Applying update. Do not power off.";
    _showProgress(progressWrap, progressBar, percent);
    return;
  }

  if (state === "staging") {
    title.textContent = "Preparing update...";
    hint.textContent = latest
      ? `Extracting v${latest}...`
      : "Extracting update...";
    _showProgress(progressWrap, progressBar, percent);
    return;
  }

  if (state === "downloading") {
    title.textContent = "Downloading update...";
    hint.textContent = latest
      ? `Downloading v${latest}: ${percent}%`
      : `Downloading: ${percent}%`;
    _showProgress(progressWrap, progressBar, percent);
    return;
  }

  // idle + available
  title.textContent = latest ? `Update available: v${latest}` : "Update available";
  hint.textContent = deferred
    ? "A model download is in progress. Update will be available after it completes."
    : (current ? `Current: v${current}. A new version is ready.` : "A new version is ready.");

  if (startBtn && !deferred) {
    startBtn.hidden = false;
    startBtn.disabled = appState.updateStartInFlight || isActive;
    startBtn.textContent = appState.updateStartInFlight ? "Starting..." : "Install update";
  }
  if (notesBtn && hasNotes) {
    notesBtn.hidden = false;
  }
}

function _showProgress(wrap, bar, percent) {
  if (!wrap || !bar) return;
  wrap.hidden = false;
  bar.style.width = `${Math.max(0, Math.min(100, percent))}%`;
}

export function isUpdateExecutionActive() {
  const state = String(appState.latestStatus?.update?.state || "idle");
  return ACTIVE_STATES.has(state);
}

export function setUpdateCheckInFlight(inFlight) {
  const btn = document.getElementById("updateCheckBtn");
  if (!btn) return;
  const blocked = Boolean(inFlight) || isUpdateExecutionActive();
  btn.disabled = blocked;
  btn.textContent = inFlight ? "Checking..." : "Check for updates";
}

export function setUpdateStartInFlight(inFlight) {
  appState.updateStartInFlight = Boolean(inFlight);
  const startBtn = document.getElementById("updateStartBtn");
  const retryBtn = document.getElementById("updateRetryBtn");
  if (startBtn) {
    startBtn.disabled = Boolean(inFlight);
    startBtn.textContent = inFlight ? "Starting..." : "Install update";
  }
  if (retryBtn) {
    retryBtn.disabled = Boolean(inFlight);
    retryBtn.textContent = inFlight ? "Retrying..." : "Retry";
  }
}
