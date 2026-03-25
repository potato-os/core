# Context Window Research: Long-Context Inference on Raspberry Pi 5

Research notes for ticket #168 — context window characterization of Qwen3-30B-A3B
on Raspberry Pi 5 (8GB and 16GB) using ik_llama.cpp with mmap.

## 1. Model Architecture: Qwen3-30B-A3B

Qwen3-30B-A3B is a Mixture-of-Experts (MoE) model. The "30B-A3B" designation means
30.5 billion total parameters with approximately 3.3 billion active per token.

Architecture from HuggingFace config.json:

| Parameter | Value |
|-----------|-------|
| num_hidden_layers | 48 |
| num_attention_heads | 32 |
| num_key_value_heads | 4 (GQA) |
| hidden_size | 2048 |
| max_position_embeddings | 262,144 |
| num_experts_total | 128 |
| num_experts_per_activation | 8 |
| vocab_size | 151,936 |

The model uses Grouped Query Attention (GQA) with 32 query heads but only 4 KV heads.
This is important for KV cache sizing — the cache scales with `num_key_value_heads`,
not `num_attention_heads`, making it 8x smaller than a standard multi-head attention
model of equivalent size.

The MoE routing mechanism selects 8 out of 128 experts per token. This means that for
any given token, only ~6.25% of the expert weights are active. The remaining 93.75%
sit idle in memory — or, with mmap, potentially on disk. This sparsity is what makes
the model feasible on constrained hardware, but it also creates a specific memory
access pattern that has significant implications for mmap-based inference.

The native context window is 262,144 tokens, extensible to 1,000,000 with DCA
(Dual Chunk Attention) and MInference. For Potato OS on Pi, the practical ceiling
is determined by available RAM for the KV cache, not the model's training window.

Source: https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507

## 2. Quantization: ByteShape ShapeLearn K-Quants

The model used in Potato OS is `Qwen3-30B-A3B-Instruct-2507-Q3_K_S-2.66bpw.gguf`
from ByteShape, weighing approximately 9.5 GB on disk.

### Standard K-Quants (llama.cpp)

Standard K-quants use a hierarchical block structure:
- **Superblock**: 256 weight values grouped together
- **Regular blocks**: 8 blocks of 32 weights each within a superblock
- **Metadata**: FP16 super-scale, FP16 super-offset, then INT8 per-block scale/offset
- Available types: Q2_K, Q3_K, Q4_K, Q5_K, Q6_K (with _S, _M, _L size variants)

The Q3_K_S ("small") variant uses 3 bits per weight with minimal metadata overhead,
achieving 2.66 bits per weight (bpw) after accounting for scales and offsets.

### ByteShape ShapeLearn

ByteShape's ShapeLearn technology differs from standard quantization in a
fundamental way: instead of applying a uniform quantization scheme across all tensors,
it **learns the optimal datatype per tensor**, treating total model size as a budget
constraint while optimizing for tokens-per-second (TPS) and output quality
simultaneously.

Key characteristics:
- Per-tensor precision learning rather than uniform quantization
- CPU-optimized variants use KQ (K-Quant) format for GGML kernel efficiency
- GPU-optimized variants use hybrid KQ+IQ for better throughput
- The advantage is most pronounced at lower bitlengths (2.5-3.5 bpw range)

Measured performance: 8.03 t/s on Raspberry Pi 5 (16GB) maintaining 94.18% of BF16
quality. This aligns with observed benchmark results of 8.6-8.9 t/s at low context
fill levels.

Going lower-bit does not always mean faster inference. Different quantization formats
trigger different compute kernels, and the 256-value superblock structure in K-quants
can create scattered memory reads at lower bit depths. ByteShape's learned per-tensor
precision mitigates this by selecting the format that best balances speed and quality
for each tensor individually.

Source: https://huggingface.co/byteshape/Qwen3-30B-A3B-Instruct-2507-GGUF

### ik_llama.cpp IQK Format

ik_llama.cpp extends K-quants with the IQK format, which provides substantial
performance improvements over both standard K-quants and mainline llama.cpp:

**Lookup Table (LUT) Based Dequantization:**
Instead of linear scaling (standard K-quants), IQK uses non-linear mapping via
small lookup tables — 4, 8, 16, or 32 INT8 values that fit in 1-2 SIMD registers.
On ARM NEON (Pi 5), this maps to `vqtbl1q_s8` instructions. On x86, `_mm256_shuffle_epi8`.

**Row-Interleaved Packing (_R4, _R8 variants):**
The `iqk_convert_repack` function transforms quantized blocks into a row-interleaved
layout at model load time. This reorganizes data for better CPU cache locality during
matrix multiplication. The _R4 variant achieves up to 7x faster inference on ARM
compared to standard llama.cpp K-quants.

**Performance Numbers (from ik_llama.cpp benchmarks):**
- IQ3_K: 6.45x faster prompt processing, 2.37x faster token generation vs IQ3_S on
  Ryzen 7950X
- At 32K context: 3-4x faster than mainline llama.cpp
- Uses integer math only — 3-4x faster than floating-point trellis implementations

The row-interleaved repacking is compatible with mmap because it happens after the
mmap load, transforming the in-memory representation. However, `--run-time-repack`
(a different feature that repacks tensors for merged QKV) is **incompatible with mmap**
and silently disables it. The `--merge-qkv` flag documentation explicitly states:
"Downside: mmap cannot be used."

Source: https://github.com/ikawrakow/ik_llama.cpp

## 3. Memory-Mapped Inference (mmap)

### How mmap Works with GGUF Models

When llama-server loads a GGUF model with mmap (the default), it calls `mmap()` to
map the model file into the process's virtual address space. This is fundamentally
different from traditional file loading:

**Traditional load (`--no-mmap`):**
1. Allocate 10 GB of RAM
2. Read entire file from disk into that allocation
3. Model weights exist as a copy in process memory
4. The OS page cache may also hold a copy — wasting memory

**Memory-mapped load (default):**
1. Call `mmap()` — maps 10 GB of virtual address space, costs ~0 bytes of physical RAM
2. Virtual Size (VSZ) jumps to ~10 GB immediately
3. Physical RAM (RSS) stays near zero
4. When inference accesses a weight, a **page fault** occurs
5. The kernel loads that 4 KB page from disk into the **page cache**
6. The process reads directly from the page cache — no copy needed
7. Multiple processes can share the same cached pages

The critical insight: mmap does not "load" the model. It creates a mapping that allows
the kernel to load pages on demand. The model "fits" in memory only in the sense that
not all of it needs to be resident simultaneously.

### What RSS Actually Means with mmap

RSS (Resident Set Size) reports the physical memory currently occupied by the process.
With mmap, this is deeply misleading:

- RSS includes mmap'd pages currently in the page cache
- These pages can be **evicted by the kernel** under memory pressure without any
  process involvement — they are "file-backed" and can be re-read from disk
- RSS does not distinguish between evictable mmap'd pages and non-evictable
  allocations (like the KV cache)
- A process showing 10.6 GB RSS with a 10 GB mmap'd model does NOT mean 10.6 GB is
  "locked" in RAM

**Better metrics for mmap workloads:**

| Metric | Source | What it shows |
|--------|--------|---------------|
| Major page faults | `/proc/PID/stat` field 12 | Pages loaded from disk (expensive) |
| Minor page faults | `/proc/PID/stat` field 10 | Pages found in kernel cache (cheap) |
| PSS | `/proc/PID/smaps` | Proportional Set Size — splits shared page costs |
| Available memory | `free -m` "available" column | Kernel's estimate of allocatable memory |
| Page cache | `/proc/meminfo` "Cached" | How much file data is cached in RAM |

The smoking gun for memory pressure is **rising major page faults**. When the kernel
evicts mmap'd model pages and inference re-accesses them, each re-access is a major
fault that stalls for the duration of a disk read.

### Page Fault Latency by Storage Type

| Storage | Read Latency | Throughput | Impact per fault |
|---------|-------------|------------|-----------------|
| NVMe SSD (Pi 5 PCIe) | 50-200 μs | ~500 MB/s | Noticeable stall |
| SD card (Pi 5) | 500-2000 μs | ~40-80 MB/s | Severe stall |
| RAM (page cache hit) | 1-2 μs | ~10 GB/s | Negligible |

On the 8GB Pi with NVMe SSD, each page fault costs ~100 μs. During token generation,
if 8 expert weight pages need faulting, that adds ~800 μs per token — a meaningful
fraction of the ~120 ms per-token generation time.

## 4. MoE Memory Access Patterns

This is the most important section for understanding performance on memory-constrained
hardware. Dense models and MoE models have fundamentally different memory access
patterns, and this difference determines how mmap behaves.

### Dense Layers (Attention, Norms, Embeddings)

Dense layers are shared across all tokens and accessed every forward pass:
- **Attention weights** (Q, K, V projections, output projection): accessed sequentially
  through the layer stack
- **Layer norms**: tiny, always resident
- **Embeddings**: accessed at input/output, usually cached after first use

These layers have **excellent spatial locality** for mmap. Once faulted into the page
cache, they stay resident because they're accessed every token. On both 8GB and 16GB
Pi, dense layer performance is effectively identical.

For Qwen3-30B-A3B, the dense components (attention + norms + embeddings) total
approximately 3.3 GB of the 10 GB model — the "always active" subset.

### Expert Layers (MoE FFN)

Expert layers are where MoE models diverge dramatically from dense models:

1. **128 total experts** exist in the model weights, each containing FFN up/gate/down
   projections
2. A **router network** examines each token and selects 8 experts to activate
3. Only those 8 experts' weights are read during the forward pass for that token
4. The next token may activate a **completely different set of 8 experts**

This creates a **non-sequential, router-dependent memory access pattern**:

```
Token 1: Router selects experts [3, 17, 42, 55, 78, 91, 103, 120]
          → Access weights at file offsets: 0.2GB, 1.1GB, 2.8GB, 3.6GB, ...
Token 2: Router selects experts [7, 22, 38, 61, 84, 99, 111, 127]
          → Access weights at completely different file offsets
Token 3: Router selects experts [3, 11, 29, 55, 72, 88, 103, 119]
          → Partial overlap with Token 1 (experts 3, 55, 103)
```

The expert weights for Qwen3-30B-A3B total approximately 6.7 GB (the remaining model
size after dense components). Each expert is roughly 52 MB (6.7 GB / 128).

### What This Means for mmap on 8GB Pi

On the **16GB Pi**: the entire 10 GB model fits in RAM. All expert weights are
resident after the first pass. Expert routing causes no page faults during inference.
Performance is compute-bound, not memory-bound.

On the **8GB Pi** (~6-7 GB available after OS): only ~60-70% of the model can be
resident simultaneously. The 3.3 GB dense component stays cached (always accessed),
leaving ~3-4 GB for expert weights — enough for roughly 60-75 of the 128 experts.

During token generation on the 8GB Pi:
1. Router selects 8 experts
2. Some experts are in the page cache (cache hit — fast, ~2 μs per page)
3. Some experts are NOT in cache (cache miss — slow, ~100 μs per page from NVMe)
4. The kernel evicts least-recently-used expert pages to make room
5. With 128 experts competing for ~3-4 GB of cache space, cache churn is constant

**Expected impact on generation speed:**
- Each non-cached expert requires faulting in ~52 MB / 4 KB pages ≈ ~13,000 pages
- But not all pages are needed — only the specific tensor rows being multiplied
- Realistic per-expert fault cost: ~50-200 pages × 100 μs ≈ 5-20 ms per cold expert
- With 2-4 cold experts per token: 10-80 ms additional latency per token
- Baseline generation time (compute only): ~120 ms/token (8.3 t/s)
- With page faults: ~200-300 ms/token (3-5 t/s) — a 2-3x slowdown

This is the fundamental reason the 8GB Pi will show dramatically different performance
characteristics than the 16GB Pi, even though both use the same model and context size.

### Prompt Processing vs Token Generation

The access patterns differ between these two phases:

**Prompt Processing (PP):**
- Processes many tokens in parallel (batch)
- Each token in the batch activates 8 experts, but across a batch of N tokens, many
  more unique experts are activated
- With a batch of 512 tokens, statistically all 128 experts are accessed
- Access pattern is broad but occurs in one burst — the kernel can prefetch effectively
- Result: PP is compute-bound even on 8GB Pi (all expert pages get cached during the
  burst)

**Token Generation (TG):**
- Processes one token at a time (sequential, autoregressive)
- Only 8 experts per token — narrow, scattered access
- Between tokens, the kernel may evict expert pages for other uses
- No opportunity for batch-level prefetching
- Result: TG becomes memory-bound on 8GB Pi due to per-token expert page faults

This explains why prompt processing speed (measured in `prompt_per_second`) may look
similar on both Pis, while generation speed (`predicted_per_second`) diverges
significantly.

## 5. KV Cache

### What the KV Cache Is

The KV (Key-Value) cache stores precomputed attention keys and values from all previous
tokens in the conversation. Unlike model weights, the KV cache is:

- **Allocated as real RAM** — not mmap'd, not evictable by the kernel
- **Grows linearly with context length** — each token adds one KV entry per layer
- **Cannot be compressed by zram** without explicit support (it's actively used memory)
- **The primary constraint** on how long a conversation can be

### Measured KV Cache Sizes (from llama-server startup logs)

These are real numbers from the Qwen3-30B-A3B model on Pi, not calculated estimates:

| Context Size | K (q8_0) | V (q8_0) | Total KV | Notes |
|-------------|----------|----------|----------|-------|
| 16,384 | 408 MiB | 408 MiB | 816 MiB | Default Potato OS config |
| 24,576 | ~612 MiB | ~612 MiB | ~1,224 MiB | Extrapolated (linear) |
| 32,768 | ~816 MiB | ~816 MiB | ~1,632 MiB | Extrapolated |
| 49,152 | ~1,224 MiB | ~1,224 MiB | ~2,448 MiB | Extrapolated |
| 65,536 | ~1,632 MiB | ~1,632 MiB | ~3,264 MiB | Stretch goal |

The 16K measurement is exact (from server log). Larger sizes are linear extrapolations
because KV cache scales linearly with context size. The overnight sweep will provide
exact measurements at each size from the server startup log.

### KV Cache Quantization Options

The KV cache supports quantization independently of the model weights:

| Config | K size | V size | Total @ 64K | Quality impact |
|--------|--------|--------|-------------|----------------|
| q8_0 / q8_0 | 1,632 MiB | 1,632 MiB | 3,264 MiB | Minimal (+0.002-0.05 perplexity) |
| **q8_0 / q4_0** | 1,632 MiB | ~816 MiB | **~2,448 MiB** | Low (K quality preserved) |
| q4_0 / q4_0 | ~816 MiB | ~816 MiB | ~1,632 MiB | Noticeable (+0.2 perplexity) |

**Asymmetric quantization (q8_0 K / q4_0 V)** is the recommended approach when memory
is tight. The ik_llama.cpp documentation explicitly states: "K-cache may need better
quant than V-cache to reduce quality loss." The K-cache contains the attention keys
used for similarity matching, which is more precision-sensitive than the V-cache values
used for weighted averaging.

The `--k-cache-hadamard` flag in ik_llama applies a Hadamard transform to the K-cache
before quantization, which can improve quality when using aggressive quantization
(q4_0 or lower) on the K-cache.

### KV Cache vs Model Weights: Memory Budget

On the 16GB Pi at 64K context with q8_0/q8_0:
- Model weights (mmap'd, evictable): ~10 GB RSS
- KV cache (real RAM, non-evictable): ~3.3 GB
- OS + buffers: ~1.5 GB
- **Total: ~14.8 GB** — fits within 16 GB but with minimal headroom

On the 8GB Pi at 64K context with q8_0/q8_0:
- KV cache alone: 3.3 GB
- OS + buffers: 1.5 GB
- Remaining for model page cache: ~3.2 GB (out of 10 GB model)
- **68% of model weights must be paged from SSD** — severe page fault pressure

This is why asymmetric KV quantization (q8_0/q4_0) matters more on the 8GB Pi:
saving 816 MiB of KV cache frees that space for model weight caching, potentially
reducing expert page faults enough to maintain usable generation speed.

## 6. zram: Compressed RAM Swap

### What zram Is

Both Raspberry Pi configurations use zram as their swap device — a 2 GB compressed
block device that lives entirely in RAM. This is NOT disk swap.

Configuration on both Pis:
- Device: `/dev/zram0`, 2 GB capacity
- Compression algorithm: **zstd** (best ratio, moderate CPU cost)
- Compression streams: 4 (one per Pi 5 ARM core)
- Registered as swap with priority 100

### How zram Interacts with Inference

When memory pressure builds (e.g., KV cache allocation pushes total usage near
physical RAM limit):

1. The kernel identifies candidate pages for eviction
2. **File-backed pages** (mmap'd model weights) are evicted first — they can be
   re-read from disk
3. **Anonymous pages** (heap allocations, stack) go to zram if evicted — compressed
   in RAM
4. zram compresses the page using zstd and stores it in a compressed pool

The compression ratio depends on page content:
- Model weight pages: moderate compressibility (~1.5-2x) because quantized data has
  limited redundancy
- KV cache pages: potentially higher compressibility (~2-3x) depending on attention
  patterns
- Typical effective capacity: 2 GB zram ≈ 3-5 GB of uncompressed data

### The CPU Overhead Tradeoff

zram compression and decompression consume CPU cycles. On the Pi 5's 4 ARM Cortex-A76
cores, this creates direct competition with inference:

- Compression (eviction): runs on the core that triggers the page fault, blocking
  that core for the duration of zstd compression (~10-50 μs per page)
- Decompression (access): runs when a zram'd page is accessed again (~5-30 μs per
  page)
- With 4 compression streams and 4 compute threads for llama-server, heavy zram
  activity can steal 25-50% of CPU capacity

### Monitoring zram Activity

The key file is `/sys/block/zram0/mm_stat` with fields:

| Field | Meaning |
|-------|---------|
| orig_data_size | Uncompressed bytes stored in zram |
| compr_data_size | Actual compressed bytes in RAM |
| mem_used_total | Total memory used by zram (including overhead) |

When `orig_data_size` is non-zero, zram is actively holding compressed pages. The ratio
`orig_data_size / compr_data_size` shows the effective compression ratio. During the
overnight sweep, this metric will reveal when memory pressure starts forcing pages
into zram, and how much additional "virtual" memory zram provides.

The `free -m` command shows zram usage in the "Swap" line because zram registers as a
swap device. When "Swap used" is non-zero, zram is active.

## 7. Hardware Profiles

### Pi 5 16GB (potato.local)

| Aspect | Value |
|--------|-------|
| RAM | 16,218 MiB (16 GB) |
| Storage | 128 GB SD card |
| Swap | 2 GB zram (zstd) |
| Kernel | 6.12.75+rpt-rpi-2712 |
| Model revision | Rev 1.1 |

**Memory budget at 64K context (q8_0/q8_0):**
- Available after OS: ~14.7 GB
- Model weights (mmap RSS): ~10.6 GB
- KV cache: ~3.3 GB
- Headroom: ~0.8 GB

On this Pi, the entire model fits in RAM. Expert routing causes no page faults.
Performance is purely compute-bound. The KV cache is the limiting factor — at 64K,
the ~0.8 GB headroom is tight but should not trigger zram under normal operation.

Generation speed degrades linearly with context fill due to increasing attention
computation (more KV entries to attend over), not memory pressure. Observed: 8.6 t/s
at 4% fill → 4.3 t/s at 65% fill at 16K context.

### Pi 5 8GB + NVMe SSD (ssd.local)

| Aspect | Value |
|--------|-------|
| RAM | 8,062 MiB (8 GB) |
| Storage | 128 GB NVMe SSD (PCIe) |
| Swap | 2 GB zram (zstd) |
| Kernel | 6.12.75+rpt-rpi-2712 |
| Model revision | Rev 1.0 |

**Memory budget at 16K context (q8_0/q8_0):**
- Available after OS: ~6.3 GB
- KV cache: 816 MiB
- Remaining for model page cache: ~5.5 GB (out of 10 GB model)
- ~45% of model must page from SSD

**Memory budget at 64K context (q8_0/q8_0):**
- Available after OS: ~6.3 GB
- KV cache: ~3.3 GB
- Remaining for model page cache: ~3.0 GB (out of 10 GB model)
- **~70% of model must page from SSD**

On this Pi, inference performance is a function of:
1. How many experts are in the page cache (hit rate)
2. NVMe SSD read latency (~100 μs per page fault)
3. zram compression overhead when anonymous pages are evicted
4. Competition between ik_llama's 4 compute threads and zram's 4 compression streams

Expected behavior:
- Prompt processing (PP): moderate speed — batch access patterns cache many experts
- Token generation (TG): significantly slower than 16GB Pi due to per-token expert
  page faults
- Speed variance between tokens: high — some tokens hit cached experts (fast), others
  fault from SSD (slow)
- zram activation likely at context sizes above 16K

The NVMe SSD is critical here. The same experiment on an SD card would be dramatically
worse (~10-20x slower page faults), making large-context inference effectively
unusable.

## 8. ik_llama.cpp Specific Behavior

### Flash Attention on ARM

ik_llama.cpp enables flash attention by default (`--flash-attn on`). Unlike mainline
llama.cpp where flash attention **degrades** CPU performance by ~26%, ik_llama uses a
different approach: fused K*Q and V*softmax operations that reduce memory writes rather
than implementing traditional Flash Attention.

Measured impact: ~20-23% improvement at 16K-32K context on CPU. This is enabled in all
Potato OS configurations.

### Fused MoE (`--fused-moe`)

Enabled by default for MoE models. Fuses the expert routing and FFN computation into a
single kernel, reducing overhead from separate expert dispatch. This changes the
compute pattern but does NOT eliminate the scattered memory access inherent to expert
routing — the I/O pattern remains router-dependent.

ik_llama uses an adaptive GPU offload threshold for MoE: `32 * (total_experts /
active_experts)` tokens before offloading. For Qwen3-30B-A3B: 32 * (128/8) = 512
tokens. This is a CPU-only concern for Pi (no GPU).

### Smart Expert Reduction (`--smart-expert-reduction`)

ik_llama provides `-ser Kmin,t` to reduce the number of active experts at inference
time. For example, `-ser 1,4` would use 4 experts instead of the model's default 8.
This trades quality for speed and memory — fewer active experts means fewer page faults
on memory-constrained systems. This is a potential optimization lever for the 8GB Pi
if full 8-expert inference proves too slow.

### Cache Prompt to Host Memory (`--cache-ram`)

ik_llama's `--cache-ram` stores the KV cache from completed conversations in host RAM,
enabling instant context reuse when the same conversation prefix is seen again.

Default in ik_llama: 8,192 MiB (8 GB).
Potato OS override: 1,024 MiB (1 GB).

The `--cache-ram-similarity` threshold (default 0.50) controls how closely a new
prompt must match a cached one to trigger cache reuse. In multi-turn conversations,
where each request sends the full message history, the prefix match is typically very
high (>90%), making this feature effective.

When memory is very limited, the documentation recommends disabling this feature
(`-cram 0`) to avoid memory pressure from the RAM cache competing with the KV cache
and model page cache.

### Context Shift (Sliding Window)

When the KV cache fills to `--ctx-size`, ik_llama can perform a context shift: the
oldest tokens are evicted from the KV cache using a ring buffer strategy, and the
model continues generating with the remaining context. This is enabled by default
(`--context-shift on`).

Context shift allows conversations to continue beyond the configured context size, but
the oldest context is permanently lost. Quality may degrade as relevant early context
is evicted.

One documented concern: flash attention combined with continuous context shifting can
degrade output quality (vague responses, topic switching). The overnight characterization
should monitor for this by checking the `context_shift_detected` field in the JSONL
output.

### Prompt Caching Behavior (Observed)

From the 16K characterization run on the 16GB Pi, prompt caching behavior was
confirmed working correctly:

- Turn 1: `prompt_n=100` — full prompt processed (system message + first user message)
- Turn 2: `prompt_n=42` — only the new tokens processed (assistant response + new user
  message minus cached prefix)
- Subsequent turns: `prompt_n=35-51` — consistently low, confirming prefix cache reuse

The `prompt_n` field from llama-server timings reports the number of tokens actually
processed (not cached). When caching works, this stays roughly constant regardless of
total context depth. This is the key metric for verifying prompt caching effectiveness.

## 9. Monitoring Strategy

### Metrics to Capture Per Turn

| Metric | Source | Why it matters |
|--------|--------|---------------|
| `predicted_per_second` | llama-server timings | Generation speed (tok/sec) |
| `prompt_n` | llama-server timings | Prompt cache effectiveness indicator |
| `prompt_per_second` | llama-server timings | Prompt processing speed |
| `n_past` | llama-server timings | Accumulated context tokens |
| `n_ctx` | llama-server timings | Configured context size |
| RSS | `ps -o rss=` | Misleading with mmap but still useful as baseline |
| Available memory | `free -m` | Kernel's allocatable memory estimate |
| zram orig/compr | `/sys/block/zram0/mm_stat` | Compressed swap pressure indicator |
| CPU temperature | `vcgencmd measure_temp` | Thermal throttling detection |
| Context shift | server log grep | Sliding window activation |

### What to Look For in Results

**Prompt caching working:** `prompt_n` stays constant (~35-50) regardless of context
depth. If `prompt_n` jumps to match total accumulated tokens, caching has broken.

**Memory pressure onset:** `available_memory` dropping below ~500 MB, zram `orig_data`
becoming non-zero, or generation speed showing sudden drops (page fault stalls).

**Generation speed curve:** Expected to degrade linearly with context fill due to
attention computation. Any non-linear drop suggests memory pressure (page faults) or
thermal throttling.

**Context shift:** If detected, note the turn number and context depth. Generation
quality may degrade after context shift.

**8GB vs 16GB divergence:** The generation speed gap between the two Pis reveals how
much performance is lost to mmap page faults. At low context (16K), the gap shows
baseline expert paging cost. At high context (64K), the gap shows the combined effect
of expert paging plus KV cache memory pressure.

### Overnight Sweep Configuration

The sweep tests context sizes 16,384 → 24,576 → 32,768 → 49,152 → 65,536 with
q8_0/q8_0 KV cache on both Pis simultaneously. Each context size runs a multi-turn
conversation until the context fills or a failure occurs (OOM, thermal throttle,
severe TTFT degradation). Results are stored as JSONL files in `output/benchmarks/`.

---

*Research compiled for Potato OS ticket #168, March 2026.*
*Sources cited inline. Model architecture from HuggingFace, ik_llama parameters from*
*`references/ik_llama.cpp/docs/parameters.md`, measured values from benchmark runs.*
