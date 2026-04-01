"use strict";

let _shell = null;
let _statusTimer = null;
let _history = [];
let _requestInFlight = false;
let _onboardingVisible = false;
let _pendingClientSelection = false;
let _lastClientFetchTs = 0;
let _panelOpen = false;
let _schedulePanelOpen = false;
let _statsPanelOpen = false;
let _lastRenderedMode = null;
let _customListPanelOpen = false;
let _customListTab = "work";
let _latestCustomDomains = [];
let _ttlTimer = null;
let _latestExceptions = [];
let _latestPiholeAvailable = true;

const PERMITATO_API = "/app/permitato/api";

export function init(shellApi) {
  _shell = shellApi;
  _history = [];

  const form = document.getElementById("permitatoComposer");
  if (form) form.addEventListener("submit", _onSubmit);

  // Mode toggle buttons
  document.querySelectorAll(".permitato-mode-btn").forEach(btn => {
    btn.addEventListener("click", () => _switchMode(btn.dataset.mode));
  });

  const input = document.getElementById("permitatoPrompt");
  if (input) {
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        _onSubmit(e);
      }
    });
  }

  const reconfigBtn = document.getElementById("permitatoReconfigureBtn");
  if (reconfigBtn) reconfigBtn.addEventListener("click", () => _showOnboarding());

  const excToggle = document.getElementById("permitatoExceptionsToggle");
  if (excToggle) excToggle.addEventListener("click", _toggleExceptionsPanel);

  const schedToggle = document.getElementById("permitatoScheduleToggle");
  if (schedToggle) schedToggle.addEventListener("click", _toggleSchedulePanel);

  const statsToggle = document.getElementById("permitatoStatsToggle");
  if (statsToggle) statsToggle.addEventListener("click", _toggleStatsPanel);

  const addRuleBtn = document.getElementById("permitatoAddRuleBtn");
  if (addRuleBtn) addRuleBtn.addEventListener("click", _showAddRuleForm);

  const saveRuleBtn = document.getElementById("permitatoSaveRuleBtn");
  if (saveRuleBtn) saveRuleBtn.addEventListener("click", _saveScheduleRule);

  const cancelRuleBtn = document.getElementById("permitatoCancelRuleBtn");
  if (cancelRuleBtn) cancelRuleBtn.addEventListener("click", _hideAddRuleForm);

  const customToggle = document.getElementById("permitatoCustomListToggle");
  if (customToggle) customToggle.addEventListener("click", _toggleCustomListPanel);

  const addCustomBtn = document.getElementById("permitatoAddCustomBtn");
  if (addCustomBtn) addCustomBtn.addEventListener("click", _showAddCustomForm);

  const saveCustomBtn = document.getElementById("permitatoSaveCustomBtn");
  if (saveCustomBtn) saveCustomBtn.addEventListener("click", _saveCustomDomain);

  const cancelCustomBtn = document.getElementById("permitatoCancelCustomBtn");
  if (cancelCustomBtn) cancelCustomBtn.addEventListener("click", _hideAddCustomForm);

  document.querySelectorAll(".permitato-custom-tab").forEach(tab => {
    tab.addEventListener("click", () => _switchCustomTab(tab.dataset.tab));
  });

  _pollStatus();
  _statusTimer = setInterval(_pollStatus, 5000);
  _loadSession();
}

export function destroy() {
  _stopTtlTimer();
  if (_statusTimer) {
    clearInterval(_statusTimer);
    _statusTimer = null;
  }
  _saveSession();
  _shell = null;
}

async function _pollStatus() {
  try {
    const resp = await fetch(`${PERMITATO_API}/status`);
    if (!resp.ok) return;
    const data = await resp.json();
    _updateStatusBar(data);
    if (_statsPanelOpen) _fetchStats();
  } catch {
    _updateStatusBar({ pihole_available: false, mode: "unknown", active_exceptions: 0 });
  }
}

async function _switchMode(mode) {
  try {
    const resp = await fetch(`${PERMITATO_API}/mode`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    if (resp.ok) {
      _pollStatus();
    } else {
      const err = await resp.json().catch(() => ({}));
      _appendMessage("assistant", `Could not switch mode: ${err.error || "unknown error"}`);
    }
  } catch {
    _appendMessage("assistant", "Failed to switch mode — is the server running?");
  }
}

function _updateStatusBar(data) {
  // Onboarding: show overlay when no client is selected
  if (!data.client_id && !_pendingClientSelection) {
    _showOnboarding();
    return;
  }
  if (data.client_id && data.client_valid !== false && _onboardingVisible && !_pendingClientSelection) {
    _hideOnboarding();
  }
  // Reconfigure: keep refreshing clients while overlay is open with invalid client
  if (data.client_valid === false && _onboardingVisible && !_pendingClientSelection) {
    _showOnboarding();
  }

  // Recovery: show banner when client is invalid
  const banner = document.getElementById("permitatoRecoveryBanner");
  if (data.client_id && data.client_valid === false) {
    _showRecoveryBanner(data.client_id);
  } else if (banner) {
    banner.hidden = true;
  }

  // Bypass: show banner when DNS is bypassed
  const bypassBanner = document.getElementById("permitatoBypassBanner");
  if (bypassBanner) {
    bypassBanner.hidden = !data.blocking_bypassed;
  }

  const badge = document.getElementById("permitatoModeValue");
  if (badge) {
    const mode = data.mode_display || data.mode || "--";
    badge.textContent = mode;
    badge.setAttribute("data-mode", data.mode || "");
    if (_lastRenderedMode !== null && data.mode !== _lastRenderedMode) {
      badge.classList.remove("mode-changed");
      void badge.offsetWidth;
      badge.classList.add("mode-changed");
      badge.addEventListener("animationend", () => badge.classList.remove("mode-changed"), { once: true });
    }
    _lastRenderedMode = data.mode;
  }

  // Schedule/override indicator
  const indicator = document.getElementById("permitatoScheduleIndicator");
  if (indicator) {
    if (data.override_active) {
      indicator.textContent = "(override)";
      indicator.hidden = false;
    } else if (data.schedule_active) {
      indicator.textContent = "(scheduled)";
      indicator.hidden = false;
    } else {
      indicator.hidden = true;
    }
  }

  // Highlight active mode button
  document.querySelectorAll(".permitato-mode-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.mode === data.mode);
  });

  const count = document.getElementById("permitatoExceptionCount");
  if (count) count.textContent = String(data.active_exceptions || 0);

  // Store exception data for the panel
  _latestExceptions = data.exceptions || [];
  _latestPiholeAvailable = data.pihole_available !== false;
  if (_panelOpen) _renderExceptions();

  const dot = document.getElementById("permitatoPiholeDot");
  const label = document.getElementById("permitatoPiholeLabel");
  if (dot && label) {
    if (data.pihole_available && data.blocking_bypassed) {
      dot.className = "permitato-pihole-dot bypassed";
      label.textContent = "DNS bypassed";
    } else if (data.pihole_available) {
      dot.className = "permitato-pihole-dot connected";
      label.textContent = "Pi-hole connected";
    } else {
      dot.className = "permitato-pihole-dot disconnected";
      label.textContent = "Pi-hole unavailable";
    }
  }
}

async function _onSubmit(e) {
  e.preventDefault();
  const input = document.getElementById("permitatoPrompt");
  if (!input) return;
  const text = input.value.trim();
  if (!text || _requestInFlight) return;

  input.value = "";
  _appendMessage("user", text);
  _history.push({ role: "user", content: text });
  _requestInFlight = true;

  const assistantEl = _appendMessage("assistant", "");

  try {
    const resp = await fetch(`${PERMITATO_API}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // Send history WITHOUT the current user turn — server appends it
      body: JSON.stringify({ message: text, history: _history.slice(0, -1).slice(-20) }),
    });

    if (!resp.ok) {
      assistantEl.textContent = "Something went wrong. Try again.";
      return;
    }

    let accumulated = "";
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let sseBuffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      sseBuffer += decoder.decode(value, { stream: true });
      const lines = sseBuffer.split("\n");
      sseBuffer = lines.pop(); // keep incomplete last line for next chunk

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const dataStr = line.slice(6).trim();

        if (dataStr === "[DONE]") continue;

        try {
          const parsed = JSON.parse(dataStr);

          if (parsed.permitato_action) {
            _handleAction(parsed.permitato_action);
            continue;
          }

          // Handle upstream LLM errors forwarded as SSE
          if (parsed.error) {
            const msg = parsed.error.message || parsed.error.detail || "LLM unavailable";
            assistantEl.textContent = msg;
            accumulated = msg;
            continue;
          }

          const delta = parsed.choices?.[0]?.delta?.content;
          if (delta) {
            accumulated += delta;
            assistantEl.textContent = accumulated.replace(/\[ACTION:[^\]]*\]/g, "").trim();
          }
        } catch {
          // incomplete JSON — will be completed in next chunk
        }
      }
    }

    const cleanText = accumulated.replace(/\[ACTION:[^\]]*\]/g, "").trim();
    assistantEl.textContent = cleanText;
    _history.push({ role: "assistant", content: cleanText });
    _saveSession();

  } catch (err) {
    assistantEl.textContent = "Connection error. Is the server running?";
  } finally {
    _requestInFlight = false;
  }
}

function _handleAction(action) {
  if (action.type === "mode_switched" || action.type === "exception_granted" || action.type === "exception_denied") {
    _pollStatus();
  }
}

function _appendMessage(role, text) {
  const container = document.getElementById("permitatoMessages");
  if (!container) return null;
  const el = document.createElement("div");
  el.className = `permitato-msg ${role}`;
  el.textContent = text;
  container.appendChild(el);
  container.scrollTop = container.scrollHeight;
  return el;
}

// --- Exceptions panel ---

function _toggleExceptionsPanel() {
  _panelOpen = !_panelOpen;
  const panel = document.getElementById("permitatoExceptionsPanel");
  const toggle = document.getElementById("permitatoExceptionsToggle");
  if (panel) panel.hidden = !_panelOpen;
  if (toggle) toggle.setAttribute("aria-expanded", String(_panelOpen));
  if (_panelOpen) {
    _renderExceptions();
    _startTtlTimer();
  } else {
    _stopTtlTimer();
  }
}

function _renderExceptions() {
  const list = document.getElementById("permitatoExceptionsList");
  const empty = document.getElementById("permitatoExceptionsEmpty");
  const degraded = document.getElementById("permitatoExceptionsDegraded");
  if (!list) return;
  if (list.querySelector(".confirm-pending")) return;

  if (degraded) degraded.hidden = _latestPiholeAvailable;

  if (_latestExceptions.length === 0) {
    list.innerHTML = "";
    if (empty) empty.hidden = false;
    return;
  }
  if (empty) empty.hidden = true;
  list.innerHTML = "";

  for (const exc of _latestExceptions) {
    const li = document.createElement("li");

    const info = document.createElement("div");
    info.className = "exc-info";
    const domain = document.createElement("span");
    domain.className = "exc-domain";
    domain.textContent = exc.domain;
    info.appendChild(domain);
    if (exc.reason) {
      const reason = document.createElement("span");
      reason.className = "exc-reason";
      reason.textContent = exc.reason;
      info.appendChild(reason);
    }
    li.appendChild(info);

    const right = document.createElement("div");
    right.className = "exc-right";

    const ttl = document.createElement("span");
    ttl.className = "exc-ttl";
    ttl.setAttribute("data-expires-at", String(exc.expires_at));
    ttl.textContent = _formatTtl(exc.expires_at);
    right.appendChild(ttl);

    const btn = document.createElement("button");
    btn.className = "exc-revoke-btn";
    btn.textContent = "Revoke";
    btn.addEventListener("click", () => _confirmAction(btn, () => _revokeException(exc.id)));
    right.appendChild(btn);

    li.appendChild(right);
    list.appendChild(li);
  }
}

function _formatTtl(expiresAt) {
  const remaining = Math.max(0, Math.floor(expiresAt - Date.now() / 1000));
  if (remaining <= 0) return "expired";
  const h = Math.floor(remaining / 3600);
  const m = Math.floor((remaining % 3600) / 60);
  const s = remaining % 60;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

function _startTtlTimer() {
  _stopTtlTimer();
  _ttlTimer = setInterval(_tickTtl, 1000);
}

function _stopTtlTimer() {
  if (_ttlTimer) {
    clearInterval(_ttlTimer);
    _ttlTimer = null;
  }
}

function _tickTtl() {
  document.querySelectorAll(".exc-ttl[data-expires-at]").forEach(el => {
    el.textContent = _formatTtl(Number(el.getAttribute("data-expires-at")));
  });
}

async function _revokeException(id) {
  try {
    const resp = await fetch(`${PERMITATO_API}/exceptions/${id}`, { method: "DELETE" });
    if (resp.ok) _pollStatus();
  } catch {
    // silent — next poll will update state
  }
}

// --- Schedule panel ---

const _DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function _toggleSchedulePanel() {
  _schedulePanelOpen = !_schedulePanelOpen;
  const panel = document.getElementById("permitatoSchedulePanel");
  const toggle = document.getElementById("permitatoScheduleToggle");
  if (panel) panel.hidden = !_schedulePanelOpen;
  if (toggle) toggle.setAttribute("aria-expanded", String(_schedulePanelOpen));
  if (_schedulePanelOpen) _fetchSchedule();
}

async function _fetchSchedule() {
  try {
    const resp = await fetch(`${PERMITATO_API}/schedule`);
    if (!resp.ok) return;
    const data = await resp.json();
    _renderScheduleRules(data.rules || []);
    _renderNextTransition(data.next_transition);
  } catch {
    // silent
  }
}

function _renderScheduleRules(rules) {
  const list = document.getElementById("permitatoScheduleRules");
  const empty = document.getElementById("permitatoScheduleEmpty");
  if (!list) return;

  if (rules.length === 0) {
    list.innerHTML = "";
    if (empty) empty.hidden = false;
    return;
  }
  if (empty) empty.hidden = true;
  list.innerHTML = "";

  for (const rule of rules) {
    const li = document.createElement("li");

    const info = document.createElement("div");
    info.className = "sched-info";

    const mode = document.createElement("span");
    mode.className = "sched-mode";
    mode.textContent = rule.mode.charAt(0).toUpperCase() + rule.mode.slice(1);
    mode.setAttribute("data-mode", rule.mode);
    info.appendChild(mode);

    const days = document.createElement("span");
    days.className = "sched-days";
    days.textContent = rule.days.map(d => _DAY_NAMES[d]).join(", ");
    info.appendChild(days);

    const time = document.createElement("span");
    time.className = "sched-time";
    time.textContent = `${rule.start_time} – ${rule.end_time}`;
    info.appendChild(time);

    li.appendChild(info);

    const right = document.createElement("div");
    right.className = "sched-right";

    if (!rule.enabled) {
      const dis = document.createElement("span");
      dis.className = "sched-disabled";
      dis.textContent = "disabled";
      right.appendChild(dis);
    }

    const btn = document.createElement("button");
    btn.className = "sched-delete-btn";
    btn.textContent = "Delete";
    btn.addEventListener("click", () => _confirmAction(btn, () => _deleteScheduleRule(rule.id)));
    right.appendChild(btn);

    li.appendChild(right);
    list.appendChild(li);
  }
}

function _renderNextTransition(next) {
  const el = document.getElementById("permitatoScheduleNext");
  if (!el) return;
  if (!next) {
    el.hidden = true;
    return;
  }
  const dayName = _DAY_NAMES[next.day];
  el.textContent = `Next: ${next.mode.charAt(0).toUpperCase() + next.mode.slice(1)} at ${dayName} ${next.time}`;
  el.hidden = false;
}

function _showAddRuleForm() {
  const form = document.getElementById("permitatoAddRuleForm");
  const errEl = document.getElementById("permitatoRuleError");
  if (form) form.hidden = false;
  if (errEl) errEl.hidden = true;
}

function _hideAddRuleForm() {
  const form = document.getElementById("permitatoAddRuleForm");
  if (form) form.hidden = true;
}

async function _saveScheduleRule() {
  const mode = document.getElementById("permitatoRuleMode")?.value;
  const startTime = document.getElementById("permitatoRuleStart")?.value;
  const endTime = document.getElementById("permitatoRuleEnd")?.value;
  const errEl = document.getElementById("permitatoRuleError");

  const days = [];
  document.querySelectorAll("#permitatoDayPicker input:checked").forEach(cb => {
    days.push(Number(cb.value));
  });

  if (days.length === 0) {
    if (errEl) { errEl.textContent = "Select at least one day."; errEl.hidden = false; }
    return;
  }

  try {
    const resp = await fetch(`${PERMITATO_API}/schedule`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, days, start_time: startTime, end_time: endTime }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      if (errEl) { errEl.textContent = err.error || "Failed to save rule."; errEl.hidden = false; }
      return;
    }
    _hideAddRuleForm();
    _fetchSchedule();
    _pollStatus();
  } catch {
    if (errEl) { errEl.textContent = "Connection error."; errEl.hidden = false; }
  }
}

async function _deleteScheduleRule(id) {
  try {
    const resp = await fetch(`${PERMITATO_API}/schedule/${id}`, { method: "DELETE" });
    if (resp.ok) {
      _fetchSchedule();
      _pollStatus();
    }
  } catch {
    // silent
  }
}

// --- Custom Lists ---

function _toggleCustomListPanel() {
  _customListPanelOpen = !_customListPanelOpen;
  const panel = document.getElementById("permitatoCustomListPanel");
  const toggle = document.getElementById("permitatoCustomListToggle");
  if (panel) panel.hidden = !_customListPanelOpen;
  if (toggle) toggle.setAttribute("aria-expanded", String(_customListPanelOpen));
  if (_customListPanelOpen) _fetchCustomDomains();
}

async function _fetchCustomDomains() {
  try {
    const resp = await fetch(`${PERMITATO_API}/custom-domains`);
    if (!resp.ok) return;
    const data = await resp.json();
    _latestCustomDomains = data.entries || [];
    _renderCustomDomains();
  } catch {
    // silent
  }
}

function _renderCustomDomains() {
  const list = document.getElementById("permitatoCustomList");
  const empty = document.getElementById("permitatoCustomListEmpty");
  if (!list) return;

  const filtered = _latestCustomDomains.filter(e => e.mode === _customListTab);

  if (filtered.length === 0) {
    list.innerHTML = "";
    if (empty) empty.hidden = false;
    return;
  }
  if (empty) empty.hidden = true;
  list.innerHTML = "";

  for (const entry of filtered) {
    const li = document.createElement("li");

    const domain = document.createElement("span");
    domain.className = "custom-domain-name";
    domain.textContent = entry.domain;
    li.appendChild(domain);

    const btn = document.createElement("button");
    btn.className = "custom-domain-remove-btn";
    btn.textContent = "Remove";
    btn.addEventListener("click", () => _confirmAction(btn, () => _deleteCustomDomain(entry.id)));
    li.appendChild(btn);

    list.appendChild(li);
  }
}

function _switchCustomTab(tab) {
  _customListTab = tab;
  document.querySelectorAll(".permitato-custom-tab").forEach(el => {
    el.classList.toggle("active", el.dataset.tab === tab);
  });
  _renderCustomDomains();
}

function _showAddCustomForm() {
  const form = document.getElementById("permitatoAddCustomForm");
  const errEl = document.getElementById("permitatoCustomError");
  const modeSelect = document.getElementById("permitatoCustomMode");
  if (form) form.hidden = false;
  if (errEl) errEl.hidden = true;
  if (modeSelect) modeSelect.value = _customListTab;
}

function _hideAddCustomForm() {
  const form = document.getElementById("permitatoAddCustomForm");
  const domainInput = document.getElementById("permitatoCustomDomain");
  if (form) form.hidden = true;
  if (domainInput) domainInput.value = "";
}

async function _saveCustomDomain() {
  const mode = document.getElementById("permitatoCustomMode")?.value;
  const domainInput = document.getElementById("permitatoCustomDomain");
  const domain = domainInput?.value?.trim();
  const errEl = document.getElementById("permitatoCustomError");

  if (!domain) {
    if (errEl) { errEl.textContent = "Enter a domain."; errEl.hidden = false; }
    return;
  }

  try {
    const resp = await fetch(`${PERMITATO_API}/custom-domains`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, domain }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      if (errEl) { errEl.textContent = err.error || "Failed to add domain."; errEl.hidden = false; }
      return;
    }
    _hideAddCustomForm();
    _fetchCustomDomains();
  } catch {
    if (errEl) { errEl.textContent = "Connection error."; errEl.hidden = false; }
  }
}

async function _deleteCustomDomain(id) {
  try {
    const resp = await fetch(`${PERMITATO_API}/custom-domains/${id}`, { method: "DELETE" });
    if (resp.ok) _fetchCustomDomains();
  } catch {
    // silent
  }
}

// --- Onboarding ---

async function _showOnboarding() {
  const overlay = document.getElementById("permitatoOnboarding");
  if (!overlay) return;
  overlay.hidden = false;
  const wasAlreadyVisible = _onboardingVisible;
  _onboardingVisible = true;
  // First open: fetch immediately. Already open: refresh every 30s to avoid flicker.
  const now = Date.now();
  if (!wasAlreadyVisible || (now - _lastClientFetchTs > 30000 && !_pendingClientSelection)) {
    await _fetchClients();
  }
}

function _hideOnboarding() {
  const overlay = document.getElementById("permitatoOnboarding");
  if (overlay) overlay.hidden = true;
  _onboardingVisible = false;
  _pendingClientSelection = false;
}

async function _fetchClients() {
  const list = document.getElementById("permitatoClientList");
  const status = document.getElementById("permitatoOnboardingStatus");
  if (!list) return;

  // Only show "Loading..." on first fetch (avoid flicker on refresh)
  if (!list.children.length && status) status.textContent = "Loading...";

  try {
    const resp = await fetch(`${PERMITATO_API}/clients`);
    if (!resp.ok) {
      if (status) status.textContent = "Failed to load clients.";
      return;
    }
    const data = await resp.json();

    if (!data.pihole_available) {
      list.innerHTML = "";
      if (status) status.textContent = "Pi-hole is not connected. Connect Pi-hole to discover devices.";
      return;
    }

    if (data.clients.length === 0) {
      list.innerHTML = "";
      if (status) status.textContent = "No devices discovered by Pi-hole yet.";
      return;
    }

    // Successful fetch with clients — throttle further refreshes to avoid flicker
    _lastClientFetchTs = Date.now();
    if (status) status.textContent = "";
    list.innerHTML = "";

    for (const c of data.clients) {
      const li = document.createElement("li");
      if (c.is_requester) li.classList.add("this-device");

      const info = document.createElement("div");
      info.className = "client-info";

      const label = document.createElement("span");
      label.className = "client-label";
      if (c.is_requester) {
        label.textContent = "Your device";
      } else if (c.name) {
        label.textContent = c.name;
      } else {
        label.textContent = c.client;
      }
      info.appendChild(label);

      const sub = document.createElement("span");
      sub.className = "client-sub";
      sub.textContent = c.is_requester || c.name ? c.client : "";
      if (sub.textContent) info.appendChild(sub);

      li.appendChild(info);

      const btn = document.createElement("button");
      btn.className = "client-select-btn";
      btn.textContent = c.is_requester ? "Select this device" : "Select";
      btn.addEventListener("click", () => _selectClient(c.client));
      li.appendChild(btn);

      // Your device goes to top
      if (c.is_requester) {
        list.prepend(li);
      } else {
        list.appendChild(li);
      }
    }
  } catch {
    if (status) status.textContent = "Failed to load clients.";
  }
}

async function _selectClient(clientId) {
  _pendingClientSelection = true;
  const errEl = document.getElementById("permitatoOnboardingError");

  try {
    const resp = await fetch(`${PERMITATO_API}/client`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_id: clientId }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      if (errEl) { errEl.textContent = err.error || "Failed to set client."; errEl.hidden = false; }
      _pendingClientSelection = false;
      return;
    }
    _hideOnboarding();
    _pollStatus();
  } catch {
    if (errEl) { errEl.textContent = "Connection error."; errEl.hidden = false; }
    _pendingClientSelection = false;
  }
}

function _showRecoveryBanner(clientId) {
  const banner = document.getElementById("permitatoRecoveryBanner");
  const text = document.getElementById("permitatoRecoveryText");
  if (!banner || !text) return;
  text.textContent = `Your controlled device (${clientId}) is no longer available in Pi-hole.`;
  banner.hidden = false;
}

// --- Confirm-before-delete utility ---

function _confirmAction(btn, action) {
  if (btn.dataset.confirming) return;
  const original = btn.textContent;
  btn.textContent = "Sure?";
  btn.classList.add("confirm-pending");
  btn.dataset.confirming = "1";

  const timer = setTimeout(() => revert(), 3000);

  function onConfirm() {
    clearTimeout(timer);
    revert();
    action();
  }
  function revert() {
    btn.removeEventListener("click", onConfirm);
    btn.textContent = original;
    btn.classList.remove("confirm-pending");
    delete btn.dataset.confirming;
  }

  btn.addEventListener("click", onConfirm, { once: true });
}

// --- Session persistence (IndexedDB) ---

const DB_NAME = "permitato_sessions";
const DB_VERSION = 1;
const STORE_NAME = "session";
let _db = null;

async function _openDb() {
  if (_db) return _db;
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME);
      }
    };
    req.onsuccess = () => { _db = req.result; resolve(_db); };
    req.onerror = () => reject(req.error);
  });
}

async function _saveSession() {
  try {
    const db = await _openDb();
    const tx = db.transaction(STORE_NAME, "readwrite");
    tx.objectStore(STORE_NAME).put({ history: _history, updatedAt: Date.now() }, "current");
  } catch {
    // silent
  }
}

async function _loadSession() {
  try {
    const db = await _openDb();
    const tx = db.transaction(STORE_NAME, "readonly");
    const req = tx.objectStore(STORE_NAME).get("current");
    req.onsuccess = () => {
      const data = req.result;
      if (data && Array.isArray(data.history)) {
        _history = data.history;
        const container = document.getElementById("permitatoMessages");
        if (container) {
          for (const msg of _history) {
            _appendMessage(msg.role, msg.content);
          }
        }
      }
    };
  } catch {
    // silent
  }
}


// ---------------------------------------------------------------------------
// Stats panel
// ---------------------------------------------------------------------------

function _toggleStatsPanel() {
  _statsPanelOpen = !_statsPanelOpen;
  const panel = document.getElementById("permitatoStatsPanel");
  const toggle = document.getElementById("permitatoStatsToggle");
  if (panel) panel.hidden = !_statsPanelOpen;
  if (toggle) toggle.setAttribute("aria-expanded", String(_statsPanelOpen));
  if (_statsPanelOpen) _fetchStats();
}

async function _fetchStats() {
  try {
    const resp = await fetch(`${PERMITATO_API}/stats`);
    if (!resp.ok) return;
    const data = await resp.json();
    _renderStats(data);
  } catch {
    // silent
  }
}

function _renderStats(data) {
  const empty = document.getElementById("permitatoStatsEmpty");
  const grid = document.getElementById("permitatoStatsGrid");
  const domainsEl = document.getElementById("permitatoTopDomains");
  const footer = document.getElementById("permitatoStatsFooter");

  const hasData = data.data_span_days > 0 ||
    data.requests_today.granted > 0 ||
    data.requests_today.denied > 0 ||
    data.top_domains.length > 0 ||
    data.deny_rate.total > 0;

  if (!hasData) {
    if (empty) empty.hidden = false;
    if (grid) grid.hidden = true;
    if (domainsEl) domainsEl.hidden = true;
    if (footer) footer.hidden = true;
    return;
  }

  if (empty) empty.hidden = true;
  if (grid) grid.hidden = false;

  // Streak
  const streakVal = document.getElementById("permitatoStreakValue");
  if (streakVal) {
    streakVal.textContent = String(data.focus_streak_days);
    streakVal.classList.toggle("streak-active", data.focus_streak_days > 0);
  }

  // Today
  const todayVal = document.getElementById("permitatoTodayValue");
  const todayLabel = document.getElementById("permitatoTodayLabel");
  const total = data.requests_today.granted + data.requests_today.denied;
  if (todayVal) todayVal.textContent = String(total);
  if (todayLabel) {
    if (total === 0) todayLabel.textContent = "requests today";
    else todayLabel.textContent = `today (${data.requests_today.denied} denied)`;
  }

  // Deny rate
  const denyVal = document.getElementById("permitatoDenyRateValue");
  if (denyVal) {
    denyVal.textContent = data.deny_rate.rate !== null
      ? Math.round(data.deny_rate.rate * 100) + "%"
      : "--";
  }

  // Mode duration
  const modeVal = document.getElementById("permitatoModeDurationValue");
  if (modeVal) {
    modeVal.textContent = data.mode_duration_seconds !== null
      ? _formatDuration(data.mode_duration_seconds)
      : "--";
  }

  // Top domains
  if (domainsEl) {
    const list = document.getElementById("permitatoTopDomainsList");
    if (data.top_domains.length > 0) {
      domainsEl.hidden = false;
      if (list) {
        list.textContent = data.top_domains
          .map(d => `${d.domain} (${d.count})`)
          .join(", ");
      }
    } else {
      domainsEl.hidden = true;
    }
  }

  // Footer
  if (footer) {
    const span = document.getElementById("permitatoDataSpan");
    if (span) span.textContent = String(data.data_span_days);
    footer.hidden = data.data_span_days < 1;
  }
}

function _formatDuration(seconds) {
  if (seconds < 60) return "<1m";
  const m = Math.floor(seconds / 60);
  const h = Math.floor(m / 60);
  const rm = m % 60;
  if (h === 0) return `${m}m`;
  if (rm === 0) return `${h}h`;
  return `${h}h ${rm}m`;
}
