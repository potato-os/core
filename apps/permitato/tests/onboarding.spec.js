const { test, expect } = require("@playwright/test");
const { makeStatusPayload } = require("../../../tests/ui/helpers");

/**
 * Navigate to the Permitato app tab and wait for it to be loaded.
 * Mocks the platform /status and /internal/apps so the shell boots.
 */
async function openPermitato(page, { statusRoute, permitatoStatusRoute, clientsRoute } = {}) {
  // Mock platform status so the shell boots
  await page.route("**/status", async (route) => {
    if (route.request().url().includes("/app/permitato/")) return route.fallback();
    await route.fulfill({ status: 200, body: JSON.stringify(makeStatusPayload()) });
  });

  // Mock the Permitato-specific status endpoint
  if (permitatoStatusRoute) {
    await page.route("**/app/permitato/api/status", permitatoStatusRoute);
  }

  // Mock the clients discovery endpoint
  if (clientsRoute) {
    await page.route("**/app/permitato/api/clients", clientsRoute);
  }

  await page.goto("/");
  // Wait for shell to discover apps and render navigation
  await page.waitForSelector('button[data-app="permitato"]', { timeout: 5000 });
  // Click the Permitato tab
  await page.locator('button[data-app="permitato"]').click();
  // Wait for Permitato to load its HTML — either the status bar or the onboarding overlay becomes visible
  await page.waitForFunction(() => {
    const badge = document.getElementById("permitatoModeValue");
    const onb = document.getElementById("permitatoOnboarding");
    // Wait until either the mode badge is updated (poll completed with client)
    // or the onboarding overlay is shown (poll completed without client)
    return (badge && badge.textContent !== "--" && badge.textContent !== "") || (onb && !onb.hidden);
  }, { timeout: 10000 });
}

const FAKE_PERMITATO_STATUS_NO_CLIENT = {
  mode: "normal",
  mode_display: "Normal",
  mode_description: "No extra restrictions",
  active_exceptions: 0,
  exceptions: [],
  pihole_available: true,
  degraded_since: null,
  client_id: "",
  client_valid: null,
};

const FAKE_PERMITATO_STATUS_VALID_CLIENT = {
  ...FAKE_PERMITATO_STATUS_NO_CLIENT,
  client_id: "192.168.1.106",
  client_valid: true,
};

const FAKE_PERMITATO_STATUS_INVALID_CLIENT = {
  ...FAKE_PERMITATO_STATUS_NO_CLIENT,
  client_id: "192.168.1.106",
  client_valid: false,
};

const FAKE_CLIENTS = {
  clients: [
    { client: "192.168.1.106", name: "", id: 1, selected: false, is_requester: true },
    { client: "192.168.1.200", name: "iPhone", id: 2, selected: false, is_requester: false },
  ],
  pihole_available: true,
};


test("shows onboarding overlay when client_id is empty", async ({ page }) => {
  await openPermitato(page, {
    permitatoStatusRoute: async (route) => {
      await route.fulfill({ status: 200, body: JSON.stringify(FAKE_PERMITATO_STATUS_NO_CLIENT) });
    },
    clientsRoute: async (route) => {
      await route.fulfill({ status: 200, body: JSON.stringify(FAKE_CLIENTS) });
    },
  });

  await expect(page.locator("#permitatoOnboarding")).toBeVisible();
  await expect(page.locator("#permitatoClientList li")).toHaveCount(2);
  // Your device should be highlighted and sorted first
  await expect(page.locator("#permitatoClientList li").first()).toHaveClass(/this-device/);
  await expect(page.locator(".client-label").first()).toHaveText("Your device");
  await expect(page.locator(".client-select-btn").first()).toHaveText("Select this device");
});


test("selecting a client hides onboarding and shows normal UI", async ({ page }) => {
  let statusReturnsClient = false;

  await openPermitato(page, {
    permitatoStatusRoute: async (route) => {
      const data = statusReturnsClient
        ? FAKE_PERMITATO_STATUS_VALID_CLIENT
        : FAKE_PERMITATO_STATUS_NO_CLIENT;
      await route.fulfill({ status: 200, body: JSON.stringify(data) });
    },
    clientsRoute: async (route) => {
      await route.fulfill({ status: 200, body: JSON.stringify(FAKE_CLIENTS) });
    },
  });

  // Mock the POST /client endpoint
  await page.route("**/app/permitato/api/client", async (route) => {
    if (route.request().method() === "POST") {
      statusReturnsClient = true;
      await route.fulfill({
        status: 200,
        body: JSON.stringify({
          client_id: "192.168.1.106",
          mode: "normal",
          client_valid: true,
          warning: null,
        }),
      });
    } else {
      await route.fallback();
    }
  });

  // Onboarding should be visible
  await expect(page.locator("#permitatoOnboarding")).toBeVisible();

  // Click the Select button on the first client
  await page.locator(".client-select-btn").first().click();

  // Onboarding should hide, status bar should appear
  await expect(page.locator("#permitatoOnboarding")).toBeHidden({ timeout: 10000 });
  await expect(page.locator("#permitatoModeValue")).toBeVisible();
});


test("shows recovery banner when client_valid is false", async ({ page }) => {
  await openPermitato(page, {
    permitatoStatusRoute: async (route) => {
      await route.fulfill({ status: 200, body: JSON.stringify(FAKE_PERMITATO_STATUS_INVALID_CLIENT) });
    },
  });

  await expect(page.locator("#permitatoRecoveryBanner")).toBeVisible();
  await expect(page.locator("#permitatoRecoveryText")).toContainText("192.168.1.106");
});


test("reconfigure button opens onboarding overlay", async ({ page }) => {
  await openPermitato(page, {
    permitatoStatusRoute: async (route) => {
      await route.fulfill({ status: 200, body: JSON.stringify(FAKE_PERMITATO_STATUS_INVALID_CLIENT) });
    },
    clientsRoute: async (route) => {
      await route.fulfill({ status: 200, body: JSON.stringify(FAKE_CLIENTS) });
    },
  });

  await expect(page.locator("#permitatoRecoveryBanner")).toBeVisible();
  await page.locator("#permitatoReconfigureBtn").click();
  await expect(page.locator("#permitatoOnboarding")).toBeVisible();

  // Wait for a status poll to complete — overlay must survive it
  await page.waitForResponse((resp) => resp.url().includes("/app/permitato/api/status") && resp.status() === 200);
  await expect(page.locator("#permitatoOnboarding")).toBeVisible();
});


test("reconfigure overlay picks up newly discovered devices", async ({ page }) => {
  let clientFetchCount = 0;
  const UPDATED_CLIENTS = {
    clients: [
      ...FAKE_CLIENTS.clients,
      { client: "192.168.1.50", name: "New device", id: 3, selected: false, is_requester: false },
    ],
    pihole_available: true,
  };

  await page.clock.install();

  await openPermitato(page, {
    permitatoStatusRoute: async (route) => {
      await route.fulfill({ status: 200, body: JSON.stringify(FAKE_PERMITATO_STATUS_INVALID_CLIENT) });
    },
    clientsRoute: async (route) => {
      clientFetchCount++;
      const data = clientFetchCount >= 2 ? UPDATED_CLIENTS : FAKE_CLIENTS;
      await route.fulfill({ status: 200, body: JSON.stringify(data) });
    },
  });

  await expect(page.locator("#permitatoRecoveryBanner")).toBeVisible();
  await page.locator("#permitatoReconfigureBtn").click();
  await expect(page.locator("#permitatoOnboarding")).toBeVisible();
  await expect(page.locator("#permitatoClientList li")).toHaveCount(2);

  // Advance past the 30-second client refresh throttle and wait for refresh
  const clientRefresh = page.waitForResponse(
    (resp) => resp.url().includes("/app/permitato/api/clients") && resp.status() === 200
  );
  await page.clock.fastForward(35000);
  await clientRefresh;

  await expect(page.locator("#permitatoClientList li")).toHaveCount(3);
  await expect(page.locator("#permitatoClientList")).toContainText("New device");
});


test("shows message when Pi-hole unavailable during onboarding", async ({ page }) => {
  await openPermitato(page, {
    permitatoStatusRoute: async (route) => {
      await route.fulfill({
        status: 200,
        body: JSON.stringify({
          ...FAKE_PERMITATO_STATUS_NO_CLIENT,
          pihole_available: false,
        }),
      });
    },
    clientsRoute: async (route) => {
      await route.fulfill({
        status: 200,
        body: JSON.stringify({ clients: [], pihole_available: false }),
      });
    },
  });

  await expect(page.locator("#permitatoOnboarding")).toBeVisible();
  await expect(page.locator("#permitatoOnboardingStatus")).toContainText("Pi-hole");
});
