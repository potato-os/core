#!/usr/bin/env python3
"""Multi-turn conversation runner for MNN llm_demo context sweep.

Runs ON the Pi. Drives llm_demo locally via pexpect-style pty interaction.

Usage (on Pi):
    python3 mnn_overnight_conversation.py \
        --model-dir /tmp/qwen35-4b-mnn \
        --llm-demo /tmp/mnn-build/llm_demo \
        --hardware-tag pi5-16gb \
        --output /tmp/mnn_bench.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import pty
import re
import select
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

SYSTEM_PROMPT = (
    "You are a knowledgeable storyteller. Continue the story with vivid detail, "
    "expanding on the characters, setting, and plot. Write approximately 250 words "
    "per response. Do not repeat yourself. Do not summarize previous events."
)

STORY_PROMPTS = [
    "Begin a story about a lighthouse keeper on a remote island who discovers a strange metallic object washed ashore during a violent storm. Describe the island, the keeper's daily routine, and the moment of discovery.",
    "Continue the story. The keeper examines the object more closely in the morning light. It has unusual markings and a faint warmth to the touch. Describe what happens next.",
    "Continue. A small fishing vessel approaches through thick fog. The keeper watches through the telescope. Describe the arrival and the people aboard.",
    "Continue. The visitors from the vessel enter the lighthouse. They seem to know about the object. Describe the conversation and growing tension.",
    "Continue. That night, the object begins to emit a low hum and a faint blue glow. The keeper is alone. Describe what happens.",
    "Continue. The keeper must now make a difficult choice about the object and the visitors demands. Describe the internal conflict and decision.",
    "Continue. Dawn breaks and the island has transformed overnight. Strange flora and crystalline formations have appeared. Describe the changed landscape.",
    "Continue. The keeper ventures out to explore the transformed island, discovering that the wildlife has also changed. Describe the expedition.",
    "Continue. A radio message arrives from the mainland, garbled but urgent. Other islands are reporting similar phenomena. Describe the message and reaction.",
    "Continue. The keeper prepares for what comes next, fortifying the lighthouse and studying the objects patterns. Describe the preparations and discoveries.",
]

MAX_TURNS = 200
MAX_CONSECUTIVE_FAILURES = 5


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--model-dir", default="/tmp/qwen35-4b-mnn")
    p.add_argument("--llm-demo", default="/tmp/mnn-build/llm_demo")
    p.add_argument("--ctx-size", type=int, default=65536, help="For metadata only")
    p.add_argument("--hardware-tag", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--threads", type=int, default=4)
    return p.parse_args()


def sample_metrics() -> dict:
    """Sample system metrics locally on the Pi."""
    rss = None
    try:
        pid_out = subprocess.run(
            ["pgrep", "-f", "llm_demo"], capture_output=True, text=True
        ).stdout.strip().split("\n")[0]
        if pid_out:
            rss_out = subprocess.run(
                ["ps", "-o", "rss=", "-p", pid_out],
                capture_output=True, text=True,
            ).stdout.strip()
            if rss_out:
                rss = int(rss_out) // 1024
    except Exception:
        pass

    avail = None
    swap_used = None
    try:
        free_out = subprocess.run(
            ["free", "-m"], capture_output=True, text=True
        ).stdout
        for line in free_out.splitlines():
            parts = line.split()
            if line.lower().startswith("mem:") and len(parts) >= 7:
                avail = int(parts[6])
            if line.lower().startswith("swap:") and len(parts) >= 3:
                swap_used = int(parts[2])
    except Exception:
        pass

    temp = None
    try:
        temp_out = subprocess.run(
            ["vcgencmd", "measure_temp"], capture_output=True, text=True
        ).stdout
        m = re.search(r"temp=([\d.]+)", temp_out)
        if m:
            temp = float(m.group(1))
    except Exception:
        pass

    zram_orig = 0
    zram_compr = 0
    try:
        zram_data = Path("/sys/block/zram0/mm_stat").read_text().split()
        if len(zram_data) >= 2:
            zram_orig = int(zram_data[0]) // (1024 * 1024)
            zram_compr = int(zram_data[1]) // (1024 * 1024)
    except Exception:
        pass

    return {
        "rss_mb": rss,
        "avail_mb": avail,
        "swap_used_mb": swap_used,
        "temp_c": temp,
        "zram_orig_mb": zram_orig,
        "zram_compr_mb": zram_compr,
    }


def read_until(fd: int, marker: str, timeout: float = 1800) -> str:
    """Read from pty fd until marker is found or timeout."""
    buf = ""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        ready, _, _ = select.select([fd], [], [], min(remaining, 1.0))
        if ready:
            try:
                chunk = os.read(fd, 4096).decode("utf-8", errors="replace")
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            if marker in buf:
                return buf
    return buf


def main() -> None:
    args = parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    # Update thread count in config
    config_path = Path(args.model_dir) / "llm_config.json"
    try:
        config = json.loads(config_path.read_text())
        config["thread_num"] = args.threads
        config_path.write_text(json.dumps(config, indent=2))
        print(f"Set thread_num={args.threads}")
    except Exception as e:
        print(f"Warning: could not update config: {e}")

    # Start llm_demo with a pty so it doesn't buffer
    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        [args.llm_demo, str(config_path)],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        cwd=args.model_dir,
    )
    os.close(slave_fd)

    print("Waiting for model load...")
    load_output = read_until(master_fd, "User:", timeout=120)
    if "User:" not in load_output:
        print(f"ERROR: llm_demo did not reach chat prompt. Output:\n{load_output[-500:]}")
        proc.kill()
        return

    print("Model loaded. Starting conversation...\n")

    consecutive_failures = 0

    for turn in range(1, MAX_TURNS + 1):
        prompt = STORY_PROMPTS[(turn - 1) % len(STORY_PROMPTS)]

        start = time.monotonic()
        try:
            os.write(master_fd, (prompt + "\n").encode())
        except OSError:
            print(f"Turn {turn}: write failed — llm_demo crashed")
            row = {"turn": turn, "error": "write_failed",
                   "ctx_size": args.ctx_size, "hardware_tag": args.hardware_tag,
                   "timestamp": datetime.now(timezone.utc).isoformat()}
            with open(output, "a") as f:
                f.write(json.dumps(row) + "\n")
            break

        # Read until next "User:" prompt
        raw = read_until(master_fd, "User:", timeout=1800)
        total_s = time.monotonic() - start

        if "User:" not in raw:
            print(f"Turn {turn}: no User: prompt — llm_demo likely crashed (got {len(raw)} bytes)")
            row = {"turn": turn, "error": "no_response",
                   "ctx_size": args.ctx_size, "hardware_tag": args.hardware_tag,
                   "timestamp": datetime.now(timezone.utc).isoformat()}
            with open(output, "a") as f:
                f.write(json.dumps(row) + "\n")
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                print(f"Aborting: {MAX_CONSECUTIVE_FAILURES} consecutive failures")
                break
            continue

        consecutive_failures = 0

        # Extract response text (between prompt echo and "User:")
        response_text = raw.split("User:")[0]
        # Strip ANSI codes and control chars
        response_text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", response_text)
        response_text = re.sub(r"[\r\n]+", " ", response_text).strip()
        # Remove the echoed prompt if present
        if prompt[:30] in response_text:
            idx = response_text.find(prompt[:30])
            response_text = response_text[idx + len(prompt):].strip()
        # Remove "A: " prefix
        if response_text.startswith("A:"):
            response_text = response_text[2:].strip()

        approx_tokens = len(response_text.split())
        metrics = sample_metrics()

        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "turn_number": turn,
            "ctx_size": args.ctx_size,
            "hardware_tag": args.hardware_tag,
            "runtime": "mnn",
            "approx_response_tokens": approx_tokens,
            "total_s": total_s,
            "approx_tok_per_s": approx_tokens / total_s if total_s > 0 else 0,
            **metrics,
            "response_preview": response_text[:200],
        }

        with open(output, "a") as f:
            f.write(json.dumps(row) + "\n")

        zram_tag = f" | zram={metrics['zram_orig_mb']}MB" if metrics.get("zram_orig_mb") else ""
        print(
            f"T{turn:>3} | ~{approx_tokens} tok | "
            f"~{approx_tokens / total_s:.1f} tok/s | "
            f"total={total_s:.0f}s | "
            f"rss={metrics.get('rss_mb', '?')}MB avail={metrics.get('avail_mb', '?')}MB "
            f"temp={metrics.get('temp_c', '?')}C{zram_tag}",
            flush=True,
        )

    # Cleanup
    try:
        os.write(master_fd, b"/exit\n")
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    os.close(master_fd)

    print(f"Done: {output}")


if __name__ == "__main__":
    main()
