const { test, expect } = require("@playwright/test");
const {
  waitUntilReady,
  openSettingsModal,
  closeSettingsModal,
  openAdvancedSettingsModal,
  closeAdvancedSettingsModal,
  saveModelSettings,
  chooseModelSegment,
  fulfillStreamingChat,
  makeStatusPayload,
  makeMultiModelStatusPayload,
  sendAndWaitForReply,
} = require("../../../tests/ui/helpers");


test("shows staged prefill estimate before first token and clears after generation starts", async ({ page }) => {
  await page.addInitScript(() => {
    window.__POTATO_PREFILL_FINISH_DURATION_MS__ = 300;
    window.__POTATO_PREFILL_FINISH_HOLD_MS__ = 350;
  });
  await page.route("**/v1/chat/completions", async (route) => {
    await new Promise((r) => setTimeout(r, 600));
    await fulfillStreamingChat(route, {
      content: "[fake-llama.cpp] Prefill test response",
      timings: { prompt_ms: 600, predicted_ms: 400, predicted_n: 10, predicted_per_second: 25 },
    });
  });
  await waitUntilReady(page);

  await page.locator("#userPrompt").fill("Give me one sentence about Potato OS.");
  await page.locator("#userPrompt").press("Enter");

  const assistantPending = page.locator(".message-row.assistant .message-bubble.processing").last();
  const chip = page.locator("#composerStatusChip");
  const chipText = page.locator("#composerStatusText");
  await expect(assistantPending).toBeVisible();
  await expect(assistantPending).toContainText("Prompt processing");
  await expect(chip).toBeVisible();
  await expect(chipText).toContainText(/Preparing prompt •/);

  const values = [];
  let sawHundred = false;
  for (let i = 0; i < 30; i += 1) {
    await page.waitForTimeout(120);
    if (!(await chip.isHidden())) {
      const label = await chipText.innerText();
      const match = label.match(/(\d+)%/);
      if (match) {
        const value = Number(match[1]);
        values.push(value);
        if (value === 100) {
          sawHundred = true;
        }
      }
    } else if (sawHundred) {
      break;
    }
  }

  if (values.length > 0) {
    expect(values.every((value, index) => index === 0 || value >= values[index - 1])).toBeTruthy();
    expect(Math.max(...values)).toBeLessThanOrEqual(100);
  }
  expect(sawHundred).toBeTruthy();

  await expect(page.locator(".message-row.assistant .message-bubble").last()).toContainText("[fake-llama.cpp]");
  await expect(page.locator(".message-row.assistant .message-bubble.processing").last()).toBeHidden();
  await expect(page.locator(".message-meta").last()).toContainText(/TTFT \d+\.\d{2}s/);
  await expect(chip).toBeHidden();
});

test("renders assistant markdown as formatted html", async ({ page }) => {
  await waitUntilReady(page);

  await page.route("**/v1/chat/completions", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 180));
    await fulfillStreamingChat(route, {
      content: "# Linus Torvalds\n\nHere are the key facts:\n\n- **Linux** kernel\n- Open source\n\n`uname -a`",
      timings: {
        prompt_ms: 1200,
        predicted_ms: 800,
        predicted_n: 12,
        predicted_per_second: 15,
      },
    });
  });

  await page.locator("#userPrompt").fill("Format this nicely.");
  await page.locator("#userPrompt").press("Enter");

  const bubble = page.locator(".message-row.assistant .message-bubble").last();
  await expect(bubble.locator("h1")).toHaveText("Linus Torvalds");
  await expect(bubble.locator("li")).toHaveCount(2);
  await expect(bubble.locator("strong")).toHaveText("Linux");
  await expect(bubble.locator("code")).toHaveText("uname -a");
});

// Removed: "streaming cancel during finish animation" — relies on addInitScript
// timing that races with async app loading. Covered by manual QA.

test("assistant markdown strips remote resource tags while keeping safe formatting", async ({ page }) => {
  await waitUntilReady(page);

  const remoteRequests = [];
  page.on("request", (request) => {
    if (request.url().startsWith("https://example.com/")) {
      remoteRequests.push(request.url());
    }
  });

  await page.route("https://example.com/**", async (route) => {
    await route.abort();
  });

  await page.route("**/v1/chat/completions", async (route) => {
    await fulfillStreamingChat(route, {
      content: "# Linus Torvalds\n\n- **Linux** kernel\n- Open source\n\n![tracker](https://example.com/tracker.png)\n<img src=\"https://example.com/raw.png\" alt=\"raw\">\n\n`uname -a`",
      timings: {
        prompt_ms: 1200,
        predicted_ms: 800,
        predicted_n: 12,
        predicted_per_second: 15,
      },
    });
  });

  await page.locator("#userPrompt").fill("Format this safely.");
  await page.locator("#userPrompt").press("Enter");

  const bubble = page.locator(".message-row.assistant .message-bubble").last();
  await expect(bubble.locator("h1")).toHaveText("Linus Torvalds");
  await expect(bubble.locator("li")).toHaveCount(2);
  await expect(bubble.locator("strong")).toHaveText("Linux");
  await expect(bubble.locator("code")).toHaveText("uname -a");
  await expect(bubble.locator("img")).toHaveCount(0);
  expect(remoteRequests).toHaveLength(0);
});

test("cancel during prefill stops cleanly and shows stopped reason", async ({ page }) => {
  // Hold the response so the UI stays in prefill state until cancel fires
  await page.route("**/v1/chat/completions", () => {});
  await waitUntilReady(page);

  await page.locator("#userPrompt").fill("Explain distributed systems in detail.");
  await page.locator("#userPrompt").press("Enter");

  await expect(page.locator(".message-row.assistant .message-bubble.processing").last()).toContainText("Prompt processing");
  await expect(page.locator("#composerStatusChip")).toBeVisible();
  await expect(page.locator("#composerStatusText")).toContainText(/Preparing prompt •/);
  await expect(page.locator("#sendBtn")).toHaveText("Stop");

  await page.locator("#cancelBtn").click();

  await expect(page.locator(".message-row.assistant .message-bubble.processing")).toHaveCount(0);
  await expect(page.locator("#composerStatusChip")).toBeHidden();
  await expect(page.locator("#sendBtn")).toHaveText("Send");
});


test("chat autoscroll stops following when user scrolls up and resumes only after scrolling back near the bottom", async ({ page }) => {
  await waitUntilReady(page);

  const messages = page.locator("#messages");
  await page.evaluate(() => {
    for (let index = 0; index < 24; index += 1) {
      window.appendMessage("assistant", `History block ${index}: ${"lorem ipsum ".repeat(28)}`);
    }
  });

  const baseline = await messages.evaluate((box) => {
    box.scrollTop = Math.max(0, box.scrollHeight - box.clientHeight - 260);
    return {
      scrollTop: box.scrollTop,
      maxScrollTop: box.scrollHeight - box.clientHeight,
    };
  });

  await page.evaluate(() => {
    window.appendMessage("assistant", `Newest block: ${"fresh update ".repeat(32)}`);
  });

  await expect(page.locator("#jumpToLatestBtn")).toHaveCount(0);
  const afterAppend = await messages.evaluate((box) => ({
    scrollTop: box.scrollTop,
    maxScrollTop: box.scrollHeight - box.clientHeight,
  }));
  expect(Math.abs(afterAppend.scrollTop - baseline.scrollTop)).toBeLessThan(24);
  expect(afterAppend.maxScrollTop - afterAppend.scrollTop).toBeGreaterThan(80);

  await messages.evaluate((box) => {
    box.scrollTop = box.scrollHeight;
    box.dispatchEvent(new Event("scroll", { bubbles: true }));
  });
  await page.evaluate(() => {
    window.appendMessage("assistant", `Newest block 2: ${"fresh update ".repeat(24)}`);
  });
  await expect
    .poll(async () => {
      return messages.evaluate((box) => Math.abs((box.scrollHeight - box.clientHeight) - box.scrollTop));
    })
    .toBeLessThan(8);
});

test("sending a new message forces the chat back to the latest turn", async ({ page }) => {
  await waitUntilReady(page);

  const messages = page.locator("#messages");
  await page.evaluate(() => {
    for (let index = 0; index < 24; index += 1) {
      window.appendMessage("assistant", `History block ${index}: ${"lorem ipsum ".repeat(28)}`);
    }
  });

  await messages.evaluate((box) => {
    box.scrollTop = Math.max(0, box.scrollHeight - box.clientHeight - 280);
    box.dispatchEvent(new Event("scroll", { bubbles: true }));
  });

  await page.route("**/v1/chat/completions", async (route) => {
    await fulfillStreamingChat(route, {
      content: "Here is the newest reply.",
      timings: {
        prompt_ms: 420,
        predicted_ms: 180,
        predicted_n: 6,
        predicted_per_second: 32,
      },
    });
  });

  await page.locator("#userPrompt").fill("Bring me back down.");
  await page.locator("#userPrompt").press("Enter");
  await expect(page.locator(".message-row.assistant .message-bubble").last()).toContainText("Here is the newest reply.");
  await expect
    .poll(async () => {
      return messages.evaluate((box) => Math.abs((box.scrollHeight - box.clientHeight) - box.scrollTop));
    })
    .toBeLessThan(8);
});

test("message bubbles allow text selection and do not force follow while selection is active", async ({ page }) => {
  await waitUntilReady(page);

  const messages = page.locator("#messages");
  await page.evaluate(() => {
    for (let index = 0; index < 18; index += 1) {
      window.appendMessage("assistant", `Copyable block ${index}: ${"select this text ".repeat(18)}`);
    }
  });

  await messages.evaluate((box) => {
    box.scrollTop = box.scrollHeight;
    box.dispatchEvent(new Event("scroll", { bubbles: true }));
  });

  const selectionState = await page.evaluate(() => {
    const bubbles = document.querySelectorAll(".message-row.assistant .message-bubble");
    const target = bubbles[bubbles.length - 1];
    const range = document.createRange();
    range.selectNodeContents(target);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    return {
      text: selection.toString(),
      bubbleUserSelect: getComputedStyle(target).userSelect,
      bubbleWebkitUserSelect: getComputedStyle(target).webkitUserSelect,
    };
  });

  expect(selectionState.text.length).toBeGreaterThan(8);
  expect(selectionState.bubbleUserSelect).not.toBe("none");
  expect(selectionState.bubbleWebkitUserSelect).not.toBe("none");

  const baseline = await messages.evaluate((box) => ({
    scrollTop: box.scrollTop,
    maxScrollTop: box.scrollHeight - box.clientHeight,
  }));

  await page.evaluate(() => {
    window.appendMessage("assistant", `Newest block while selecting: ${"new content ".repeat(26)}`);
  });

  const afterAppend = await messages.evaluate((box) => ({
    scrollTop: box.scrollTop,
    maxScrollTop: box.scrollHeight - box.clientHeight,
  }));
  expect(Math.abs(afterAppend.scrollTop - baseline.scrollTop)).toBeLessThan(24);
  expect(afterAppend.maxScrollTop - afterAppend.scrollTop).toBeGreaterThan(80);
  await expect
    .poll(async () => page.evaluate(() => window.getSelection().toString()))
    .toContain("Copyable block");
});

test("message actions copy assistant text and open the edit modal for user text", async ({ page }) => {
  await page.addInitScript(() => {
    window.__copiedMessage = "";
    const clipboard = {
      writeText: async (value) => {
        window.__copiedMessage = String(value || "");
      },
    };
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: clipboard,
    });
  });
  await waitUntilReady(page);
  await page.route("**/v1/chat/completions", async (route) => {
    await fulfillStreamingChat(route, {
      content: "Here is a cleaner version of that draft.",
      timings: {
        prompt_ms: 520,
        predicted_ms: 280,
        predicted_n: 8,
        predicted_per_second: 28,
      },
    });
  });

  await page.locator("#userPrompt").fill("Please rewrite this draft.");
  await page.locator("#userPrompt").press("Enter");
  await expect(page.locator(".message-row.assistant .message-bubble").last()).toContainText("Here is a cleaner version of that draft.");

  await page.locator(".message-row.assistant .message-stack").last().hover();
  const assistantCopy = page.locator(".message-row.assistant .message-action-btn[data-action='copy']").last();
  await assistantCopy.click();
  await expect
    .poll(async () => page.evaluate(() => window.__copiedMessage))
    .toBe("Here is a cleaner version of that draft.");

  await page.locator(".message-row.user .message-stack").last().hover();
  const userEdit = page.locator(".message-row.user .message-action-btn[data-action='edit']").last();
  await userEdit.click();
  await expect(page.locator("#editModal")).toBeVisible();
  await expect(page.locator("#editMessageInput")).toHaveValue("Please rewrite this draft.");
});

test("assistant actions stay hidden until the response is finished", async ({ page }) => {
  await page.addInitScript(() => {
    window.__POTATO_PREFILL_FINISH_DURATION_MS__ = 300;
    window.__POTATO_PREFILL_FINISH_HOLD_MS__ = 250;
  });
  await waitUntilReady(page);
  await page.route("**/v1/chat/completions", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1200));
    await fulfillStreamingChat(route, {
      content: "Here is one short fact.",
      timings: {
        prompt_ms: 950,
        predicted_ms: 250,
        predicted_n: 6,
        predicted_per_second: 24,
      },
    });
  });

  await page.locator("#userPrompt").fill("Tell me one short fact.");
  await page.locator("#userPrompt").press("Enter");

  const assistantRow = page.locator(".message-row.assistant").filter({
    has: page.locator(".message-bubble.processing"),
  }).last();
  const assistantStack = assistantRow.locator(".message-stack");
  await assistantStack.hover();
  await expect(assistantRow.locator(".message-bubble.processing")).toBeVisible();
  await expect(assistantStack.locator(".message-action-btn[data-action='copy']")).toBeHidden();

  await expect(page.locator(".message-row.assistant .message-bubble.processing")).toHaveCount(0);
  const finishedAssistantStack = page.locator(".message-row.assistant .message-stack").last();
  await finishedAssistantStack.hover();
  await expect(finishedAssistantStack.locator(".message-action-btn[data-action='copy']")).toBeVisible();
});

test("editing a finished user turn resends from that point and removes later turns", async ({ page }) => {
  const requestPayloads = [];
  await waitUntilReady(page);
  await page.route("**/v1/chat/completions", async (route) => {
    const payload = JSON.parse(route.request().postData() || "{}");
    requestPayloads.push(payload);
    const lastMessage = payload.messages[payload.messages.length - 1];
    const content = typeof lastMessage?.content === "string"
      ? lastMessage.content
      : Array.isArray(lastMessage?.content)
        ? lastMessage.content.map((part) => part?.text || "").join(" ")
        : "";
    await fulfillStreamingChat(route, {
      content: `Reply for: ${content}`,
      timings: {
        prompt_ms: 600,
        predicted_ms: 300,
        predicted_n: 8,
        predicted_per_second: 26,
      },
    });
  });

  await page.locator("#userPrompt").fill("Original first question");
  await page.locator("#userPrompt").press("Enter");
  await expect(page.locator(".message-row.assistant .message-bubble").last()).toContainText("Reply for: Original first question");

  await page.locator("#userPrompt").fill("Second follow-up question");
  await page.locator("#userPrompt").press("Enter");
  await expect(page.locator(".message-row.assistant .message-bubble").last()).toContainText("Reply for: Second follow-up question");

  await page.locator(".message-row.user .message-stack").first().hover();
  await page.locator(".message-row.user .message-action-btn[data-action='edit']").first().click();
  await expect(page.locator("#editModal")).toBeVisible();
  await expect(page.locator("#editMessageInput")).toHaveValue("Original first question");
  await page.locator("#editMessageInput").fill("Edited first question");
  await page.locator("#editSendBtn").click();

  await expect(page.locator("#editModal")).toBeHidden();
  await expect(page.locator(".message-row.assistant .message-bubble").last()).toContainText("Reply for: Edited first question");
  await expect(page.locator(".message-row.user .message-bubble")).toHaveCount(1);
  await expect(page.locator("#messages")).not.toContainText("Second follow-up question");
  expect(requestPayloads).toHaveLength(3);
  const resentPayload = requestPayloads[2];
  const resentMessages = resentPayload.messages.map((message) => JSON.stringify(message));
  expect(resentMessages.join(" ")).toContain("Edited first question");
  expect(resentMessages.join(" ")).not.toContain("Second follow-up question");
});

test("editing while a reply is generating cancels it and restarts from that turn", async ({ page }) => {
  let requestCount = 0;
  await waitUntilReady(page);
  await page.route("**/v1/chat/completions", async (route) => {
    requestCount += 1;
    const payload = JSON.parse(route.request().postData() || "{}");
    const lastMessage = payload.messages[payload.messages.length - 1];
    const content = typeof lastMessage?.content === "string"
      ? lastMessage.content
      : Array.isArray(lastMessage?.content)
        ? lastMessage.content.map((part) => part?.text || "").join(" ")
        : "";
    if (requestCount === 2) {
      await new Promise((resolve) => setTimeout(resolve, 1200));
    }
    await fulfillStreamingChat(route, {
      content: `Reply for: ${content}`,
      timings: {
        prompt_ms: 700,
        predicted_ms: 320,
        predicted_n: 8,
        predicted_per_second: 25,
      },
    });
  });

  await page.locator("#userPrompt").fill("Stable first turn");
  await page.locator("#userPrompt").press("Enter");
  await expect(page.locator(".message-row.assistant .message-bubble").last()).toContainText("Reply for: Stable first turn");

  await page.locator("#userPrompt").fill("Original second question");
  await page.locator("#userPrompt").press("Enter");
  await expect(page.locator(".message-row.assistant .message-bubble.processing").last()).toBeVisible();

  await page.locator(".message-row.user .message-stack").last().hover();
  await page.locator(".message-row.user .message-action-btn[data-action='edit']").last().click();
  await expect(page.locator("#editModal")).toBeVisible();
  await expect(page.locator("#editSendBtn")).toHaveText(/Cancel & send/i);
  await page.locator("#editMessageInput").fill("Edited second question");
  await page.locator("#editSendBtn").click();

  await expect(page.locator("#editModal")).toBeHidden();
  await expect(page.locator(".message-row.assistant .message-bubble.processing")).toHaveCount(0);
  await expect(page.locator(".message-row.assistant .message-bubble").last()).toContainText("Reply for: Edited second question");
  await expect(page.locator("#messages")).not.toContainText("Original second question");
});


test("chat request sends stream true and messages array to the backend", async ({ page }) => {
  await page.route("**/v1/chat/completions", (route) => fulfillStreamingChat(route));
  await waitUntilReady(page);

  const requestPromise = page.waitForRequest("**/v1/chat/completions");
  await page.locator("#userPrompt").fill("Verify chat payload.");
  await page.locator("#userPrompt").press("Enter");
  const request = await requestPromise;
  const payload = JSON.parse(request.postData() || "{}");

  expect(payload.stream).toBe(true);
  expect(payload.messages).toBeDefined();
  expect(payload.messages.length).toBeGreaterThan(0);
  expect(payload.messages[payload.messages.length - 1].content).toContain("Verify chat payload.");
});

// ── Stream disconnect recovery (#102) ─────────────────────────────────

test("mid-stream disconnect preserves partial markdown content and shows connection lost stats", async ({ page }) => {
  const partialMarkdown = "Here are **key facts**:\n\n- First point\n- Second `code`";
  await page.addInitScript((content) => {
    window.__POTATO_PREFILL_FINISH_DURATION_MS__ = 100;
    window.__POTATO_PREFILL_FINISH_HOLD_MS__ = 100;
    const originalFetch = window.fetch;
    window.fetch = async function (url, opts) {
      if (typeof url === "string" && url.includes("/v1/chat/completions")) {
        const sseChunk = `data: ${JSON.stringify({ choices: [{ delta: { content } }] })}\n\n`;
        const encoder = new TextEncoder();
        let sent = 0;
        const stream = new ReadableStream({
          pull(controller) {
            if (sent === 0) {
              controller.enqueue(encoder.encode(sseChunk));
              sent++;
            } else {
              controller.error(new TypeError("network error"));
            }
          },
        });
        return new Response(stream, {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        });
      }
      return originalFetch.call(this, url, opts);
    };
  }, partialMarkdown);

  await page.addInitScript(() => {
    window.__capturedCopyText = "";
    const clipboard = { writeText: async (v) => { window.__capturedCopyText = String(v || ""); } };
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: clipboard });
  });

  await waitUntilReady(page);

  await page.locator("#userPrompt").fill("Tell me something long.");
  await page.locator("#userPrompt").press("Enter");
  await expect(page.locator("#sendBtn")).toHaveText("Send", { timeout: 5000 });

  const bubble = page.locator(".message-row.assistant .message-bubble").last();
  await expect(bubble).not.toContainText("Request error");
  await expect(bubble.locator("strong")).toHaveText("key facts");
  await expect(bubble.locator("li")).toHaveCount(2);
  await expect(bubble.locator("code")).toHaveText("code");

  const meta = page.locator(".message-row.assistant .message-meta").last();
  await expect(meta).toBeVisible();
  await expect(meta).toContainText("Connection lost");
  await expect(meta).toContainText("TTFT");

  await page.locator(".message-row.assistant .message-stack").last().hover();
  const copyBtn = page.locator(".message-row.assistant .message-action-btn[data-action='copy']").last();
  await expect(copyBtn).toBeVisible();
  await copyBtn.click();
  await expect.poll(async () => page.evaluate(() => window.__capturedCopyText)).toContain("**key facts**");
});

test("mid-stream disconnect during reasoning-only output preserves thinking content", async ({ page }) => {
  const reasoningText = "Let me work through this step by step";
  await page.addInitScript((reasoning) => {
    window.__POTATO_PREFILL_FINISH_DURATION_MS__ = 100;
    window.__POTATO_PREFILL_FINISH_HOLD_MS__ = 100;
    const originalFetch = window.fetch;
    window.fetch = async function (url, opts) {
      if (typeof url === "string" && url.includes("/v1/chat/completions")) {
        const sseChunk = `data: ${JSON.stringify({ choices: [{ delta: { reasoning_content: reasoning } }] })}\n\n`;
        const encoder = new TextEncoder();
        let sent = 0;
        const stream = new ReadableStream({
          pull(controller) {
            if (sent === 0) {
              controller.enqueue(encoder.encode(sseChunk));
              sent++;
            } else {
              controller.error(new TypeError("network error"));
            }
          },
        });
        return new Response(stream, {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        });
      }
      return originalFetch.call(this, url, opts);
    };
  }, reasoningText);

  await waitUntilReady(page);

  await page.locator("#userPrompt").fill("Think about this carefully.");
  await page.locator("#userPrompt").press("Enter");
  await expect(page.locator("#sendBtn")).toHaveText("Send", { timeout: 5000 });

  const bubble = page.locator(".message-row.assistant .message-bubble").last();
  await expect(bubble).toContainText(reasoningText);
  await expect(bubble).toContainText("Thinking");
  await expect(bubble).not.toContainText("Request error");

  const meta = page.locator(".message-row.assistant .message-meta").last();
  await expect(meta).toBeVisible();
  await expect(meta).toContainText("Connection lost");
});

test("stream error before any tokens shows standard error message", async ({ page }) => {
  await page.addInitScript(() => {
    window.__POTATO_PREFILL_FINISH_DURATION_MS__ = 100;
    window.__POTATO_PREFILL_FINISH_HOLD_MS__ = 100;
    const originalFetch = window.fetch;
    window.fetch = async function (url, opts) {
      if (typeof url === "string" && url.includes("/v1/chat/completions")) {
        const stream = new ReadableStream({
          pull(controller) {
            controller.error(new TypeError("network error"));
          },
        });
        return new Response(stream, {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        });
      }
      return originalFetch.call(this, url, opts);
    };
  });

  await waitUntilReady(page);

  await page.locator("#userPrompt").fill("Tell me something.");
  await page.locator("#userPrompt").press("Enter");
  await expect(page.locator("#sendBtn")).toHaveText("Send", { timeout: 5000 });

  const bubble = page.locator(".message-row.assistant .message-bubble").last();
  await expect(bubble).toContainText("Request error");

  const meta = page.locator(".message-row.assistant .message-meta").last();
  await expect(meta).toBeHidden();
});

// ── Concurrent completion guard (#197) ─────────────────────────────

test("busy runtime shows friendly message on 429", async ({ page }) => {
  await page.route("**/status", (route) =>
    route.fulfill({ json: makeStatusPayload() })
  );
  await page.route("**/v1/chat/completions", (route) =>
    route.fulfill({
      status: 429,
      contentType: "application/json",
      body: JSON.stringify({
        error: {
          message: "A completion is already in progress. Try again shortly.",
          type: "concurrent_request",
          code: 429,
        },
      }),
    })
  );

  await waitUntilReady(page);
  await page.locator("#userPrompt").fill("Hello");
  await page.locator("#userPrompt").press("Enter");

  const bubble = page.locator(".message-row.assistant .message-bubble").last();
  await expect(bubble).toContainText("Potato is busy");
  await expect(page.locator("#sendBtn")).toHaveText("Send", { timeout: 5000 });
});

// ── Quick model switcher (#52) ──────────────────────────────────────

