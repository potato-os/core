"use strict";

import { postJson } from "./utils.js";

// ── Model API Repository ───────────────────────────────────────────
//
// Pure async functions for model CRUD and settings API calls.
// Returns normalized { ok, ...data, error } — no DOM, no state mutation.

export async function registerModel(sourceUrl) {
  try {
    const { res, body } = await postJson("/internal/models/register", { source_url: sourceUrl });
    if (!res.ok) {
      return { ok: false, reason: body?.reason || null, status: res.status };
    }
    return { ok: true, reason: body?.reason || null };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function downloadModel(modelId) {
  try {
    const { res, body } = await postJson("/internal/models/download", { model_id: modelId });
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

export async function cancelDownload() {
  try {
    const { res, body } = await postJson("/internal/models/cancel-download", {});
    if (!res.ok) {
      return { ok: false, error: body?.reason || String(res.status) };
    }
    return { ok: true };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function activateModel(modelId) {
  try {
    const { res, body } = await postJson("/internal/models/activate", { model_id: modelId });
    if (!res.ok) {
      return { ok: false, error: body?.reason || String(res.status) };
    }
    return { ok: true };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function deleteModel(modelId) {
  try {
    const { res, body } = await postJson("/internal/models/delete", { model_id: modelId });
    if (!res.ok) {
      return { ok: false, error: body?.reason || String(res.status) };
    }
    return { ok: true };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function purgeModels() {
  try {
    const { res, body } = await postJson("/internal/models/purge", { reset_bootstrap_flag: false });
    if (!res.ok || body?.purged !== true) {
      return { ok: false, error: body?.reason || String(res.status) };
    }
    return { ok: true, purged: true };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function cancelUpload() {
  try {
    const { res, body } = await postJson("/internal/models/cancel-upload", {});
    if (!res.ok) {
      return { ok: false, error: body?.reason || String(res.status) };
    }
    return { ok: true };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function loadSettingsDocument() {
  try {
    const res = await fetch("/internal/settings-document");
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      return { ok: false, error: body?.reason || String(res.status) };
    }
    return { ok: true, document: body?.document || "" };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

export async function applySettingsDocument(documentText) {
  try {
    const { res, body } = await postJson("/internal/settings-document", { document: documentText });
    if (!res.ok) {
      return { ok: false, error: body?.reason || String(res.status) };
    }
    return { ok: true, activeModelId: body?.active_model_id || null };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}
