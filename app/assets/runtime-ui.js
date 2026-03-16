"use strict";

import { appState, CPU_CLOCK_MAX_HZ_PI5, GPU_CLOCK_MAX_HZ_PI5 } from "./state.js";
import { formatBytes, formatPercent, formatClockMHz, percentFromRatio, applyRuntimeMetricSeverity } from "./utils.js";

    export function setRuntimeDetailsExpanded(expanded) {
      appState.runtimeDetailsExpanded = Boolean(expanded);
      const details = document.getElementById("runtimeDetails");
      const toggle = document.getElementById("runtimeViewToggle");
      const compact = document.getElementById("runtimeCompact");
      if (details) {
        details.hidden = !appState.runtimeDetailsExpanded;
      }
      if (compact) {
        compact.hidden = appState.runtimeDetailsExpanded;
      }
      if (toggle) {
        toggle.textContent = appState.runtimeDetailsExpanded ? "Hide details" : "Show details";
        toggle.setAttribute("aria-expanded", appState.runtimeDetailsExpanded ? "true" : "false");
      }
    }

    export function renderSystemRuntime(systemPayload) {
      const compact = document.getElementById("runtimeCompact");
      if (!compact) return;

      const available = systemPayload?.available === true;
      const cpuDetail = document.getElementById("runtimeDetailCpuValue");
      const coresDetail = document.getElementById("runtimeDetailCoresValue");
      const cpuClockDetail = document.getElementById("runtimeDetailCpuClockValue");
      const memoryDetail = document.getElementById("runtimeDetailMemoryValue");
      const swapLabelDetail = document.getElementById("runtimeDetailSwapLabel");
      const swapDetail = document.getElementById("runtimeDetailSwapValue");
      const storageDetail = document.getElementById("runtimeDetailStorageValue");
      const tempDetail = document.getElementById("runtimeDetailTempValue");
      const piModelDetail = document.getElementById("runtimeDetailPiModelValue");
      const osDetail = document.getElementById("runtimeDetailOsValue");
      const kernelDetail = document.getElementById("runtimeDetailKernelValue");
      const bootloaderDetail = document.getElementById("runtimeDetailBootloaderValue");
      const firmwareDetail = document.getElementById("runtimeDetailFirmwareValue");
      const powerDetail = document.getElementById("runtimeDetailPower");
      const powerRawDetail = document.getElementById("runtimeDetailPowerRaw");
      const gpuDetail = document.getElementById("runtimeDetailGpuValue");
      const throttleDetail = document.getElementById("runtimeDetailThrottleValue");
      const throttleHistoryDetail = document.getElementById("runtimeDetailThrottleHistoryValue");
      const updatedDetail = document.getElementById("runtimeDetailUpdatedValue");

      if (!available) {
        compact.textContent = "CPU -- | Cores -- | GPU -- | Swap -- | Throttle --";
        if (cpuDetail) cpuDetail.textContent = "--";
        if (coresDetail) coresDetail.textContent = "--";
        if (cpuClockDetail) cpuClockDetail.textContent = "--";
        if (memoryDetail) memoryDetail.textContent = "--";
        if (swapLabelDetail) swapLabelDetail.textContent = "zram";
        if (swapDetail) swapDetail.textContent = "--";
        if (storageDetail) storageDetail.textContent = "--";
        if (tempDetail) tempDetail.textContent = "--";
        if (piModelDetail) piModelDetail.textContent = "--";
        if (osDetail) osDetail.textContent = "--";
        if (kernelDetail) kernelDetail.textContent = "--";
        if (bootloaderDetail) bootloaderDetail.textContent = "--";
        if (firmwareDetail) firmwareDetail.textContent = "--";
        if (powerDetail) powerDetail.textContent = "Power (estimated total): --";
        if (powerRawDetail) powerRawDetail.textContent = "Power (PMIC raw): --";
        if (gpuDetail) gpuDetail.textContent = "--";
        if (throttleDetail) throttleDetail.textContent = "--";
        if (throttleHistoryDetail) throttleHistoryDetail.textContent = "--";
        if (updatedDetail) updatedDetail.textContent = "--";
        applyRuntimeMetricSeverity(cpuClockDetail, Number.NaN);
        applyRuntimeMetricSeverity(memoryDetail, Number.NaN);
        applyRuntimeMetricSeverity(swapDetail, Number.NaN);
        applyRuntimeMetricSeverity(storageDetail, Number.NaN);
        applyRuntimeMetricSeverity(tempDetail, Number.NaN);
        applyRuntimeMetricSeverity(gpuDetail, Number.NaN);
        return;
      }

      const cpuTotal = formatPercent(systemPayload?.cpu_percent, 0);
      const coreValues = Array.isArray(systemPayload?.cpu_cores_percent)
        ? systemPayload.cpu_cores_percent.map((value) => Number(value)).filter((value) => Number.isFinite(value))
        : [];
      const coresText = coreValues.length > 0
        ? `[${coreValues.map((value) => Math.round(value)).join(", ")}]`
        : "--";
      const cpuClock = formatClockMHz(systemPayload?.cpu_clock_arm_hz);
      const gpuCore = formatClockMHz(systemPayload?.gpu_clock_core_hz);
      const gpuV3d = formatClockMHz(systemPayload?.gpu_clock_v3d_hz);
      const gpuCompact = (gpuCore !== "--" || gpuV3d !== "--")
        ? `${gpuCore.replace(" MHz", "")}/${gpuV3d.replace(" MHz", "")} MHz`
        : "--";
      const swapLabel = String(systemPayload?.swap_label || "swap").trim() || "swap";
      const swapPercent = formatPercent(systemPayload?.swap_percent, 0);
      const storageFree = formatBytes(systemPayload?.storage_free_bytes);
      const storagePercent = formatPercent(systemPayload?.storage_percent, 0);
      const throttlingNow = systemPayload?.throttling?.any_current === true ? "Yes" : "No";
      compact.textContent = `CPU ${cpuTotal} @ ${cpuClock} | Cores ${coresText} | GPU ${gpuCompact} | ${swapLabel} ${swapPercent} | Free ${storageFree} | Throttle ${throttlingNow}`;

      if (cpuDetail) cpuDetail.textContent = cpuTotal;
      if (coresDetail) coresDetail.textContent = coresText;
      if (cpuClockDetail) cpuClockDetail.textContent = cpuClock;
      applyRuntimeMetricSeverity(cpuClockDetail, percentFromRatio(systemPayload?.cpu_clock_arm_hz, CPU_CLOCK_MAX_HZ_PI5));

      const memUsed = formatBytes(systemPayload?.memory_used_bytes);
      const memTotal = formatBytes(systemPayload?.memory_total_bytes);
      const memPercent = formatPercent(systemPayload?.memory_percent, 0);
      if (memoryDetail) memoryDetail.textContent = `${memUsed} / ${memTotal} (${memPercent})`;
      applyRuntimeMetricSeverity(memoryDetail, systemPayload?.memory_percent);

      const swapUsed = formatBytes(systemPayload?.swap_used_bytes);
      const swapTotal = formatBytes(systemPayload?.swap_total_bytes);
      if (swapLabelDetail) swapLabelDetail.textContent = swapLabel;
      if (swapDetail) swapDetail.textContent = `${swapUsed} / ${swapTotal} (${swapPercent})`;
      applyRuntimeMetricSeverity(swapDetail, systemPayload?.swap_percent);

      const storageUsed = formatBytes(systemPayload?.storage_used_bytes);
      const storageTotal = formatBytes(systemPayload?.storage_total_bytes);
      if (storageDetail) storageDetail.textContent = `${storageFree} (${storageUsed} / ${storageTotal} used, ${storagePercent})`;
      applyRuntimeMetricSeverity(storageDetail, systemPayload?.storage_percent);

      const tempRaw = systemPayload?.temperature_c;
      const tempValue = typeof tempRaw === "number" ? tempRaw : Number.NaN;
      if (tempDetail) {
        tempDetail.textContent = Number.isFinite(tempValue)
          ? `${tempValue.toFixed(1)}°C`
          : "--";
      }
      applyRuntimeMetricSeverity(tempDetail, tempValue);

      const piModelName = String(systemPayload?.pi_model_name || "").trim();
      if (piModelDetail) {
        piModelDetail.textContent = piModelName || "--";
      }

      const osPrettyName = String(systemPayload?.os_pretty_name || "").trim();
      if (osDetail) {
        osDetail.textContent = osPrettyName || "--";
      }

      const kernelRelease = String(systemPayload?.kernel_release || "").trim();
      const kernelVersion = String(systemPayload?.kernel_version || "").trim();
      if (kernelDetail) {
        if (kernelRelease && kernelVersion) {
          kernelDetail.textContent = `${kernelRelease} • ${kernelVersion}`;
        } else if (kernelRelease || kernelVersion) {
          kernelDetail.textContent = kernelRelease || kernelVersion;
        } else {
          kernelDetail.textContent = "--";
        }
      }

      const bootloader = systemPayload?.bootloader_version || {};
      const bootloaderDate = String(bootloader?.date || "").trim();
      const bootloaderVersion = String(bootloader?.version || "").trim();
      if (bootloaderDetail) {
        if (bootloaderDate && bootloaderVersion) {
          bootloaderDetail.textContent = `${bootloaderDate} • ${bootloaderVersion}`;
        } else if (bootloaderDate || bootloaderVersion) {
          bootloaderDetail.textContent = bootloaderDate || bootloaderVersion;
        } else {
          bootloaderDetail.textContent = "--";
        }
      }

      const firmware = systemPayload?.firmware_version || {};
      const firmwareDate = String(firmware?.date || "").trim();
      const firmwareVersion = String(firmware?.version || "").trim();
      if (firmwareDetail) {
        if (firmwareDate && firmwareVersion) {
          firmwareDetail.textContent = `${firmwareDate} • ${firmwareVersion}`;
        } else if (firmwareDate || firmwareVersion) {
          firmwareDetail.textContent = firmwareDate || firmwareVersion;
        } else {
          firmwareDetail.textContent = "--";
        }
      }

      const powerEstimate = systemPayload?.power_estimate || {};
      const rawPowerWatts = Number(powerEstimate?.raw_total_watts ?? powerEstimate?.total_watts);
      const adjustedPowerWatts = Number(powerEstimate?.adjusted_total_watts);
      if (powerDetail) {
        powerDetail.textContent = Number.isFinite(adjustedPowerWatts) && powerEstimate?.available === true
          ? `Power (estimated total): ${adjustedPowerWatts.toFixed(3)} W`
          : "Power (estimated total): --";
      }
      if (powerRawDetail) {
        powerRawDetail.textContent = Number.isFinite(rawPowerWatts) && powerEstimate?.available === true
          ? `Power (PMIC raw): ${rawPowerWatts.toFixed(3)} W`
          : "Power (PMIC raw): --";
      }

      if (gpuDetail) gpuDetail.textContent = `core ${gpuCore}, v3d ${gpuV3d}`;
      const gpuPeakHz = Math.max(
        Number(systemPayload?.gpu_clock_core_hz) || 0,
        Number(systemPayload?.gpu_clock_v3d_hz) || 0,
      );
      applyRuntimeMetricSeverity(gpuDetail, percentFromRatio(gpuPeakHz, GPU_CLOCK_MAX_HZ_PI5));

      const currentFlags = Array.isArray(systemPayload?.throttling?.current_flags)
        ? systemPayload.throttling.current_flags
        : [];
      const historyFlags = Array.isArray(systemPayload?.throttling?.history_flags)
        ? systemPayload.throttling.history_flags
        : [];
      if (throttleDetail) {
        throttleDetail.textContent = currentFlags.length > 0
          ? `Yes (${currentFlags.join(", ")})`
          : "No";
      }
      if (throttleHistoryDetail) {
        throttleHistoryDetail.textContent = historyFlags.length > 0
          ? historyFlags.join(", ")
          : "None";
      }

      const updatedTs = Number(systemPayload?.updated_at_unix);
      if (updatedDetail) {
        updatedDetail.textContent = Number.isFinite(updatedTs) && updatedTs > 0
          ? new Date(updatedTs * 1000).toLocaleTimeString()
          : "--";
      }
    }

    export function setModelUploadStatus(message) {
      const el = document.getElementById("modelUploadStatus");
      if (!el) return;
      el.textContent = String(message || "No upload in progress.");
    }

    export function setLlamaRuntimeSwitchStatus(message) {
      const el = document.getElementById("llamaRuntimeSwitchStatus");
      if (!el) return;
      el.textContent = String(message || "No runtime switch in progress.");
    }

    export function setLlamaMemoryLoadingStatus(message) {
      const el = document.getElementById("llamaMemoryLoadingStatus");
      if (!el) return;
      el.textContent = String(message || "Current memory loading: unknown");
    }

    export function setLargeModelOverrideStatus(message) {
      const el = document.getElementById("largeModelOverrideStatus");
      if (!el) return;
      el.textContent = String(message || "Compatibility override: default warnings");
    }

    export function setPowerCalibrationStatus(message) {
      const el = document.getElementById("powerCalibrationStatus");
      if (!el) return;
      el.textContent = String(message || "Power calibration: default correction");
    }

    export function setPowerCalibrationLiveStatus(message) {
      const el = document.getElementById("powerCalibrationLiveStatus");
      if (!el) return;
      el.textContent = String(message || "Current PMIC raw power: --");
    }

    export function setLlamaRuntimeSwitchButtonState(inFlight) {
      const btn = document.getElementById("switchLlamaRuntimeBtn");
      if (!btn) return;
      btn.disabled = Boolean(inFlight);
      btn.textContent = inFlight ? "Switching..." : "Switch llama runtime";
    }

    export function setLlamaMemoryLoadingButtonState(inFlight) {
      const btn = document.getElementById("applyLlamaMemoryLoadingBtn");
      if (!btn) return;
      btn.disabled = Boolean(inFlight);
      btn.textContent = inFlight ? "Applying..." : "Apply memory loading + restart";
    }

    export function setLargeModelOverrideButtonState(inFlight) {
      const btn = document.getElementById("applyLargeModelOverrideBtn");
      if (btn) {
        btn.disabled = Boolean(inFlight);
        btn.textContent = inFlight ? "Applying..." : "Apply compatibility override";
      }
      const quickBtn = document.getElementById("compatibilityOverrideBtn");
      if (quickBtn) {
        quickBtn.disabled = Boolean(inFlight);
        quickBtn.textContent = inFlight ? "Applying..." : "Try anyway";
      }
    }

    export function setPowerCalibrationButtonsState(inFlight) {
      const captureBtn = document.getElementById("capturePowerCalibrationSampleBtn");
      const fitBtn = document.getElementById("fitPowerCalibrationBtn");
      const resetBtn = document.getElementById("resetPowerCalibrationBtn");
      for (const btn of [captureBtn, fitBtn, resetBtn]) {
        if (!btn) continue;
        btn.disabled = Boolean(inFlight);
      }
      if (captureBtn) {
        captureBtn.textContent = inFlight ? "Capturing..." : "Capture calibration sample";
      }
      if (fitBtn) {
        fitBtn.textContent = inFlight ? "Computing..." : "Compute calibration";
      }
      if (resetBtn) {
        resetBtn.textContent = inFlight ? "Resetting..." : "Reset calibration";
      }
    }

    export function renderLlamaRuntimeStatus(statusPayload) {
      const runtimePayload = statusPayload?.llama_runtime || {};
      const currentEl = document.getElementById("llamaRuntimeCurrent");
      const selectEl = document.getElementById("llamaRuntimeFamilySelect");
      if (currentEl) {
        const current = runtimePayload?.current || {};
        const family = String(current?.family || current?.source_bundle_name || "").trim();
        const commit = String(current?.llama_cpp_commit || "").trim();
        const profile = String(current?.profile || "").trim();
        const serverPresent = current?.has_server_binary === true;
        const parts = [];
        if (family) parts.push(family);
        if (commit) parts.push(commit);
        if (profile) parts.push(`profile=${profile}`);
        if (!parts.length && serverPresent) {
          parts.push("custom/current install");
        }
        currentEl.textContent = `Current runtime: ${parts.join(" | ") || "unknown"}`;
      }

      if (selectEl) {
        const runtimes = Array.isArray(runtimePayload?.available_runtimes) ? runtimePayload.available_runtimes : [];
        const prevValue = String(selectEl.value || "");
        selectEl.replaceChildren();
        if (!runtimes.length) {
          const option = document.createElement("option");
          option.value = "";
          option.textContent = "No runtimes available";
          selectEl.appendChild(option);
          selectEl.disabled = true;
        } else {
          for (const rt of runtimes) {
            const option = document.createElement("option");
            option.value = String(rt?.family || "");
            const label = String(rt?.family || "unknown").replace("_", " ");
            const commit = String(rt?.commit || "").substring(0, 8);
            option.textContent = commit ? `${label} (${commit})` : label;
            if (rt?.is_active === true || option.value === prevValue) {
              option.selected = true;
            }
            selectEl.appendChild(option);
          }
          selectEl.disabled = false;
        }
      }

      const memoryLoadingSelect = document.getElementById("llamaMemoryLoadingMode");
      const memoryLoading = runtimePayload?.memory_loading || {};
      if (memoryLoadingSelect) {
        const mode = String(memoryLoading?.mode || "auto");
        const normalizedMode = ["auto", "full_ram", "mmap"].includes(mode) ? mode : "auto";
        memoryLoadingSelect.value = normalizedMode;
      }
      if (memoryLoading?.label) {
        const restartNote = memoryLoading?.no_mmap_env === "1"
          ? " (full RAM preload enabled)"
          : memoryLoading?.no_mmap_env === "0"
          ? " (mmap enabled)"
          : " (auto)";
        setLlamaMemoryLoadingStatus(`Current memory loading: ${memoryLoading.label}${restartNote}`);
      } else {
        setLlamaMemoryLoadingStatus("Current memory loading: unknown");
      }

      const largeModelOverrideToggle = document.getElementById("largeModelOverrideEnabled");
      const largeModelOverride = runtimePayload?.large_model_override || {};
      const overrideEnabled = largeModelOverride?.enabled === true || statusPayload?.compatibility?.override_enabled === true;
      if (largeModelOverrideToggle) {
        largeModelOverrideToggle.checked = overrideEnabled;
      }
      if (overrideEnabled) {
        setLargeModelOverrideStatus("Compatibility override: trying unsupported large models is enabled");
      } else {
        setLargeModelOverrideStatus("Compatibility override: default warnings");
      }

      const powerEstimate = statusPayload?.system?.power_estimate || {};
      const calibration = powerEstimate?.calibration || {};
      const rawPower = Number(powerEstimate?.raw_total_watts ?? powerEstimate?.total_watts);
      if (Number.isFinite(rawPower) && powerEstimate?.available === true) {
        setPowerCalibrationLiveStatus(`Current PMIC raw power: ${rawPower.toFixed(3)} W`);
      } else {
        setPowerCalibrationLiveStatus("Current PMIC raw power: --");
      }
      const mode = String(calibration?.mode || "default");
      const sampleCount = Number(calibration?.sample_count || 0);
      const coeffA = Number(calibration?.a);
      const coeffB = Number(calibration?.b);
      if (mode === "custom") {
        setPowerCalibrationStatus(
          `Power calibration: meter-calibrated (${sampleCount} samples, a=${Number.isFinite(coeffA) ? coeffA.toFixed(4) : "--"}, b=${Number.isFinite(coeffB) ? coeffB.toFixed(4) : "--"})`
        );
      } else {
        setPowerCalibrationStatus(
          `Power calibration: default correction (${sampleCount} stored samples${sampleCount >= 2 ? ", ready to fit" : ""})`
        );
      }

      const switchState = runtimePayload?.switch || {};
      if (switchState?.active) {
        const target = String(switchState?.target_family || "selected runtime");
        setLlamaRuntimeSwitchStatus(`Switching runtime... ${target}`);
      } else if (switchState?.error) {
        setLlamaRuntimeSwitchStatus(`Last runtime switch error: ${switchState.error}`);
      } else if (runtimePayload?.current?.family || runtimePayload?.current?.source_bundle_name) {
        setLlamaRuntimeSwitchStatus(`Active runtime: ${runtimePayload.current.family || runtimePayload.current.source_bundle_name}`);
      } else {
        setLlamaRuntimeSwitchStatus("No runtime switch in progress.");
      }

      setLlamaRuntimeSwitchButtonState(appState.llamaRuntimeSwitchInFlight || switchState?.active === true);
      setLlamaMemoryLoadingButtonState(appState.llamaMemoryLoadingApplyInFlight);
      setLargeModelOverrideButtonState(appState.largeModelOverrideApplyInFlight);
      setPowerCalibrationButtonsState(appState.powerCalibrationActionInFlight);
    }

    export function renderUploadState(statusPayload) {
      const upload = statusPayload?.upload || {};
      const cancelBtn = document.getElementById("cancelUploadBtn");
      if (upload?.active) {
        if (cancelBtn) cancelBtn.hidden = false;
        const percent = Number(upload.percent || 0);
        setModelUploadStatus(`Uploading model... ${percent}% (${formatBytes(upload.bytes_received)} / ${formatBytes(upload.bytes_total)})`);
        return;
      }
      if (cancelBtn) cancelBtn.hidden = true;
      if (upload?.error) {
        setModelUploadStatus(`Upload state: ${upload.error}`);
      } else {
        setModelUploadStatus("No upload in progress.");
      }
    }
