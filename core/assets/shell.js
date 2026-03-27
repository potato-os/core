"use strict";

import { appState, defaultSettings, STATUS_POLL_TIMEOUT_MS } from "./state.js";
import { formatCountdownSeconds } from "./utils.js";
import { isLocalModelConnected, updateLlamaIndicator, renderDownloadPrompt, renderStatusActions, renderCompatibilityWarnings, formatSidebarStatusDetail, findResumableFailedModel } from "./status.js";
import { setRuntimeDetailsExpanded, renderSystemRuntime, renderLlamaRuntimeStatus, renderUploadState } from "./runtime-ui.js";
import { renderUpdateCard } from "./update-ui.js";
import { populateModelSwitcher, openModelSwitcher, closeModelSwitcher, toggleModelSwitcher } from "./model-switcher.js";
import { loadSettings, saveSettings, renderSettingsWorkspace, closeSettingsModal, closeLegacySettingsModal, setSettingsModalOpen, registerSettingsPlatformCallbacks } from "./settings-ui.js";
import { setSendEnabled } from "./chat-engine.js";
import { registerPlatformShell } from "./platform-controls.js";
import { init as initChatApp } from "./chat.js";


    // ── Shell: viewport & sidebar ────────────────────────────────────────

    function isMobileSidebarViewport() {
      if (!appState.mobileSidebarMql) {
        appState.mobileSidebarMql = window.matchMedia("(max-width: 900px)");
      }
      return appState.mobileSidebarMql.matches;
    }

    function setSidebarOpen(open) {
      const sidebar = document.getElementById("sidebarPanel");
      const backdrop = document.getElementById("sidebarBackdrop");
      const toggle = document.getElementById("sidebarToggle");
      const closeBtn = document.getElementById("sidebarCloseBtn");
      const mobile = isMobileSidebarViewport();
      const shouldOpen = Boolean(open) && mobile;

      document.body.classList.toggle("sidebar-open", shouldOpen);

      if (sidebar) {
        sidebar.setAttribute("aria-hidden", mobile ? (shouldOpen ? "false" : "true") : "false");
      }
      if (backdrop) {
        backdrop.hidden = !shouldOpen;
      }
      if (toggle) {
        toggle.hidden = !mobile;
        toggle.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
      }
      if (closeBtn) {
        closeBtn.hidden = !shouldOpen;
      }
    }


    // ── Shell: Escape handler & mobile sidebar binding ───────────────────

    let _appEscapeHandler = null;

    function registerEscapeHandler(handler) {
      _appEscapeHandler = handler;
    }

    function bindMobileSidebar() {
      appState.mobileSidebarMql = window.matchMedia("(max-width: 900px)");
      const sync = () => {
        if (!appState.mobileSidebarMql.matches) {
          setSidebarOpen(false);
        } else {
          setSidebarOpen(document.body.classList.contains("sidebar-open"));
        }
      };

      const onViewportChange = () => {
        sync();
      };
      if (typeof appState.mobileSidebarMql.addEventListener === "function") {
        appState.mobileSidebarMql.addEventListener("change", onViewportChange);
      } else if (typeof appState.mobileSidebarMql.addListener === "function") {
        appState.mobileSidebarMql.addListener(onViewportChange);
      }

      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          if (appState.modelSwitcherOpen) {
            closeModelSwitcher();
            return;
          }
          if (_appEscapeHandler && _appEscapeHandler()) return;
          if (appState.legacySettingsModalOpen) {
            closeLegacySettingsModal();
            return;
          }
          if (appState.settingsModalOpen) {
            closeSettingsModal();
            return;
          }
          setSidebarOpen(false);
        }
      });

      sync();
    }


    // ── Shell: theme ─────────────────────────────────────────────────────

    function applyTheme(theme) {
      const resolved = theme === "light" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", resolved);
      const toggle = document.getElementById("themeToggle");
      const target = resolved === "dark" ? "light" : "dark";
      toggle.setAttribute("aria-label", `Switch to ${target} theme`);
      toggle.setAttribute("title", `Switch to ${target} theme`);
    }

    function toggleTheme() {
      const current = document.documentElement.getAttribute("data-theme") || defaultSettings.theme;
      const next = current === "dark" ? "light" : "dark";
      applyTheme(next);
      saveSettings({ theme: next });
    }


    // ── Shell: status & polling ──────────────────────────────────────────

    function classifyPi5MemoryTier(totalBytes) {
      const value = Number(totalBytes);
      if (!Number.isFinite(value) || value <= 0) return null;
      const gib = value / (1024 ** 3);
      const supportedTiers = [1, 2, 4, 8, 16];
      let bestTier = supportedTiers[0];
      let bestDistance = Math.abs(gib - bestTier);
      for (const tier of supportedTiers.slice(1)) {
        const distance = Math.abs(gib - tier);
        if (distance < bestDistance) {
          bestTier = tier;
          bestDistance = distance;
        }
      }
      return `${bestTier}GB`;
    }

    function setSidebarNote(statusPayload) {
      const noteEl = document.getElementById("sidebarNote");
      if (!noteEl) return;
      const version = String(statusPayload?.version || "").trim();
      const systemPayload = statusPayload?.system;
      const piModelName = String(systemPayload?.pi_model_name || "").trim();
      const memoryTier = classifyPi5MemoryTier(systemPayload?.memory_total_bytes);
      const parts = [];
      if (version) parts.push(version);
      if (piModelName) parts.push(piModelName);
      if (memoryTier) parts.push(memoryTier);
      noteEl.textContent = parts.length > 0 ? parts.join(" · ") : "";
    }

    function setStatus(statusPayload) {
      appState.latestStatus = statusPayload;
      const downloadText = formatSidebarStatusDetail(statusPayload);
      const text = `State: ${statusPayload.state} | ${downloadText}`;
      document.getElementById("statusText").textContent = text;
      renderStatusActions(statusPayload);
      setSidebarNote(statusPayload);
      const modelNameField = document.getElementById("modelName");
      if (modelNameField) {
        const modelName = statusPayload?.model?.filename || "Unknown model";
        modelNameField.textContent = statusPayload?.model_present ? modelName : `${modelName} (not loaded)`;
      }
      const countdownSelect = document.getElementById("downloadCountdownEnabled");
      if (countdownSelect) {
        countdownSelect.value = statusPayload?.download?.countdown_enabled === false ? "false" : "true";
      }
      updateLlamaIndicator(statusPayload);
      if (appState.modelSwitcherOpen) populateModelSwitcher();
      renderDownloadPrompt(statusPayload);
      renderCompatibilityWarnings(statusPayload);
      renderLlamaRuntimeStatus(statusPayload);
      renderSystemRuntime(statusPayload?.system, statusPayload);
      renderUpdateCard(statusPayload?.update);
      renderSettingsWorkspace(statusPayload);
      renderUploadState(statusPayload);
      setSendEnabled();
    }

    async function pollStatus(options = {}) {
      const timeoutMs = Math.max(500, Number(options?.timeoutMs || STATUS_POLL_TIMEOUT_MS));
      const seq = ++appState.statusPollSeq;
      const controller = new AbortController();
      const timeoutHandle = window.setTimeout(() => {
        controller.abort();
      }, timeoutMs);
      try {
        const res = await fetch("/status", { cache: "no-store", signal: controller.signal });
        const body = await res.json();
        if (seq < appState.statusPollAppliedSeq) {
          return appState.latestStatus;
        }
        appState.statusPollAppliedSeq = seq;
        setStatus(body);
        return body;
      } catch (err) {
        if (seq < appState.statusPollAppliedSeq) {
          return appState.latestStatus;
        }
        appState.statusPollAppliedSeq = seq;
        const statusErrText = err?.name === "AbortError" ? "request timeout" : String(err);
        if (appState.latestStatus && typeof appState.latestStatus === "object" && appState.latestStatus.state && appState.latestStatus.state !== "DOWN") {
          document.getElementById("statusText").textContent = `Status warning: ${statusErrText}`;
          renderStatusActions({});
          return appState.latestStatus;
        }
        appState.latestStatus = {
          state: "DOWN",
          model_present: false,
          model: { filename: "Unknown model", active_model_id: null },
          models: [],
          download: {
            percent: 0,
            bytes_downloaded: 0,
            bytes_total: 0,
            active: false,
            auto_start_seconds: 0,
            auto_start_remaining_seconds: 0,
            countdown_enabled: true,
            current_model_id: null,
          },
          upload: {
            active: false,
            model_id: null,
            bytes_total: 0,
            bytes_received: 0,
            percent: 0,
            error: null,
          },
          compatibility: {
            device_class: "unknown",
            large_model_warn_threshold_bytes: 0,
            warnings: [],
          },
          llama_runtime: {
            current: {
              install_dir: "",
              exists: false,
              has_server_binary: false,
              source_bundle_path: null,
              source_bundle_name: null,
              profile: null,
            },
            available_bundles: [],
            switch: {
              active: false,
              target_bundle_path: null,
              error: null,
            },
          },
          system: {
            available: false,
            cpu_percent: null,
            cpu_cores_percent: [],
            cpu_clock_arm_hz: null,
            memory_total_bytes: 0,
            memory_used_bytes: 0,
            memory_percent: null,
            swap_total_bytes: 0,
            swap_used_bytes: 0,
            swap_percent: null,
            temperature_c: null,
            gpu_clock_core_hz: null,
            gpu_clock_v3d_hz: null,
            updated_at_unix: null,
            throttling: { any_current: false, current_flags: [], history_flags: [] },
          },
          update: {
            available: false,
            state: "idle",
            deferred: false,
            progress: { phase: null, percent: 0, error: null },
          },
        };
        document.getElementById("statusText").textContent = `Status error: ${statusErrText}`;
        renderStatusActions({});
        const modelNameField = document.getElementById("modelName");
        if (modelNameField) {
          modelNameField.textContent = "Unknown model (status unavailable)";
        }
        updateLlamaIndicator(appState.latestStatus);
        renderDownloadPrompt(appState.latestStatus);
        renderCompatibilityWarnings(appState.latestStatus);
        renderSystemRuntime(appState.latestStatus.system, appState.latestStatus);
        renderSettingsWorkspace(appState.latestStatus);
        renderUploadState(appState.latestStatus);
        setSendEnabled();
        return appState.latestStatus;
      } finally {
        window.clearTimeout(timeoutHandle);
      }
    }


    // ── Shell: model switcher event bindings ─────────────────────────────

    function bindModelSwitcher(activateSelectedModel) {
      document.getElementById("statusBadge").addEventListener("click", (event) => {
        event.stopPropagation();
        toggleModelSwitcher();
      });
      document.getElementById("statusBadge").addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          event.stopPropagation();
          toggleModelSwitcher();
        }
      });
      function activateSwitcherItem(item) {
        if (!item) return;
        if (item.classList.contains("disabled")) return;
        if (item.classList.contains("active")) {
          closeModelSwitcher();
          return;
        }
        const modelId = item.dataset.modelId;
        if (modelId) {
          closeModelSwitcher();
          activateSelectedModel(modelId);
        }
      }
      document.getElementById("modelSwitcherList").addEventListener("click", (event) => {
        activateSwitcherItem(event.target.closest(".model-switcher-item"));
      });
      document.getElementById("modelSwitcher").addEventListener("keydown", (event) => {
        const list = document.getElementById("modelSwitcherList");
        if (!list) return;
        const items = Array.from(list.querySelectorAll(".model-switcher-item"));
        if (items.length === 0) return;
        const focused = list.querySelector(".model-switcher-item.focused");
        const idx = focused ? items.indexOf(focused) : -1;
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          const next = event.key === "ArrowDown"
            ? (idx + 1) % items.length
            : (idx - 1 + items.length) % items.length;
          if (focused) focused.classList.remove("focused");
          items[next].classList.add("focused");
          items[next].scrollIntoView({ block: "nearest" });
        } else if (event.key === "Enter") {
          event.preventDefault();
          if (focused) activateSwitcherItem(focused);
        }
      });
      document.addEventListener("click", (event) => {
        if (!appState.modelSwitcherOpen) return;
        const anchor = document.querySelector(".model-switcher-anchor");
        if (anchor && !anchor.contains(event.target)) {
          closeModelSwitcher();
        }
      });
    }


    // ── Shell exports ────────────────────────────────────────────────────

    export { pollStatus, setSidebarOpen, isMobileSidebarViewport, registerEscapeHandler };


    // ── Shell init ───────────────────────────────────────────────────────

    const settings = loadSettings();
    applyTheme(settings.theme);
    setRuntimeDetailsExpanded(true);

    // Register platform-level callbacks (work without chat)
    registerPlatformShell({ pollStatus });
    registerSettingsPlatformCallbacks({ setSidebarOpen, pollStatus });

    // Load and initialize chat app
    initChatApp({ pollStatus, setSidebarOpen, isMobileSidebarViewport, registerEscapeHandler, bindModelSwitcher });

    // Bind shell event handlers
    bindMobileSidebar();
    document.getElementById("themeToggle").addEventListener("click", toggleTheme);
    document.getElementById("sidebarToggle").addEventListener("click", () => setSidebarOpen(!document.body.classList.contains("sidebar-open")));
    document.getElementById("sidebarCloseBtn").addEventListener("click", () => setSidebarOpen(false));
    document.getElementById("sidebarBackdrop").addEventListener("click", () => setSidebarOpen(false));
    document.getElementById("runtimeViewToggle").addEventListener("click", () => setRuntimeDetailsExpanded(!appState.runtimeDetailsExpanded));

    // Start status polling
    pollStatus();
    setInterval(() => {
      if (appState.settingsModalOpen) return;
      pollStatus();
    }, 2000);
