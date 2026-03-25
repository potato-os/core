# Context Window Characterization: Overnight Sweep Results

Findings from ticket #168 overnight sweep, 2026-03-24/25.

## Test Configuration

| Parameter | Value |
|-----------|-------|
| Model | `Qwen3-30B-A3B-Instruct-2507-Q3_K_S-2.66bpw.gguf` (ByteShape, 9.5 GB) |
| Runtime | ik_llama.cpp (Pi5-opt build) |
| KV cache | q8_0 / q8_0 (K and V) |
| Flash attention | on (ik_llama default) |
| Fused MoE | on (ik_llama default) |
| Cache RAM | 1024 MiB (prompt cache in host RAM) |
| Parallel slots | 1 |
| Threads | 4 (all Pi 5 cores) |
| mmap | on (default — model weights memory-mapped from storage) |
| Conversation | Multi-turn storytelling, ~100 tok user / ~700 tok assistant per turn |
| Sampling | temperature=0, seed=42, max_tokens=1024, cache_prompt=true |

### Hardware

| | Pi 5 16GB | Pi 5 8GB + SSD |
|---|-----------|---------------|
| Hostname | `potato.local` | `ssd.local` |
| RAM | 16,218 MiB | 8,062 MiB |
| Storage | 128 GB SD card | 128 GB NVMe SSD |
| Swap | 2 GB zram (zstd, 4 streams) | 2 GB zram (zstd, 4 streams) |
| Revision | Rev 1.1 | Rev 1.0 |

## KV Cache Sizes (from server startup logs)

| Context | K (q8_0) | V (q8_0) | Total | Notes |
|---------|----------|----------|-------|-------|
| 16,384 | 408 MiB | 408 MiB | 816 MiB | Default Potato OS config |
| 24,576 | 612 MiB | 612 MiB | 1,224 MiB | Measured overnight |
| 32,768 | 816 MiB | 816 MiB | 1,632 MiB | 1 turn captured before termination |
| 49,152 | — | — | ~2,448 MiB | Extrapolated (linear) |
| 65,536 | — | — | ~3,264 MiB | Extrapolated (linear) |

KV cache scales linearly: **~50 MiB per 1K tokens** at q8_0/q8_0.

## Runs Completed

| Hardware | Context | Clean turns | Max fill | Context shifts | Status |
|----------|---------|-------------|----------|---------------|--------|
| 16GB | 16K | 15 | 65% | 0 | Interrupted (Pi went offline) |
| 16GB | 16K | 4* | 13% | 6 total | Completed 60 turns (shifted in circles) |
| 16GB | 24K | 35 | 99% | 2 total | Completed 60 turns |
| 16GB | 32K | 1 | 2% | 0 | Terminated (sweep killed) |
| 8GB+SSD | 16K | 23 | 97% | 5 total | Completed 60 turns |
| 8GB+SSD | 24K | 35 | 99% | 1 total | Terminated at turn 49 (~90% fill) |

*The overnight 16K run on 16GB has only 4 clean turns due to a stale JSONL
from the earlier interrupted run being continued. The earlier 16K run (15 turns, 65%)
is the primary 16K dataset for the 16GB Pi.

32K, 48K, and 64K were not reached in the overnight sweep due to time spent in
post-shift cycles at 16K and 24K.

## Generation Speed Degradation

Speed drops linearly with context fill. The table below uses pre-shift data only.

### Pi 5 16GB — 24K context (35 clean turns)

| Context fill | Gen (t/s) | PP (t/s) | TTFT | Turn time |
|-------------|-----------|----------|------|-----------|
| 3% | 8.5 | 29 | 3.4s | 78s |
| 11% | 7.1 | 18 | 2.0s | 93s |
| 20% | 6.0 | 14 | 2.5s | 108s |
| 32% | 5.1 | 11 | 4.7s | 131s |
| 43% | 4.4 | 8 | 4.4s | 151s |
| 52% | 3.9 | 8 | 4.3s | 172s |
| 63% | 3.5 | 6 | 6.8s | 199s |
| 71% | 3.3 | 6 | 6.4s | 212s |
| 80% | 3.0 | 5 | 6.0s | 240s |
| 91% | 2.8 | 5 | 9.1s | 245s |
| 99% | 2.6 | 4 | 8.4s | 256s |

At 99% fill, generation speed is **69% slower** than at empty context (8.5 → 2.6 t/s).
Turn time tripled from 78s to 256s.

### Pi 5 8GB + NVMe SSD — 24K context (35 clean turns)

| Context fill | Gen (t/s) | PP (t/s) | TTFT | Turn time |
|-------------|-----------|----------|------|-----------|
| 3% | 7.1 | 6 | 18.0s | 107s |
| 11% | 6.3 | 16 | 2.2s | 107s |
| 20% | 5.4 | 12 | 2.7s | 123s |
| 32% | 4.5 | 9 | 5.5s | 147s |
| 43% | 3.9 | 7 | 5.3s | 162s |
| 52% | 3.6 | 7 | 5.0s | 179s |
| 63% | 3.2 | 5 | 8.5s | 204s |
| 71% | 3.0 | 5 | 7.4s | 230s |
| 80% | 2.8 | 5 | 7.3s | 239s |
| 91% | 2.5 | 4 | 11.5s | 276s |
| 99% | 2.4 | 4 | 10.5s | 281s |

Note the 18.0s TTFT at 3% fill — this is the cold-start cost of mmap on the 8GB Pi.
The first request faults in model weight pages from SSD. Subsequent turns drop to
2-3s TTFT as pages settle into the page cache.

## 16GB vs 8GB Comparison

### Head-to-head at 24K context

| Metric | 16GB | 8GB+SSD | Delta |
|--------|------|---------|-------|
| Gen speed (empty) | 8.5 t/s | 7.1 t/s | -16% |
| Gen speed (50%) | 3.9 t/s | 3.6 t/s | -8% |
| Gen speed (99%) | 2.6 t/s | 2.4 t/s | -8% |
| TTFT (cold start) | 3.4s | 18.0s | +430% |
| TTFT (steady state, 50%) | 4.3s | 5.0s | +16% |
| RSS | 11,030–11,079 MB | 6,729–7,193 MB | — |
| Available memory | 13,186–13,298 MB | 6,194–6,846 MB | — |
| zram usage | 0 MB | 620–700 MB | — |
| Max temperature | 64°C | 76°C | +12°C |
| Turn time (99%) | 256s | 281s | +10% |

Key observations:

1. **Gen speed gap narrows with fill.** At empty context, the 8GB Pi is 16% slower
   (mmap page faults for expert weights). At full context, the gap shrinks to 8%
   because attention computation dominates over I/O.

2. **Cold start TTFT is 5x worse on 8GB.** The first request forces the entire model
   to be paged in from NVMe SSD. After warmup, TTFT converges to within 16%.

3. **zram is active on 8GB throughout.** 620-700 MB of data compressed in zram,
   meaning the kernel is actively managing memory pressure. The zstd compression on
   4 ARM cores adds ~12°C thermal overhead.

4. **Both Pis successfully run 24K context.** The 8GB Pi is slower but functional.
   The NVMe SSD makes large-model mmap viable.

## Prompt Caching

Prompt caching (cache_prompt=true) works correctly on both hardware profiles.

| Metric | Expected (no cache) | Observed |
|--------|-------------------|----------|
| prompt_n per turn | Full accumulated context (grows to 24K) | 33–51 tokens (constant) |
| TTFT growth | Linear with context | Sub-linear (only new tokens processed) |

The `prompt_n` field stays at 33–51 tokens throughout the conversation, confirming
that llama-server reuses the KV cache prefix from the previous turn. Only the new
assistant response + user message tokens are processed each turn.

Without prompt caching, TTFT at 99% fill would be ~6000ms (24K tokens at 4 t/s PP).
With caching, TTFT is ~8-10s (only ~40 new tokens at 4 t/s PP plus overhead). This
is a **~600x reduction** in redundant prompt processing.

## Context Shift Behavior

Context shift triggers automatically when the KV cache reaches ~99% capacity.

### Mechanics observed

At 24K context on the 16GB Pi:
- T35 (n_past=24264/24576): context 99% full, gen=2.5 t/s
- T36 (n_past=12534/24576): shift — first ~12K tokens evicted, gen recovered to 3.0 t/s
- T37 (n_past=13348/24576): post-shift, gen=3.8 t/s (speed recovered)

The shift evicts approximately half the non-kept tokens (`n_discard ≈ n_ctx/2`),
adjusts RoPE position embeddings on the surviving KV entries, and continues generation.

### Post-shift speed recovery

| State | Gen (t/s) | PP (t/s) |
|-------|-----------|----------|
| Pre-shift (99% full) | 2.5 | 4 |
| Post-shift (51% full) | 3.0–3.8 | 4–8 |

Speed recovers because attention computation scales with context length. Halving the
context approximately restores speed to the 50% fill level.

### Cycle behavior

At 16K context, the shift cycle is rapid (fills in ~23 turns, shifts, refills).
The 16K overnight run went through 5-6 shift cycles in 60 turns, spending most time
in redundant post-shift territory. The 24K run shifted twice in 60 turns.

For characterization purposes, **data after the first context shift is not useful**
for measuring max-context performance — it only measures ring-buffer cycling behavior.

## Memory Profiles

### Pi 5 16GB — no memory pressure

| Metric | 16K | 24K | 32K (1 turn) |
|--------|-----|-----|-----|
| Model RSS | 10,621 MB | 11,030 MB | 11,438 MB |
| KV cache | 816 MiB | 1,224 MiB | 1,632 MiB |
| Available | 14,682 MB | 13,298 MB | 12,837 MB |
| zram | 0 MB | 0 MB | 0 MB |
| Headroom | ~4 GB | ~2 GB | ~1.2 GB |

RSS grows with context size because the KV cache is allocated as real (non-evictable)
memory. At 32K, headroom is ~1.2 GB. Extrapolating: 48K would leave ~400 MB headroom,
64K would likely trigger zram or OOM.

### Pi 5 8GB + NVMe SSD — active memory pressure

| Metric | 16K | 24K |
|--------|-----|-----|
| Model RSS | 6,469–7,199 MB | 6,729–7,193 MB |
| Available | 6,650–7,241 MB | 6,194–6,846 MB |
| zram | 0–620 MB | 560–700 MB |
| Temperature | 68–76°C | 71–76°C |

RSS is capped by physical RAM. The model is 9.5 GB on disk but RSS peaks at ~7.2 GB
because the kernel can only keep ~7 GB of mmap'd pages resident. The remaining ~2.3 GB
of model weights are faulted from NVMe SSD on demand.

zram activates early and stabilizes at 620-700 MB, indicating the kernel is compressing
~1.2-1.4 GB of data (at ~2x zstd ratio) to fit in the 2 GB zram device.

## Measured KV Cache Budget

Based on observed RSS and available memory, the maximum context size before memory
pressure can be estimated:

### Pi 5 16GB

| Context | KV cache | Model RSS | Total | Fits? |
|---------|----------|-----------|-------|-------|
| 16K | 816 MiB | 10,621 MB | ~11.4 GB | Easily (4 GB headroom) |
| 24K | 1,224 MiB | 11,030 MB | ~12.2 GB | Yes (2 GB headroom) |
| 32K | 1,632 MiB | 11,438 MB | ~13.0 GB | Yes (~1.2 GB headroom) |
| 48K | ~2,448 MiB | ~12,050 MB | ~14.4 GB | Tight (~200 MB headroom) |
| 64K | ~3,264 MiB | ~12,660 MB | ~15.9 GB | Marginal (may trigger zram) |

### Pi 5 8GB + NVMe SSD

| Context | KV cache | Remaining for model cache | Model paged from SSD |
|---------|----------|--------------------------|---------------------|
| 16K | 816 MiB | ~5.5 GB | ~4 GB (42%) |
| 24K | 1,224 MiB | ~5.1 GB | ~4.4 GB (46%) |
| 32K | 1,632 MiB | ~4.7 GB | ~4.8 GB (51%) |
| 48K | ~2,448 MiB | ~3.9 GB | ~5.6 GB (59%) |
| 64K | ~3,264 MiB | ~3.1 GB | ~6.4 GB (67%) |

At 64K on the 8GB Pi, two-thirds of the model would be constantly paged from SSD.
Combined with zram overhead, generation speed would likely drop below 1 t/s.

## Recommendations

1. **Default context (16K) is well within budget** on both hardware profiles.
   The 16GB Pi has 4 GB headroom; the 8GB Pi runs with moderate memory pressure but
   stable performance (7.4 t/s starting, 3.1 t/s at 97% fill).

2. **24K is achievable on both Pis** but speed degrades to 2.4-2.6 t/s at full
   context. Whether this is acceptable depends on the use case.

3. **32K is likely the practical ceiling for the 16GB Pi** without zram activation.
   48K and 64K need testing — the overnight sweep was terminated before reaching them.

4. **Asymmetric KV quantization (q8_0 K / q4_0 V)** would save ~50% on the V cache,
   freeing 408 MiB at 16K, 816 MiB at 32K. This could push the 16GB Pi ceiling from
   32K toward 48K, and give the 8GB Pi more room for model weight caching.

5. **Context shift should be treated as a run terminator** in future characterization
   sweeps. Post-shift data is not useful for measuring max-context capability.

6. **The 8GB Pi + NVMe SSD is viable for the 30B model** thanks to mmap. The 17%
   gen speed penalty at empty context (vs 16GB) and 12°C thermal overhead are the
   cost of running a 10 GB model on 8 GB of RAM. An SD card would not be viable —
   NVMe SSD latency (~100 μs) vs SD card (~1-2 ms) makes a 10-20x difference in
   page fault cost.

## Next Steps

- Run 32K → 48K → 64K sweep on both Pis (with context-shift-as-stop-signal)
- Test asymmetric q8_0/q4_0 KV cache at 32K+
- Add major page fault monitoring (`/proc/PID/stat` field 12) to quantify mmap I/O
- Run llama-sweep-bench for controlled PP/TG measurements at various KV fill levels

---

*Data collected 2026-03-24/25. JSONL files in `output/benchmarks/ctx_window_overnight_*`.
Server logs at `/opt/potato/state/bench-18081.log` on each Pi.*
