"use strict";

let _shell = null;
let _statusTimer = null;
let _history = [];
let _requestInFlight = false;
let _onboardingVisible = false;
let _pendingClientSelection = false;
let _lastClientFetchTs = 0;
let _panelOpen = false;
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
  if (data.client_id && _onboardingVisible && !_pendingClientSelection) {
    _hideOnboarding();
  }

  // Recovery: show banner when client is invalid
  const banner = document.getElementById("permitatoRecoveryBanner");
  if (data.client_id && data.client_valid === false) {
    _showRecoveryBanner(data.client_id);
  } else if (banner) {
    banner.hidden = true;
  }

  const badge = document.getElementById("permitatoModeValue");
  if (badge) {
    const mode = data.mode_display || data.mode || "--";
    badge.textContent = mode;
    badge.setAttribute("data-mode", data.mode || "");
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
    if (data.pihole_available) {
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
    btn.addEventListener("click", () => _revokeException(exc.id));
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
