"use strict";

import { appState, SESSIONS_DB_NAME, SESSIONS_DB_VERSION, SESSIONS_STORE, ACTIVE_SESSION_KEY, SESSION_TITLE_MAX_LENGTH, SESSION_LIST_MAX_VISIBLE } from "./state.js";

// Late-bound references (set by chat.js during init to avoid circular deps)
let _appendMessage = null;
let _setMessageMeta = null;

export function registerAppendMessage(fn) {
  _appendMessage = fn;
}

export function registerSetMessageMeta(fn) {
  _setMessageMeta = fn;
}

function generateId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

let _sessionsDb = null;

function openSessionsDb() {
  if (_sessionsDb) return Promise.resolve(_sessionsDb);
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(SESSIONS_DB_NAME, SESSIONS_DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(SESSIONS_STORE)) {
        db.createObjectStore(SESSIONS_STORE, { keyPath: "id" });
      }
    };
    req.onsuccess = () => { _sessionsDb = req.result; resolve(_sessionsDb); };
    req.onerror = () => reject(req.error);
  });
}

async function putSession(session) {
  const db = await openSessionsDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(SESSIONS_STORE, "readwrite");
    tx.objectStore(SESSIONS_STORE).put(session);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

async function getSession(id) {
  const db = await openSessionsDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(SESSIONS_STORE, "readonly");
    const req = tx.objectStore(SESSIONS_STORE).get(id);
    req.onsuccess = () => resolve(req.result || null);
    req.onerror = () => reject(req.error);
  });
}

async function getAllSessions() {
  const db = await openSessionsDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(SESSIONS_STORE, "readonly");
    const req = tx.objectStore(SESSIONS_STORE).getAll();
    req.onsuccess = () => resolve(req.result || []);
    req.onerror = () => reject(req.error);
  });
}

async function deleteSessionFromDb(id) {
  const db = await openSessionsDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(SESSIONS_STORE, "readwrite");
    tx.objectStore(SESSIONS_STORE).delete(id);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export function generateSessionTitle(firstMessage) {
  let text = "";
  if (typeof firstMessage === "string") {
    text = firstMessage;
  } else if (Array.isArray(firstMessage)) {
    const textPart = firstMessage.find((p) => p?.type === "text");
    text = textPart?.text || "";
  }
  text = text.trim();
  if (!text) return "New chat";
  if (text.length <= SESSION_TITLE_MAX_LENGTH) return text;
  const truncated = text.slice(0, SESSION_TITLE_MAX_LENGTH);
  const lastSpace = truncated.lastIndexOf(" ");
  return (lastSpace > 20 ? truncated.slice(0, lastSpace) : truncated) + "...";
}

export function stripImagesForPersistence(messages) {
  return messages.map((msg) => {
    if (!Array.isArray(msg.content)) return { ...msg };
    return {
      ...msg,
      content: msg.content.map((part) => {
        if (part?.type === "image_url" && part?.image_url?.url?.startsWith("data:")) {
          return { type: "image_url", image_url: { url: "[stripped]" } };
        }
        return { ...part };
      }),
    };
  });
}

export function sanitizeRestoredMessages(messages) {
  return messages.map((msg) => {
    if (!Array.isArray(msg.content)) return { ...msg };
    const filtered = msg.content.filter(
      (part) => !(part?.type === "image_url" && part?.image_url?.url === "[stripped]")
    );
    if (filtered.length === 0) return { ...msg, content: "" };
    if (filtered.length === 1 && filtered[0]?.type === "text") {
      return { ...msg, content: filtered[0].text || "" };
    }
    return { ...msg, content: filtered };
  });
}

export async function saveActiveSession() {
  if (appState.chatHistory.length === 0) return;
  const now = Date.now();
  if (!appState.activeSessionId) {
    appState.activeSessionId = "sess_" + generateId();
    try { localStorage.setItem(ACTIVE_SESSION_KEY, appState.activeSessionId); } catch (_e) { /* ignore */ }
  }
  const firstUserMsg = appState.chatHistory.find((m) => m.role === "user");
  const existingMeta = appState.sessionIndex.find((s) => s.id === appState.activeSessionId);
  const title = existingMeta?.title || generateSessionTitle(firstUserMsg?.content || "");
  const baseMeta = {
    id: appState.activeSessionId,
    title,
    createdAt: existingMeta?.createdAt || now,
    updatedAt: now,
  };
  try {
    // Try saving with full image data URLs (IndexedDB handles ~140KB/image fine for typical chats)
    await putSession({ ...baseMeta, messages: appState.chatHistory.map((m) => ({ ...m })) });
  } catch (_e) {
    // Quota exceeded or write error — fall back to stripped images for safe persistence
    await putSession({ ...baseMeta, messages: stripImagesForPersistence(appState.chatHistory) });
  }
  const metaIdx = appState.sessionIndex.findIndex((s) => s.id === appState.activeSessionId);
  const meta = { id: baseMeta.id, title: baseMeta.title, createdAt: baseMeta.createdAt, updatedAt: now, messageCount: appState.chatHistory.length };
  if (metaIdx >= 0) {
    appState.sessionIndex[metaIdx] = meta;
  } else {
    appState.sessionIndex.unshift(meta);
  }
  appState.sessionIndex.sort((a, b) => b.updatedAt - a.updatedAt);
  renderSessionList();
}

export function clearChatState() {
  appState.chatHistory.length = 0;
  appState.conversationTurns.length = 0;
  appState.activeEditState = null;
  const box = document.getElementById("messages");
  if (box) box.replaceChildren();
}

export function restoreMessagesFromHistory(messages) {
  clearChatState();
  const sanitized = sanitizeRestoredMessages(messages);
  let historyIndex = 0;
  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i];
    appState.chatHistory.push(sanitized[i]);
    if (msg.role === "user") {
      const imagePart = Array.isArray(msg.content)
        ? msg.content.find((p) => p?.type === "image_url")
        : null;
      const imageUrl = imagePart?.image_url?.url || "";
      const hasRealImage = imageUrl.startsWith("data:");
      const hasStrippedImage = imageUrl === "[stripped]";
      const textContent = Array.isArray(msg.content)
        ? (msg.content.find((p) => p?.type === "text")?.text || "")
        : String(msg.content || "");
      const baseHistoryLength = historyIndex;
      const userView = _appendMessage("user", textContent, {
        imageName: (hasRealImage || hasStrippedImage) ? "image" : "",
        imageDataUrl: hasRealImage ? imageUrl : "",
      });
      historyIndex++;
      let assistantView = null;
      if (i + 1 < messages.length && messages[i + 1].role === "assistant") {
        i++;
        appState.chatHistory.push(sanitized[i]);
        assistantView = _appendMessage("assistant", String(messages[i].content || ""));
        if (_setMessageMeta && messages[i].meta) {
          _setMessageMeta(assistantView, messages[i].meta);
        }
        historyIndex++;
      }
      const turn = { baseHistoryLength, userText: textContent, userView, assistantView };
      userView.turnRef = turn;
      if (assistantView) assistantView.turnRef = turn;
      appState.conversationTurns.push(turn);
    } else if (msg.role === "assistant" && appState.conversationTurns.length === 0) {
      _appendMessage("assistant", String(msg.content || ""));
      historyIndex++;
    }
  }
}

export async function loadSessionIntoView(sessionId) {
  if (appState.sessionSwitchInFlight || appState.requestInFlight) return;
  appState.sessionSwitchInFlight = true;
  try {
    if (appState.chatHistory.length > 0 && appState.activeSessionId) {
      await saveActiveSession();
    }
    const session = await getSession(sessionId);
    if (!session) { appState.sessionSwitchInFlight = false; return; }
    restoreMessagesFromHistory(session.messages || []);
    appState.activeSessionId = sessionId;
    try { localStorage.setItem(ACTIVE_SESSION_KEY, appState.activeSessionId); } catch (_e) { /* ignore */ }
    renderSessionList();
    const prompt = document.getElementById("userPrompt");
    if (prompt) prompt.focus({ preventScroll: true });
  } finally {
    appState.sessionSwitchInFlight = false;
  }
}

export async function startNewChat() {
  if (appState.requestInFlight) return;
  if (appState.chatHistory.length > 0 && appState.activeSessionId) {
    await saveActiveSession();
  }
  clearChatState();
  appState.activeSessionId = null;
  try { localStorage.removeItem(ACTIVE_SESSION_KEY); } catch (_e) { /* ignore */ }
  renderSessionList();
  const prompt = document.getElementById("userPrompt");
  if (prompt) prompt.focus({ preventScroll: true });
}

export async function deleteSession(sessionId) {
  if (appState.requestInFlight) return;
  await deleteSessionFromDb(sessionId);
  appState.sessionIndex = appState.sessionIndex.filter((s) => s.id !== sessionId);
  if (appState.activeSessionId === sessionId) {
    clearChatState();
    appState.activeSessionId = null;
    try { localStorage.removeItem(ACTIVE_SESSION_KEY); } catch (_e) { /* ignore */ }
  }
  renderSessionList();
}

export async function deleteAllSessions() {
  if (appState.requestInFlight) return;
  const db = await openSessionsDb();
  await new Promise((resolve, reject) => {
    const tx = db.transaction(SESSIONS_STORE, "readwrite");
    tx.objectStore(SESSIONS_STORE).clear();
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  appState.sessionIndex = [];
  clearChatState();
  appState.activeSessionId = null;
  try { localStorage.removeItem(ACTIVE_SESSION_KEY); } catch (_e) { /* ignore */ }
  renderSessionList();
}

export function renderSessionList() {
  const list = document.getElementById("chatSessionList");
  if (!list) return;
  list.replaceChildren();
  const visible = appState.sessionIndex.slice(0, SESSION_LIST_MAX_VISIBLE);
  for (const meta of visible) {
    const item = document.createElement("div");
    item.className = "chat-session-item" + (meta.id === appState.activeSessionId ? " active" : "");
    item.dataset.sessionId = meta.id;
    const title = document.createElement("span");
    title.className = "chat-session-title";
    title.textContent = meta.title || "New chat";
    const del = document.createElement("button");
    del.className = "chat-session-delete";
    del.type = "button";
    del.setAttribute("aria-label", "Delete chat");
    del.textContent = "×";
    item.appendChild(title);
    item.appendChild(del);
    list.appendChild(item);
  }
  const deleteAllBtn = document.getElementById("deleteAllChatsBtn");
  if (deleteAllBtn) deleteAllBtn.hidden = appState.sessionIndex.length === 0;
}

export async function initSessionManager() {
  try {
    const sessions = await getAllSessions();
    appState.sessionIndex = sessions
      .map((s) => ({ id: s.id, title: s.title, createdAt: s.createdAt, updatedAt: s.updatedAt, messageCount: (s.messages || []).length }))
      .sort((a, b) => b.updatedAt - a.updatedAt);
    renderSessionList();
    const lastActiveId = localStorage.getItem(ACTIVE_SESSION_KEY);
    if (lastActiveId && appState.sessionIndex.some((s) => s.id === lastActiveId)) {
      await loadSessionIntoView(lastActiveId);
    }
  } catch (err) {
    // IndexedDB unavailable — degrade gracefully to single-session mode
  }
}
