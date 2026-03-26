# Platform Architecture — Design Proposal v0.2

> **Status:** Proposal — everything here is subject to change.
> **v0.1** was the initial MVP: a single-purpose chat UI on a Pi with a local llama runtime.
> **v0.2** is the vision for what Potato OS becomes next.

## Vision

Potato OS is a local AI operating system for Raspberry Pi — a hobby project and a passion for making local AI accessible, fun, and truly yours.

It exists because running AI locally should be easy, not a chore. You shouldn't need a CS degree to run a model on your own hardware, and you shouldn't have to pay a subscription or give up your privacy to use AI. Potato OS is for people who want to tinker, experiment, and own what they build.

Everything flows from a few core beliefs:
- **Privacy** — your data, your models, your conversations. Nothing leaves your device unless you want it to.
- **Freedom** — no one tells you what you can or can't do with your own AI. No content policies, no usage limits, no terms of service that change overnight.
- **Affordability** — local AI shouldn't cost a fortune. A Raspberry Pi and an open model gets you surprisingly far. Potato OS is named after the hardware it runs on — making the most of modest setups.
- **Fun** — this should be enjoyable. Easy to set up, easy to use, easy to tinker with. If it's not fun, something's wrong.

## Proposed Stack

```
┌──────────────────────────────────────────────────────┐
│                      Potato OS                       │
│                                                      │
│  Mother (supervisor / FDIR daemon)                   │
│  ├── Prime directive: preserve and restore inference │
│  ├── FDIR loop: Detect → Isolate → Recover           │
│  ├── Deterministic mode (default)                    │
│  └── Agentic mode (reasons via Inferno)              │
│                                                      │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐               │
│  │  Chat   │  │  App A  │  │  App B  │               │
│  │ (demo)  │  │ (no UI) │  │(has UI) │               │
│  └────┬────┘  └────┬────┘  └────┬────┘               │
│       │            │            │                    │
│  ┌────▼────────────▼────────────▼────┐               │
│  │            Inferno                │               │
│  │    (inference orchestrator)       │               │
│  │    local or remote, multi-model,  │               │
│  │    scheduler, network-transparent │               │
│  └───────────────────────────────────┘               │
│                                                      │
│  Dashboard (optional web UI)                         │
│  └── just a window into the system                   │
└──────────────────────────────────────────────────────┘
```

---

## Mother — The Supervisor

Mother is the system's always-on supervisor — a compiled, self-contained, small and efficient daemon that implements a NASA-style FDIR (Fault Detection, Isolation, and Recovery) loop. In CS terms: a supervisor in the Erlang/OTP lineage, purpose-built for a single-board computer running in a closet with no human nearby.

Mother has one prime directive and follows it absolutely.

She will wake subsystems before they're needed. She will reallocate resources without asking. She will bring Inferno back from the dead at 3 AM on a Tuesday. Inference is the priority. All other concerns are secondary.

**Mother ships with Inferno, not with Potato.** She knows nothing about apps, the dashboard, or the Potato daemon. She is Inferno's guardian and works standalone — on a Pi running Potato, on a desktop GPU server with no Potato anywhere near it, on any device that runs Inferno. This is a hard architectural boundary.

### Prime Directive

Preserve and restore inference capability. At minimum: one model loaded, Inferno responding to health checks, clients able to make inference requests. Mother does whatever it takes to maintain this.

### FDIR Loop

FDIR originated in spacecraft systems engineering at NASA and ESA. Spacecraft operate in environments where human intervention takes minutes to hours (or is impossible), so onboard systems must autonomously detect faults, isolate the failing component, and recover without human input. Mother applies this same pattern to a Pi running unattended in a closet — same problem, smaller scale.

```
    ┌──────────┐
    │  Detect  │  Is Inferno responding to health checks?
    └────┬─────┘  Is a model loaded and ready?
         │        Can a test inference request complete?
         │
    ┌────▼─────┐
    │  Isolate │  What failed? Process crash? Model corruption?
    └────┬─────┘  Disk full? OOM kill? Network interface down?
         │        GPU/NPU hang? Memory leak?
         │
    ┌────▼─────┐
    │  Recover │  Restart Inferno process
    └────┬─────┘  Redownload corrupted model
         │        Clean disk to free space
         │        Kill runaway processes
         │        Reconfigure and retry
         │
    ┌────▼─────┐
    │  Verify  │  Did the fix work?
    └────┬─────┘  Run the same health checks from Detect.
         │        If yes → return to monitoring.
         │        If no → escalate (try next recovery strategy,
         └──────→   or in the worst case, reboot)
```

### Two Proposed Modes

**Deterministic (default)** — a fixed recovery playbook. Ordered list of recovery strategies, tried in sequence. Predictable, auditable, no surprises. Think systemd restart policies with smarter ordering and dependency awareness. Every action is logged. No intelligence required — this mode works even if Inferno is completely dead.

Example playbook:
1. Health check fails → restart Inferno process
2. Still failing → check disk space, clean temp files if needed, retry
3. Still failing → check if model file exists and is valid, redownload if corrupted
4. Still failing → kill all non-essential processes, reclaim memory, retry
5. Still failing → full system reboot
6. Still failing → enter degraded mode, log error, wait for human

**Agentic** — Mother uses Inferno itself to reason about what's wrong and takes corrective action. She reads system logs, inspects process states, analyzes error patterns, and generates targeted fixes. When Inferno is dead, she falls back to deterministic mode to bring it back — then she has her brain again.

This mode is more powerful but less predictable. It's opt-in, not default. The deterministic playbook is the safety net that's always there.

Mother can also ship with bundled knowledge — documentation about common failure modes, hardware quirks, and recovery procedures. In agentic mode, she consults these alongside live system data to make better diagnostic decisions.

### What Mother Is Not

- Not an app manager — she doesn't know what apps are running
- Not a network manager — she doesn't manage communications
- Not part of Potato OS — she ships with Inferno
- Not a general-purpose supervisor — she has exactly one job

---

## Inferno — The Inference Orchestrator

Not just "llama-server with a name." Inferno is a standalone inference service designed to be network-transparent from day one. It abstracts multiple inference backends — llama.cpp, ik_llama, MNN, tinygrad, and whatever else works — behind a unified API. Whatever backend performs best on the target hardware, Inferno uses it.

### Proposed Capabilities

- **Model management** — load, unload, swap between models, advertise available models to the network
- **Scheduler** — queue and prioritize inference requests from multiple concurrent apps. On a Pi with one model loaded, requests are serialized — the scheduler decides who goes next
- **Network-transparent** — runs on the same Pi or on a separate device on the LAN (later internet). The API is the same either way
- **Multi-client** — multiple Potato OS instances can share one Inferno. A beefy desktop running Inferno can serve inference to a fleet of lightweight Pis
- **Service discovery** — advertises itself and available models to the network so Potatoes can find it automatically

### Deployment Scenarios

```
Scenario 1: Self-contained Pi
┌──────────────┐
│  Potato OS   │
│  ┌────────┐  │
│  │Inferno │  │  Inferno runs locally with a small model.
│  │Qwen 3B │  │  Mother guards it on the same device.
│  └────────┘  │  Simple, self-sufficient, no network needed.
└──────────────┘

Scenario 2: Distributed
┌──────────────┐         ┌──────────────┐
│  Potato OS   │         │   Inferno    │
│  (Pi 4 2GB)  │────────▶│  (Desktop)   │
│  no local    │   LAN   │  GPT-OSS 120B│  Potato has no local inference.
│  inferno     │         │  Qwen 30B    │  All Model Steps go to the remote
└──────────────┘         └──────────────┘  Inferno over the network.
                                           Mother guards Inferno on the desktop.

Scenario 3: Hybrid
┌──────────────┐         ┌──────────────┐
│  Potato OS   │         │   Inferno    │
│  ┌────────┐  │         │  (remote)    │
│  │Inferno │  │────────▶│  big models  │  Local Inferno for fast, simple
│  │(local) │  │   WAN   │              │  inference. Remote Inferno for
│  │small   │  │         │              │  heavy reasoning. Potato routes
│  └────────┘  │         └──────────────┘  Model Steps to the right one.
│  local=fast  │
│  remote=smart│
└──────────────┘
```

### Design Principle

Even when Inferno runs locally, apps talk to it through the same API as if it were remote. Local → remote becomes a configuration change, not an architecture change. An app developed on a laptop against a local Inferno deploys to a Pi talking to a remote Inferno with zero code changes.

---

## App Supervisor — The OS Daemon

The App Supervisor is the Potato OS daemon that manages everything above the inference layer. It's what makes a Pi into a local AI platform rather than just a computer with an LLM on it.

### What the App Supervisor Manages

- **App lifecycle** — starts app processes, monitors their health, restarts crashed apps. Apps marked as critical get restarted automatically. Non-critical apps can be started/stopped on demand.
- **Communications** — network connectivity, device discovery (mDNS), interagent protocol for Potato-to-Potato communication.
- **Dashboard** — the optional web UI for monitoring and configuration. Serves static assets, proxies to app web panels, shows system status.
- **Inferno connection** — manages the connection to one or more Inferno instances (local, remote, or both). Routes inference requests from apps to the appropriate Inferno.

### What the App Supervisor Is Not

- Not the inference engine — that's Inferno
- Not the inference guardian — that's Mother
- Not an app itself — it's the platform that runs apps

systemd supervises the App Supervisor itself. If it crashes, systemd restarts it. It doesn't need an AI supervisor — a simple restart is enough to recover.

---

## Apps — Isolated Background Services

Apps on Potato OS are **isolated background services** that run as separate processes. Each app gets its own lifecycle, its own state, and its own connection to Inferno for inference. One app crashing doesn't take down others or the platform.

### What an App Is

- A **background service** that runs whether or not anyone has a browser open
- Consumes **Inferno** for inference (never talks to llama-server directly)
- Has a **manifest** declaring its identity and requirements
- Implements a **lifecycle contract** (the platform controls when apps start, stop, suspend)
- **Optionally** exposes a web panel for human interaction
- Multiple apps run **concurrently**, sharing Inferno

### What an App Is Not

- Not a UI module — the UI is optional
- Not a standalone process that manages itself — the App Supervisor owns the lifecycle
- Not something that manages models — that's Inferno's job

---

## Dashboard — The Window

The web UI is not the system — it's a window into it. Everything works without it. A Pi in a closet running apps headlessly is the primary use case. The dashboard is for when a human wants to check in.

### What the Dashboard Shows

- **System status** — hardware metrics (CPU, memory, temperature, storage), network status, uptime
- **Inferno status** — loaded models, health, queue depth, connected clients, active Model Steps
- **Mother status** — current FDIR state, last recovery action, mode (deterministic/agentic)
- **App status** — all running apps, their current step, progress, last output, health
- **App panels** — mount/unmount individual app web panels for apps that have a UI (most won't)
- **Configuration** — settings, model management, app management, interagent protocol peers

### Design Principle

The shell knows app **metadata** (name, icon, status, current step) but never reaches into app internals. Apps push status updates through a defined protocol. This is the Chrome tab architecture pattern — the browser manages tabs using only metadata, never page content.

---

## Interagent Protocol (Future)

The protocol layer for Potato-to-Potato communication. Potatoes discover each other, delegate work, share Inferno instances, and potentially transact.

### Two Proposed Modes

**Trusted hierarchy** — for your own devices on your own network. One Potato delegates scoped authority to another: "run this work using your Inferno, here are the inputs, return the results." Trust is established by network membership and shared keys. This is how a fleet of Pis on a home network would cooperate.

**Open market** — for broader, less-trusted networks. Potatoes advertise available Inferno capacity and capabilities. Other Potatoes can bid on work, negotiate terms, and execute work for payment. Trust is established through reputation and crypto-backed settlement.

### Building Blocks Under Evaluation

- **Transport:** mDNS/DNS-SD for LAN discovery, HTTP/WebSocket for messaging
- **Identity:** UCAN-style capability delegation for scoped authority
- **Negotiation:** Contract Net / bidding model for open market
- **Settlement:** Crypto-backed escrow and payment (future extension point, not v1)

### Connection to the Architecture

The interagent protocol connects naturally to the rest of the stack:
- **Inferno** is already network-transparent — one Potato using another's Inferno is literally the first delegation use case
- **App step envelopes** (typed JSON in, typed JSON out) are a natural wire format for delegated work
- **Mother** on each device independently guards its own Inferno — the protocol doesn't change her job

---

## The Supervision Chain

Each layer supervises the one below it with the appropriate level of intelligence:

```
systemd (PID 1)
│
│   "Is the process alive?"
│   Dumb, reliable. Restart loop. Been doing this since 2010.
│
├── Inferno + Mother
│   │
│   │   Mother: "Is inference healthy?"
│   │   Smart. FDIR loop. Diagnoses problems.
│   │   Deterministic playbook or agentic reasoning.
│   │   Compiled, self-contained, ships with Inferno.
│   │
│   └── llama-server / inference runtime
│       Models, KV cache, request queue
│
└── App Supervisor (Potato OS daemon)
    │
    │   "Are apps running? Are comms up?"
    │   Restarts crashed apps.
    │   Manages network and discovery.
    │   Python/FastAPI, supervised by systemd.
    │
    ├── App: chat (demo)
    ├── App: weather-briefing (headless)
    ├── App: home-monitor (has web panel)
    └── Dashboard (web UI)
```

Why this layering works:
- systemd is always there, always works, never confused. It's the bedrock.
- Mother is specialized — she only understands inference, but she understands it deeply. She can survive the App Supervisor crashing.
- The App Supervisor is the generalist — it manages everything else. If it crashes, systemd restarts it and apps resume.
- Each layer can fail independently without taking down the layers above it. A crashed app doesn't kill the App Supervisor. A crashed App Supervisor doesn't kill Inferno. A crashed Inferno gets restored by Mother.

---

## Research Influences

Proven patterns from existing systems that directly inform this architecture.

| Pattern | Source | How it applies |
|---------|--------|---------------|
| LLM syscall abstraction + scheduler | AIOS (COLM 2025) | Apps don't talk to the model directly — inference requests are dispatched and scheduled by the platform |
| Sidecar supervisor with behavioral memory | VIGIL (arxiv:2512.07094) | Mother watches system health, builds a persistent model with decay, emits targeted fixes — not just restart |
| Hub-and-spoke with message queue | llama-deploy | Control plane routes inference requests, message queue decouples apps from inference execution |
| P2P distributed inference | LocalAI / exo | Automatic node discovery, inference across network devices, no master-worker hierarchy |
| 4-tier self-healing | OpenClaw | systemd restart → watchdog health check → AI diagnosis + repair → human escalation |
| Dual-model (fast + smart) | Max Headbox | Small model for quick Tool Step guidance, bigger model for complex Model Steps — manage latency on constrained hardware |
| Plan-Execute-Verify loop | Autonomic Computing (arxiv:2407.14402) | Safe remediation with rollback — detect, reason, act, verify the fix worked |
| Agent OS kernel with sandbox isolation | OpenFang | Agents as OS-level processes, sandboxed execution, typed message channels, single-binary deployment |
| Localhost OpenAI-compatible API | Jan.ai / LM Studio / Ollama | Standard interface for all apps to consume inference — the `/v1/chat/completions` contract |
| Command deny-list + human escalation | Rampart | Safety guardrails for agentic mode — deny dangerous commands, escalate to human for high-risk actions |

### What Nobody Does Yet

No existing project combines all five of these on Pi-class hardware:

1. OS-level multi-agent orchestration with structured workflows
2. Owned/shared inference engine with scheduling
3. Network-transparent inference (local or remote)
4. Self-healing AI supervisor
5. Runs on a Raspberry Pi

That's the gap.

---

## Where We Are Today

| Component | v0.1 (current MVP) | v0.2 (this proposal) |
|-----------|--------------------|-----------------------|
| Mother | Doesn't exist. systemd restarts the service | Compiled FDIR supervisor, ships with Inferno |
| Inferno | llama-server subprocess, managed by runtime_state.py | Standalone orchestrator, multi-model, multi-client, network-discoverable |
| Apps | Chat hardwired into shell (partially extracted in #144) | Isolated background services with lifecycle management |
| App Supervisor | FastAPI monolith serving chat + API + status | OS daemon managing app lifecycles, comms, dashboard |
| Dashboard | Shell + chat monolith | Thin shell showing app/Inferno/Mother status, mounting app panels |
| Interagent | Doesn't exist | Potato-to-Potato delegation, discovery, and negotiation protocol |

---

## Open Questions

These are intentionally unresolved — the proposal needs input before they're decided.

- **App process model** — each app as a separate Python process? How do they communicate with the App Supervisor? Unix sockets for local, HTTP for network-crossing?
- **Inferno API** — is OpenAI-compatible `/v1/chat/completions` the right interface, or does Inferno need its own protocol with scheduling, priority, and model selection?
- **Mother's deterministic playbook** — what are the exact recovery steps and their ordering?
- **App-to-Inferno contract** — how does an app express what it needs from inference? Capability requests ("I need vision") vs model requests ("I need Qwen 3B") vs resource requests ("I need 64k context")?
- **Interagent protocol shape** — thin Potato-specific protocol, layered hierarchy + market, or existing ecosystem adaptation?
- **Security model** — how does remote Inferno authenticate clients? How does agentic Mother scope her access? How are app permissions declared and enforced?
- **Build sequencing** — what do we build first?
