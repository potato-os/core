#!/usr/bin/env bash
set -uo pipefail

# Overnight context window sweep — runs on Mac, talks to Pi.
# Usage:
#   ./tests/e2e/overnight_context_sweep.sh potato.local pi5-16gb
#   ./tests/e2e/overnight_context_sweep.sh ssd.local pi5-8gb-ssd

PI_HOST="${1:?Usage: $0 <host> <hardware-tag>}"
HW_TAG="${2:?Usage: $0 <host> <hardware-tag>}"
PI_USER="pi"
PI_PASS="raspberry"
PORT=18081
MODEL="/opt/potato/models/Qwen3-30B-A3B-Instruct-2507-Q3_K_S-2.66bpw.gguf"
STAMP="overnight_30b_${HW_TAG}"
OUTPUT_DIR="output/benchmarks"
CTX_SIZES="${CTX_SIZES:-32768 49152 65536}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "${OUTPUT_DIR}"

ssh_pi() {
  sshpass -p "${PI_PASS}" ssh \
    -o StrictHostKeyChecking=no \
    -o ConnectTimeout=10 \
    "${PI_USER}@${PI_HOST}" "$1" 2>/dev/null || true
}

log() {
  printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

kill_all_llama() {
  log "Killing all llama-server on ${PI_HOST}..."
  ssh_pi "echo ${PI_PASS} | sudo -S pkill -9 -f llama-server || true"
  sleep 5
  log "Memory: $(ssh_pi 'free -m' | grep Mem)"
}

start_server() {
  local ctx_size="$1"
  log "Starting server: ctx=${ctx_size} on ${PI_HOST}:${PORT}"

  ssh_pi "mkdir -p /opt/potato/state"
  ssh_pi "LD_LIBRARY_PATH=/opt/potato/llama/lib GGML_BACKEND_DIR=/opt/potato/llama/lib nohup /opt/potato/llama/bin/llama-server \
    --model ${MODEL} \
    --host 0.0.0.0 --port ${PORT} \
    --ctx-size ${ctx_size} \
    --cache-ram 1024 --parallel 1 --threads 4 \
    --cache-type-k q8_0 --cache-type-v q8_0 \
    --jinja --flash-attn on --no-warmup \
    --reasoning-format none --reasoning-budget 0 \
    --chat-template-kwargs '{\"enable_thinking\": false}' \
    >/opt/potato/state/bench-${PORT}.log 2>&1 &"

  local deadline=$((SECONDS + 300))
  while [ "${SECONDS}" -lt "${deadline}" ]; do
    local code
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://${PI_HOST}:${PORT}/v1/models" 2>/dev/null || echo 0)
    if [ "${code}" = "200" ]; then
      log "Server ready (ctx=${ctx_size})"
      ssh_pi "grep -i 'KV.*size\|kv_cache' /opt/potato/state/bench-${PORT}.log" || true
      return 0
    fi
    sleep 2
  done
  log "FAILED: server did not start within 5 min"
  ssh_pi "tail -20 /opt/potato/state/bench-${PORT}.log"
  return 1
}

run_conversation() {
  local ctx_size="$1"
  local jsonl="${OUTPUT_DIR}/ctx_window_${STAMP}_${ctx_size}_${HW_TAG}.jsonl"

  log "=== Conversation: ctx=${ctx_size} → ${jsonl} ==="

  # Delegate to Python — it handles JSON properly
  python3 "${SCRIPT_DIR}/overnight_conversation.py" \
    --host "${PI_HOST}" \
    --port "${PORT}" \
    --ctx-size "${ctx_size}" \
    --hardware-tag "${HW_TAG}" \
    --output "${jsonl}" \
    --pi-user "${PI_USER}" \
    --pi-pass "${PI_PASS}"

  log "=== Done: ctx=${ctx_size} ==="
}

# ── Main ─────────────────────────────────────────────────────────────────

log "Overnight context sweep: ${PI_HOST} (${HW_TAG})"
log "Configs: ${CTX_SIZES}"

for ctx_size in ${CTX_SIZES}; do
  kill_all_llama
  if start_server "${ctx_size}"; then
    run_conversation "${ctx_size}" || log "Conversation failed for ctx=${ctx_size}"
  else
    log "SKIPPING ctx=${ctx_size} — server failed to start"
  fi
  sleep 10
done

kill_all_llama
log "=== ALL DONE ==="
