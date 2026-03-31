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
    const badge = document.getElementById("permitatoModeValue");
    return badge && badge.textContent !== "--" && badge.textContent !== "";
  }, { timeout: 10000 });
}

const FAKE_STATUS_OK = {
  mode: "work",
  mode_display: "Work",
  mode_description: "Social media blocked",
  active_exceptions: 0,
  exceptions: [],
  pihole_available: true,
  degraded_since: null,
  client_id: "192.168.1.106",
  client_valid: true,
  schedule_active: false,
  scheduled_mode: null,
  override_active: false,
  override_mode: null,
  custom_domain_count: 0,
  blocking_bypassed: false,
};

const FAKE_STATUS_BYPASSED = {
  ...FAKE_STATUS_OK,
  blocking_bypassed: true,
};


test("shows bypass banner when blocking_bypassed is true", async ({ page }) => {
  await openPermitato(page, {
    permitatoStatusRoute: async (route) => {
      await route.fulfill({ status: 200, body: JSON.stringify(FAKE_STATUS_BYPASSED) });
    },
  });

  await expect(page.locator("#permitatoBypassBanner")).toBeVisible();
  await expect(page.locator("#permitatoBypassBanner")).toContainText("not reaching Pi-hole");
});


test("hides bypass banner when blocking_bypassed is false", async ({ page }) => {
  await openPermitato(page, {
    permitatoStatusRoute: async (route) => {
      await route.fulfill({ status: 200, body: JSON.stringify(FAKE_STATUS_OK) });
    },
  });

  await expect(page.locator("#permitatoBypassBanner")).toBeHidden();
});


test("pihole dot shows bypassed state when blocking_bypassed is true", async ({ page }) => {
  await openPermitato(page, {
    permitatoStatusRoute: async (route) => {
      await route.fulfill({ status: 200, body: JSON.stringify(FAKE_STATUS_BYPASSED) });
    },
  });

  const dot = page.locator("#permitatoPiholeDot");
  await expect(dot).toHaveClass(/bypassed/);
  const label = page.locator("#permitatoPiholeLabel");
  await expect(label).toHaveText("DNS bypassed");
});
