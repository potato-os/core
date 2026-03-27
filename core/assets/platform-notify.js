"use strict";

// ── Platform Notification Bar ──────────────────────────────────────
//
// Displays transient platform operation feedback in a notification
// bar below the header. Replaces appendMessage("assistant", ...) for
// non-chat platform operations (runtime switch, power calibration,
// model management, etc.).
//
// Single notification at a time — new ones replace the old one.
// When a modal is open the auto-dismiss is deferred — the bar is
// hidden behind the backdrop so the user can't see it yet.

import { appState } from "./state.js";

let _dismissTimer = null;
let _pendingDurationMs = 0;

function ensureNoticeBar() {
  let bar = document.getElementById("platformNotice");
  if (bar) return bar;
  const header = document.querySelector(".chat-header");
  if (!header?.parentElement) return null;
  bar = document.createElement("div");
  bar.id = "platformNotice";
  bar.className = "platform-notice";
  bar.hidden = true;
  bar.setAttribute("aria-live", "polite");
  header.insertAdjacentElement("afterend", bar);
  return bar;
}

function isModalOpen() {
  return appState.settingsModalOpen || appState.legacySettingsModalOpen;
}

export function showPlatformNotice(message, { level = "info", durationMs = 6000 } = {}) {
  const bar = ensureNoticeBar();
  if (!bar) return;
  if (_dismissTimer) {
    clearTimeout(_dismissTimer);
    _dismissTimer = null;
  }
  _pendingDurationMs = 0;
  bar.className = `platform-notice platform-notice--${level}`;
  bar.textContent = message;
  bar.hidden = false;
  if (durationMs > 0) {
    if (isModalOpen()) {
      _pendingDurationMs = durationMs;
    } else {
      _dismissTimer = setTimeout(() => dismissPlatformNotice(), durationMs);
    }
  }
}

export function flushPendingNoticeDismissal() {
  if (_pendingDurationMs > 0 && !isModalOpen()) {
    const ms = _pendingDurationMs;
    _pendingDurationMs = 0;
    if (_dismissTimer) clearTimeout(_dismissTimer);
    _dismissTimer = setTimeout(() => dismissPlatformNotice(), ms);
  }
}

export function dismissPlatformNotice() {
  if (_dismissTimer) {
    clearTimeout(_dismissTimer);
    _dismissTimer = null;
  }
  const bar = document.getElementById("platformNotice");
  if (bar) {
    bar.hidden = true;
    bar.textContent = "";
  }
}
