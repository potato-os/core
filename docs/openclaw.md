# Installing OpenClaw on Potato OS

OpenClaw is an open-source AI agent framework that turns your Potato OS into a local coding assistant. This guide walks you through the installation — it takes about 5 minutes.

## What you need

- Potato OS running on your Raspberry Pi 5 (`http://potato.local` loads in your browser)
- Internet connection on the Pi (for downloading packages)
- A browser on the same network

## Install OpenClaw

### 1. Open the Potato OS dashboard

Open `http://potato.local` in your browser. You should see the Potato OS interface with the sidebar on the left and the chat area on the right.

<img src="assets/openclaw_steps/01-potato-home.jpg" alt="Potato OS dashboard with sidebar and chat" width="680">

### 2. Open the terminal

Click the **Terminal** button in the sidebar (highlighted below). This opens a browser-based terminal connected to your Pi — no SSH needed.

<img src="assets/openclaw_steps/02-click-terminal.jpg" alt="Potato OS dashboard with Terminal button highlighted" width="680">

### 3. Terminal ready

The terminal opens in a modal window. You should see a shell prompt (`pi@potato:~$`) and a **Connected** status at the top.

<img src="assets/openclaw_steps/03-terminal-open.jpg" alt="Terminal modal showing connected shell prompt" width="680">

### 4. Run the installer

Copy and paste this command into the terminal and press **Enter**:

```
curl -fsSL https://raw.githubusercontent.com/slomin/potato-os/main/bin/install_openclaw.sh | sudo bash
```

<img src="assets/openclaw_steps/04-install-command.jpg" alt="Terminal with install command typed" width="680">

### 5. Wait for installation

The installer runs through 7 phases: Node.js, OpenClaw, configuration, skills, performance tuning, systemd service, and verification. This takes about 5 minutes on a fresh install.

<img src="assets/openclaw_steps/05-install-progress.jpg" alt="Terminal showing installation phases in progress" width="680">

### 6. Installation complete

When finished, the installer prints **"OpenClaw is running!"** along with your dashboard URL. The URL includes an access token — copy it, you will need it in the next step.

<img src="assets/openclaw_steps/06-install-complete.jpg" alt="Terminal showing OpenClaw is running with dashboard URL" width="680">

### 7. Open the OpenClaw dashboard

Paste the dashboard URL into a new browser tab. The OpenClaw Control UI loads with the sidebar navigation on the left and the chat area in the center.

<img src="assets/openclaw_steps/07-dashboard.jpg" alt="OpenClaw dashboard loaded with Assistant ready to chat" width="680">

### 8. Start chatting

Type a message and press **Enter**. OpenClaw sends it to the local model running on your Pi and streams back a response. You now have a local AI coding assistant on your Raspberry Pi.

<img src="assets/openclaw_steps/08-chat-ready.jpg" alt="OpenClaw chat showing a question and response from the local model" width="680">

## How it works

This is a **standard OpenClaw installation** — the same version you'd run anywhere else. The only difference is the configuration: stock OpenClaw assumes a cloud model with 100k+ tokens of context, but your Pi runs a local 2B model with 16k. The installer writes a config that strips the prompt down to fit:

- **Bootstrap skipped** — OpenClaw normally spends ~5,000 tokens on startup instructions. The config disables this.
- **All 51 bundled skills disabled** — Skills like web search, code analysis, and file editing each inject hundreds of tokens into every prompt. They're disabled so none of that gets loaded.
- **Minimal tool profile** — Only essential tools are enabled, keeping tool descriptions short.
- **Memory search off** — Prevents the agent from pulling in past conversation context that would eat into your budget.
- **Aggressive compaction** — When the conversation gets long, OpenClaw summarises earlier messages to stay within the window.

**What this means in practice:** You get a capable chat assistant that can answer questions and have conversations, but it won't have OpenClaw's full agent toolkit (no file editing, no web browsing, no multi-step coding workflows). Think of it as OpenClaw on a diet — same engine, smaller appetite. Fast, local, and private, but simpler.

**Note:** The current configuration has been tested with a 16,384-token context window. I have an open ticket to explore the limits — larger context budgets, re-enabling skills, and finding the sweet spot for different models. For now, 16,384 tokens is the proven baseline.

If you connect a larger model in the future (or run on a Pi with more headroom), you can expand the budget and re-enable features:

| Setting | Default | Override with |
|---------|---------|---------------|
| Context window | 16,384 tokens | `POTATO_CONTEXT_WINDOW` |
| Max output tokens | 4,096 tokens | `POTATO_MAX_TOKENS` |

```bash
POTATO_CONTEXT_WINDOW=32768 curl -fsSL https://raw.githubusercontent.com/slomin/potato-os/main/bin/install_openclaw.sh | sudo bash
```

The configuration lives at `~/.openclaw/openclaw.json`. The gateway token (needed to access the dashboard) is stored there under `gateway.auth.token`.

## Uninstall

To remove OpenClaw completely:

```
curl -fsSL https://raw.githubusercontent.com/slomin/potato-os/main/bin/uninstall_openclaw.sh | sudo bash
```

This stops the gateway, removes the package, restores disabled skills, and cleans up configuration. Node.js is kept by default — set `REMOVE_NODEJS=1` to remove it too.

## Troubleshooting

**Dashboard doesn't load:**
- Check that the gateway is running: open the terminal and run `systemctl --user status openclaw-gateway`
- Restart it with `systemctl --user restart openclaw-gateway`

**Model not responding:**
- Make sure Potato OS is running and the model is loaded — check `http://potato.local` for a green **CONNECTED** status

**Lost your dashboard token:**
- Open the terminal and run: `grep token ~/.openclaw/openclaw.json`
- Or re-run the installer — it preserves your config and prints the URL again

**Need to start over:**
- Uninstall with the command above, then re-install
