"use strict";

import { postJson } from "./utils.js";

// ── Platform API Repository ────────────────────────────────────────
//
// Pure async functions for platform-level API calls.
// Returns normalized { ok, ...data, error } — no DOM, no state mutation.

// ── Runtime ────────────────────────────────────────────────────────

export async function switchRuntime(family) {
  try {
    const { res, body } = await postJson("/internal/llama-runtime/switch", { family });
    if (!res.ok || body?.switched !== true) {
      return { ok: false, error: body?.reason || String(res.status) };
    }
    return { ok: true, family: body?.family || family };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function setMemoryLoadingMode(mode) {
  try {
    const { res, body } = await postJson("/internal/llama-runtime/memory-loading", { mode });
    if (!res.ok) {
      return { ok: false, error: body?.reason || String(res.status) };
    }
    return {
      ok: true,
      memoryLoading: body?.memory_loading || null,
      restartReason: body?.restart_reason || "requested",
    };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function resetRuntime() {
  try {
    const res = await fetch("/internal/reset-runtime", {
      method: "POST",
      headers: { "content-type": "application/json" },
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      return { ok: false, error: body?.reason || String(res.status) };
    }
    return { ok: true, started: body?.started === true, reason: body?.reason || null };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

// ── Compatibility ──────────────────────────────────────────────────

export async function setLargeModelOverride(enabled) {
  try {
    const { res, body } = await postJson("/internal/compatibility/large-model-override", { enabled: Boolean(enabled) });
    if (!res.ok || body?.updated !== true) {
      return { ok: false, error: body?.reason || String(res.status) };
    }
    return { ok: true, override: body?.override || null };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

// ── Power calibration ──────────────────────────────────────────────

export async function captureCalibrationSample(wallWatts) {
  try {
    const { res, body } = await postJson("/internal/power-calibration/sample", { wall_watts: wallWatts });
    if (!res.ok || body?.captured !== true) {
      return { ok: false, error: body?.reason || String(res.status) };
    }
    return { ok: true, sample: body?.sample || null };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function fitCalibrationModel() {
  try {
    const { res, body } = await postJson("/internal/power-calibration/fit", {});
    if (!res.ok || body?.updated !== true) {
      return { ok: false, error: body?.reason || String(res.status) };
    }
    return { ok: true, calibration: body?.calibration || null };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function resetCalibration() {
  try {
    const { res, body } = await postJson("/internal/power-calibration/reset", {});
    if (!res.ok || body?.updated !== true) {
      return { ok: false, error: body?.reason || String(res.status) };
    }
    return { ok: true };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

// ── Download control ───────────────────────────────────────────────

export async function setDownloadCountdown(enabled) {
  try {
    const { res, body } = await postJson("/internal/download-countdown", { enabled });
    if (!res.ok) {
      return { ok: false, error: body?.reason || String(res.status) };
    }
    return { ok: true };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function startDefaultModelDownload() {
  try {
    const res = await fetch("/internal/start-model-download", {
      method: "POST",
      headers: { "content-type": "application/json" },
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      return { ok: false, error: body?.reason || String(res.status) };
    }
    return {
      ok: true,
      started: body?.started === true,
      reason: body?.reason || null,
      freeBytes: body?.free_bytes ?? null,
      requiredBytes: body?.required_bytes ?? null,
    };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

// ── Update ─────────────────────────────────────────────────────────

export async function checkForUpdate() {
  try {
    const res = await fetch("/internal/update/check", {
      method: "POST",
      headers: { "content-type": "application/json" },
    });
    if (res.status === 409) {
      const body = await res.json().catch(() => ({}));
      return { ok: false, error: body?.reason || "orchestrator disabled" };
    }
    if (!res.ok) {
      return { ok: false, error: `HTTP ${res.status}` };
    }
    return { ok: true };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function startUpdate() {
  try {
    const res = await fetch("/internal/update/start", {
      method: "POST",
      headers: { "content-type": "application/json" },
    });
    const body = await res.json().catch(() => ({}));
    if (res.status === 409 || !body?.started) {
      return { ok: false, started: false, reason: body?.reason || "unknown" };
    }
    return { ok: true, started: true };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}
