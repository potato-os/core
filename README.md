# Potato OS v0.2 MVP

Potato OS provides a Raspberry Pi-hosted chat UI and OpenAI-compatible front door.

## Project workflow

GitHub Project board: <https://github.com/users/slomin/projects/8>
Detailed process guide: [`WORKFLOW.md`](WORKFLOW.md)

We use a simple flow for issues and PRs:
- `Todo`: ready to start
- `In Progress`: actively being worked
- `In Review`: waiting for review/validation
- `Done`: completed and merged

Working rules:
- Open an issue for each meaningful change.
- Link both issue and PR to the `Potato OS` project.
- Keep the status updated as work moves.
- Use labels for fast triage: `type:feature`, `type:bug`, `type:chore`.
- Use area labels for ownership: `area:ui`, `area:backend`, `area:pi-image`, `area:ops`.
- Use `blocked` when progress is externally blocked.

## Local dev

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Run web app locally:

```bash
POTATO_ENABLE_ORCHESTRATOR=0 .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 1983
```

Backend mode toggle:

```bash
# auto = use llama.cpp when healthy, otherwise fake backend fallback
POTATO_CHAT_BACKEND=auto
# force fake backend
POTATO_CHAT_BACKEND=fake
# force llama.cpp backend only
POTATO_CHAT_BACKEND=llama
```

## Pi install (idempotent)

Copy repo to Pi and run:

```bash
./bin/install_dev.sh
```

## True single-flash image builds (pi-gen)

Use this for release-style SD images (no bootfs hook patching required).

Prerequisites:
- Local `pi-gen` checkout already installed and buildable.
- Export `POTATO_PI_GEN_DIR` to that checkout path.
- Precompiled llama runtime bundle available (auto-resolved from `references/old_reference_design/llama_cpp_binary` or set `POTATO_LLAMA_BUNDLE_SRC`).

Build variants:

```bash
# One-go bootstrap + build (clones/updates pi-gen, then builds both variants)
./image/build-all.sh

# Lite image: includes app + precompiled llama runtime; model/mmproj download on first boot
POTATO_PI_GEN_DIR=/path/to/pi-gen ./image/build-lite.sh

# Full image: includes app + precompiled llama runtime + model/mmproj
POTATO_PI_GEN_DIR=/path/to/pi-gen ./image/build-full.sh
```

`build-all.sh` uses `uv` and supports:

```bash
./image/build-all.sh --variant lite --dry-run
./image/build-all.sh --variant both --hostname potato --ssh-user pi --ssh-password raspberry
./image/build-all.sh --variant lite --setup-docker
```

On macOS, `build-all.sh` automatically enables pi-gen Docker mode for real builds.
If Docker runtime is missing, pass `--setup-docker` to install/start Colima via Homebrew.

Defaults (overridable via env):
- hostname: `potato`
- SSH user/password: `pi` / `raspberry`

Outputs are written to `output/images/`:
- `potato-lite-<timestamp>.img.xz` or `potato-full-<timestamp>.img.xz`
- `SHA256SUMS`
- build metadata/config snapshots (`potato-*-build-info.json`, `potato-*-config.txt`)

Local flash bundle helper:

```bash
./bin/build_local_image.sh --variant lite
```

If previous artifacts exist in `output/images/`, the script asks whether to delete them first.
For non-interactive runs, use `--clean-artifacts yes` or `--clean-artifacts no`.

This creates a `local-test-*` folder that includes:
- image file + checksums
- `potato-lite.rpi-imager-manifest` (valid Raspberry Pi Imager repository file, Pi 5-only via `pi5-64bit`)

Use the `.rpi-imager-manifest` file in Imager Content Repository.  
Do not use `METADATA.json` or `potato-*-build-info.json` in Imager.

## Bundled SD workflow (first-boot auto install)

If you want a card that boots and installs Potato automatically (including model download startup), use:

```bash
./bin/prepare_imager_bundle.sh --boot-path /Volumes/bootfs
```

Prerequisites:
- Flash `raspberry_os_clean_image/2025-12-04-raspios-trixie-arm64-lite.img.xz` with Raspberry Pi Imager.
- In Imager OS customisation, set hostname/user/password and enable SSH so `firstrun.sh` is generated.
- Keep the card mounted, run the command above, then eject and boot the Pi.

What it injects:
- `bootfs/potato/potato_bundle.tar.gz` (repo payload + llama runtime bundle)
- `bootfs/potato/install_potato_from_bundle.sh`
- a hook in `bootfs/firstrun.sh` that runs the installer once on first boot.

Useful checks:

```bash
systemctl status potato --no-pager
journalctl -u potato -e
curl http://potato.local/status
```

## Pi smoke test from workstation

Requires `sshpass` locally and defaults to `pi/raspberry`:

```bash
./tests/e2e/smoke_pi.sh
```

## Qwen3.5-35B-A3B (Pi 5 16GB only)

Current support target for large Qwen3.5 A3B GGUF models is:
- Raspberry Pi 5 `16GB` only (validated path)

Notes:
- Keep the default bootstrap model (`Qwen3-VL-4B`) for first boot.
- For Qwen3.5 A3B, use manual model upload (for example `Qwen_Qwen3.5-35B-A3B-Q2_K_L.gguf`).
- Potato now shows warning-only compatibility notices for large models (default threshold `5 GiB`) on Pi 5 `8GB` and other Raspberry Pi devices.
- If the bundled `llama-server` is too old for a newer GGUF, rebuild `llama.cpp` on the target Pi (aarch64) and package a fresh `llama_server_bundle_*` under `references/old_reference_design/llama_cpp_binary/` while keeping the previous bundle for rollback.

## Pi rollback only (no Mac rollback)

Local helper to uninstall Potato services/files from Pi:

```bash
./tests/e2e/uninstall_pi.sh
```

Optional package removal on Pi:

```bash
REMOVE_PACKAGES=1 ./tests/e2e/uninstall_pi.sh
```
