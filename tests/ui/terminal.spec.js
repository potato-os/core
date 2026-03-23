const { test, expect } = require("@playwright/test");
const { waitUntilReady } = require("./helpers");

test("terminal button is visible in sidebar", async ({ page }) => {
  await waitUntilReady(page);
  await expect(page.locator("#terminalOpenBtn")).toBeVisible();
});

test("clicking terminal button opens modal", async ({ page }) => {
  await waitUntilReady(page);
  await page.locator("#terminalOpenBtn").click();
  await expect(page.locator("#terminalModal")).toBeVisible();
});

test("terminal modal shows connected status", async ({ page }) => {
  await waitUntilReady(page);
  await page.locator("#terminalOpenBtn").click();
  await expect(page.locator("#terminalModal")).toBeVisible();
  await expect(page.locator("#terminalStatusText")).toContainText("Connected", { timeout: 5000 });
});

test("terminal receives shell output", async ({ page }) => {
  await waitUntilReady(page);
  await page.locator("#terminalOpenBtn").click();
  await expect(page.locator("#terminalStatusText")).toContainText("Connected", { timeout: 5000 });

  // The xterm.js canvas should have rendered content (non-empty terminal)
  const hasContent = await page.evaluate(() => {
    const container = document.getElementById("terminalContainer");
    // xterm.js creates a .xterm-screen element with canvas children
    const screen = container?.querySelector(".xterm-screen");
    return screen !== null && screen.children.length > 0;
  });
  expect(hasContent).toBe(true);
});

test("terminal accepts input and shows output", async ({ page }) => {
  await waitUntilReady(page);
  await page.locator("#terminalOpenBtn").click();
  await expect(page.locator("#terminalStatusText")).toContainText("Connected", { timeout: 5000 });

  // Type a command into the terminal via the WebSocket protocol
  // xterm.js captures keyboard events on its textarea
  const termArea = page.locator("#terminalContainer textarea");
  await termArea.focus();
  await page.keyboard.type("echo pw-terminal-test", { delay: 10 });
  await page.keyboard.press("Enter");

  // Wait for the output to appear in the xterm buffer
  const found = await page.waitForFunction(() => {
    // Access the xterm.js buffer via the global reference we expose
    const buf = window.__potatoTerminal?.buffer?.active;
    if (!buf) return false;
    for (let i = 0; i < buf.length; i++) {
      const line = buf.getLine(i)?.translateToString(true) || "";
      if (line.includes("pw-terminal-test")) return true;
    }
    return false;
  }, { timeout: 5000 });
  expect(found).toBeTruthy();
});

test("terminal modal closes on close button click", async ({ page }) => {
  await waitUntilReady(page);
  await page.locator("#terminalOpenBtn").click();
  await expect(page.locator("#terminalModal")).toBeVisible();

  await page.locator("#terminalCloseBtn").click();
  await expect(page.locator("#terminalModal")).toBeHidden();
});

test("terminal modal closes on Escape key", async ({ page }) => {
  await waitUntilReady(page);
  await page.locator("#terminalOpenBtn").click();
  await expect(page.locator("#terminalModal")).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(page.locator("#terminalModal")).toBeHidden();
});

test("terminal modal closes on backdrop click", async ({ page }) => {
  await waitUntilReady(page);
  await page.locator("#terminalOpenBtn").click();
  await expect(page.locator("#terminalModal")).toBeVisible();

  // Click the modal overlay area (outside the shell) — this acts as backdrop
  // The modal overlay is pointer-events: none, but the backdrop behind it catches clicks
  await page.locator("#terminalBackdrop").dispatchEvent("click");
  await expect(page.locator("#terminalModal")).toBeHidden();
});

test("terminal reconnects when reconnect button is clicked", async ({ page }) => {
  await waitUntilReady(page);
  await page.locator("#terminalOpenBtn").click();
  await expect(page.locator("#terminalStatusText")).toContainText("Connected", { timeout: 5000 });

  // Force-close the WebSocket from the client side
  await page.evaluate(() => {
    window.__potatoTerminalWs?.close();
  });

  // Reconnect button should appear
  await expect(page.locator("#terminalReconnectBtn")).toBeVisible({ timeout: 3000 });
  await expect(page.locator("#terminalStatusText")).toContainText("Disconnected");

  // Click reconnect
  await page.locator("#terminalReconnectBtn").click();
  await expect(page.locator("#terminalStatusText")).toContainText("Connected", { timeout: 5000 });
  await expect(page.locator("#terminalReconnectBtn")).toBeHidden();
});
