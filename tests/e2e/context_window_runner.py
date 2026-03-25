#!/usr/bin/env python3
"""Context window characterization runner for Potato OS on Raspberry Pi 5.

Pushes llama-server to its context limits with multi-turn conversations,
measuring prompt caching effectiveness, generation speed degradation,
and memory pressure at increasing context depths.

Usage (single config):
    python tests/e2e/context_window_runner.py \
        --stamp 20260324_30b --hardware-tag pi5-16gb \
        --model /opt/potato/models/Qwen3-30B-A3B*.gguf \
        --ctx-size 32768

Usage (sweep):
    python tests/e2e/context_window_runner.py \
        --stamp 20260324_30b --hardware-tag pi5-16gb \
        --model /opt/potato/models/Qwen3-30B-A3B*.gguf \
        --sweep
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════════
# PURE LOGIC — no I/O, fully testable
# ═══════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = (
    "You are a knowledgeable storyteller. Continue the story with vivid detail, "
    "expanding on the characters, setting, and plot. Write approximately 250 words "
    "per response. Do not repeat yourself. Do not summarize previous events."
)

STORY_PROMPTS = [
    (
        "Begin a story about a lighthouse keeper on a remote island who discovers "
        "a strange metallic object washed ashore during a violent storm. Describe "
        "the island, the keeper's daily routine, and the moment of discovery."
    ),
    (
        "Continue the story. The keeper examines the object more closely in the "
        "morning light. It has unusual markings and a faint warmth to the touch. "
        "Describe what happens next."
    ),
    (
        "Continue. A small fishing vessel approaches through thick fog. The keeper "
        "watches through the telescope. Describe the arrival and the people aboard."
    ),
    (
        "Continue. The visitors from the vessel enter the lighthouse. They seem to "
        "know about the object. Describe the conversation and growing tension."
    ),
    (
        "Continue. That night, the object begins to emit a low hum and a faint "
        "blue glow. The keeper is alone. Describe what happens."
    ),
    (
        "Continue. The keeper must now make a difficult choice about the object "
        "and the visitors' demands. Describe the internal conflict and decision."
    ),
    (
        "Continue. Dawn breaks and the island has transformed overnight. Strange "
        "flora and crystalline formations have appeared. Describe the changed landscape."
    ),
    (
        "Continue. The keeper ventures out to explore the transformed island, "
        "discovering that the wildlife has also changed. Describe the expedition."
    ),
    (
        "Continue. A radio message arrives from the mainland — garbled but urgent. "
        "Other islands are reporting similar phenomena. Describe the message and reaction."
    ),
    (
        "Continue. The keeper prepares for what comes next, fortifying the lighthouse "
        "and studying the object's patterns. Describe the preparations and discoveries."
    ),
]


class ConversationBuilder:
    """Builds multi-turn OpenAI-format message arrays for context testing."""

    def __init__(self) -> None:
        self._messages: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]

    def add_user_turn(self, turn_number: int) -> list[dict[str, str]]:
        prompt = STORY_PROMPTS[(turn_number - 1) % len(STORY_PROMPTS)]
        self._messages.append({"role": "user", "content": prompt})
        return list(self._messages)

    def add_assistant_response(self, content: str) -> None:
        self._messages.append({"role": "assistant", "content": content})

    def get_messages(self) -> list[dict[str, str]]:
        return list(self._messages)

    def total_messages(self) -> int:
        return len(self._messages)

    def estimated_tokens(self) -> int:
        return sum(len(m["content"]) for m in self._messages) // 4


def build_chat_request(messages: list[dict[str, str]]) -> dict:
    return {
        "model": "qwen-local",
        "stream": True,
        "temperature": 0,
        "top_p": 1,
        "seed": 42,
        "max_tokens": 1024,
        "presence_penalty": 0,
        "frequency_penalty": 0,
        "cache_prompt": True,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": messages,
    }


# ── System metrics parsers ───────────────────────────────────────────────


def parse_rss_from_ps(stdout: str) -> int | None:
    """Parse RSS in KB from `ps -o rss=` output, return MB."""
    text = stdout.strip()
    if not text:
        return None
    try:
        return int(text) // 1024
    except ValueError:
        return None


def parse_swap_from_free(stdout: str) -> tuple[int, int] | None:
    """Parse (swap_used_mb, swap_total_mb) from `free -m` output."""
    for line in stdout.splitlines():
        if line.lower().startswith("swap:"):
            parts = line.split()
            if len(parts) >= 3:
                try:
                    return (int(parts[2]), int(parts[1]))
                except ValueError:
                    return None
    return None


def parse_cpu_temp(stdout: str) -> float | None:
    """Parse temperature from `vcgencmd measure_temp` output like temp=62.5'C."""
    m = re.search(r"temp=([\d.]+)", stdout)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def parse_available_memory(stdout: str) -> int | None:
    """Parse available memory in MB from `free -m` output."""
    for line in stdout.splitlines():
        if line.lower().startswith("mem:"):
            parts = line.split()
            if len(parts) >= 7:
                try:
                    return int(parts[6])
                except (ValueError, IndexError):
                    return None
    return None


def parse_zram_mm_stat(stdout: str) -> dict | None:
    """Parse /sys/block/zram0/mm_stat.

    Fields: orig_data_size compr_data_size mem_used_total mem_limit
            mem_used_max same_pages pages_compacted huge_pages huge_pages_since
    All values in bytes.
    """
    text = stdout.strip()
    if not text:
        return None
    parts = text.split()
    if len(parts) < 3:
        return None
    try:
        orig = int(parts[0])
        compr = int(parts[1])
        mem_used = int(parts[2])
        return {
            "zram_orig_mb": orig // (1024 * 1024),
            "zram_compr_mb": compr // (1024 * 1024),
            "zram_mem_used_mb": mem_used // (1024 * 1024),
            "zram_ratio": round(orig / compr, 2) if compr > 0 else 0,
        }
    except (ValueError, IndexError):
        return None


# ── Failure detectors ────────────────────────────────────────────────────


def detect_context_shift(log_lines: list[str]) -> bool:
    for line in log_lines:
        if "context_shift" in line or "context shift" in line.lower():
            return True
    return False


def detect_severe_ttft_degradation(
    ttft: float, baseline: float, threshold: float = 10.0
) -> bool:
    if baseline <= 0:
        return False
    return ttft / baseline > threshold


def detect_swap_thrashing(swap_history: list[int], window: int = 3) -> bool:
    if len(swap_history) < window:
        return False
    tail = swap_history[-window:]
    return all(tail[i] > tail[i - 1] for i in range(1, len(tail)))


def detect_thermal_throttle(temp_c: float, threshold: float = 80.0) -> bool:
    return temp_c > threshold


def should_abort(failures: dict[str, bool]) -> tuple[bool, str]:
    if failures.get("oom_crash"):
        return (True, "OOM crash — server died")
    if failures.get("thermal_throttle"):
        return (True, "Thermal throttle — CPU too hot")
    if failures.get("severe_ttft"):
        return (True, "Severe TTFT degradation — unusable latency")
    # context_shift and swap_thrashing are recorded but do NOT abort
    return (False, "")


# ═══════════════════════════════════════════════════════════════════════════
# I/O LAYER — SSH, HTTP, CLI
# ═══════════════════════════════════════════════════════════════════════════

DEFAULT_HOST = "potato.local"
USER = "pi"
PASSWORD = "raspberry"
HOST = DEFAULT_HOST  # overridden by --host CLI arg
REQUEST_SEED = 42
REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / "output" / "benchmarks"

SWEEP_CTX_SIZES = [16384, 24576, 32768, 49152, 65536]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Context window characterization runner")
    p.add_argument("--stamp", required=True, help="Timestamp for output filenames")
    p.add_argument("--hardware-tag", required=True, help="e.g. pi5-16gb, pi5-8gb-ssd")
    p.add_argument("--host", default=DEFAULT_HOST, help="Pi hostname or IP")
    p.add_argument("--model", default="/opt/potato/models/Qwen3.5-2B-Q4_K_M.gguf")
    p.add_argument(
        "--server-bin", default="/opt/potato/llama/bin/llama-server"
    )
    p.add_argument("--port", type=int, default=18081)
    p.add_argument("--ctx-size", type=int, default=16384)
    p.add_argument("--cache-type-k", default="q8_0")
    p.add_argument("--cache-type-v", default="q8_0")
    p.add_argument("--flash-attn", choices=["on", "off"], default="on")
    p.add_argument("--cache-ram", type=int, default=1024, help="MiB for RAM cache")
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--max-turns", type=int, default=60)
    p.add_argument("--target-tokens", type=int, default=65000)
    p.add_argument("--no-mmap", action="store_true")
    p.add_argument("--extra-flags", default="")
    p.add_argument(
        "--sweep", action="store_true", help="Sweep through context sizes"
    )
    return p.parse_args()


def run_local(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def ssh(cmd: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run_local(
        [
            "sshpass", "-p", PASSWORD,
            "ssh", "-o", "StrictHostKeyChecking=no",
            f"{USER}@{HOST}", cmd,
        ],
        check=check,
    )


def sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def remote(cmd: str, *, check: bool = True) -> str:
    return ssh(cmd, check=check).stdout.strip()


def kill_remote_server(port: int) -> None:
    remote(
        "bash -lc "
        + sh_quote(
            f"if [ -f /tmp/ik-bench-{port}.pid ]; then "
            f"kill $(cat /tmp/ik-bench-{port}.pid) 2>/dev/null || true; "
            f"sleep 1; "
            f"kill -9 $(cat /tmp/ik-bench-{port}.pid) 2>/dev/null || true; "
            f"rm -f /tmp/ik-bench-{port}.pid; "
            f"fi"
        ),
        check=False,
    )


def build_server_tokens(args: argparse.Namespace) -> list[str]:
    tokens = [
        args.server_bin,
        "--model", args.model,
        "--host", "0.0.0.0",
        "--port", str(args.port),
        "--ctx-size", str(args.ctx_size),
        "--cache-ram", str(args.cache_ram),
        "--parallel", "1",
        "--threads", str(args.threads),
        "--cache-type-k", args.cache_type_k,
        "--cache-type-v", args.cache_type_v,
        "--jinja",
        "--flash-attn", args.flash_attn,
        "--no-warmup",
        "--reasoning-format", "none",
        "--reasoning-budget", "0",
        "--chat-template-kwargs", '{"enable_thinking": false}',
    ]
    if args.no_mmap:
        tokens.append("--no-mmap")
    if args.extra_flags:
        tokens.extend(shlex.split(args.extra_flags))
    return tokens


def render_shell_tokens(tokens: list[str]) -> str:
    return " ".join(sh_quote(t) for t in tokens)


def stop_all_llama_servers() -> None:
    """Kill ALL llama-server processes to free memory."""
    remote(
        "echo raspberry | sudo -S pkill -9 -f llama-server 2>/dev/null || true",
        check=False,
    )
    time.sleep(3)


def start_server(args: argparse.Namespace) -> tuple[float, str]:
    """Start llama-server, return (startup_seconds, kv_cache_info)."""
    stop_all_llama_servers()  # kill everything — we need all the RAM
    kill_remote_server(args.port)

    lib_dir = str(Path(args.server_bin).parent.parent / "lib")
    ld = f"LD_LIBRARY_PATH={sh_quote(lib_dir)} GGML_BACKEND_DIR={sh_quote(lib_dir)}"
    server_cmd = render_shell_tokens(build_server_tokens(args))
    cmd = (
        "bash -lc "
        + sh_quote(
            f"mkdir -p /opt/potato/state && "
            f"nohup env {ld} {server_cmd} "
            f">/opt/potato/state/bench-{args.port}.log 2>&1 & "
            f"echo $! >/tmp/ik-bench-{args.port}.pid"
        )
    )
    start = time.monotonic()
    ssh(cmd)

    deadline = start + 300  # 5 minutes for large models
    while time.monotonic() < deadline:
        code = remote(
            f"curl -s -o /dev/null -w '%{{http_code}}' "
            f"http://127.0.0.1:{args.port}/v1/models || true",
            check=False,
        )
        if code == "200":
            startup_s = time.monotonic() - start
            # Read KV cache info from startup log
            kv_info = remote(
                f"grep -i 'kv.*size\\|kv.*buffer\\|KV self' "
                f"/opt/potato/state/bench-{args.port}.log 2>/dev/null || true",
                check=False,
            )
            return startup_s, kv_info
        time.sleep(2)

    last = remote(
        f"tail -n 20 /opt/potato/state/bench-{args.port}.log 2>/dev/null || true",
        check=False,
    )
    raise RuntimeError(f"Server failed to start within 5 min. Last log:\n{last}")


def stream_chat_request(port: int, payload: dict) -> dict:
    """Send streaming chat request, return parsed result."""
    req = urllib.request.Request(
        f"http://{HOST}:{port}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    start = time.monotonic()
    ttft = None
    parts: list[str] = []
    timings: dict = {}
    finish_reason = None

    with urllib.request.urlopen(req, timeout=600) as response:
        for raw in response:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data: "):
                continue
            event = line[6:]
            if event == "[DONE]":
                break
            chunk = json.loads(event)
            choice = (chunk.get("choices") or [{}])[0]
            delta = choice.get("delta", {})
            content = delta.get("content")
            if content and ttft is None:
                ttft = time.monotonic() - start
            if content:
                parts.append(content)
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
            if chunk.get("timings"):
                timings = chunk["timings"]

    total_s = time.monotonic() - start
    response_text = "".join(parts)

    return {
        "ttft_s": ttft or total_s,
        "total_s": total_s,
        "response": response_text,
        "finish_reason": finish_reason,
        "timings": timings,
        "prompt_n": timings.get("prompt_n", 0),
        "prompt_ms": timings.get("prompt_ms", 0),
        "prompt_per_second": timings.get("prompt_per_second", 0),
        "predicted_n": timings.get("predicted_n", 0),
        "predicted_ms": timings.get("predicted_ms", 0),
        "predicted_per_second": timings.get("predicted_per_second", 0),
        "n_past": timings.get("n_past", 0),
        "n_ctx": timings.get("n_ctx", 0),
    }


def sample_system_metrics(port: int) -> dict:
    pid = remote(
        f"cat /tmp/ik-bench-{port}.pid 2>/dev/null || true", check=False
    )
    rss_raw = remote(f"ps -o rss= -p {pid} 2>/dev/null || true", check=False) if pid else ""
    free_raw = remote("free -m 2>/dev/null || true", check=False)
    temp_raw = remote("vcgencmd measure_temp 2>/dev/null || true", check=False)
    zram_raw = remote("cat /sys/block/zram0/mm_stat 2>/dev/null || true", check=False)

    swap = parse_swap_from_free(free_raw)
    zram = parse_zram_mm_stat(zram_raw)
    result = {
        "system_rss_mb": parse_rss_from_ps(rss_raw),
        "swap_used_mb": swap[0] if swap else None,
        "swap_total_mb": swap[1] if swap else None,
        "available_memory_mb": parse_available_memory(free_raw),
        "cpu_temp_c": parse_cpu_temp(temp_raw),
    }
    if zram:
        result.update(zram)
    return result


def get_server_log_tail(port: int, lines: int = 20) -> list[str]:
    raw = remote(
        f"tail -n {lines} /opt/potato/state/bench-{port}.log 2>/dev/null || true",
        check=False,
    )
    return raw.splitlines() if raw else []


def log_path(stamp: str, ctx_size: int, hardware_tag: str) -> Path:
    return OUTPUT_DIR / f"ctx_window_{stamp}_{ctx_size}_{hardware_tag}.jsonl"


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(row, default=str) + "\n")


def print_turn_summary(row: dict) -> None:
    prompt_n = row.get("prompt_n", 0)
    n_past = row.get("n_past", 0)
    n_ctx = row.get("n_ctx", 0)
    gen_speed = row.get("predicted_per_second", 0)
    ttft = row.get("ttft_s", 0)
    rss = row.get("system_rss_mb", "?")
    swap = row.get("swap_used_mb", "?")
    temp = row.get("cpu_temp_c", "?")
    shift = " | CONTEXT SHIFT" if row.get("context_shift_detected") else ""

    accumulated = row.get("accumulated_tokens_estimate", 0)
    cache_tag = "CACHED" if prompt_n < accumulated * 0.5 else "FULL!"

    zram_orig = row.get("zram_orig_mb", 0)
    zram_ratio = row.get("zram_ratio", 0)
    zram_tag = f" | zram={zram_orig}MB({zram_ratio}x)" if zram_orig else ""

    print(
        f"Turn {row['turn_number']:>3} | "
        f"n_past={n_past}/{n_ctx} | "
        f"prompt_n={prompt_n} ({cache_tag}) | "
        f"gen={gen_speed:.1f}t/s | "
        f"TTFT={ttft:.2f}s | "
        f"RSS={rss}MB | avail={row.get('available_memory_mb', '?')}MB | {temp}C"
        f"{zram_tag}{shift}"
    )


def run_conversation(args: argparse.Namespace) -> list[dict]:
    print(f"\n{'='*70}")
    print(f"Config: ctx={args.ctx_size} kv={args.cache_type_k} hw={args.hardware_tag}")
    print(f"Model: {args.model}")
    print(f"{'='*70}\n")

    try:
        startup_s, kv_info = start_server(args)
    except RuntimeError as e:
        print(f"FAILED to start server: {e}")
        return []

    print(f"Server started in {startup_s:.1f}s")
    if kv_info:
        print(f"KV cache info:\n{kv_info}\n")

    builder = ConversationBuilder()
    results: list[dict] = []
    baseline_ttft: float | None = None
    swap_history: list[int] = []
    output = log_path(args.stamp, args.ctx_size, args.hardware_tag)

    for turn in range(1, args.max_turns + 1):
        messages = builder.add_user_turn(turn)
        payload = build_chat_request(messages)

        oom_crash = False
        try:
            result = stream_chat_request(args.port, payload)
        except Exception as e:
            print(f"Turn {turn}: REQUEST FAILED — {e}")
            oom_crash = True
            result = {
                "ttft_s": 0, "total_s": 0, "response": "",
                "finish_reason": "error", "timings": {},
                "prompt_n": 0, "prompt_ms": 0, "prompt_per_second": 0,
                "predicted_n": 0, "predicted_ms": 0, "predicted_per_second": 0,
                "n_past": 0, "n_ctx": args.ctx_size,
            }

        if result["response"]:
            builder.add_assistant_response(result["response"])

        sys_metrics = sample_system_metrics(args.port)
        log_tail = get_server_log_tail(args.port)
        ctx_shift = detect_context_shift(log_tail)

        if baseline_ttft is None and result["ttft_s"] > 0:
            baseline_ttft = result["ttft_s"]

        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "phase": "context-window",
            "variant": f"{args.cache_type_k}-{args.ctx_size}-{args.hardware_tag}",
            "hardware_tag": args.hardware_tag,
            "model": Path(args.model).name,
            "turn_number": turn,
            "total_messages": builder.total_messages(),
            "accumulated_tokens_estimate": builder.estimated_tokens(),
            "startup_s": startup_s if turn == 1 else None,
            "ttft_s": result["ttft_s"],
            "total_s": result["total_s"],
            "prompt_n": result["prompt_n"],
            "prompt_ms": result["prompt_ms"],
            "prompt_per_second": result["prompt_per_second"],
            "predicted_n": result["predicted_n"],
            "predicted_ms": result["predicted_ms"],
            "predicted_per_second": result["predicted_per_second"],
            "n_past": result["n_past"],
            "n_ctx": result["n_ctx"],
            **sys_metrics,
            "context_shift_detected": ctx_shift,
            "finish_reason": result["finish_reason"],
            "ctx_size": args.ctx_size,
            "cache_type_k": args.cache_type_k,
            "cache_type_v": args.cache_type_v,
            "flash_attn": args.flash_attn,
            "cache_ram_mib": args.cache_ram,
            "kv_cache_info": kv_info if turn == 1 else None,
            "timings": result["timings"],
            "response_preview": result["response"][:200],
        }

        results.append(row)
        append_jsonl(output, row)
        print_turn_summary(row)

        # Check abort conditions
        swap_val = sys_metrics.get("swap_used_mb") or 0
        swap_history.append(swap_val)
        failures = {
            "oom_crash": oom_crash,
            "context_shift": ctx_shift,
            "severe_ttft": detect_severe_ttft_degradation(
                result["ttft_s"], baseline_ttft or 1.0
            ),
            "swap_thrashing": detect_swap_thrashing(swap_history),
            "thermal_throttle": detect_thermal_throttle(
                sys_metrics.get("cpu_temp_c") or 0
            ),
        }

        abort, reason = should_abort(failures)
        if abort:
            print(f"\nABORT at turn {turn}: {reason}")
            break

        accumulated = result.get("n_past", 0) or builder.estimated_tokens()
        if accumulated >= args.target_tokens:
            print(f"\nTarget reached at turn {turn}: ~{accumulated} tokens")
            break

    kill_remote_server(args.port)
    print(f"\nResults: {output}")
    print(f"Turns completed: {len(results)}")
    return results


def main() -> None:
    global HOST
    args = parse_args()
    HOST = args.host
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.sweep:
        for ctx_size in SWEEP_CTX_SIZES:
            args.ctx_size = ctx_size
            try:
                run_conversation(args)
            except Exception as e:
                print(f"\n!!! Config ctx={ctx_size} CRASHED: {e}")
                print("Moving to next config...\n")
            # Cooldown — let the Pi recover between configs
            time.sleep(10)
        print("\n=== SWEEP COMPLETE ===")
    else:
        run_conversation(args)


if __name__ == "__main__":
    main()
