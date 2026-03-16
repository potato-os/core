"use strict";

import { appState } from "./state.js";

    let _openEditMessageModal = null;

    export function registerOpenEditMessageModal(fn) {
      _openEditMessageModal = fn;
    }

    export function getMessagesBox() {
      return document.getElementById("messages");
    }

    export function isMessagesPinned(box = getMessagesBox()) {
      if (!box) return true;
      return (box.scrollHeight - box.clientHeight - box.scrollTop) <= 24;
    }

    export function setMessagesPinnedState(pinned) {
      appState.messagesPinnedToBottom = Boolean(pinned);
    }

    function createMessageActionIcon(kind) {
      const svgNS = "http://www.w3.org/2000/svg";
      const svg = document.createElementNS(svgNS, "svg");
      svg.setAttribute("viewBox", "0 0 24 24");
      svg.setAttribute("aria-hidden", "true");
      if (kind === "copy") {
        const back = document.createElementNS(svgNS, "rect");
        back.setAttribute("x", "9");
        back.setAttribute("y", "4");
        back.setAttribute("width", "11");
        back.setAttribute("height", "13");
        back.setAttribute("rx", "2");
        const front = document.createElementNS(svgNS, "rect");
        front.setAttribute("x", "4");
        front.setAttribute("y", "9");
        front.setAttribute("width", "11");
        front.setAttribute("height", "11");
        front.setAttribute("rx", "2");
        svg.appendChild(back);
        svg.appendChild(front);
        return svg;
      }
      const path = document.createElementNS(svgNS, "path");
      path.setAttribute("d", "M4 20h4l10-10a2.5 2.5 0 0 0-4-4L4 16v4");
      const tip = document.createElementNS(svgNS, "path");
      tip.setAttribute("d", "M13.5 6.5l4 4");
      svg.appendChild(path);
      svg.appendChild(tip);
      return svg;
    }

    async function copyTextToClipboard(text) {
      const value = String(text || "");
      if (!value) return false;
      if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
        await navigator.clipboard.writeText(value);
        return true;
      }
      const probe = document.createElement("textarea");
      probe.value = value;
      probe.setAttribute("readonly", "readonly");
      probe.style.position = "fixed";
      probe.style.top = "-9999px";
      probe.style.opacity = "0";
      document.body.appendChild(probe);
      probe.focus();
      probe.select();
      try {
        return document.execCommand("copy");
      } finally {
        document.body.removeChild(probe);
      }
    }

    function flashCopiedState(button) {
      if (!button) return;
      button.dataset.copied = "true";
      button.setAttribute("title", "Copied");
      window.setTimeout(() => {
        if (!button.isConnected) return;
        delete button.dataset.copied;
        button.setAttribute("title", "Copy message");
      }, 1400);
    }

    export function createMessageActions(messageView, options = {}) {
      const actions = document.createElement("div");
      actions.className = "message-actions";
      actions.dataset.visible = "false";

      const copyBtn = document.createElement("button");
      copyBtn.type = "button";
      copyBtn.className = "message-action-btn";
      copyBtn.dataset.action = "copy";
      copyBtn.setAttribute("aria-label", "Copy message");
      copyBtn.setAttribute("title", "Copy message");
      copyBtn.appendChild(createMessageActionIcon("copy"));
      copyBtn.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        try {
          const copied = await copyTextToClipboard(messageView.copyText || messageView.editText || messageView.bubble?.innerText || "");
          if (copied) {
            flashCopiedState(copyBtn);
          }
        } catch (error) {
          console.warn("Clipboard copy failed", error);
        }
      });
      actions.appendChild(copyBtn);

      if (options.editable === true) {
        const editBtn = document.createElement("button");
        editBtn.type = "button";
        editBtn.className = "message-action-btn";
        editBtn.dataset.action = "edit";
        editBtn.setAttribute("aria-label", "Edit message");
        editBtn.setAttribute("title", "Edit message");
        editBtn.appendChild(createMessageActionIcon("edit"));
        editBtn.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          if (_openEditMessageModal) _openEditMessageModal(messageView);
        });
        actions.appendChild(editBtn);
      }

      return actions;
    }

    export function setMessageActionsVisible(messageView, visible) {
      if (!messageView?.actions) return;
      if (messageView.row) {
        messageView.row.classList.toggle("message-row-actions-hidden", !visible);
      }
      messageView.actions.hidden = !visible;
      messageView.actions.dataset.visible = visible ? "true" : "false";
    }

    export function hasActiveMessageSelection(box = getMessagesBox()) {
      if (!box || typeof window === "undefined" || typeof window.getSelection !== "function") {
        return false;
      }
      const selection = window.getSelection();
      if (!selection || selection.isCollapsed || selection.rangeCount < 1) {
        return false;
      }
      const range = selection.getRangeAt(0);
      const container = range.commonAncestorContainer;
      const node = container?.nodeType === Node.TEXT_NODE ? container.parentNode : container;
      return Boolean(node && box.contains(node));
    }

    export function handleMessagesChanged(shouldFollow, options = {}) {
      const box = getMessagesBox();
      if (!box) return;
      const forceFollow = options.forceFollow === true;
      if (forceFollow || (shouldFollow && !appState.messagePointerSelectionActive && !hasActiveMessageSelection(box))) {
        box.scrollTop = box.scrollHeight;
        setMessagesPinnedState(true);
      }
    }

    appState.markdownRendererConfigured = false;

    function renderAssistantMarkdownToHtml(text) {
      const source = String(text || "");
      if (!window.marked?.parse || !window.DOMPurify?.sanitize) {
        return null;
      }
      if (!appState.markdownRendererConfigured && typeof window.marked.setOptions === "function") {
        window.marked.setOptions({
          gfm: true,
          breaks: true,
        });
        appState.markdownRendererConfigured = true;
      }
      const renderedHtml = window.marked?.parse(source) || "";
      return window.DOMPurify?.sanitize(renderedHtml, {
        ALLOWED_TAGS: [
          "a", "blockquote", "br", "code", "em", "h1", "h2", "h3", "h4",
          "li", "ol", "p", "pre", "strong", "ul",
        ],
        ALLOWED_ATTR: ["href", "title"],
      }) || "";
    }

    export function renderBubbleContent(bubble, content, options = {}) {
      if (!bubble) return;
      const text = String(content || "");
      const imageDataUrl = typeof options.imageDataUrl === "string" ? options.imageDataUrl : "";
      const imageName = typeof options.imageName === "string" ? options.imageName : "uploaded image";
      const role = String(options.role || "");

      bubble.classList.remove("markdown-rendered");
      if (!imageDataUrl) {
        bubble.classList.remove("with-image");
        if (role === "assistant") {
          const sanitizedHtml = renderAssistantMarkdownToHtml(text);
          if (sanitizedHtml !== null) {
            bubble.classList.add("markdown-rendered");
            bubble.innerHTML = sanitizedHtml;
            return;
          }
        }
        bubble.textContent = text;
        return;
      }

      bubble.classList.add("with-image");
      bubble.replaceChildren();
      const thumbnail = document.createElement("img");
      thumbnail.className = "message-image-thumb";
      thumbnail.src = imageDataUrl;
      thumbnail.alt = `Uploaded image: ${imageName}`;
      thumbnail.loading = "lazy";
      bubble.appendChild(thumbnail);

      if (text) {
        const caption = document.createElement("div");
        caption.className = "message-text";
        caption.textContent = text;
        bubble.appendChild(caption);
      }
    }

    export function appendMessage(role, content = "", options = {}) {
      const box = document.getElementById("messages");
      const forceFollow = options.forceFollow === true;
      const shouldFollow = forceFollow ? true : isMessagesPinned(box);
      const row = document.createElement("div");
      row.className = `message-row ${role}`;

      const stack = document.createElement("div");
      stack.className = "message-stack";

      const bubble = document.createElement("div");
      bubble.className = "message-bubble";
      renderBubbleContent(bubble, content, { ...options, role });

      const messageView = {
        row,
        stack,
        bubble,
        role,
        copyText: String(options.copyText ?? content ?? ""),
        editText: String(options.editText ?? content ?? ""),
      };

      const actions = createMessageActions(messageView, {
        editable: role === "user" && !options.imageDataUrl && !options.imageName,
      });
      messageView.actions = actions;

      const meta = document.createElement("div");
      meta.className = "message-meta";
      meta.hidden = true;
      messageView.meta = meta;

      stack.appendChild(bubble);
      stack.appendChild(actions);
      stack.appendChild(meta);
      row.appendChild(stack);
      box.appendChild(row);
      const actionsVisible = options.actionsHidden === true ? false : Boolean(String(content || "").trim());
      setMessageActionsVisible(messageView, actionsVisible);
      handleMessagesChanged(shouldFollow, { forceFollow });
      return messageView;
    }

    export function setMessageProcessingState(messageView, options = {}) {
      const bubble = messageView?.bubble || messageView;
      if (!bubble) return;
      const box = document.getElementById("messages");
      const shouldFollow = isMessagesPinned(box);
      const phase = String(options.phase || "prefill");
      const percentRaw = Number(options.percent);
      const percent = Number.isFinite(percentRaw)
        ? Math.max(0, Math.min(100, Math.round(percentRaw)))
        : null;
      const label = String(options.label || "Prompt processing");

      bubble.classList.remove("with-image");
      bubble.classList.add("processing");
      bubble.dataset.phase = phase;
      bubble.replaceChildren();
      setMessageActionsVisible(messageView, false);

      const shell = document.createElement("div");
      shell.className = "message-processing-shell";

      const labelEl = document.createElement("div");
      labelEl.className = "message-processing-label";
      labelEl.textContent = label;

      const meter = document.createElement("div");
      meter.className = "message-processing-meter";

      const bar = document.createElement("div");
      bar.className = "message-processing-bar";

      const barFill = document.createElement("div");
      barFill.className = "message-processing-bar-fill";
      if (percent !== null && phase !== "generating") {
        barFill.style.width = `${percent}%`;
      }
      bar.appendChild(barFill);

      const percentEl = document.createElement("div");
      percentEl.className = "message-processing-percent";
      percentEl.textContent = phase === "generating"
        ? "Live"
        : `${percent ?? 0}%`;

      meter.appendChild(bar);
      meter.appendChild(percentEl);

      shell.appendChild(labelEl);
      shell.appendChild(meter);
      bubble.appendChild(shell);
      handleMessagesChanged(shouldFollow);
    }

    export function updateMessage(messageView, content, options = {}) {
      const bubble = messageView?.bubble || messageView;
      if (!bubble) return;
      const box = document.getElementById("messages");
      const shouldFollow = isMessagesPinned(box);
      bubble.classList.remove("processing");
      delete bubble.dataset.phase;
      renderBubbleContent(bubble, content, { ...options, role: messageView?.role || options.role });
      if (messageView && typeof messageView === "object") {
        messageView.copyText = String(options.copyText ?? content ?? "");
        if (messageView.role === "user") {
          messageView.editText = String(options.editText ?? content ?? "");
        }
        const requestedVisibility = options.showActions;
        const nextVisibility = requestedVisibility === undefined
          ? messageView.role !== "assistant"
          : Boolean(requestedVisibility);
        setMessageActionsVisible(messageView, nextVisibility);
      }
      handleMessagesChanged(shouldFollow);
    }

    export function setMessageMeta(messageView, content) {
      const meta = messageView?.meta;
      if (!meta) return;
      const box = getMessagesBox();
      const shouldFollow = isMessagesPinned(box);
      const text = String(content || "").trim();
      meta.hidden = text.length === 0;
      meta.textContent = text;
      handleMessagesChanged(shouldFollow);
    }

    export function removeMessage(messageView) {
      const row = messageView?.row;
      if (row && row.parentNode) {
        row.parentNode.removeChild(row);
      }
    }
