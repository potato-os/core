#!/usr/bin/env python3
"""Multi-turn conversation runner for overnight context sweep.

Called by overnight_context_sweep.sh for each context size.
Handles all JSON properly — no bash string escaping nightmares.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import re
import urllib.request
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

MAX_TURNS = 120  # 64K context needs ~95 turns at ~700 tok/turn


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--ctx-size", type=int, required=True)
    p.add_argument("--hardware-tag", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--pi-user", default="pi")
    p.add_argument("--pi-pass", default="raspberry")
    return p.parse_args()


def ssh_cmd(host: str, user: str, password: str, cmd: str) -> str:
    try:
        r = subprocess.run(
            ["sshpass", "-p", password, "ssh", "-o", "StrictHostKeyChecking=no",
             "-o", "ConnectTimeout=10", f"{user}@{host}", cmd],
            capture_output=True, text=True, timeout=15,
        )
        return r.stdout.strip()
    except Exception:
        return ""


def sample_metrics(host: str, user: str, password: str, port: int) -> dict:
    pid = ssh_cmd(host, user, password,
                  f"pgrep -f 'llama-server.*{port}' | head -1")
    rss_raw = ssh_cmd(host, user, password,
                      f"ps -o rss= -p {pid}") if pid else ""
    free_raw = ssh_cmd(host, user, password, "free -m")
    temp_raw = ssh_cmd(host, user, password, "vcgencmd measure_temp")
    zram_raw = ssh_cmd(host, user, password, "cat /sys/block/zram0/mm_stat")

    rss = None
    if rss_raw.strip():
        try:
            rss = int(rss_raw.strip()) // 1024
        except ValueError:
            pass

    avail = None
    swap_used = None
    for line in free_raw.splitlines():
        parts = line.split()
        if line.lower().startswith("mem:") and len(parts) >= 7:
            try:
                avail = int(parts[6])
            except ValueError:
                pass
        if line.lower().startswith("swap:") and len(parts) >= 3:
            try:
                swap_used = int(parts[2])
            except ValueError:
                pass

    temp = None
    m = re.search(r"temp=([\d.]+)", temp_raw)
    if m:
        try:
            temp = float(m.group(1))
        except ValueError:
            pass

    zram_orig = 0
    zram_compr = 0
    if zram_raw.strip():
        zparts = zram_raw.split()
        if len(zparts) >= 2:
            try:
                zram_orig = int(zparts[0]) // (1024 * 1024)
                zram_compr = int(zparts[1]) // (1024 * 1024)
            except ValueError:
                pass

    return {
        "rss_mb": rss,
        "avail_mb": avail,
        "swap_used_mb": swap_used,
        "temp_c": temp,
        "zram_orig_mb": zram_orig,
        "zram_compr_mb": zram_compr,
    }


def send_chat(host: str, port: int, messages: list[dict]) -> dict:
    payload = {
        "model": "qwen-local",
        "stream": False,
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
    req = urllib.request.Request(
        f"http://{host}:{port}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    start = time.monotonic()
    with urllib.request.urlopen(req, timeout=600) as resp:
        body = json.loads(resp.read())
    total_s = time.monotonic() - start

    choice = (body.get("choices") or [{}])[0]
    timings = body.get("timings", {})
    content = choice.get("message", {}).get("content", "")

    return {
        "content": content,
        "finish_reason": choice.get("finish_reason", ""),
        "total_s": total_s,
        "prompt_n": timings.get("prompt_n", 0),
        "prompt_ms": timings.get("prompt_ms", 0),
        "prompt_per_second": timings.get("prompt_per_second", 0),
        "predicted_n": timings.get("predicted_n", 0),
        "predicted_ms": timings.get("predicted_ms", 0),
        "predicted_per_second": timings.get("predicted_per_second", 0),
        "n_past": timings.get("n_past", 0),
        "n_ctx": timings.get("n_ctx", 0),
    }


def main() -> None:
    args = parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    prev_n_past = 0

    for turn in range(1, MAX_TURNS + 1):
        prompt = STORY_PROMPTS[(turn - 1) % len(STORY_PROMPTS)]
        messages.append({"role": "user", "content": prompt})

        try:
            result = send_chat(args.host, args.port, messages)
        except Exception as e:
            print(f"Turn {turn}: FAILED — {e}")
            row = {"turn": turn, "error": str(e), "ctx_size": args.ctx_size,
                   "hardware_tag": args.hardware_tag,
                   "timestamp": datetime.now(timezone.utc).isoformat()}
            with open(output, "a") as f:
                f.write(json.dumps(row) + "\n")
            break

        messages.append({"role": "assistant", "content": result["content"]})

        metrics = sample_metrics(args.host, args.pi_user, args.pi_pass, args.port)
        ttft_s = result["prompt_ms"] / 1000 if result["prompt_ms"] else 0

        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "turn_number": turn,
            "ctx_size": args.ctx_size,
            "hardware_tag": args.hardware_tag,
            "n_past": result["n_past"],
            "n_ctx": result["n_ctx"],
            "prompt_n": result["prompt_n"],
            "prompt_per_second": result["prompt_per_second"],
            "predicted_n": result["predicted_n"],
            "predicted_per_second": result["predicted_per_second"],
            "predicted_ms": result["predicted_ms"],
            "prompt_ms": result["prompt_ms"],
            "ttft_s": ttft_s,
            "total_s": result["total_s"],
            "finish_reason": result["finish_reason"],
            **metrics,
            "response_preview": result["content"][:200],
        }

        with open(output, "a") as f:
            f.write(json.dumps(row) + "\n")

        zram_tag = f" | zram={metrics['zram_orig_mb']}MB" if metrics.get("zram_orig_mb") else ""
        print(
            f"T{turn:>2} | n_past={result['n_past']}/{result['n_ctx']} | "
            f"pp={result['prompt_n']}@{result['prompt_per_second']:.0f}t/s | "
            f"gen={result['predicted_per_second']:.1f}t/s | "
            f"TTFT={ttft_s:.1f}s | total={result['total_s']:.0f}s | "
            f"rss={metrics.get('rss_mb','?')}MB avail={metrics.get('avail_mb','?')}MB "
            f"temp={metrics.get('temp_c','?')}C{zram_tag}",
            flush=True,
        )

        # Detect context shift — n_past dropped (any significant decrease)
        if prev_n_past > 0 and result["n_past"] < prev_n_past - 500:
            print(f"CONTEXT SHIFT at turn {turn}: n_past dropped {prev_n_past} → {result['n_past']}")
            print(f"Context limit reached. Stopping.")
            break

        # Stop if context is full (shouldn't happen if shift is on, but safety net)
        if result["n_past"] >= args.ctx_size:
            print(f"Context full at turn {turn}")
            break

        prev_n_past = result["n_past"]

    print(f"Done: {output}")


if __name__ == "__main__":
    main()
