#!/usr/bin/env bash
set -euo pipefail
#
# Benchmark IK llama-server on Raspberry Pi 5 (baseline for MNN comparison).
#
# Runs from macOS, SSHs to Pi for execution.
# Writes JSONL results to output/benchmarks/.
#
# Uses non-streaming requests for simple JSON parsing of timings.
# Same prompts as mnn_spike_bench.sh for fair comparison.
#
# Prerequisites on Pi:
#   - ik_llama runtime installed (llama-server at /opt/potato/llama/bin/llama-server)
#   - Model: Qwen3.5-4B-Q4_K_M.gguf at /opt/potato/models/
#
# Usage:
#   export SSHPASS=raspberry
#   ./benchmarks/mnn_spike_bench_ik_baseline.sh
#
# Refs #24

PI_HOST="${PI_HOST:-potato.local}"
PI_USER="${PI_USER:-pi}"
SERVER_BIN="${SERVER_BIN:-/opt/potato/llama/bin/llama-server}"
LIB_DIR="${LIB_DIR:-/opt/potato/llama/lib}"
MODEL="${MODEL:-/opt/potato/models/Qwen3.5-4B-Q4_K_M.gguf}"
PORT="${PORT:-18081}"
CTX_SIZE="${CTX_SIZE:-4096}"
MAX_TOKENS="${MAX_TOKENS:-128}"
REPS="${REPS:-3}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUTPUT_DIR="${REPO_ROOT}/output/benchmarks"
OUTPUT_FILE="${OUTPUT_DIR}/mnn_spike_ik_baseline_${STAMP}.jsonl"

mkdir -p "${OUTPUT_DIR}"

ssh_cmd() {
  sshpass -e ssh -o StrictHostKeyChecking=accept-new "${PI_USER}@${PI_HOST}" "$1"
}

echo "=== IK Baseline Benchmark — Qwen3.5-4B on Pi 5 ==="
echo "  Host:       ${PI_HOST}"
echo "  Server:     ${SERVER_BIN}"
echo "  Model:      ${MODEL}"
echo "  Ctx size:   ${CTX_SIZE}"
echo "  Max tokens: ${MAX_TOKENS}"
echo "  Reps:       ${REPS}"
echo "  Output:     ${OUTPUT_FILE}"
echo ""

# Verify binary and model exist
ssh_cmd "test -f ${SERVER_BIN}" || {
  echo "ERROR: ${SERVER_BIN} not found on Pi." >&2
  exit 1
}
ssh_cmd "test -f ${MODEL}" || {
  echo "ERROR: ${MODEL} not found on Pi. Download with:" >&2
  echo "  huggingface-cli download unsloth/Qwen3.5-4B-GGUF Qwen3.5-4B-Q4_K_M.gguf --local-dir /opt/potato/models/" >&2
  exit 1
}

# Same prompts as MNN benchmark
PROMPTS=(
  "Explain how a lighthouse lamp works in about 100 words."
  "Describe the water cycle and why it matters for agriculture."
  "What happens inside a CPU when it executes a single instruction?"
  "Explain how sourdough bread fermentation works step by step."
  "How does a GPS receiver determine its position from satellite signals?"
)

kill_server() {
  ssh_cmd "
    if [ -f /tmp/ik-bench-${PORT}.pid ]; then
      kill \$(cat /tmp/ik-bench-${PORT}.pid) 2>/dev/null || true
      sleep 1
      kill -9 \$(cat /tmp/ik-bench-${PORT}.pid) 2>/dev/null || true
      rm -f /tmp/ik-bench-${PORT}.pid
    fi
    pkill -f 'llama-server.*${PORT}' 2>/dev/null || true
  "
}

start_server() {
  kill_server
  sleep 1

  # Stop potato service to free memory and port
  ssh_cmd "sudo systemctl stop potato 2>/dev/null || true"
  sleep 2

  echo "Starting llama-server..."
  ssh_cmd "
    nohup env LD_LIBRARY_PATH=${LIB_DIR} \
      ${SERVER_BIN} \
      --model ${MODEL} \
      --host 0.0.0.0 \
      --port ${PORT} \
      --ctx-size ${CTX_SIZE} \
      --threads 4 \
      --cache-type-k q8_0 \
      --cache-type-v q8_0 \
      --flash-attn on \
      --jinja \
      --reasoning-format none \
      --reasoning-budget 0 \
      >/tmp/ik-bench-${PORT}.log 2>&1 &
    echo \$! >/tmp/ik-bench-${PORT}.pid
  "

  # Wait for server to be ready (up to 180s for model load)
  local deadline=$((SECONDS + 180))
  while [ "${SECONDS}" -lt "${deadline}" ]; do
    local code
    code="$(ssh_cmd "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:${PORT}/v1/models 2>/dev/null || echo 0")"
    if [ "${code}" = "200" ]; then
      echo "  Server ready."
      return 0
    fi
    sleep 2
  done

  echo "ERROR: Server failed to start. Last log:" >&2
  ssh_cmd "tail -20 /tmp/ik-bench-${PORT}.log 2>/dev/null" >&2
  return 1
}

run_prompt() {
  local prompt="$1"
  local prompt_idx="$2"

  # Non-streaming request — response includes timings in single JSON
  local payload
  payload="$(python3 -c "
import json
print(json.dumps({
    'model': 'qwen-local',
    'stream': False,
    'temperature': 0,
    'top_p': 1,
    'seed': 42,
    'max_tokens': ${MAX_TOKENS},
    'presence_penalty': 0,
    'frequency_penalty': 0,
    'messages': [
        {'role': 'system', 'content': 'You are a helpful assistant. Answer concisely.'},
        {'role': 'user', 'content': $(python3 -c "import json; print(json.dumps('${prompt}'))")},
    ],
}))")"

  local start_ns
  start_ns="$(date +%s%N)"

  local response
  response="$(ssh_cmd "curl -s --max-time 300 -X POST http://127.0.0.1:${PORT}/v1/chat/completions -H 'Content-Type: application/json' -d '$(echo "${payload}" | sed "s/'/'\\\\''/g")'")"

  local end_ns
  end_ns="$(date +%s%N)"
  local wall_s
  wall_s="$(echo "scale=3; (${end_ns} - ${start_ns}) / 1000000000" | bc)"

  # Parse response
  python3 -c "
import json, sys

resp = json.loads('''${response}''')
timings = resp.get('timings', {})
usage = resp.get('usage', {})
choices = resp.get('choices', [{}])
content = choices[0].get('message', {}).get('content', '') if choices else ''

row = {
    'timestamp': '$(date -u +%Y-%m-%dT%H:%M:%SZ)',
    'runtime': 'ik_llama',
    'model': '$(basename "${MODEL}")',
    'quant': 'Q4_K_M',
    'prompt_idx': ${prompt_idx},
    'prompt_tokens': usage.get('prompt_tokens', timings.get('prompt_n', 0)),
    'decode_tokens': usage.get('completion_tokens', timings.get('predicted_n', 0)),
    'prefill_tps': timings.get('prompt_per_second', 0),
    'decode_tps': timings.get('predicted_per_second', 0),
    'prefill_time_s': timings.get('prompt_ms', 0) / 1000 if timings.get('prompt_ms') else 0,
    'decode_time_s': timings.get('predicted_ms', 0) / 1000 if timings.get('predicted_ms') else 0,
    'total_wall_s': ${wall_s},
    'max_tokens': ${MAX_TOKENS},
    'ctx_size': ${CTX_SIZE},
    'response_preview': content[:200],
}
print(json.dumps(row, ensure_ascii=False))
" || echo "ERROR: Failed to parse response for prompt ${prompt_idx}" >&2
}

# Main benchmark loop
for rep in $(seq 1 "${REPS}"); do
  echo ""
  echo "=== Rep ${rep}/${REPS} ==="

  # Cold start — restart server each rep
  start_server

  # Get RSS after model load
  rss_kb="$(ssh_cmd "ps -C llama-server -o rss= 2>/dev/null || echo 0")"
  rss_mb="$(echo "scale=1; ${rss_kb:-0} / 1024" | bc)"
  echo "  Server RSS after load: ${rss_mb} MB"

  for i in "${!PROMPTS[@]}"; do
    prompt="${PROMPTS[$i]}"
    echo "  Prompt $((i+1))/${#PROMPTS[@]}: ${prompt:0:50}..."

    result="$(run_prompt "${prompt}" "$((i+1))")"

    # Add rep and RSS to the row
    enriched="$(echo "${result}" | python3 -c "
import json, sys
row = json.load(sys.stdin)
row['rep'] = ${rep}
row['rss_mb'] = ${rss_mb}
print(json.dumps(row, ensure_ascii=False))
")"

    echo "${enriched}" >> "${OUTPUT_FILE}"

    # Print summary
    decode_tps="$(echo "${enriched}" | python3 -c "import json,sys; print(json.load(sys.stdin).get('decode_tps', 0))")"
    prefill_tps="$(echo "${enriched}" | python3 -c "import json,sys; print(json.load(sys.stdin).get('prefill_tps', 0))")"
    echo "    Prefill: ${prefill_tps} tok/s, Decode: ${decode_tps} tok/s"
  done

  kill_server
  sleep 2

  # Drop caches before next rep
  ssh_cmd "sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches' 2>/dev/null || true"
done

echo ""
echo "=== Done ==="
echo "Results: ${OUTPUT_FILE}"
