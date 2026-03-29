const { test, expect } = require("@playwright/test");
const { makeStatusPayload } = require("../../../tests/ui/helpers");

async function openPermitato(page, { permitatoStatusRoute } = {}) {
  await page.route("**/status", async (route) => {
    if (route.request().url().includes("/app/permitato/")) return route.fallback();
    await route.fulfill({ status: 200, body: JSON.stringify(makeStatusPayload()) });
  });
  if (permitatoStatusRoute) {
    await page.route("**/app/permitato/api/status", permitatoStatusRoute);
  }
  await page.goto("/");
  await page.waitForSelector('button[data-app="permitato"]', { timeout: 5000 });
  await page.locator('button[data-app="permitato"]').click();
  await page.waitForFunction(() => {
    const bar = document.getElementById("permitatoStatusBar");
    const onb = document.getElementById("permitatoOnboarding");
    return (bar && bar.offsetParent !== null) || (onb && !onb.hidden);
  }, { timeout: 10000 });
}

const NOW = Math.floor(Date.now() / 1000);

const STATUS_WITH_EXCEPTIONS = {
  mode: "work",
  mode_display: "Work",
  mode_description: "Social media blocked",
  active_exceptions: 2,
  exceptions: [
    { id: "exc-1", domain: "twitter.com", reason: "check DMs", granted_at: NOW - 600, expires_at: NOW + 2400, ttl_seconds: 3600 },
    { id: "exc-2", domain: "reddit.com", reason: "research", granted_at: NOW - 300, expires_at: NOW + 1500, ttl_seconds: 1800 },
  ],
  pihole_available: true,
  degraded_since: null,
  client_id: "192.168.1.100",
  client_valid: true,
};

const STATUS_EMPTY = {
  ...STATUS_WITH_EXCEPTIONS,
  active_exceptions: 0,
  exceptions: [],
};


test("clicking exception count toggles panel open and closed", async ({ page }) => {
  await openPermitato(page, {
    permitatoStatusRoute: async (route) => {
      await route.fulfill({ status: 200, body: JSON.stringify(STATUS_WITH_EXCEPTIONS) });
    },
  });

  // Panel hidden by default
  await expect(page.locator("#permitatoExceptionsPanel")).toBeHidden();

  // Click count to open
  await page.locator("#permitatoExceptionsToggle").click();
  await expect(page.locator("#permitatoExceptionsPanel")).toBeVisible();
  await expect(page.locator("#permitatoExceptionsList li")).toHaveCount(2);

  // Click again to close
  await page.locator("#permitatoExceptionsToggle").click();
  await expect(page.locator("#permitatoExceptionsPanel")).toBeHidden();
});


test("exception items show domain, reason, TTL, and revoke button", async ({ page }) => {
  await openPermitato(page, {
    permitatoStatusRoute: async (route) => {
      await route.fulfill({ status: 200, body: JSON.stringify(STATUS_WITH_EXCEPTIONS) });
    },
  });

  await page.locator("#permitatoExceptionsToggle").click();

  const firstItem = page.locator("#permitatoExceptionsList li").first();
  await expect(firstItem.locator(".exc-domain")).toHaveText("twitter.com");
  await expect(firstItem.locator(".exc-reason")).toHaveText("check DMs");
  await expect(firstItem.locator(".exc-ttl")).toContainText(/\d+m/);
  await expect(firstItem.locator(".exc-revoke-btn")).toBeVisible();
});


test("revoke calls DELETE and refreshes the panel", async ({ page }) => {
  let revoked = false;

  await openPermitato(page, {
    permitatoStatusRoute: async (route) => {
      const data = revoked
        ? { ...STATUS_WITH_EXCEPTIONS, active_exceptions: 1, exceptions: [STATUS_WITH_EXCEPTIONS.exceptions[1]] }
        : STATUS_WITH_EXCEPTIONS;
      await route.fulfill({ status: 200, body: JSON.stringify(data) });
    },
  });

  await page.route("**/app/permitato/api/exceptions/exc-1", async (route) => {
    if (route.request().method() === "DELETE") {
      revoked = true;
      await route.fulfill({ status: 200, body: JSON.stringify({ revoked: true }) });
    } else {
      await route.fallback();
    }
  });

  await page.locator("#permitatoExceptionsToggle").click();
  await expect(page.locator("#permitatoExceptionsList li")).toHaveCount(2);

  // Click revoke on first item
  await page.locator(".exc-revoke-btn").first().click();

  // Should update to 1 exception after the re-poll
  await expect(page.locator("#permitatoExceptionsList li")).toHaveCount(1, { timeout: 10000 });
  await expect(page.locator("#permitatoExceptionCount")).toHaveText("1");
});
