"use strict";

import { appState } from "./state.js";

let _terminal = null;
let _fitAddon = null;
let _ws = null;
let _resizeObserver = null;
let _Terminal = null;
let _FitAddon = null;

async function _ensureXtermLoaded() {
  if (_Terminal) return;
  const xtermMod = await import("/assets/vendor/xterm/xterm.mjs");
  const fitMod = await import("/assets/vendor/xterm/addon-fit.mjs");
  _Terminal = xtermMod.Terminal;
  _FitAddon = fitMod.FitAddon;
}

function _setStatus(text) {
  const el = document.getElementById("terminalStatusText");
  if (el) el.textContent = text;
}

function _setReconnectVisible(visible) {
  const btn = document.getElementById("terminalReconnectBtn");
  if (btn) btn.hidden = !visible;
}

function _connectWebSocket() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${proto}//${location.host}/ws/terminal`;

  _setStatus("Connecting...");
  _setReconnectVisible(false);

  _ws = new WebSocket(url);
  // Expose for tests and debugging
  window.__potatoTerminalWs = _ws;

  _ws.onopen = () => {
    _setStatus("Connected");
    _setReconnectVisible(false);
    // Send initial terminal size
    if (_terminal) {
      _ws.send(JSON.stringify({ type: "resize", cols: _terminal.cols, rows: _terminal.rows }));
    }
  };

  _ws.onmessage = (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return;
    }
    if (msg.type === "output" && _terminal) {
      _terminal.write(msg.data);
    } else if (msg.type === "exit") {
      _setStatus("Session ended");
      _setReconnectVisible(true);
    } else if (msg.type === "error") {
      _setStatus(`Error: ${msg.message || "unknown"}`);
      _setReconnectVisible(true);
    }
  };

  _ws.onclose = () => {
    if (appState.terminalModalOpen) {
      _setStatus("Disconnected");
      _setReconnectVisible(true);
    }
    _ws = null;
    window.__potatoTerminalWs = null;
  };

  _ws.onerror = () => {
    _setStatus("Connection error");
    _setReconnectVisible(true);
  };
}

function _disconnectWebSocket() {
  if (_ws) {
    try { _ws.close(); } catch { /* ignore */ }
    _ws = null;
    window.__potatoTerminalWs = null;
  }
}

function _initTerminal() {
  const container = document.getElementById("terminalContainer");
  if (!container || !_Terminal) return;

  _terminal = new _Terminal({
    theme: {
      background: "#081425",
      foreground: "#e7eefb",
      cursor: "#f59e0b",
      cursorAccent: "#081425",
      selectionBackground: "rgba(245, 158, 11, 0.3)",
      black: "#0e1f35",
      red: "#ef4444",
      green: "#22c55e",
      yellow: "#f59e0b",
      blue: "#3b82f6",
      magenta: "#a855f7",
      cyan: "#06b6d4",
      white: "#e7eefb",
      brightBlack: "#5f6f86",
      brightRed: "#f87171",
      brightGreen: "#4ade80",
      brightYellow: "#fbbf24",
      brightBlue: "#60a5fa",
      brightMagenta: "#c084fc",
      brightCyan: "#22d3ee",
      brightWhite: "#ffffff",
    },
    fontFamily: 'ui-monospace, "SF Mono", Menlo, Monaco, "Cascadia Mono", monospace',
    fontSize: 14,
    cursorBlink: true,
    scrollback: 5000,
    allowProposedApi: true,
  });

  _fitAddon = new _FitAddon();
  _terminal.loadAddon(_fitAddon);

  // Try WebGL addon for GPU-accelerated rendering
  import("/assets/vendor/xterm/addon-webgl.mjs")
    .then((mod) => {
      const webgl = new mod.WebglAddon();
      webgl.onContextLoss(() => webgl.dispose());
      _terminal.loadAddon(webgl);
    })
    .catch(() => {
      // WebGL not available — DOM renderer works fine
    });

  // Let Escape bubble to the document so the modal can close
  _terminal.attachCustomKeyEventHandler((event) => {
    if (event.key === "Escape") return false;
    return true;
  });

  _terminal.open(container);
  _fitAddon.fit();

  // Expose terminal buffer for Playwright tests
  window.__potatoTerminal = _terminal;

  // Wire input to WebSocket
  _terminal.onData((data) => {
    if (_ws && _ws.readyState === WebSocket.OPEN) {
      _ws.send(JSON.stringify({ type: "input", data }));
    }
  });

  // Wire resize to WebSocket
  _terminal.onResize(({ cols, rows }) => {
    if (_ws && _ws.readyState === WebSocket.OPEN) {
      _ws.send(JSON.stringify({ type: "resize", cols, rows }));
    }
  });

  // Auto-fit on container resize
  _resizeObserver = new ResizeObserver(() => {
    if (_fitAddon && _terminal) {
      try { _fitAddon.fit(); } catch { /* ignore during teardown */ }
    }
  });
  _resizeObserver.observe(container);
}

function _disposeTerminal() {
  if (_resizeObserver) {
    _resizeObserver.disconnect();
    _resizeObserver = null;
  }
  if (_terminal) {
    _terminal.dispose();
    _terminal = null;
    window.__potatoTerminal = null;
  }
  _fitAddon = null;
  const container = document.getElementById("terminalContainer");
  if (container) container.replaceChildren();
}

function _setTerminalModalOpen(open) {
  appState.terminalModalOpen = Boolean(open);
  const modal = document.getElementById("terminalModal");
  const backdrop = document.getElementById("terminalBackdrop");
  document.body.classList.toggle("terminal-modal-open", appState.terminalModalOpen);
  if (modal) modal.hidden = !appState.terminalModalOpen;
  if (backdrop) backdrop.hidden = !appState.terminalModalOpen;
}

export async function openTerminalModal() {
  await _ensureXtermLoaded();
  _setTerminalModalOpen(true);
  _initTerminal();
  _connectWebSocket();
  if (_terminal) {
    // Small delay to let the modal render before focusing
    requestAnimationFrame(() => _terminal.focus());
  }
}

export function closeTerminalModal() {
  _disconnectWebSocket();
  _disposeTerminal();
  _setTerminalModalOpen(false);
}

export function reconnectTerminal() {
  if (_terminal) {
    _terminal.clear();
  } else {
    _initTerminal();
  }
  _connectWebSocket();
}

// Bind modal UI events (called once from chat.js init)
export function bindTerminalModal() {
  document.getElementById("terminalCloseBtn")?.addEventListener("click", closeTerminalModal);
  document.getElementById("terminalReconnectBtn")?.addEventListener("click", reconnectTerminal);
  document.getElementById("terminalBackdrop")?.addEventListener("click", closeTerminalModal);
  // Close when clicking the modal overlay (outside the shell)
  const modal = document.getElementById("terminalModal");
  if (modal) {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) closeTerminalModal();
    });
  }
}
