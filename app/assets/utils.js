"use strict";

import { RUNTIME_METRIC_SEVERITY_CLASSES } from "./state.js";

export function formatBytes(rawBytes) {
  const bytes = Number(rawBytes);
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1000 && unitIndex < units.length - 1) {
    value /= 1000;
    unitIndex += 1;
  }
  const precision = value >= 100 ? 0 : value >= 10 ? 1 : 2;
  return `${value.toFixed(precision)} ${units[unitIndex]}`;
}

export function formatPercent(rawValue, digits = 0) {
  const value = Number(rawValue);
  if (!Number.isFinite(value)) return "--";
  return `${value.toFixed(digits)}%`;
}

export function formatClockMHz(rawHz) {
  const hz = Number(rawHz);
  if (!Number.isFinite(hz) || hz <= 0) return "--";
  return `${Math.round(hz / 1_000_000)} MHz`;
}

export function normalizePercent(rawValue) {
  const value = Number(rawValue);
  if (!Number.isFinite(value)) return Number.NaN;
  return Math.min(100, Math.max(0, value));
}

export function percentFromRatio(rawCurrent, rawMax) {
  const current = Number(rawCurrent);
  const max = Number(rawMax);
  if (!Number.isFinite(current) || !Number.isFinite(max) || current < 0 || max <= 0) {
    return Number.NaN;
  }
  return normalizePercent((current / max) * 100);
}

export function runtimeMetricSeverityClass(rawPercent) {
  const percent = normalizePercent(rawPercent);
  if (!Number.isFinite(percent)) return "runtime-metric-normal";
  if (percent >= 90) return "runtime-metric-critical";
  if (percent >= 75) return "runtime-metric-high";
  if (percent >= 60) return "runtime-metric-warn";
  return "runtime-metric-normal";
}

export function applyRuntimeMetricSeverity(element, rawPercent) {
  if (!element) return;
  element.classList.remove(...RUNTIME_METRIC_SEVERITY_CLASSES);
  element.classList.add(runtimeMetricSeverityClass(rawPercent));
}

export function formatCountdownSeconds(rawSeconds) {
  const totalSeconds = Math.max(0, Math.floor(Number(rawSeconds) || 0));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function estimateDataUrlBytes(dataUrl) {
  const marker = "base64,";
  const idx = dataUrl.indexOf(marker);
  if (idx < 0) return 0;
  const base64Payload = dataUrl.slice(idx + marker.length);
  const padding = base64Payload.endsWith("==") ? 2 : base64Payload.endsWith("=") ? 1 : 0;
  return Math.floor((base64Payload.length * 3) / 4) - padding;
}

export async function postJson(url, payload) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  let body = null;
  try {
    body = await res.json();
  } catch (_err) {
    body = null;
  }
  return { res, body };
}
