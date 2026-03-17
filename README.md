# Potato OS

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

![Potato OS Raspberry Pi next to a real potato](docs/assets/potato_os_next_to_a_real_spud.jpg)

Experimental Raspberry Pi 5 Linux mod with optimised local LLM inference. Runs quantised models on-device with a browser chat UI — no cloud, no GPU, just a Pi.

**Hardware:** Raspberry Pi 5 (8GB / 16GB) | **Runtime:** [ik_llama.cpp](https://github.com/ikawrakow/ik_llama.cpp) (IQK-optimised) + upstream [llama.cpp](https://github.com/ggerganov/llama.cpp) | **Default model:** Qwen3.5-2B

## Install (recommended)

1. Download the latest SD card image from [Releases](https://github.com/slomin/potato-os/releases)
2. Flash it to a microSD card with [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
3. Insert the card, power on the Pi, and wait for first boot to complete
4. Open `http://potato.local` in a browser

A starter model (~1.8 GB) downloads automatically on first boot. Chat is ready once the download finishes and the status shows CONNECTED.

Before you start, back up any SD card or Pi data you may want to keep. Potato OS is currently a reflash-first MVP: there is no OTA update flow, no one-click rollback, and the safest way to recover or move to a newer version is to reflash the card.

### What you need

- Raspberry Pi 5 (8 GB or 16 GB)
- microSD card (16 GB minimum, 32 GB recommended)
- Power supply (27W USB-C recommended)
- Ethernet or Wi-Fi connection (for first-boot model download)

## Recovery and rollback

Need to back out or recover from a failed setup? See [docs/recovery.md](docs/recovery.md) for the practical rollback paths for:

- restoring a previous Raspberry Pi OS or other system image
- reflashing to a newer Potato OS image

---

## Development

Everything below is for contributors and developers.

### Local dev

```bash
uv sync
POTATO_ENABLE_ORCHESTRATOR=0 uv run uvicorn app.main:app --host 0.0.0.0 --port 1983
```

### Tests

```bash
uv run python -m pytest tests/unit tests/api -q -n auto
npx playwright test --reporter=dot --timeout=15000 --workers=3
```

### Building llama runtimes

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

### SD card images

```bash
# Single-flash image (pi-gen)
./image/build-all.sh

# Bootfs bundle (patch existing Raspberry Pi OS card)
./bin/prepare_imager_bundle.sh --boot-path /Volumes/bootfs
```

### Project

- Board: [github.com/users/slomin/projects/8](https://github.com/users/slomin/projects/8)
- Process: [`WORKFLOW.md`](WORKFLOW.md)
- Conventions: [`AGENTS.md`](AGENTS.md)
- Defaults: hostname `potato`, SSH `pi`/`raspberry`

## License

Apache License 2.0. See [LICENSE](LICENSE) for details.
