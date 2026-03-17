# Potato OS

Experimental Raspberry Pi 5 Linux mod with optimised local LLM inference. Runs quantised models on-device with a browser chat UI — no cloud, no GPU, just a Pi.

**Hardware:** Raspberry Pi 5 (8GB / 16GB) | **Runtime:** [ik_llama.cpp](https://github.com/ikawrakow/ik_llama.cpp) (IQK-optimised) + upstream [llama.cpp](https://github.com/ggerganov/llama.cpp) | **Default model:** Qwen3.5-2B

## Quick start

Flash Raspberry Pi OS, copy the repo to the Pi, and install:

```bash
./bin/install_dev.sh
```

Open `http://potato.local` in a browser. A starter model downloads automatically on first boot.

## Building llama runtimes

Runtimes are built on the Pi (aarch64, no cross-compilation). From your Mac:

```bash
# Build both ik_llama + llama_cpp from latest source on Pi, sync back
./bin/build_and_publish_remote.sh

# Build + publish to GitHub Releases
./bin/build_and_publish_remote.sh --publish

# Just one family
./bin/build_and_publish_remote.sh --family ik_llama
```

This SSHs to `potato.local` (default: `pi`/`raspberry`), clones latest source, builds, and syncs the binaries back. Use `--publish` to upload tarballs to GitHub Releases.

Or build directly on the Pi:

```bash
./bin/build_llama_runtime.sh --family both --fetch --clean
```

Published runtimes are available at [GitHub Releases](https://github.com/slomin/potato-os/releases). Fresh installs auto-download when no local build is present:

```bash
POTATO_LLAMA_RELEASE_AUTO=1 ./bin/install_dev.sh
```

## Local dev

```bash
uv sync
POTATO_ENABLE_ORCHESTRATOR=0 uv run uvicorn app.main:app --host 0.0.0.0 --port 1983
```

## Tests

```bash
uv run python -m pytest tests/unit tests/api -q -n auto
npx playwright test --reporter=dot --timeout=15000 --workers=3
```

## SD card images

```bash
# Single-flash image (pi-gen)
./image/build-all.sh

# Bootfs bundle (patch existing Raspberry Pi OS card)
./bin/prepare_imager_bundle.sh --boot-path /Volumes/bootfs
```

## Project

- Board: [github.com/users/slomin/projects/8](https://github.com/users/slomin/projects/8)
- Process: [`WORKFLOW.md`](WORKFLOW.md)
- Conventions: [`AGENTS.md`](AGENTS.md)
- Defaults: hostname `potato`, SSH `pi`/`raspberry`
