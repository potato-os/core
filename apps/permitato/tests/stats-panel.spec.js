const { test, expect } = require("@playwright/test");
const { makeStatusPayload } = require("../../../tests/ui/helpers");

async function openPermitato(page, { permitatoStatusRoute, statsRoute } = {}) {
  await page.route("**/status", async (route) => {
    if (route.request().url().includes("/app/permitato/")) return route.fallback();
    await route.fulfill({ status: 200, body: JSON.stringify(makeStatusPayload()) });
  });
  if (permitatoStatusRoute) {
    await page.route("**/app/permitato/api/status", permitatoStatusRoute);
  }
  if (statsRoute) {
    await page.route("**/app/permitato/api/stats", statsRoute);
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

const FAKE_STATUS = {
  mode: "work",
  mode_display: "Work",
  mode_description: "Social media blocked",
  active_exceptions: 0,
  exceptions: [],
  pihole_available: true,
  degraded_since: null,
  client_id: "192.168.1.100",
  client_valid: true,
  schedule_active: false,
  scheduled_mode: null,
  override_active: false,
  override_mode: null,
};

const FAKE_STATS = {
  focus_streak_days: 5,
  requests_today: { granted: 1, denied: 2 },
  top_domains: [
    { domain: "twitter.com", count: 8 },
    { domain: "reddit.com", count: 5 },
    { domain: "youtube.com", count: 2 },
  ],
  mode_duration_seconds: 8100,
  deny_rate: { rate: 0.6, total: 15, denied: 9 },
  data_span_days: 20,
};

const EMPTY_STATS = {
  focus_streak_days: 0,
  requests_today: { granted: 0, denied: 0 },
  top_domains: [],
  mode_duration_seconds: null,
  deny_rate: { rate: null, total: 0, denied: 0 },
  data_span_days: 0,
};


test("stats toggle opens and closes panel", async ({ page }) => {
  await openPermitato(page, {
    permitatoStatusRoute: async (route) => {
      await route.fulfill({ status: 200, body: JSON.stringify(FAKE_STATUS) });
    },
    statsRoute: async (route) => {
      await route.fulfill({ status: 200, body: JSON.stringify(FAKE_STATS) });
    },
  });

  const panel = page.locator("#permitatoStatsPanel");
  const toggle = page.locator("#permitatoStatsToggle");

  await expect(panel).toBeHidden();
  await toggle.click();
  await expect(panel).toBeVisible();
  await toggle.click();
  await expect(panel).toBeHidden();
});


test("stats panel shows all metrics with data", async ({ page }) => {
  await openPermitato(page, {
    permitatoStatusRoute: async (route) => {
      await route.fulfill({ status: 200, body: JSON.stringify(FAKE_STATUS) });
    },
    statsRoute: async (route) => {
      await route.fulfill({ status: 200, body: JSON.stringify(FAKE_STATS) });
    },
  });

  await page.locator("#permitatoStatsToggle").click();
  const panel = page.locator("#permitatoStatsPanel");
  await expect(panel).toBeVisible();

  await expect(page.locator("#permitatoStreakValue")).toHaveText("5");
  await expect(page.locator("#permitatoTodayValue")).toHaveText("3");
  await expect(page.locator("#permitatoDenyRateValue")).toHaveText("60%");
  await expect(page.locator("#permitatoModeDurationValue")).toHaveText("2h 15m");
  await expect(page.locator("#permitatoTopDomainsList")).toContainText("twitter.com");
  await expect(page.locator("#permitatoDataSpan")).toHaveText("20");
});


test("stats panel shows empty state when no data", async ({ page }) => {
  await openPermitato(page, {
    permitatoStatusRoute: async (route) => {
      await route.fulfill({ status: 200, body: JSON.stringify(FAKE_STATUS) });
    },
    statsRoute: async (route) => {
      await route.fulfill({ status: 200, body: JSON.stringify(EMPTY_STATS) });
    },
  });

  await page.locator("#permitatoStatsToggle").click();
  await expect(page.locator("#permitatoStatsEmpty")).toBeVisible();
  await expect(page.locator("#permitatoStatsGrid")).toBeHidden();
});


test("single-day past activity is not treated as empty", async ({ page }) => {
  const singleDayStats = {
    ...EMPTY_STATS,
    top_domains: [{ domain: "twitter.com", count: 2 }],
    deny_rate: { rate: null, total: 2, denied: 1 },
    // data_span_days is 0 (all on one day) and requests_today is 0
  };
  await openPermitato(page, {
    permitatoStatusRoute: async (route) => {
      await route.fulfill({ status: 200, body: JSON.stringify(FAKE_STATUS) });
    },
    statsRoute: async (route) => {
      await route.fulfill({ status: 200, body: JSON.stringify(singleDayStats) });
    },
  });

  await page.locator("#permitatoStatsToggle").click();
  await expect(page.locator("#permitatoStatsGrid")).toBeVisible();
  await expect(page.locator("#permitatoStatsEmpty")).toBeHidden();
  await expect(page.locator("#permitatoTopDomainsList")).toContainText("twitter.com");
});


test("streak value gets green highlight when positive", async ({ page }) => {
  await openPermitato(page, {
    permitatoStatusRoute: async (route) => {
      await route.fulfill({ status: 200, body: JSON.stringify(FAKE_STATUS) });
    },
    statsRoute: async (route) => {
      await route.fulfill({ status: 200, body: JSON.stringify(FAKE_STATS) });
    },
  });

  await page.locator("#permitatoStatsToggle").click();
  const streakEl = page.locator("#permitatoStreakValue");
  await expect(streakEl).toHaveClass(/streak-active/);
});
