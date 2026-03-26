# MNN Spike Research — Qwen3.5-4B on Raspberry Pi 5

Refs #24

## Overview

[MNN](https://github.com/alibaba/MNN) (Mobile Neural Network) is Alibaba's on-device inference framework. Version 3.4.1 (March 2026) added Qwen3.5 support and claims dramatically faster LLM inference on ARM compared to llama.cpp:

- **8.6x faster prefill**, **2.3x faster decode** on CPU
- **25x faster prefill** on GPU

**Critical caveat**: those benchmarks were run on Snapdragon 8 Gen 3 (ARMv9 with i8mm and SME instructions). The Pi 5's Cortex-A76 is ARMv8.2 — it has NEON and sdot but *not* i8mm or SME. The real-world advantage on Pi 5 will be smaller. This spike measures how much actually transfers.

## Why MNN is interesting for Potato OS

- Alibaba ships MNN in production across their mobile apps — it's not a research project
- Native ARM kernel optimizations (NEON, sdot, FP16) usable on Pi 5
- DRAM-Flash hybrid storage can offload embeddings to disk (~15% DRAM savings)
- Fused transformer ops reduce memory bandwidth pressure
- Active development with Qwen-first support (same model family Potato already uses)

## Model path

### Pre-converted model (used in this spike)

A pre-converted 4-bit quantized Qwen3.5-4B already exists on HuggingFace:

```
taobao-mnn/Qwen3.5-4B-MNN
```

The `taobao-mnn` org maintains 208+ pre-converted models including a 13-model Qwen3.5 collection. This skips the entire conversion pipeline — just download and run.

```bash
pip install huggingface_hub
huggingface-cli download taobao-mnn/Qwen3.5-4B-MNN --local-dir /tmp/qwen35-4b-mnn
```

### General conversion path (documented for completeness)

For models without pre-converted MNN versions, the pipeline is:

1. Install MNN Python tools:
   ```bash
   pip install mnn-llm
   ```

2. Export HuggingFace model to MNN format:
   ```bash
   python -m mnn_llm.llm_export \
     --path /path/to/hf-model \
     --type qwen \
     --quant_bit 4 \
     --quant_block 128 \
     --dst_path ./output_model
   ```

3. The export produces a directory with:
   - `config.json` — MNN runtime config (thread count, memory mode, precision)
   - `*.mnn` — quantized weight shards
   - `tokenizer.txt` — vocabulary

This spike uses the pre-converted model and does not exercise the conversion pipeline.

## Building MNN on Pi 5

No prebuilt binaries exist for aarch64 Linux. Must compile from source.

### Prerequisites

```bash
sudo apt install -y cmake g++ make
```

### Build recipe

See `benchmarks/mnn_spike_build.sh` for the scripted version. The key cmake flags:

```bash
cmake -S /tmp/mnn -B /tmp/mnn-build \
  -DCMAKE_BUILD_TYPE=Release \
  -DMNN_BUILD_LLM=ON \
  -DMNN_LOW_MEMORY=ON \
  -DMNN_CPU_WEIGHT_DEQUANT_GEMM=ON \
  -DMNN_SUPPORT_TRANSFORMER_FUSE=ON \
  -DMNN_ARM82=ON \
  -DMNN_USE_THREAD_POOL=ON

cmake --build /tmp/mnn-build --config Release -j4
```

### Flag rationale

| Flag | Purpose |
|------|---------|
| `MNN_BUILD_LLM` | Build the LLM inference engine and `llm_demo` binary |
| `MNN_LOW_MEMORY` | Enable low-memory weight decompression |
| `MNN_CPU_WEIGHT_DEQUANT_GEMM` | Fused dequantization + matrix multiply (lower peak memory) |
| `MNN_SUPPORT_TRANSFORMER_FUSE` | Fused attention/FFN ops (fewer memory round-trips) |
| `MNN_ARM82` | ARMv8.2 features: FP16 compute and sdot (both available on Pi 5) |
| `MNN_USE_THREAD_POOL` | Thread pool for multi-core inference (Pi 5 has 4 cores) |

**Not used**: `MNN_ARM86=ON` (requires ARMv8.6+ for i8mm — Pi 5 is ARMv8.2).

### Expected build time

~15 minutes on Pi 5 (based on comparable llama.cpp build times with `-j4`).

### Binary location

After build: `/tmp/mnn-build/llm_demo`

## Using llm_demo

The `llm_demo` binary has two modes:

### Interactive chat

```bash
./llm_demo /path/to/model/config.json
```

Enters an interactive loop reading from stdin. Type `/exit` to quit, `/reset` to clear context.

### Benchmark mode (used in this spike)

```bash
./llm_demo /path/to/model/config.json prompts.txt <max_tokens> <no_thinking>
```

Reads prompts from a text file (one per line), generates up to `max_tokens` per prompt, and prints performance statistics:

```
#################################
prompt tokens num = X
decode tokens num = X
prefill time = X.XX s
 decode time = X.XX s
prefill speed = X.XX tok/s
 decode speed = X.XX tok/s
##################################
```

The optional 5th argument disables thinking mode for Qwen3 models (any non-empty value triggers it).

## Architecture comparison

| Capability | MNN (`llm_demo`) | IK (`llama-server`) |
|------------|-------------------|---------------------|
| HTTP server | None | Yes (OpenAI-compatible) |
| Streaming API | None | SSE via `/v1/chat/completions` |
| Prompt caching | Within single session | Across requests (persistent) |
| Multi-turn | Within session only | Full via API |
| Vision/multimodal | Supported (with extra build flags) | Yes (mmproj) |
| Quantization | MNN 4-bit (custom scheme) | GGUF Q4_K_M, IQK formats |
| Community models | ~208 pre-converted | Thousands of GGUF models |
| Documentation | Primarily Chinese | English-first, extensive |

The biggest integration gap for Potato OS: **MNN has no HTTP server**. Potato's entire architecture routes through `llama-server`'s OpenAI-compatible API. Adopting MNN would require either building an HTTP wrapper or rearchitecting the inference path.

## Benchmark methodology

### Workload

5 fixed text-only prompts, 3 repetitions each, deterministic generation:
- Temperature: 0 (MNN default), seed: 42 (IK)
- Max tokens: 128 per prompt
- Thinking mode disabled (both runtimes)
- Cold start between runtime switches (kill process, clear caches)

### Fair comparison approach

| Dimension | MNN | IK baseline |
|-----------|-----|-------------|
| Model | `taobao-mnn/Qwen3.5-4B-MNN` (4-bit) | `Qwen3.5-4B-Q4_K_M.gguf` (~4-bit) |
| Hardware | Same Pi 5, tested sequentially | Same Pi 5, tested sequentially |
| Prompts | Identical text prompts | Identical text prompts |
| Max tokens | 128 | 128 |

The quantization schemes differ (MNN's internal 4-bit vs GGUF Q4_K_M) but both target the same bit-width tier. This is acknowledged as a best-effort comparison.

### Metrics (priority order)

1. **Decode tok/s** — generation throughput (user-perceived speed)
2. **Prefill tok/s** — prompt processing speed (affects TTFT)
3. **Time to first token (TTFT)** — end-to-end latency including model load
4. **Peak RSS (MB)** — memory footprint during inference
5. **Model load time (s)** — cold start latency
6. **Output coherence** — sanity check, not a quality benchmark
