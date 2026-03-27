"use strict";

import { appState } from "./state.js";

function truncateModelName(filename) {
  const name = String(filename || "");
  const base = name.replace(/\.gguf$/i, "");
  return base.length > 32 ? base.slice(0, 29) + "..." : base;
}

function statusLabel(status) {
  const s = String(status || "").toLowerCase();
  if (s === "ready") return "Ready";
  if (s === "downloading") return "Downloading";
  if (s === "failed" || s === "error") return "Failed";
  if (s === "not_downloaded") return "Not downloaded";
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function populateModelSwitcher() {
  const list = document.getElementById("modelSwitcherList");
  if (!list) return;
  const models = Array.isArray(appState.latestStatus?.models) ? appState.latestStatus.models : [];
  list.innerHTML = "";
  if (models.length === 0) {
    const li = document.createElement("li");
    li.className = "model-switcher-item disabled";
    li.textContent = "No models installed";
    list.appendChild(li);
    return;
  }
  for (const model of models) {
    const li = document.createElement("li");
    const isReady = String(model.status || "").toLowerCase() === "ready";
    const isActive = model.is_active === true;
    li.className = "model-switcher-item";
    li.dataset.modelId = String(model.id || "");
    li.setAttribute("role", "option");
    li.setAttribute("aria-selected", String(isActive));
    if (isActive) li.classList.add("active");
    if (!isReady) li.classList.add("disabled");

    const check = document.createElement("span");
    check.className = "model-switcher-check";
    check.textContent = isActive ? "✓" : "";
    check.setAttribute("aria-hidden", "true");

    const name = document.createElement("span");
    name.className = "model-switcher-name";
    name.textContent = truncateModelName(model.filename);
    name.title = String(model.filename || "");

    const statusChip = document.createElement("span");
    statusChip.className = "model-switcher-status";
    statusChip.textContent = isActive ? "" : statusLabel(model.status);

    li.appendChild(check);
    li.appendChild(name);
    li.appendChild(statusChip);
    list.appendChild(li);
  }
}

export function openModelSwitcher() {
  const el = document.getElementById("modelSwitcher");
  const badge = document.getElementById("statusBadge");
  if (!el) return;
  populateModelSwitcher();
  el.hidden = false;
  requestAnimationFrame(() => {
    el.classList.add("open");
    el.focus();
  });
  if (badge) badge.setAttribute("aria-expanded", "true");
  appState.modelSwitcherOpen = true;
}

export function closeModelSwitcher() {
  const el = document.getElementById("modelSwitcher");
  const badge = document.getElementById("statusBadge");
  if (!el) return;
  el.classList.remove("open");
  el.hidden = true;
  if (badge) badge.setAttribute("aria-expanded", "false");
  appState.modelSwitcherOpen = false;
}

export function toggleModelSwitcher() {
  if (appState.modelSwitcherOpen) {
    closeModelSwitcher();
  } else {
    openModelSwitcher();
  }
}
