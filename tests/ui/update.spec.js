const { test, expect } = require("@playwright/test");
const { makeStatusPayload, waitForStatusApplied } = require("./helpers");

function makeUpdatePayload(overrides = {}) {
  return makeStatusPayload({
    update: {
      available: false,
      current_version: "0.4.0",
      latest_version: null,
      release_notes: null,
      checked_at_unix: null,
      state: "idle",
      deferred: false,
      defer_reason: null,
      progress: { phase: null, percent: 0, error: null },
      ...overrides,
    },
  });
}

test("shows update-available card with version info and install button", async ({ page }) => {
  const status = makeUpdatePayload({
    available: true,
    current_version: "0.4.0",
    latest_version: "0.5.0",
    state: "idle",
    release_notes: "## What's new\n- Feature A",
  });
  await page.route("**/status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(status) })
  );
  await page.goto("/");
  await waitForStatusApplied(page);

  await expect(page.locator("#updateCard")).toBeVisible();
  await expect(page.locator("#updateCardTitle")).toContainText("0.5.0");
  await expect(page.locator("#updateStartBtn")).toBeVisible();
  await expect(page.locator("#updateNotesBtn")).toBeVisible();
});

test("hides update card when no update available", async ({ page }) => {
  const status = makeUpdatePayload({ available: false, state: "idle" });
  await page.route("**/status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(status) })
  );
  await page.goto("/");
  await waitForStatusApplied(page);

  await expect(page.locator("#updateCard")).toBeHidden();
  await expect(page.locator("#updateCheckBtn")).toBeVisible();
});

test("check-for-updates button calls endpoint and refreshes", async ({ page }) => {
  let checkCalled = false;
  const noUpdate = makeUpdatePayload({ available: false, state: "idle" });
  const hasUpdate = makeUpdatePayload({
    available: true,
    current_version: "0.4.0",
    latest_version: "0.5.0",
    state: "idle",
  });

  await page.route("**/status", (route) => {
    const payload = checkCalled ? hasUpdate : noUpdate;
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) });
  });
  await page.route("**/internal/update/check", (route) => {
    checkCalled = true;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ checked: true, ...hasUpdate.update }),
    });
  });
  await page.goto("/");
  await waitForStatusApplied(page);

  await expect(page.locator("#updateCard")).toBeHidden();
  await page.locator("#updateCheckBtn").click();
  await expect(page.locator("#updateCard")).toBeVisible({ timeout: 5000 });
});

test("check shows orchestrator-disabled message on 409", async ({ page }) => {
  const status = makeUpdatePayload({ available: false, state: "idle" });
  await page.route("**/status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(status) })
  );
  await page.route("**/internal/update/check", (route) =>
    route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({ reason: "orchestrator_disabled" }),
    })
  );
  await page.goto("/");
  await waitForStatusApplied(page);

  await page.locator("#updateCheckBtn").click();
  await expect(page.locator("#messages")).toContainText(/orchestrator/i, { timeout: 5000 });
});

test("install button hidden when deferred due to active download", async ({ page }) => {
  const status = makeUpdatePayload({
    available: true,
    current_version: "0.4.0",
    latest_version: "0.5.0",
    state: "idle",
    deferred: true,
    defer_reason: "download_active",
  });
  await page.route("**/status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(status) })
  );
  await page.goto("/");
  await waitForStatusApplied(page);

  await expect(page.locator("#updateCard")).toBeVisible();
  await expect(page.locator("#updateCardHint")).toContainText(/download/i);
  await expect(page.locator("#updateStartBtn")).toBeHidden();
});

test("shows downloading progress during update", async ({ page }) => {
  const status = makeUpdatePayload({
    available: true,
    current_version: "0.4.0",
    latest_version: "0.5.0",
    state: "downloading",
    progress: { phase: "downloading", percent: 42, error: null },
  });
  await page.route("**/status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(status) })
  );
  await page.goto("/");
  await waitForStatusApplied(page);

  await expect(page.locator("#updateCard")).toBeVisible();
  await expect(page.locator("#updateCardTitle")).toContainText(/download/i);
  await expect(page.locator("#updateCardProgress")).toBeVisible();
  await expect(page.locator("#updateCardHint")).toContainText("42%");
});

test("shows staging state during update", async ({ page }) => {
  const status = makeUpdatePayload({
    available: true,
    current_version: "0.4.0",
    latest_version: "0.5.0",
    state: "staging",
    progress: { phase: "staging", percent: 50, error: null },
  });
  await page.route("**/status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(status) })
  );
  await page.goto("/");
  await waitForStatusApplied(page);

  await expect(page.locator("#updateCard")).toBeVisible();
  await expect(page.locator("#updateCardTitle")).toContainText(/prepar/i);
});

test("shows applying state during update", async ({ page }) => {
  const status = makeUpdatePayload({
    available: true,
    current_version: "0.4.0",
    latest_version: "0.5.0",
    state: "applying",
    progress: { phase: "applying", percent: 80, error: null },
  });
  await page.route("**/status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(status) })
  );
  await page.goto("/");
  await waitForStatusApplied(page);

  await expect(page.locator("#updateCard")).toBeVisible();
  await expect(page.locator("#updateCardTitle")).toContainText(/install/i);
  await expect(page.locator("#updateCardHint")).toContainText(/power off/i);
});

test("shows restart-pending state", async ({ page }) => {
  const status = makeUpdatePayload({
    available: true,
    current_version: "0.4.0",
    latest_version: "0.5.0",
    state: "restart_pending",
    progress: { phase: null, percent: 100, error: null },
  });
  await page.route("**/status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(status) })
  );
  await page.goto("/");
  await waitForStatusApplied(page);

  await expect(page.locator("#updateCard")).toBeVisible();
  await expect(page.locator("#updateCardTitle")).toContainText(/restart/i);
});

test("shows failed state with error and retry button", async ({ page }) => {
  const status = makeUpdatePayload({
    available: true,
    current_version: "0.4.0",
    latest_version: "0.5.0",
    state: "failed",
    progress: { phase: null, percent: 0, error: "network_timeout" },
  });
  await page.route("**/status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(status) })
  );
  await page.goto("/");
  await waitForStatusApplied(page);

  await expect(page.locator("#updateCard")).toBeVisible();
  await expect(page.locator("#updateCardTitle")).toContainText(/failed/i);
  await expect(page.locator("#updateCardHint")).toContainText("network_timeout");
  await expect(page.locator("#updateRetryBtn")).toBeVisible();
});

test("install button calls start endpoint", async ({ page }) => {
  let startCalled = false;
  const status = makeUpdatePayload({
    available: true,
    current_version: "0.4.0",
    latest_version: "0.5.0",
    state: "idle",
  });
  await page.route("**/status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(status) })
  );
  await page.route("**/internal/update/start", (route) => {
    startCalled = true;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ started: true, ...status.update }),
    });
  });
  await page.goto("/");
  await waitForStatusApplied(page);

  await page.locator("#updateStartBtn").click();
  await page.waitForTimeout(500);
  expect(startCalled).toBe(true);
});

test("release notes displayed as chat message", async ({ page }) => {
  const status = makeUpdatePayload({
    available: true,
    current_version: "0.4.0",
    latest_version: "0.5.0",
    state: "idle",
    release_notes: "## What's new\n- Feature A\n- Bug fix B",
  });
  await page.route("**/status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(status) })
  );
  await page.goto("/");
  await waitForStatusApplied(page);

  await page.locator("#updateNotesBtn").click();
  await expect(page.locator("#messages")).toContainText("Feature A", { timeout: 5000 });
});

test("check button disabled during active update execution", async ({ page }) => {
  const status = makeUpdatePayload({
    available: true,
    current_version: "0.4.0",
    latest_version: "0.5.0",
    state: "downloading",
    progress: { phase: "downloading", percent: 30, error: null },
  });
  await page.route("**/status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(status) })
  );
  await page.goto("/");
  await waitForStatusApplied(page);

  await expect(page.locator("#updateCheckBtn")).toBeDisabled();
});

test("shows check error when update check fails", async ({ page }) => {
  const status = makeUpdatePayload({
    available: false,
    state: "idle",
    progress: { phase: null, percent: 0, error: "rate_limited" },
  });
  await page.route("**/status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(status) })
  );
  await page.goto("/");
  await waitForStatusApplied(page);

  await expect(page.locator("#updateCard")).toBeVisible();
  await expect(page.locator("#updateCardTitle")).toContainText(/check failed/i);
  await expect(page.locator("#updateCardHint")).toContainText(/rate limit/i);
});
