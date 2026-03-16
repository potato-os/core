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
} = require("./helpers");


test("new chat button clears the conversation", async ({ page }) => {
  await page.route("**/v1/chat/completions", (route) => fulfillStreamingChat(route, { content: "Reply A" }));
  await waitUntilReady(page);

  await sendAndWaitForReply(page, "Hello session");
  await expect(page.locator(".message-row")).toHaveCount(2);

  await page.locator("#newChatBtn").click();
  await expect(page.locator(".message-row")).toHaveCount(0);
  await expect(page.locator("#userPrompt")).toBeFocused();
});

test("first message auto-creates a session in the sidebar list", async ({ page }) => {
  await page.route("**/v1/chat/completions", (route) => fulfillStreamingChat(route, { content: "Sure thing" }));
  await waitUntilReady(page);

  await sendAndWaitForReply(page, "What is a potato?");
  const sessionItems = page.locator(".chat-session-item");
  await expect(sessionItems).toHaveCount(1);
  await expect(sessionItems.first()).toContainText("What is a potato?");
});

test("switching sessions restores previous messages", async ({ page }) => {
  await page.route("**/v1/chat/completions", (route) => fulfillStreamingChat(route, { content: "Reply" }));
  await waitUntilReady(page);
  // Collapse runtime details so session items are clickable in the test viewport
  await page.locator("#runtimeViewToggle").click();

  // Create session A
  await sendAndWaitForReply(page, "Session A message");
  await expect(page.locator(".message-row")).toHaveCount(2);

  // Create session B
  await page.locator("#newChatBtn").click();
  await expect(page.locator(".message-row")).toHaveCount(0);
  await sendAndWaitForReply(page, "Session B message");
  await expect(page.locator(".message-row")).toHaveCount(2);

  // Switch to session A (most recent first, so session B is .first(), session A is .nth(1))
  await page.locator(".chat-session-item").nth(1).click();
  await expect(page.locator(".message-bubble").first()).toContainText("Session A message");

  // Switch back to session B
  await page.locator(".chat-session-item").first().click();
  await expect(page.locator(".message-bubble").first()).toContainText("Session B message");
});

test("sessions persist across page reload", async ({ page }) => {
  await page.route("**/v1/chat/completions", (route) => fulfillStreamingChat(route, { content: "Persistent reply" }));
  await waitUntilReady(page);

  await sendAndWaitForReply(page, "Remember me");
  await expect(page.locator(".chat-session-item")).toHaveCount(1);

  // Reload the page
  await page.reload();
  await expect(page.locator("#statusText")).toContainText("State: READY");

  // Session should be in sidebar
  await expect(page.locator(".chat-session-item")).toHaveCount(1);
  await expect(page.locator(".chat-session-item").first()).toContainText("Remember me");

  // Messages should be restored (active session auto-loaded)
  await expect(page.locator(".message-bubble").first()).toContainText("Remember me");
  await expect(page.locator(".message-bubble").nth(1)).toContainText("Persistent reply");
});

test("delete session removes it from sidebar and starts new chat if active", async ({ page }) => {
  await page.route("**/v1/chat/completions", (route) => fulfillStreamingChat(route, { content: "Doomed reply" }));
  await waitUntilReady(page);
  await page.locator("#runtimeViewToggle").click();

  await sendAndWaitForReply(page, "Delete me later");
  await expect(page.locator(".chat-session-item")).toHaveCount(1);

  // Delete the session
  await page.locator(".chat-session-item").first().hover();
  await page.locator(".chat-session-delete").first().click();
  await expect(page.locator(".chat-session-item")).toHaveCount(0);
  await expect(page.locator(".message-row")).toHaveCount(0);
});

test("image messages show placeholder after session restore", async ({ page }) => {
  // Intercept chat with a reply
  await page.route("**/v1/chat/completions", (route) => fulfillStreamingChat(route, { content: "Nice image" }));
  await waitUntilReady(page);
  await page.locator("#runtimeViewToggle").click();

  // Attach a small test image and send
  const imageInput = page.locator("#imageInput");
  await imageInput.setInputFiles("tests/ui/fixtures/test-cat-small.jpg");
  await expect(page.locator("#imagePreview")).toBeVisible();
  await sendAndWaitForReply(page, "Describe this cat");
  await expect(page.locator(".chat-session-item")).toHaveCount(1);

  // Switch away and back
  await page.locator("#newChatBtn").click();
  await page.locator(".chat-session-item").first().click();

  // Image bubble should show placeholder, not the original data URL
  const userBubble = page.locator(".message-row.user .message-bubble").first();
  await expect(userBubble).toContainText("Describe this cat");
  // Should NOT have a full data: URL image displayed
  const hasDataUrl = await page.evaluate(() => {
    const imgs = document.querySelectorAll(".message-row.user .message-bubble img");
    return Array.from(imgs).some((img) => img.src.startsWith("data:"));
  });
  expect(hasDataUrl).toBe(false);
});

test("editing a message in a restored session works correctly", async ({ page }) => {
  let replyCount = 0;
  await page.route("**/v1/chat/completions", (route) => {
    replyCount++;
    return fulfillStreamingChat(route, { content: `Reply ${replyCount}` });
  });
  await waitUntilReady(page);
  await page.locator("#runtimeViewToggle").click();

  // Build a 2-turn session
  await sendAndWaitForReply(page, "First question");
  await sendAndWaitForReply(page, "Second question");
  await expect(page.locator(".message-row")).toHaveCount(4);

  // Switch away and back to restore from IndexedDB
  await page.locator("#newChatBtn").click();
  await page.locator(".chat-session-item").first().click();
  await expect(page.locator(".message-row")).toHaveCount(4);

  // Edit the first user message
  await page.locator(".message-row.user").first().locator("button[aria-label='Edit message']").click();
  await expect(page.locator("#editModal")).toBeVisible();
  await page.locator("#editMessageInput").fill("Edited question");
  await page.locator("#editSendBtn").click();

  // Second turn should be removed, new reply generated
  await expect(page.locator("#sendBtn")).toHaveText("Send", { timeout: 5000 });
  await expect(page.locator(".message-row")).toHaveCount(2);
  await expect(page.locator(".message-row.user .message-bubble").first()).toContainText("Edited question");
});

test("session title is auto-generated from first message and truncated", async ({ page }) => {
  await page.route("**/v1/chat/completions", (route) => fulfillStreamingChat(route, { content: "OK" }));
  await waitUntilReady(page);

  const longMessage = "This is a very long message that should definitely be truncated when used as a session title";
  await sendAndWaitForReply(page, longMessage);

  const titleEl = page.locator(".chat-session-title").first();
  const title = await titleEl.textContent();
  expect(title.length).toBeLessThanOrEqual(43); // 40 + "..." ellipsis
  expect(longMessage.startsWith(title.replace("...", "").trim())).toBe(true);
});

