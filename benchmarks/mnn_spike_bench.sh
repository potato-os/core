#!/usr/bin/env bash
set -euo pipefail
#
# Benchmark MNN llm_demo on Raspberry Pi 5.
#
# Runs from macOS, SSHs to Pi for execution.
# Writes JSONL results to output/benchmarks/.
#
# Prerequisites on Pi:
#   - MNN built via mnn_spike_build.sh (llm_demo at /tmp/mnn-build/llm_demo)
#   - Model downloaded: taobao-mnn/Qwen3.5-4B-MNN at /tmp/qwen35-4b-mnn/
#
# Usage:
#   export SSHPASS=raspberry
#   ./benchmarks/mnn_spike_bench.sh
#
# Refs #24

PI_HOST="${PI_HOST:-potato.local}"
PI_USER="${PI_USER:-pi}"
LLM_DEMO="${LLM_DEMO:-/tmp/mnn-build/llm_demo}"
MODEL_DIR="${MODEL_DIR:-/tmp/qwen35-4b-mnn}"
MAX_TOKENS="${MAX_TOKENS:-128}"
REPS="${REPS:-3}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUTPUT_DIR="${REPO_ROOT}/output/benchmarks"
OUTPUT_FILE="${OUTPUT_DIR}/mnn_spike_${STAMP}.jsonl"

mkdir -p "${OUTPUT_DIR}"

ssh_cmd() {
  sshpass -e ssh -o StrictHostKeyChecking=accept-new "${PI_USER}@${PI_HOST}" "$1"
}

echo "=== MNN Benchmark — Qwen3.5-4B on Pi 5 ==="
echo "  Host:       ${PI_HOST}"
echo "  Binary:     ${LLM_DEMO}"
echo "  Model:      ${MODEL_DIR}"
echo "  Max tokens: ${MAX_TOKENS}"
echo "  Reps:       ${REPS}"
echo "  Output:     ${OUTPUT_FILE}"
echo ""

# Verify binary and model exist
ssh_cmd "test -f ${LLM_DEMO}" || {
  echo "ERROR: ${LLM_DEMO} not found on Pi. Run mnn_spike_build.sh first." >&2
  exit 1
}
ssh_cmd "test -f ${MODEL_DIR}/config.json" || {
  echo "ERROR: ${MODEL_DIR}/config.json not found on Pi. Download model first." >&2
  exit 1
}

# Kill any existing llm_demo
ssh_cmd "pkill -f llm_demo 2>/dev/null || true"
sleep 1

# Prompts — one per line in a temp file on Pi
# llm_demo benchmark mode: reads prompts from file, one per line
PROMPTS=(
  "Explain how a lighthouse lamp works in about 100 words."
  "Describe the water cycle and why it matters for agriculture."
  "What happens inside a CPU when it executes a single instruction?"
  "Explain how sourdough bread fermentation works step by step."
  "How does a GPS receiver determine its position from satellite signals?"
)

echo "Creating prompt file on Pi..."
PROMPT_FILE="/tmp/mnn_bench_prompts.txt"
prompt_content=""
for p in "${PROMPTS[@]}"; do
  prompt_content+="${p}"$'\n'
done
echo "${prompt_content}" | ssh_cmd "cat > ${PROMPT_FILE}"

for rep in $(seq 1 "${REPS}"); do
  echo ""
  echo "=== Rep ${rep}/${REPS} ==="

  # Drop caches for cold start
  ssh_cmd "sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches' 2>/dev/null || true"
  sleep 2

  # Run llm_demo in benchmark mode with thinking disabled (5th arg)
  # Capture full output for parsing
  echo "Running llm_demo..."
  load_start="$(date +%s%N)"
  raw_output="$(ssh_cmd "${LLM_DEMO} ${MODEL_DIR}/config.json ${PROMPT_FILE} ${MAX_TOKENS} no_thinking 2>&1")" || {
    echo "WARNING: llm_demo exited with error on rep ${rep}" >&2
    echo "${raw_output}" >&2
    continue
  }
  load_end="$(date +%s%N)"
  total_wall_s="$(echo "scale=3; (${load_end} - ${load_start}) / 1000000000" | bc)"

  # Parse MNN benchmark output
  # Format:
  #   prompt tokens num = X
  #   decode tokens num = X
  #   prefill time = X.XX s
  #    decode time = X.XX s
  #   prefill speed = X.XX tok/s
  #    decode speed = X.XX tok/s
  prompt_tokens="$(echo "${raw_output}" | grep -oP 'prompt tokens num = \K[0-9]+' || echo "0")"
  decode_tokens="$(echo "${raw_output}" | grep -oP 'decode tokens num = \K[0-9]+' || echo "0")"
  prefill_time="$(echo "${raw_output}" | grep -oP 'prefill time = \K[0-9.]+' || echo "0")"
  decode_time="$(echo "${raw_output}" | grep -oP 'decode time = \K[0-9.]+' || echo "0")"
  prefill_tps="$(echo "${raw_output}" | grep -oP 'prefill speed = \K[0-9.]+' || echo "0")"
  decode_tps="$(echo "${raw_output}" | grep -oP 'decode speed = \K[0-9.]+' || echo "0")"

  # Get RSS of llm_demo process (it may have exited, so this is best-effort)
  rss_kb="$(ssh_cmd "ps -C llm_demo -o rss= 2>/dev/null || echo 0")"
  rss_mb="$(echo "scale=1; ${rss_kb:-0} / 1024" | bc)"

  # Extract a preview of generated text (first 200 chars before the stats block)
  response_preview="$(echo "${raw_output}" | sed -n '/^#/q;p' | tail -5 | tr '\n' ' ' | head -c 200)"

  echo "  Prefill: ${prefill_tps} tok/s (${prompt_tokens} tokens)"
  echo "  Decode:  ${decode_tps} tok/s (${decode_tokens} tokens)"
  echo "  RSS:     ${rss_mb} MB"
  echo "  Wall:    ${total_wall_s} s"

  # Write JSONL row
  python3 -c "
import json, sys
row = {
    'timestamp': '$(date -u +%Y-%m-%dT%H:%M:%SZ)',
    'runtime': 'mnn',
    'runtime_version': '3.4.1',
    'model': 'Qwen3.5-4B-MNN',
    'quant': '4bit',
    'rep': ${rep},
    'prompt_tokens': int('${prompt_tokens}' or 0),
    'decode_tokens': int('${decode_tokens}' or 0),
    'prefill_time_s': float('${prefill_time}' or 0),
    'decode_time_s': float('${decode_time}' or 0),
    'prefill_tps': float('${prefill_tps}' or 0),
    'decode_tps': float('${decode_tps}' or 0),
    'total_wall_s': float('${total_wall_s}' or 0),
    'rss_mb': float('${rss_mb}' or 0),
    'max_tokens': ${MAX_TOKENS},
    'num_prompts': ${#PROMPTS[@]},
    'response_preview': '''${response_preview}'''[:200],
}
print(json.dumps(row, ensure_ascii=False))
" >> "${OUTPUT_FILE}"

done

# Cleanup
ssh_cmd "pkill -f llm_demo 2>/dev/null || true"
ssh_cmd "rm -f ${PROMPT_FILE}"

echo ""
echo "=== Done ==="
echo "Results: ${OUTPUT_FILE}"
