# Spike #268: ik_llama Gemma 4 benchmark

## Research question

Can the experimental ik_llama Gemma 4 branch deliver meaningfully better
inference performance than upstream llama.cpp on the Gemma 4 models Potato
already supports, without introducing unacceptable instability or operational
complexity?

## Build details

| | ik_llama (experimental) | llama.cpp (baseline) |
|---|---|---|
| Source | `slomin/ik_llama.cpp` @ `slomin/gemma4_wip` | `ggerganov/llama.cpp` |
| Commit | `9a0a4628` | `a1cfb64` |
| Profile | pi5-opt (GGML_NATIVE, LTO, IQK_FA_ALL_QUANTS) | universal (CPU_ALL_VARIANTS, BACKEND_DL) |
| Built on | Pi 5 (Cortex-A76, `-mcpu=native`) | Pi 5 (portable, NATIVE=OFF) |
| Build script | `bin/build_llama_runtime.sh --family ik_llama` | `bin/build_llama_runtime.sh --family llama_cpp` |

## Benchmark results

Parameters: pp512 (prompt processing, 512 tokens), tg128 (text generation,
128 tokens), 3 repetitions, flash attention enabled, f16 KV cache for ik_llama
(see KV cache section), default for llama.cpp. Potato service stopped during
benchmarks for clean measurements.

### Pi 5 8 GB (passive.local) — E2B

Model: `gemma-4-E2B-it-Q4_K_M.gguf` (3.09 GiB, 5.05B params)

| Runtime | Prompt (pp512 t/s) | Generation (tg128 t/s) |
|---------|-------------------|----------------------|
| **ik_llama** | **46.72 ± 0.16** | **7.30 ± 0.13** |
| llama_cpp | 31.98 ± 1.15 | 6.44 ± 0.09 |
| **Delta** | **+46%** | **+13%** |

### Pi 5 8 GB (passive.local) — E4B

Model: `gemma-4-E4B-it-Q4_0.gguf` (4.84 GiB, 8.19B params)

| Runtime | Prompt (pp512 t/s) | Generation (tg128 t/s) |
|---------|-------------------|----------------------|
| **ik_llama** | **19.18 ± 0.79** | **3.07 ± 0.02** |
| llama_cpp | 18.86 ± 0.15 | 2.79 ± 0.00 |
| **Delta** | **+2%** | **+10%** |

### Pi 5 16 GB (potato.local) — 26B-A4B

Model: `gemma-4-26B-A4B-it-UD-IQ4_NL.gguf` (13.21 GiB, 25.97B params)

| Runtime | Prompt (pp512 t/s) | Generation (tg128 t/s) |
|---------|-------------------|----------------------|
| **ik_llama** | **29.64 ± 0.15** | **3.10 ± 0.02** |
| llama_cpp | 8.57 ± 0.30 | 2.70 ± 0.09 |
| **Delta** | **+246%** | **+15%** |

### Pi 4 8 GB (pi4.local) — llama_cpp only

ik_llama cannot run on Pi 4 (IQK kernels require ARMv8.2-A dot product
instructions; Pi 4 Cortex-A72 is ARMv8.0). Included for cross-device
comparison.

| Model | Prompt (pp512 t/s) | Generation (tg128 t/s) |
|-------|-------------------|----------------------|
| E2B Q4_K_M | 4.36 ± 0.07 | 1.75 ± 0.01 |
| E4B Q4_0 | 2.18 ± 0.07 | 0.98 ± 0.01 |

## Correctness verification

Quick chat completion test on potato.local (26B-A4B via ik_llama):

```
Prompt:  "What is 2+2? One sentence."
Reply:   "Two plus two equals four."
Latency: pp 1562 ms (23 tokens), tg 1903 ms (7 tokens)
```

Output is coherent and correct. No obvious quality regressions observed in
casual testing.

## Blocker: quantized KV cache

### The problem

Potato uses `--cache-type-k q8_0 --cache-type-v q8_0` to reduce KV cache
memory by ~2x. On ik_llama with Gemma 4, **every quantized cache type crashes**
with flash attention enabled, and ik_llama **refuses quantized V cache with
flash attention disabled**.

Tested combinations — all failed:

| K cache | V cache | Flash attn | Result |
|---------|---------|-----------|--------|
| q8_0 | q8_0 | on | crash: `GGML_ASSERT(S > 0)` in `FlashQKV::normalize_and_store_1row` |
| q4_0 | q4_0 | on | same crash |
| q8_0 | f16 | on | same crash |
| q8_0 | q8_0 | off | refused: `Quantized V cache cannot be used without flash attention` |
| **f16** | **f16** | **on** | **works** |

### Root cause

Gemma 4 26B-A4B has two types of attention layers with different head
dimensions:

- **24 SWA (local) layers**: `head_dim=256`, 8 KV heads, 16 Q heads
- **6 global layers**: `head_dim=512`, 2 KV heads, 16 Q heads (shared K=V)

IQK flash-attention kernels are template-specialized per head dimension. The
supported (Dk, Dv) pairs in ik_llama include 256×256 (fixed in PR #1452) but
**there is no `iqk_fa_512_512` kernel**. When the 6 global layers hit the FA
dispatcher, it returns `false` and falls back to ggml FA — but this fallback
is broken for quantized cache (acknowledged in PR #1562: "the calculation will
fall back to the ggml flash attention implementation. But that seems to have
been broken somewhere along the way.").

With f16 KV cache, the fallback code path works, which is why f16 is the
only option that doesn't crash.

### Impact

The f16 KV cache workaround costs ~2× KV cache memory:

- 26B-A4B with q8_0 cache (4096 ctx): ~935 MiB per K/V = 1870 MiB total
- 26B-A4B with f16 cache (4096 ctx): ~1870 MiB per K/V = 3740 MiB total

On the 16 GB Pi, this makes the 26B model barely fit. RSS is ~15.9 GB,
requiring mmap mode and swap, which kills generation speed.

### Path to fixing

Three options, in order of preference:

1. **Add `iqk_fa_512_512` kernel** to the fork — follow the pattern from
   `iqk_fa_576_512.cpp`. This enables quantized cache for the global layers
   and is the proper fix.

2. **Fix the ggml FA fallback** so it correctly handles quantized KV cache
   when IQK FA returns false for unsupported dimensions.

3. **Per-layer cache split** — q8_0 for the 24 SWA layers (D=256, kernel
   exists and is fixed) + f16 for the 6 global layers (D=512, no kernel).
   This saves ~75% of the cache memory difference vs full f16.

### Relevant upstream references

- [ik_llama #1572](https://github.com/ikawrakow/ik_llama.cpp/issues/1572) — Gemma 4 feature request (open)
- [ik_llama #1452](https://github.com/ikawrakow/ik_llama.cpp/pull/1452) — D=256 quantized KV fix (merged)
- [ik_llama #1562](https://github.com/ikawrakow/ik_llama.cpp/pull/1562) — CPU FA type checking, fallback broken (merged)
- [ik_llama #1205](https://github.com/ikawrakow/ik_llama.cpp/issues/1205) — Gemma 3 q8_0 cache fix (closed)
- [llama.cpp #21309](https://github.com/ggml-org/llama.cpp/pull/21309) — upstream Gemma 4 support (merged)
- [llama.cpp #21277](https://github.com/ggml-org/llama.cpp/pull/21277) — SWA KV cache quantization (merged then reverted)

## Other findings

- **Vision projector unsupported**: `clip_init: unknown projector type: gemma4v`.
  The fork adds text support only. Vision must be disabled for Gemma 4 on
  ik_llama.

- **Pi 4 incompatible**: IQK code (`iqk_common.h`) does not compile on
  Cortex-A72. Pi 4 stays on llama_cpp for all models.

- **`--jinja` required**: Gemma 4's chat template needs `--jinja`. Works fine
  on ik_llama.

- **Upstream Gemma 4 is also unstable**: llama.cpp has open issues for
  segfaults on longer context (#21336), infinite output (#21365), broken
  KV rotation (#21394). Both runtimes are early.

## Recommendation

**Pursue ik_llama Gemma 4 support further**, with the q8_0 KV cache fix as
the gating prerequisite before shipping.

### Why

The speedups are real and significant where they matter most:

- **E2B on Pi 5 8 GB**: +46% prompt, +13% generation. Best model for 8 GB
  devices. Fits comfortably with room for KV cache.
- **26B-A4B on Pi 5 16 GB**: +246% prompt, +15% generation. Transforms the
  model from barely usable (8.57 t/s prompt) to responsive (29.64 t/s).
- **E4B on Pi 5 8 GB**: modest gains (+2% prompt, +10% generation). Q4_0
  quant doesn't benefit as much from IQK kernels as Q4_K_M.

### Before shipping as default

1. **Fix q8_0 KV cache** — add the missing `iqk_fa_512_512` kernel or fix the
   ggml FA fallback. This is the hard blocker. Without it, the 26B model
   can't fit on 16 GB and E2B/E4B waste memory on f16 cache.
2. **Vision projector** — add `gemma4v` clip type support, or keep vision
   disabled for ik_llama Gemma 4 as a known limitation.
3. **Guard the auto-switch** — `recommended_runtime_for_model` should check
   device compatibility before switching (Pi 4 compat check currently runs
   after the model-level switch).
