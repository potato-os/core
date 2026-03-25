const { test, expect } = require("@playwright/test");
const {
  waitForStatusApplied,
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


test("renders compact Pi runtime info and toggles details view", async ({ page }) => {
  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        state: "READY",
        model_present: true,
        model: { filename: "Qwen3.5-2B-Q4_K_M.gguf" },
        llama_server: { healthy: true },
        backend: { mode: "llama", active: "llama", fallback_active: false },
        download: {
          bytes_total: 2497282336,
          bytes_downloaded: 2497282336,
          percent: 100,
          speed_bps: 0,
          eta_seconds: 0,
          error: null,
          active: false,
          auto_start_seconds: 300,
          auto_start_remaining_seconds: 0,
        },
        system: {
          available: true,
          updated_at_unix: 1771778048,
          cpu_percent: 21.4,
          cpu_cores_percent: [18, 24, 19, 22],
          cpu_clock_arm_hz: 2400023808,
          memory_total_bytes: 7900000000,
          memory_used_bytes: 4800000000,
          memory_percent: 61,
          swap_total_bytes: 2000000000,
          swap_used_bytes: 7000000,
          swap_percent: 0.35,
          temperature_c: 67.5,
          gpu_clock_core_hz: 910007424,
          gpu_clock_v3d_hz: 960012800,
          throttling: {
            raw: "0x80000",
            any_current: false,
            any_history: true,
            current_flags: [],
            history_flags: ["Soft temp limit occurred"],
          },
        },
      }),
    });
  });

  await page.goto("/");
  await waitForStatusApplied(page);
  await expect(page.locator("#runtimeDetails")).toBeVisible();
  await expect(page.locator("#runtimeViewToggle")).toHaveText("Hide details");
  await expect(page.locator("#runtimeDetailCpuClockValue")).toHaveText("2400 MHz");
  await expect(page.locator("#runtimeDetails")).toContainText("Soft temp limit occurred");

  await page.locator("#runtimeViewToggle").dispatchEvent("click");
  await expect(page.locator("#runtimeCompact")).toContainText("CPU 21% @ 2400 MHz");
  await expect(page.locator("#runtimeCompact")).toContainText("GPU 910/960 MHz");
  await expect(page.locator("#runtimeCompact")).toBeVisible();
  await expect(page.locator("#runtimeDetails")).toBeHidden();
  await expect(page.locator("#runtimeViewToggle")).toHaveText("Show details");
});

test("runtime details apply threshold colors for clock, memory, swap, and temperature", async ({ page }) => {
  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        state: "READY",
        model_present: true,
        model: { filename: "Qwen3.5-2B-Q4_K_M.gguf" },
        llama_server: { healthy: true },
        backend: { mode: "llama", active: "llama", fallback_active: false },
        download: {
          bytes_total: 2497282336,
          bytes_downloaded: 2497282336,
          percent: 100,
          speed_bps: 0,
          eta_seconds: 0,
          error: null,
          active: false,
          auto_start_seconds: 300,
          auto_start_remaining_seconds: 0,
        },
        system: {
          available: true,
          updated_at_unix: 1771778048,
          cpu_percent: 12,
          cpu_cores_percent: [10, 14, 11, 13],
          cpu_clock_arm_hz: 2300000000,
          memory_total_bytes: 8000000000,
          memory_used_bytes: 7500000000,
          memory_percent: 93.75,
          swap_total_bytes: 1000000000,
          swap_used_bytes: 790000000,
          swap_percent: 79,
          temperature_c: 88,
          gpu_clock_core_hz: 500000000,
          gpu_clock_v3d_hz: 500000000,
          throttling: {
            raw: "0x0",
            any_current: false,
            any_history: false,
            current_flags: [],
            history_flags: [],
          },
        },
      }),
    });
  });

  await page.goto("/");
  await waitForStatusApplied(page);
  await page.locator("#runtimeViewToggle").click();

  await expect(page.locator("#runtimeDetailCpuClockValue")).toHaveClass(/runtime-metric-critical/);
  // Memory severity uses relaxed fallback thresholds when PSI unavailable:
  // 93.75% maps to "high" (>=90%), not "critical" (>=95%).
  await expect(page.locator("#runtimeDetailMemoryValue")).toHaveClass(/runtime-metric-high/);
  await expect(page.locator("#runtimeDetailSwapValue")).toHaveClass(/runtime-metric-high/);
  await expect(page.locator("#runtimeDetailTempValue")).toHaveClass(/runtime-metric-high/);
});

test("fake backend ready state shows connected badge", async ({ page }) => {
  await waitUntilReady(page);
  await expect(page.locator("#statusLabel")).toHaveText("CONNECTED:Fake Backend");
  await expect(page.locator("#statusBadge")).toHaveClass(/online/);
});

test("llama booting with model present shows loading badge", async ({ page }) => {
  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        state: "BOOTING",
        model_present: true,
        model: { filename: "Qwen3.5-2B-Q4_K_M.gguf" },
        llama_server: { healthy: false, running: false, url: "http://127.0.0.1:8080" },
        backend: { mode: "llama", active: "llama", fallback_active: false },
        download: {
          bytes_total: 2497282336,
          bytes_downloaded: 2497282336,
          percent: 100,
          speed_bps: 0,
          eta_seconds: 0,
          error: null,
          active: false,
          auto_start_seconds: 300,
          auto_start_remaining_seconds: 0,
        },
      }),
    });
  });

  await page.goto("/");
  await waitForStatusApplied(page);
  await expect(page.locator("#statusLabel")).toHaveText("LOADING:llama.cpp:Qwen3.5-2B-Q4_K_M.gguf");
  await expect(page.locator("#statusBadge")).toHaveClass(/loading/);
});

test("llama error state shows failed badge", async ({ page }) => {
  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        state: "ERROR",
        model_present: true,
        model: { filename: "Qwen3.5-2B-Q4_K_M.gguf" },
        llama_server: { healthy: false, running: false, url: "http://127.0.0.1:8080" },
        backend: { mode: "llama", active: "llama", fallback_active: false },
        download: {
          bytes_total: 2497282336,
          bytes_downloaded: 2497282336,
          percent: 100,
          speed_bps: 0,
          eta_seconds: 0,
          error: "model_load_failed",
          active: false,
          auto_start_seconds: 300,
          auto_start_remaining_seconds: 0,
        },
      }),
    });
  });

  await page.goto("/");
  await waitForStatusApplied(page);
  await expect(page.locator("#statusLabel")).toHaveText("FAILED:llama.cpp:Qwen3.5-2B-Q4_K_M.gguf");
  await expect(page.locator("#statusBadge")).toHaveClass(/failed/);
});

test("mobile hamburger controls sidebar drawer and keeps composer actions aligned", async ({ page }) => {
  await page.setViewportSize({ width: 500, height: 844 });
  await waitUntilReady(page);

  await expect(page.locator("#sidebarBackdrop")).toBeHidden();
  await expect(page.locator("#sidebarToggle")).toBeVisible();

  const sidebarClosed = await page.locator(".sidebar").evaluate((el) => {
    const rect = el.getBoundingClientRect();
    return rect.right <= 0 || rect.left < 0;
  });
  expect(sidebarClosed).toBeTruthy();

  await page.locator("#sidebarToggle").click();
  await expect(page.locator("#sidebarBackdrop")).toBeVisible();
  await expect(page.locator("body")).toHaveClass(/sidebar-open/);

  await page.locator("#sidebarBackdrop").click({ position: { x: 450, y: 400 } });
  await expect(page.locator("#sidebarBackdrop")).toBeHidden();
  await expect(page.locator("body")).not.toHaveClass(/sidebar-open/);

  const composer = page.locator(".composer");
  await expect(composer).toBeVisible();
  await expect(page.locator("#attachImageBtn")).toBeVisible();
  await expect(page.locator("#sendBtn")).toBeVisible();

  const [attachBox, sendBox, composerBox] = await Promise.all([
    page.locator("#attachImageBtn").boundingBox(),
    page.locator("#sendBtn").boundingBox(),
    composer.boundingBox(),
  ]);

  expect(attachBox).not.toBeNull();
  expect(sendBox).not.toBeNull();
  expect(composerBox).not.toBeNull();

  const attach = attachBox;
  const send = sendBox;
  const comp = composerBox;
  expect(attach.y).toBeGreaterThanOrEqual(comp.y);
  expect(send.y).toBeGreaterThanOrEqual(comp.y);
  expect(send.y + send.height).toBeLessThanOrEqual(comp.y + comp.height + 1);
});


test("sidebar status avoids stale completed download text when downloads are idle", async ({ page }) => {
  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        state: "READY",
        model_present: true,
        model: { filename: "Qwen3.5-2B-Q4_K_M.gguf", active_model_id: "default" },
        models: [],
        upload: { active: false, model_id: null, bytes_total: 0, bytes_received: 0, percent: 0, error: null },
        download: {
          bytes_total: 12_400_000_000,
          bytes_downloaded: 12_400_000_000,
          percent: 100,
          speed_bps: 0,
          eta_seconds: 0,
          error: null,
          active: false,
          auto_start_seconds: 300,
          auto_start_remaining_seconds: 0,
          countdown_enabled: false,
          auto_download_paused: true,
          current_model_id: null,
        },
        llama_server: { healthy: true, running: true, url: "http://127.0.0.1:8080" },
        backend: { mode: "llama", active: "llama", fallback_active: false },
        system: { available: false, cpu_cores_percent: [] },
      }),
    });
  });

  await page.goto("/");
  await waitForStatusApplied(page);

  await expect(page.locator("#statusText")).toContainText("Auto-download paused");
  await expect(page.locator("#statusText")).not.toContainText("Download: 100%");
  await expect(page.locator("#statusResumeDownloadBtn")).toBeHidden();
});

test("model upload sends file with filename header", async ({ page }) => {
  let sawUpload = false;
  let uploadName = "";

  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        state: "READY",
        model_present: true,
        model: { filename: "Qwen3.5-2B-Q4_K_M.gguf", active_model_id: "default" },
        models: [],
        upload: { active: false, model_id: null, bytes_total: 0, bytes_received: 0, percent: 0, error: null },
        download: {
          bytes_total: 0,
          bytes_downloaded: 0,
          percent: 0,
          speed_bps: 0,
          eta_seconds: 0,
          error: null,
          active: false,
          auto_start_seconds: 300,
          auto_start_remaining_seconds: 0,
          countdown_enabled: true,
          current_model_id: null,
        },
        llama_server: { healthy: true, running: true, url: "http://127.0.0.1:8080" },
        backend: { mode: "llama", active: "llama", fallback_active: false },
        system: { available: false, cpu_cores_percent: [] },
      }),
    });
  });

  await page.route("**/internal/models/upload", async (route) => {
    sawUpload = true;
    uploadName = route.request().headers()["x-potato-filename"] || "";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        uploaded: true,
        model: {
          id: "tiny-upload",
          filename: "tiny.gguf",
          source_url: null,
          source_type: "upload",
          status: "ready",
          error: null,
        },
      }),
    });
  });

  await page.goto("/");
  await waitForStatusApplied(page);
  await openSettingsModal(page);

  await page.locator("#modelUploadInput").setInputFiles({
    name: "tiny.gguf",
    mimeType: "application/octet-stream",
    buffer: Buffer.from("tiny"),
  });
  await page.locator("#uploadModelBtn").click();
  await expect.poll(() => sawUpload).toBeTruthy();
  expect(uploadName).toBe("tiny.gguf");
});

test("power display shows PMIC labels for Pi 5 method", async ({ page }) => {
  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...makeStatusPayload(),
        system: {
          available: true,
          cpu_cores_percent: [10, 10, 10, 10],
          power_estimate: {
            available: true,
            method: "pmic_read_adc",
            total_watts: 4.5,
            raw_total_watts: 4.5,
            adjusted_total_watts: 6.37,
            adjusted_label: "Estimated total power",
            confidence: "experimental-default",
          },
        },
      }),
    });
  });
  await page.goto("/");
  await waitForStatusApplied(page);
  await expect(page.locator("#runtimeDetailPower")).toContainText("Power (estimated total): 6.370 W");
  await expect(page.locator("#runtimeDetailPowerRaw")).toContainText("Power (PMIC raw): 4.500 W");
});

test("power display shows CPU load raw label for Pi 4", async ({ page }) => {
  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...makeStatusPayload(),
        system: {
          available: true,
          cpu_cores_percent: [80, 80, 80, 80],
          power_estimate: {
            available: true,
            method: "cpu_load_estimate",
            total_watts: 5.4,
            raw_total_watts: 5.4,
            adjusted_total_watts: 5.4,
            adjusted_label: "CPU load estimate",
            confidence: "cpu-load-model",
          },
        },
      }),
    });
  });
  await page.goto("/");
  await waitForStatusApplied(page);
  await expect(page.locator("#runtimeDetailPower")).toContainText("Power (estimated total): 5.400 W");
  await expect(page.locator("#runtimeDetailPowerRaw")).toContainText("Power (CPU load raw): 5.400 W");
});

test("runtime dropdown hides incompatible runtimes on Pi 4 mock", async ({ page }) => {
  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(makeStatusPayload({
        llama_runtime: {
          current: { family: "llama_cpp", llama_cpp_commit: "def67890", profile: "universal", has_server_binary: true },
          available_runtimes: [
            { family: "ik_llama", commit: "abc12345", is_active: false, compatible: false },
            { family: "llama_cpp", commit: "def67890", is_active: true, compatible: true },
          ],
          switch: { active: false, target_family: null, error: null },
          memory_loading: { mode: "auto", label: "Automatic", no_mmap_env: "0" },
          large_model_override: { enabled: false },
        },
      })),
    });
  });
  await page.goto("/");
  await waitForStatusApplied(page);
  await openSettingsModal(page);
  await openAdvancedSettingsModal(page);
  const options = await page.locator("#llamaRuntimeFamilySelect option").allTextContents();
  expect(options).toHaveLength(1);
  expect(options[0]).toContain("llama cpp");
});

test("runtime dropdown shows all compatible runtimes on Pi 5 mock", async ({ page }) => {
  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(makeStatusPayload({
        llama_runtime: {
          current: { family: "ik_llama", llama_cpp_commit: "abc12345", profile: "pi5-opt", has_server_binary: true },
          available_runtimes: [
            { family: "ik_llama", commit: "abc12345", is_active: true, compatible: true },
            { family: "llama_cpp", commit: "def67890", is_active: false, compatible: true },
          ],
          switch: { active: false, target_family: null, error: null },
          memory_loading: { mode: "auto", label: "Automatic", no_mmap_env: "0" },
          large_model_override: { enabled: false },
        },
      })),
    });
  });
  await page.goto("/");
  await waitForStatusApplied(page);
  await openSettingsModal(page);
  await openAdvancedSettingsModal(page);
  const options = await page.locator("#llamaRuntimeFamilySelect option").allTextContents();
  expect(options).toHaveLength(2);
  expect(options[0]).toContain("ik llama");
  expect(options[1]).toContain("llama cpp");
});


// ── Memory pressure diagnostics tests ─────────────────────────────────

test("memory row shows RAM used as total minus free", async ({ page }) => {
  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(makeStatusPayload({
        system: {
          available: true,
          updated_at_unix: 1771778048,
          cpu_percent: 10,
          cpu_cores_percent: [10, 10, 10, 10],
          cpu_clock_arm_hz: 1500000000,
          memory_total_bytes: 8450000000,
          memory_used_bytes: 1940000000,
          memory_available_bytes: 6120000000,
          memory_free_bytes: 284000000,
          memory_percent: 23,
          swap_total_bytes: 2000000000,
          swap_used_bytes: 0,
          swap_percent: 0,
          temperature_c: 50,
          gpu_clock_core_hz: 500000000,
          gpu_clock_v3d_hz: 500000000,
          throttling: { raw: "0x0", any_current: false, any_history: false, current_flags: [], history_flags: [] },
        },
      })),
    });
  });
  await page.goto("/");
  await waitForStatusApplied(page);
  // RAM used = total - free = 8.45 GB - 284 MB ≈ 8.17 GB (97%)
  await expect(page.locator("#runtimeDetailMemoryValue")).toContainText("8.17 GB");
  await expect(page.locator("#runtimeDetailMemoryValue")).toContainText("97%");
  await expect(page.locator("#runtimeDetailMemoryValue")).toContainText("8.45 GB");
});

test("llama-server and model rows shown when llama_rss available", async ({ page }) => {
  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(makeStatusPayload({
        system: {
          available: true,
          updated_at_unix: 1771778048,
          cpu_percent: 10,
          cpu_cores_percent: [10, 10, 10, 10],
          cpu_clock_arm_hz: 1500000000,
          memory_total_bytes: 8450000000,
          memory_used_bytes: 1940000000,
          memory_free_bytes: 284000000,
          memory_percent: 23,
          llama_rss: {
            available: true,
            rss_bytes: 3760000000,
            rss_anon_bytes: 622000000,
            rss_file_bytes: 2400000000,
          },
          swap_total_bytes: 2000000000,
          swap_used_bytes: 0,
          swap_percent: 0,
          temperature_c: 50,
          gpu_clock_core_hz: 500000000,
          gpu_clock_v3d_hz: 500000000,
          throttling: { raw: "0x0", any_current: false, any_history: false, current_flags: [], history_flags: [] },
        },
      })),
    });
  });
  await page.goto("/");
  await waitForStatusApplied(page);
  await expect(page.locator("#runtimeDetailLlamaRssRow")).toBeVisible();
  await expect(page.locator("#runtimeDetailLlamaRssValue")).toContainText("3.76 GB");
  await expect(page.locator("#runtimeDetailLlamaRssValue")).toContainText("44%");
  await expect(page.locator("#runtimeDetailModelRamRow")).toBeVisible();
  await expect(page.locator("#runtimeDetailModelRamValue")).toContainText("2.40 GB");
});


test("pressure row appears when PSI data available", async ({ page }) => {
  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(makeStatusPayload({
        system: {
          available: true,
          updated_at_unix: 1771778048,
          cpu_percent: 10,
          cpu_cores_percent: [10, 10, 10, 10],
          cpu_clock_arm_hz: 1500000000,
          memory_total_bytes: 8000000000,
          memory_used_bytes: 6000000000,
          memory_available_bytes: 1800000000,
          memory_percent: 75,
          memory_pressure: {
            available: true,
            some_avg10: 5.2,
            some_avg60: 2.1,
            some_avg300: 0.8,
            full_avg10: 0.0,
            full_avg60: 0.0,
            full_avg300: 0.0,
          },
          swap_total_bytes: 2000000000,
          swap_used_bytes: 100000000,
          swap_percent: 5,
          temperature_c: 55,
          gpu_clock_core_hz: 500000000,
          gpu_clock_v3d_hz: 500000000,
          throttling: { raw: "0x0", any_current: false, any_history: false, current_flags: [], history_flags: [] },
        },
      })),
    });
  });
  await page.goto("/");
  await waitForStatusApplied(page);
  await expect(page.locator("#runtimeDetailPressureRow")).toBeVisible();
  await expect(page.locator("#runtimeDetailPressureValue")).toHaveText("5.2%");
});


test("pressure row shows dash when PSI unavailable", async ({ page }) => {
  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(makeStatusPayload({
        system: {
          available: true,
          updated_at_unix: 1771778048,
          cpu_percent: 10,
          cpu_cores_percent: [10, 10, 10, 10],
          cpu_clock_arm_hz: 1500000000,
          memory_total_bytes: 8000000000,
          memory_used_bytes: 2000000000,
          memory_percent: 25,
          memory_pressure: { available: false },
          swap_total_bytes: 2000000000,
          swap_used_bytes: 0,
          swap_percent: 0,
          temperature_c: 45,
          gpu_clock_core_hz: 500000000,
          gpu_clock_v3d_hz: 500000000,
          throttling: { raw: "0x0", any_current: false, any_history: false, current_flags: [], history_flags: [] },
        },
      })),
    });
  });
  await page.goto("/");
  await waitForStatusApplied(page);
  await expect(page.locator("#runtimeDetailPressureRow")).toBeVisible();
  await expect(page.locator("#runtimeDetailPressureValue")).toHaveText("--");
});


test("zram row shows compression ratio when zram_compression available", async ({ page }) => {
  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(makeStatusPayload({
        system: {
          available: true,
          updated_at_unix: 1771778048,
          cpu_percent: 10,
          cpu_cores_percent: [10, 10, 10, 10],
          cpu_clock_arm_hz: 1500000000,
          memory_total_bytes: 8000000000,
          memory_used_bytes: 6000000000,
          memory_percent: 75,
          swap_label: "zram",
          swap_total_bytes: 2147483648,
          swap_used_bytes: 119439360,
          swap_percent: 5.6,
          zram_compression: {
            available: true,
            orig_data_size: 119439360,
            compr_data_size: 44892922,
            mem_used_total: 51118080,
            mem_limit: 2147483648,
            compression_ratio: 2.7,
          },
          temperature_c: 55,
          gpu_clock_core_hz: 500000000,
          gpu_clock_v3d_hz: 500000000,
          throttling: { raw: "0x0", any_current: false, any_history: false, current_flags: [], history_flags: [] },
        },
      })),
    });
  });
  await page.goto("/");
  await waitForStatusApplied(page);
  await expect(page.locator("#runtimeDetailSwapValue")).toContainText("compressed");
  await expect(page.locator("#runtimeDetailSwapValue")).toContainText("2.7x");
  await expect(page.locator("#runtimeDetailSwapValue")).toContainText("2.15 GB");
});


test("memory severity uses PSI thresholds when pressure data available", async ({ page }) => {
  await page.route("**/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(makeStatusPayload({
        system: {
          available: true,
          updated_at_unix: 1771778048,
          cpu_percent: 80,
          cpu_cores_percent: [80, 80, 80, 80],
          cpu_clock_arm_hz: 2400000000,
          memory_total_bytes: 8000000000,
          memory_used_bytes: 7500000000,
          memory_available_bytes: 300000000,
          memory_percent: 93.75,
          memory_pressure: {
            available: true,
            some_avg10: 25.0,
            some_avg60: 18.0,
            some_avg300: 10.0,
            full_avg10: 15.0,
            full_avg60: 8.0,
            full_avg300: 3.0,
          },
          swap_total_bytes: 2000000000,
          swap_used_bytes: 1800000000,
          swap_percent: 90,
          temperature_c: 85,
          gpu_clock_core_hz: 500000000,
          gpu_clock_v3d_hz: 500000000,
          throttling: { raw: "0x0", any_current: false, any_history: false, current_flags: [], history_flags: [] },
        },
      })),
    });
  });
  await page.goto("/");
  await waitForStatusApplied(page);
  // full_avg10 > 10 → critical severity on memory row
  await expect(page.locator("#runtimeDetailMemoryValue")).toHaveClass(/runtime-metric-critical/);
  // Pressure row shows full_avg10 percentage (thrashing)
  await expect(page.locator("#runtimeDetailPressureRow")).toBeVisible();
  await expect(page.locator("#runtimeDetailPressureValue")).toHaveText("15.0%");
});
