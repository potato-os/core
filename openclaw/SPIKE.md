# Spike: OpenClaw on Potato OS (#115)

## Result: Working

OpenClaw v2026.3.13 runs on Potato OS (Pi 5, 16GB) using the local Qwen 30B model.
Agent responds via `openclaw agent --local` in embedded mode.

## What works

- OpenClaw agent sends messages to the local model via `/v1/chat/completions`
- Gateway runs on port 3080 (LAN-accessible, `0.0.0.0`)
- Dashboard accessible at `http://potato.local:3080/`
- System prompt reduced from 9,528 chars to 6,401 chars (skills removed)
- ~14.4k tokens available for conversation on 16k context window
- Memory overhead: ~540MB RSS when gateway is running, 0 when using embedded mode
- Gateway starts in ~55s on Pi (plugin loading is slow on ARM)

## Configuration

### Files

| File | Purpose |
|------|---------|
| `openclaw.json` | Main config (deploy to `~/.openclaw/openclaw.json` on Pi) |
| `workspace/AGENTS.md` | Agent persona (deploy to `~/.openclaw/workspace/AGENTS.md`) |
| `workspace/SOUL.md` | Tone guidance (deploy to `~/.openclaw/workspace/SOUL.md`) |
| `system_prompt.md` | Captured system prompt for reference (not deployed) |

### Key config decisions

| Setting | Value | Why |
|---------|-------|-----|
| `models.providers.potato.api` | `openai-completions` | Matches our `/v1/chat/completions` endpoint. `openai-responses` did NOT work (zero tokens, silent failure) |
| `tools.profile` | `minimal` | Only `session_status` tool. Reduces tool schema injection from ~2k to 89 chars |
| `skills.*` | All disabled | Saves ~2,500 chars of system prompt per turn. Required physically renaming SKILL.md files on disk (config alone didn't work) |
| `gateway.bind` | `lan` | Allows LAN access from Mac browser |
| `gateway.controlUi.dangerouslyDisableDeviceAuth` | `true` | Required for HTTP (non-HTTPS) dashboard access |
| `agents.defaults.bootstrapMaxChars` | `400` | Prevents large workspace files from bloating the prompt |
| `agents.defaults.bootstrapTotalMaxChars` | `1200` | Total cap across all workspace files |

### Deployment note

The `gateway.auth.token` in `openclaw.json` uses `${OPENCLAW_GATEWAY_TOKEN}` as a placeholder.
On the Pi, either set this env var or let OpenClaw auto-generate a token during `openclaw onboard`.

## Setup steps (on a fresh Potato OS Pi)

```bash
# 1. Install Node.js 24
curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -
sudo apt install -y nodejs lsof

# 2. Install OpenClaw
npm install -g openclaw@latest

# 3. Deploy config and workspace files from this repo
mkdir -p ~/.openclaw/workspace
cp /path/to/repo/openclaw/openclaw.json ~/.openclaw/openclaw.json
cp /path/to/repo/openclaw/workspace/AGENTS.md ~/.openclaw/workspace/AGENTS.md
cp /path/to/repo/openclaw/workspace/SOUL.md ~/.openclaw/workspace/SOUL.md

# 4. Set gateway mode
openclaw config set gateway.mode local

# 5. Disable skills on disk (config alone doesn't work)
for s in healthcheck node-connect skill-creator weather; do
  sudo mv /usr/lib/node_modules/openclaw/skills/$s/SKILL.md \
          /usr/lib/node_modules/openclaw/skills/$s/SKILL.md.disabled 2>/dev/null
done

# 6. Enable systemd user session persistence
sudo loginctl enable-linger $(whoami)

# 7. Add performance env vars
cat >> ~/.bashrc <<'EOF'
export NODE_COMPILE_CACHE=/var/tmp/openclaw-compile-cache
mkdir -p /var/tmp/openclaw-compile-cache
export OPENCLAW_NO_RESPAWN=1
EOF
source ~/.bashrc

# 8. Install and start gateway service
openclaw gateway install
# Edit ~/.config/systemd/user/openclaw-gateway.service:
#   Change --port 18789 to --port 3080
#   Change OPENCLAW_GATEWAY_PORT=18789 to 3080
#   Add: Environment=NODE_COMPILE_CACHE=/var/tmp/openclaw-compile-cache
#   Add: Environment=OPENCLAW_NO_RESPAWN=1
systemctl --user daemon-reload
systemctl --user restart openclaw-gateway

# 9. Test (wait ~55s for gateway startup)
openclaw agent --local --agent main --message "hi"
```

## Limitations

### System prompt overhead
- 6,401 chars (~1,600 tokens) of fixed system prompt on every turn
- ~4,500 chars of this is hardcoded in the OpenClaw binary (Reply Tags, Messaging, Silent Replies, Heartbeats, Safety) and cannot be reduced via config
- Leaves ~14.4k tokens for conversation on the 16k context window
- Multi-turn conversations will hit context limits quickly

### Skills config bug
- `skills.entries.*.enabled: false` and `skills.allowBundled: []` don't prevent skill injection in embedded mode
- Had to physically rename `SKILL.md` files on disk to stop injection
- This is a known issue: GitHub #24994

### Gateway vs embedded mode
- `openclaw agent --local` (embedded mode) works reliably
- Gateway-routed agent (`openclaw agent --agent main`) times out — likely session lock or format issue
- Dashboard chat may have the same issue
- Gateway itself runs fine for status/dashboard

### `openai-responses` vs `openai-completions`
- Our API exposes `/v1/chat/completions` (OpenAI Chat Completions format)
- `api: "openai-completions"` works correctly
- `api: "openai-responses"` silently returns zero tokens (never calls our API)
- There's a `/v1/responses` adapter in `app/routes/chat.py` (reverted for now) that could enable `openai-responses` mode in the future

### Memory
- Gateway process uses ~540MB RSS (Node.js overhead)
- Embedded mode (`--local`) exits after each request — no persistent memory cost
- On 16GB Pi this is fine; on 4GB Pi it would be tight alongside llama-server

## Recommendations for follow-up

1. **Install script** (`bin/install_openclaw.sh`) — automate the setup steps above
2. **Uninstall script** (`bin/uninstall_openclaw.sh`) — clean removal
3. **Gateway agent fix** — investigate why gateway-routed agent times out vs embedded mode
4. **Context window** — consider setting `contextWindow` to match actual model (some models support 32k+)
5. **`/v1/responses` adapter** — re-add if OpenClaw moves to `openai-responses` as default
