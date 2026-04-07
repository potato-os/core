# Spike #284: AI Edge Gallery Evaluation

## Meta

| Field | Value |
|-------|-------|
| Date | 2026-04-07 |
| Commit evaluated | `65e794b` ("Update litertlm version") |
| Build status | SUCCESS (JDK 21, 3m14s cold build) |
| Device tested | OnePlus 12R (Snapdragon 8 Gen 2, CPH2609) |
| License | Apache 2.0 |

## Executive Summary

Google's AI Edge Gallery is a well-architected Kotlin/Compose Android app for on-device LLM inference using LiteRT-LM. Its model format (.litertlm) is incompatible with Potato's GGUF/llama.cpp stack, ruling out direct model interchange. However, the benchmark data model and methodology are directly reusable for Potato #276, and the runtime interface pattern is good reference for Inferno #264. The biggest discovery: **LiteRT-LM runs natively on Raspberry Pi 5** with prebuilt Python wheels, achieving 7.6 tok/s decode on Gemma 4 E2B with only ~1.5 GB working memory — making it a viable third runtime for Potato OS.

## Build Notes

### Requirements

- **JDK 21** — mandatory. JDK 17/19 fail with `class file has wrong version 65.0` (GitHub issue #289)
- **Android SDK** — standard install at `~/Library/Android/sdk`, set via `ANDROID_HOME`
- **No NDK** required
- **No `google-services.json`** — Firebase plugin is `apply false`

### HuggingFace OAuth Setup

Required for model downloads (not for build/UI testing). Three placeholders:

1. Create HF developer app at `huggingface.co/settings/applications/new` (public, no client secret)
2. `ProjectConfig.kt` — set `clientId` and `redirectUri`
3. `app/build.gradle.kts` line 45 — set `appAuthRedirectScheme` to the URI scheme

### Build Command

```bash
cd references/gallery/Android/src
JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home \
ANDROID_HOME=$HOME/Library/Android/sdk \
./gradlew :app:assembleDebug
```

Build time: 3m14s (cold Gradle daemon). Only warnings (deprecated context receivers, Kotlin annotation targets).

### Install

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

## Architecture Summary

### Overall Structure

Single-module Android app (`com.google.aiedge.gallery`). Jetpack Compose UI, Hilt DI, Material 3. Min SDK 31, Target SDK 35, Kotlin 2.2.0.

### Inference Engine: LiteRT-LM

Three-layer stack:

```
LiteRT-LM (LLM orchestration)
  tokenization, KV cache, sampling, prefill/decode loop
  multimodal pipeline (vision, audio)
      │
LiteRT (successor to TensorFlow Lite)
  loads .tflite FlatBuffers graphs bundled in .litertlm container
  dispatches to hardware accelerators
      │
Hardware backends:
  CPU: XNNPack (ARM NEON optimized)
  GPU/Android: OpenCL
  GPU/macOS+iOS: Metal
  GPU/Linux: WebGPU (Dawn)
  NPU/Qualcomm: QAIRT SDK
  NPU/MediaTek: NeuroPilot
  NPU/Pixel: Google Tensor custom graphs
```

The `.litertlm` format is a multi-section container (FlatBuffers header, 16KB-aligned sections) bundling `.tflite` model graphs + tokenizer (SentencePiece/HF) + metadata.

### Runtime Abstraction

- **Interface**: `LlmModelHelper` — 5 methods: `initialize`, `runInference`, `cleanUp`, `resetConversation`, `stopResponse`
- **Implementation**: `LlmChatModelHelper` wraps LiteRT-LM `Engine` + `Conversation`
- **Dispatch**: `ModelHelperExt.kt` maps `Model` → helper via `RuntimeType` enum (currently only `LITERT_LM`)
- **Relevant to**: Inferno #264 — clean interface pattern for multi-runtime abstraction

### Model Management

- **Allowlist**: `model_allowlist.json` — curated registry with per-model defaults (topK/topP/temperature), memory estimates (`estimatedPeakMemoryInBytes`, `minDeviceMemoryInGb`), per-SoC model file variants (`socToModelFiles`), task type mappings
- **Data class**: `Model.kt` — 50+ fields covering metadata, download state, config values, capabilities
- **Download**: `DownloadRepository` → WorkManager → `DownloadWorker` with HTTP resume (Range header), rolling-window rate estimation, `.gallerytmp` partial files
- **Auth**: HuggingFace OAuth2 via AppAuth library, scope `read-repos`

### Benchmark System

This is the highest-value area for Potato OS.

**Metrics captured:**
- Prefill speed (tokens/sec)
- Decode speed (tokens/sec)
- Time to first token (seconds)
- First init time (ms)
- Non-first init times (ms)

**Statistical analysis** (`BenchmarkViewModel.kt` lines 364-402):
- Per-metric `ValueSeries`: min, max, avg, median, p25, p75 + raw values
- Linear interpolation for percentile calculation
- Multi-run aggregation (AVG, MEDIAN, MIN, MAX)
- Baseline comparison between accelerators

**Persistence**: Protobuf DataStore (`benchmark.proto`) with `LlmBenchmarkResult` → `LlmBenchmarkBasicInfo` + `LlmBenchmarkStats`.

**UI**: Config screen (accelerator, prefill/decode tokens, run count) + results viewer with baseline comparison, expand/collapse, copy-to-clipboard.

### Multimodal

- Image input via CameraX + `Bitmap` list
- Audio input via `ByteArray` list
- Per-model capability flags: `llmSupportImage`, `llmSupportAudio`, `llmSupportThinking`
- Vision/audio backends can use separate accelerators (vision → GPU, audio → CPU)

### Persistence

- **DataStore (Protobuf)**: settings, benchmark results, access tokens, cutouts, skills
- **External files**: downloaded models at `/storage/emulated/0/Android/data/<app_id>/files/`
- **SharedPreferences**: download timing metadata

### Custom Tasks / Plugin System

- `CustomTask` interface: `task` property, `initializeModelFn`, `cleanUpModelFn`, `MainScreen` composable
- Implementations: AgentChat (function calling), TinyGarden (game), MobileActions (device control)
- Hilt-injected, each with dedicated ViewModel
- Relevant to Inferno #264 plugin/task architecture

## Reuse Matrix

| Area | Gallery Approach | Potato Equivalent | Assessment | Target |
|------|-----------------|-------------------|------------|--------|
| Benchmark data model | Proto schema: ValueSeries with min/max/avg/median/p25/p75 | Ad-hoc markdown, llama-bench binary | **Direct reuse** | #276 |
| Benchmark methodology | Prefill/decode/TTFT/init, multi-run, statistical aggregation | Manual bench scripts | **Direct reuse** | #276 |
| LiteRT-LM runtime on Pi | Python wheels for aarch64, 7.6 tok/s Gemma 4 E2B | Not supported | **Direct adoption** | New ticket |
| Runtime interface pattern | `LlmModelHelper` (5-method interface + dispatch) | Dual llama.cpp processes | **Inspiration** | #264 |
| Model allowlist + memory estimates | JSON registry with per-device variants, peak memory | models.json without memory estimates | **Inspiration** | Model mgmt |
| Download resume + rate estimation | WorkManager, Range header, rolling-window estimator | curl -C -, .part files | **Inspiration** | Download system |
| Benchmark UI/UX | Compose screens, baseline comparison, aggregation selector | None | **Inspiration** | #276 |
| Config key pattern | Typed config classes with slider/switch/segment editors | Dict-based settings | **Inspiration** | Settings |
| Custom task/plugin architecture | Hilt-injected CustomTask interface with lifecycle | No plugin system | **Inspiration** | #264 |
| HuggingFace OAuth2 | AppAuth library, Authorization Code flow | Direct HF URLs (public models) | **Not a fit** | — |
| Proto DataStore persistence | Binary, typed, schema-evolved | Atomic JSON writes | **Not a fit** | — |
| Multimodal implementation | LiteRT vision/audio backends | llama.cpp mmproj projectors | **Not a fit** | — |
| Model format (.litertlm) | TFLite FlatBuffers in container | GGUF | **Not a fit** | — |

## Key Discovery: LiteRT-LM on Raspberry Pi 5

The most significant finding is that LiteRT-LM is a **viable third inference runtime** for Potato OS:

| Property | Detail |
|----------|--------|
| Package | `pip install litert-lm-api` (prebuilt aarch64 wheel, 30 MB) |
| Python | 3.10, 3.11, 3.12, 3.13, 3.14 |
| glibc | Requires >= 2.35 (Pi OS Bookworm has 2.36) |
| Backend | CPU via XNNPack (ARM NEON optimized) |
| Gemma 4 E2B decode | 7.6 tok/s |
| Gemma 4 E2B prefill | 133 tok/s |
| Working memory | ~1.5 GB (embeddings are mmap'd) |
| Model source | `litert-community/gemma-4-E2B-it-litert-lm` on HuggingFace |
| HTTP server | None built-in — needs wrapper |

**Python API:**

```python
import litert_lm

with litert_lm.Engine("model.litertlm", backend=litert_lm.Backend.CPU) as engine:
    with engine.create_conversation() as conversation:
        response = conversation.send_message("Hello")
        print(response["content"][0]["text"])
```

This opens up Gemma 4 edge-optimized models for Potato without requiring GGUF conversion. The E2B variant (2.58 GB file, ~1.5 GB working memory) fits comfortably on an 8 GB Pi 5.

## Recommendations

### For Potato OS: LiteRT as Third Runtime (new ticket)

Add `litert` as a third runtime family alongside `ik_llama` and `llama_cpp`. Write a thin Python HTTP wrapper exposing `/v1/chat/completions` so the existing proxy architecture works unchanged. This gives Potato access to Google's edge-optimized models (Gemma 4 E2B/E4B) with strong performance on Pi 5.

### For Potato #276 (Built-in Benchmarking)

Adopt Gallery's benchmark data model: the `ValueSeries` schema (min/max/avg/median/p25/p75) and the metric set (prefill tok/s, decode tok/s, TTFT, init time). The `calculateValueSeries()` function is trivially portable to Python. The baseline comparison UX is worth replicating in the web UI.

### For Inferno #264 (Standalone Inference)

Gallery's `LlmModelHelper` interface is a clean reference for runtime abstraction. The 5-method contract (initialize, runInference, cleanUp, resetConversation, stopResponse) with a dispatch layer (`ModelHelperExt`) maps well to Inferno's planned multi-backend architecture.

### For Model Management

Consider adopting memory estimate fields (`estimatedPeakMemoryInBytes`, `minDeviceMemoryInGb`) in Potato's model registry. The per-device model variant concept (`socToModelFiles`) could inform Pi 4 vs Pi 5 model recommendations.

### Not Recommended for Adoption

- **Model format interchange** — .litertlm and GGUF are incompatible; no conversion path exists
- **HuggingFace OAuth** — Android-specific flow; Potato uses direct HF URLs
- **Proto DataStore** — platform-specific persistence; atomic JSON is fine for Potato
- **Multimodal implementation** — entirely different runtime pipeline

## Related Issues

- #264 — Inferno extraction (runtime abstraction patterns)
- #216 — Companion app (future mobile surface for model management)
- #276 — Built-in benchmarking (benchmark data model and methodology)

## Evidence

- Repo: `https://github.com/google-ai-edge/gallery` at commit `65e794b`
- License: Apache 2.0 (compatible with Potato's use)
- Build: SUCCESS on macOS (Apple Silicon), JDK 21, 3m14s
- Install: SUCCESS on OnePlus 12R (CPH2609, Snapdragon 8 Gen 2)
- LiteRT-LM Pi 5 benchmarks: from official Google/HuggingFace model cards
