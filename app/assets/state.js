"use strict";

// ── Shared mutable state ────────────────────────────────────────────
// All modules import this object and access state via `state.varName`.
// NEVER destructure (`const { x } = state`) — always use `state.x`
// for live updates across modules.

export const appState = {
  requestInFlight: false,
  activeRequest: null,
  activePrefillProgress: null,
  imageCancelRecoveryTimer: null,
  imageCancelRestartTimer: null,
  statusChipVisibleAtMs: 0,
  statusChipHideTimer: null,
  latestStatus: null,
  downloadStartInFlight: false,
  modelActionInFlight: false,
  llamaRuntimeSwitchInFlight: false,
  llamaMemoryLoadingApplyInFlight: false,
  largeModelOverrideApplyInFlight: false,
  powerCalibrationActionInFlight: false,
  uploadRequest: null,
  runtimeResetInFlight: false,
  runtimeReconnectWatchActive: false,
  runtimeReconnectWatchTimer: null,
  runtimeReconnectAttempts: 0,
  statusPollSeq: 0,
  statusPollAppliedSeq: 0,
  runtimeDetailsExpanded: true,
  mobileSidebarMql: null,
  settingsModalOpen: false,
  legacySettingsModalOpen: false,
  settingsModalOpenedAtMs: 0,
  editModalOpen: false,
  modelSwitcherOpen: false,
  settingsWorkspaceTab: "model",
  selectedSettingsModelId: "",
  settingsYamlLoaded: false,
  settingsYamlRequestInFlight: false,
  modelSettingsSaveInFlight: false,
  modelSettingsStatusModelId: "",
  modelSettingsDraftDirty: false,
  modelSettingsDraftModelId: "",
  displayedSettingsModelId: "",
  projectorDownloadInFlight: false,
  messagesPinnedToBottom: true,
  messagePointerSelectionActive: false,
  chatHistory: [],
  conversationTurns: [],
  activeEditState: null,
  activeSessionId: null,
  sessionIndex: [],
  sessionSwitchInFlight: false,
  pendingImage: null,
  pendingImageReader: null,
  pendingImageToken: 0,
  markdownRendererConfigured: false,
};

// ── Constants (immutable, exported directly) ────────────────────────

export const defaultSettings = {
  temperature: 0.7,
  top_p: 0.8,
  top_k: 20,
  repetition_penalty: 1.0,
  presence_penalty: 1.5,
  max_tokens: 16384,
  stream: true,
  generation_mode: "random",
  seed: 42,
  theme: "light",
  system_prompt: "",
};

export const settingsKey = "potato_settings_v2";
export const PREFILL_METRICS_KEY = "potato_prefill_metrics_v1";
export const PREFILL_PROGRESS_CAP = 99;
export const PREFILL_PROGRESS_TAIL_START = 89;
export const PREFILL_PROGRESS_FLOOR = 6;
export const PREFILL_TICK_MS = 180;
export const PREFILL_FINISH_DURATION_MS = Math.max(
  120,
  Number(window.__POTATO_PREFILL_FINISH_DURATION_MS__ || 1000),
);
export const PREFILL_FINISH_TICK_MS = 40;
export const PREFILL_FINISH_HOLD_MS = Math.max(
  80,
  Number(window.__POTATO_PREFILL_FINISH_HOLD_MS__ || 220),
);
export const STATUS_CHIP_MIN_VISIBLE_MS = 260;
export const STATUS_POLL_TIMEOUT_MS = 3500;
export const RUNTIME_RECONNECT_INTERVAL_MS = 1200;
export const RUNTIME_RECONNECT_TIMEOUT_MS = 2500;
export const RUNTIME_RECONNECT_MAX_ATTEMPTS = 75;
export const IMAGE_CANCEL_RECOVERY_DELAY_MS = Math.max(
  200,
  Number(window.__POTATO_CANCEL_RECOVERY_DELAY_MS__ || 8000),
);
export const IMAGE_CANCEL_RESTART_DELAY_MS = Math.max(
  2000,
  Number(window.__POTATO_CANCEL_RESTART_DELAY_MS__ || 45000),
);
export const SESSIONS_DB_NAME = "potato_sessions";
export const SESSIONS_DB_VERSION = 1;
export const SESSIONS_STORE = "sessions";
export const ACTIVE_SESSION_KEY = "potato_active_session_id";
export const SESSION_TITLE_MAX_LENGTH = 40;
export const SESSION_LIST_MAX_VISIBLE = 50;
export const IMAGE_SAFE_MAX_BYTES = 140 * 1024;
export const IMAGE_MAX_DIMENSION = 896;
export const IMAGE_MAX_PIXEL_COUNT = IMAGE_MAX_DIMENSION * IMAGE_MAX_DIMENSION;
export const CPU_CLOCK_MAX_HZ_PI5 = 2_400_000_000;
export const GPU_CLOCK_MAX_HZ_PI5 = 1_000_000_000;
export const RUNTIME_METRIC_SEVERITY_CLASSES = [
  "runtime-metric-normal",
  "runtime-metric-warn",
  "runtime-metric-high",
  "runtime-metric-critical",
];
export const DEFAULT_MODEL_CHAT_SETTINGS = defaultSettings;
export const DEFAULT_MODEL_VISION_SETTINGS = {
  enabled: false,
  projector_mode: "default",
  projector_filename: null,
};
