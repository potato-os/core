"use strict";

let _shell = null;
let _statusTimer = null;
let _history = [];
let _requestInFlight = false;

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

  _pollStatus();
  _statusTimer = setInterval(_pollStatus, 5000);
  _loadSession();
}

export function destroy() {
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
