# Platform Architecture — Design Proposal v0.2

> **Status:** Proposal — everything here is subject to change.
> **v0.1** was the initial MVP: a single-purpose chat UI on a Pi with a local llama runtime.
> **v0.2** is the vision for what Potato OS becomes next: an agentic platform.

## Vision

Potato OS evolves from a single-purpose chat UI into a local AI operating system — autonomous agents running on Raspberry Pi hardware, powered by a shared inference engine, kept alive by a self-healing supervisor.

## Proposed Stack

```
┌──────────────────────────────────────────────────────┐
│                      Potato OS                       │
│                                                      │
│  Mother (supervisor / FDIR daemon)                   │
│  ├── Prime directive: preserve and restore inference │
│  ├── FDIR loop: Detect → Isolate → Recover           │
│  ├── Deterministic mode (default)                    │
│  └── Agentic mode (reasons via Inferno, acts w/sudo) │
│                                                      │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐               │
│  │  Chat   │  │ Agent A │  │ Agent B │               │
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

## Mother — The Supervisor

Mother is the system's always-on supervisor — a daemon that implements a NASA-style FDIR (Fault Detection, Isolation, and Recovery) loop. In CS terms: a supervisor in the Erlang/OTP lineage, purpose-built for a single-board computer running in a closet with no human nearby.

Mother has one prime directive and follows it absolutely.

She will wake subsystems before they're needed. She will reallocate resources without asking. She will bring Inferno back from the dead at 3 AM on a Tuesday. Inference is the priority. All other concerns are secondary.

### Prime Directive

Preserve and restore inference capability. At minimum: one model loaded, one agent able to make inference requests. Mother does whatever it takes to maintain this.

### FDIR Loop

```
    ┌──────────┐
    │  Detect  │  Is Inferno healthy? Is a model loaded?
    └────┬─────┘  Can agents make requests?
         │
    ┌────▼─────┐
    │  Isolate │  What failed? Runtime crash? Model corruption?
    └────┬─────┘  Disk full? OOM kill? Network issue?
         │
    ┌────▼─────┐
    │  Recover │  Restart Inferno, redownload model,
    └────┬─────┘  clean disk, kill processes, reboot
         │
         └──────→ loop
```

### Two Proposed Modes

**Deterministic (default)** — fixed recovery playbook. Predictable, auditable. Think systemd restart policies with smarter ordering and dependency awareness.

**Agentic** — Mother uses Inferno to reason about what's wrong and takes corrective action with sudo. When Inferno is dead, falls back to deterministic mode to bring it back — then she has her brain again.

## Inferno — The Inference Orchestrator

Not just "llama-server with a name." A standalone inference service designed to be network-transparent from day one.

### Proposed Capabilities

- **Model management** — load, unload, swap, advertise available models
- **Scheduler** — queue and prioritize inference requests from multiple concurrent apps
- **Network-transparent** — runs on the same Pi or on a separate device on the LAN (later internet)
- **Multi-client** — multiple Potato OS instances can share one Inferno
- **Service discovery** — advertises itself and available models to the network

### Deployment Scenarios

```
Scenario 1: Self-contained Pi
┌──────────────┐
│  Potato OS   │
│  ┌────────┐  │
│  │Inferno │  │  Inferno runs locally with a small model
│  │Qwen 3B │  │
│  └────────┘  │
└──────────────┘

Scenario 2: Distributed
┌──────────────┐         ┌──────────────┐
│  Potato OS   │         │   Inferno    │
│  (Pi 4 2GB)  │────────▶│  (Desktop)   │
│  no local    │   LAN   │  GPT-OSS 120B│
│  inferno     │         │  Qwen 30B    │
└──────────────┘         └──────────────┘

Scenario 3: Hybrid
┌──────────────┐         ┌──────────────┐
│  Potato OS   │         │   Inferno    │
│  ┌────────┐  │         │  (remote)    │
│  │Inferno │  │────────▶│  big models  │
│  │(local) │  │   WAN   │              │
│  │small   │  │         │              │
│  └────────┘  │         └──────────────┘
│  local=fast  │
│  remote=smart│
└──────────────┘
```

### Design Principle

Even when Inferno runs locally, apps talk to it through the same API as if it were remote. Local → remote becomes a configuration change, not an architecture change.

## Apps — Background Agents

Apps are backend-first autonomous agents. Chat is the demo — the poster child that validates the framework.

### What an App Would Be

- A **background service** that runs whether or not anyone has a browser open
- Consumes **Inferno** for inference (never talks to llama-server directly)
- Has a **manifest** declaring its identity and requirements
- Implements a **lifecycle contract** (the platform controls when apps start, stop, suspend)
- **Optionally** exposes a web panel for human interaction
- Multiple apps run **concurrently**, sharing Inferno

### What an App Would Not Be

- Not a UI module — the UI is optional
- Not a standalone process that manages itself — Mother and the platform own the lifecycle
- Not something that manages models — that's Inferno's job

### Research Influences

| Concept | Source | How It Could Apply |
|---------|--------|-------------------|
| Lifecycle contract | single-spa | Apps export lifecycle hooks the platform calls |
| Context injection | Home Assistant (`hass` object) | Platform injects a context object with Inferno access, status, events |
| Manifest simplicity | webOS (`appinfo.json`) | Small declarative JSON — id, name, entry, capabilities |
| System-owned lifecycle | Android (Activity lifecycle) | Apps don't decide when to run — the platform does |
| Metadata-only shell | Chrome (tab architecture) | Shell shows app name/icon/status without knowing app internals |
| Container-as-app | Umbrel, CasaOS, StartOS | Dashboard as launcher/monitor, apps as independent services |

## Dashboard — The Window

The web UI is not the system — it's a window into it. Everything works without it. A Pi in a closet running agents headlessly is the primary use case.

### What the Dashboard Would Show

- **System status** — hardware metrics, Inferno health, Mother status
- **App panels** — mount/unmount app UIs when a human navigates to them
- **Inferno status** — loaded models, queue depth, connected clients
- **Configuration** — settings, model management, app management

### Design Principle

The shell knows app **metadata** (name, icon, status badge) but never reaches into app internals. Apps push status updates through a defined protocol.

## Patterns to Steal

These are proven patterns from existing systems that directly apply to this architecture.

| Pattern | Source | What to steal |
|---------|--------|---------------|
| LLM syscall abstraction + scheduler | AIOS (COLM 2025) | Agents don't talk to the model — they make syscalls that the inference layer schedules with priority |
| Sidecar supervisor with behavioral memory | VIGIL (arxiv:2512.07094) | Mother watches agent logs, builds a persistent health model with decay, emits targeted fixes — not just restart |
| Hub-and-spoke with message queue | llama-deploy | Control plane routes tasks, message queue decouples agents from inference execution |
| P2P distributed inference | LocalAI / exo | Automatic node discovery, inference across network devices, no master-worker hierarchy |
| 4-tier self-healing | OpenClaw | systemd restart → watchdog health check → AI diagnosis + repair → human escalation |
| Dual-model (fast + smart) | Max Headbox | Small model for quick responses, bigger model for agentic reasoning — manage latency on constrained hardware |
| Plan-Execute-Verify loop | Autonomic Computing (arxiv:2407.14402) | Safe remediation with rollback — detect, reason, act, verify the fix worked |
| Agent OS kernel with WASM isolation | OpenFang | Agents as OS-level processes, sandboxed execution, typed message channels, single-binary deployment |
| Localhost OpenAI-compatible API | Jan.ai / LM Studio / Ollama | Standard interface for all agents to consume local inference — the `/v1/chat/completions` contract |
| Command deny-list + human escalation | Rampart | Safety guardrails for agentic sudo access — deny dangerous commands, escalate to human for high-risk actions |

### What Nobody Does Yet

No existing project combines all five of these on Pi-class hardware:

1. OS-level multi-agent orchestration
2. Owned/shared inference engine with scheduling
3. Network-transparent inference (local or remote)
4. Self-healing AI supervisor
5. Runs on a Raspberry Pi

OpenFang has 1 but not 2. AIOS has 1+2 but not 5. OpenClaw has 4+5 but not 1+2+3. LocalAI has 2+3 but bolts on agents as an afterthought. That's the gap.

## Where We Are Today

| Component | v0.1 (current MVP) | v0.2 (this proposal) |
|-----------|--------------------|-----------------------|
| Mother | Doesn't exist. systemd restarts the service | FDIR supervisor with deterministic + agentic modes |
| Inferno | llama-server subprocess, managed by runtime_state.py | Standalone orchestrator, multi-model, multi-client, network-discoverable |
| Apps | Chat hardwired into shell (partially extracted in #144) | Background agents with manifest, lifecycle, Inferno access, optional UI |
| Dashboard | Shell + chat monolith | Thin shell mounting app panels, monitoring system/Inferno |

## Open Questions

These are intentionally unresolved — the proposal needs input before they're decided.

- **App runtime shape** — Python modules within FastAPI? Separate processes? Containers?
- **Inferno API** — is OpenAI-compatible `/v1/chat/completions` the right interface, or does Inferno need its own protocol?
- **Mother's playbook** — what are the concrete deterministic recovery steps and ordering?
- **App-to-Inferno contract** — capability requests ("I need vision") vs model requests ("I need Qwen 3B") vs resource requests ("I need 64k context")?
- **Security model** — how does remote Inferno authenticate clients? How does agentic Mother scope its sudo access?
- **Build sequencing** — what do we build first?
