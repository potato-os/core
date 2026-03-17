# Building Potato OS Images

Build a flashable Potato OS SD card image from macOS using pi-gen and Docker.

## Prerequisites

- **macOS** with [Homebrew](https://brew.sh)
- **llama runtime binaries** — either built locally (`references/old_reference_design/llama_cpp_binary/runtimes/`) or set `POTATO_LLAMA_BUNDLE_SRC` to a pre-built slot. See the runtime build section in [README.md](../README.md).
- **~10 GB free disk** for the pi-gen Docker build
- **uv** — `brew install uv` (Python script runner used by the build pipeline)

Docker and Colima are installed automatically when you pass `--setup-docker`.

## Build a Potato OS image

```bash
./bin/build_local_image.sh --setup-docker
```

This single command:
1. Installs Docker + Colima via Homebrew (if needed)
2. Clones pi-gen (Raspberry Pi OS builder) into `.cache/pi-gen-arm64/`
3. Builds a Potato OS image inside Docker (pi-gen arm64 branch)
4. Collects the image, checksums, and a Raspberry Pi Imager manifest into `output/images/`

Build takes 20–40 minutes depending on network and disk speed.

### Variants

The default build produces a **Potato OS** image (internally `lite`) — the llama runtime is included, and a starter model (~1.8 GB Qwen3.5-2B) downloads automatically on first boot.

For an image with the model pre-loaded (no first-boot download):

```bash
./bin/build_local_image.sh --variant full --setup-docker
```

### Key options

| Flag | Default | Description |
|------|---------|-------------|
| `--setup-docker` | off | Install Docker + Colima if missing |
| `--variant <lite\|full\|both>` | `lite` | Which image variant to build |
| `--output-dir <path>` | `output/images` | Where to write build artifacts |
| `--update-pi-gen` | skip | Fetch/pull latest pi-gen before building |
| `--clean-artifacts-yes` | ask | Auto-remove previous build artifacts |
| `--skip-clean-pigen-work` | off | Don't remove stale pi-gen Docker container |

### Build output

After a successful build, `output/images/` contains:

```
output/images/
├── potato-lite-<timestamp>.img.xz          # Compressed image
├── SHA256SUMS                               # Checksum
├── potato-lite.rpi-imager-manifest          # Raspberry Pi Imager manifest
├── potato-lite-build-info.json              # Build metadata
└── local-test-lite-<stem>/                  # Bundle directory
    ├── potato-lite-<timestamp>.img.xz
    ├── SHA256SUMS
    ├── METADATA.json
    ├── potato-lite.rpi-imager-manifest
    └── README.md                            # Flashing instructions
```

## Flash the image

### Option A: Direct flash

Using dd (replace `/dev/diskN` with your SD card):

```bash
xz -dc output/images/potato-lite-*.img.xz | sudo dd of=/dev/rdiskN bs=4m
```

Or use **Raspberry Pi Imager** → "Use custom" → select the `.img.xz` file.

### Option B: Raspberry Pi Imager with manifest

1. Open Raspberry Pi Imager
2. Choose OS → scroll to bottom → "Use custom"
3. Instead of selecting an image, use the `.rpi-imager-manifest` file as a Content Repository source
4. Select **Potato OS** from the list
5. Choose your SD card and flash

The manifest file is at `output/images/potato-lite.rpi-imager-manifest`.

### After flashing

Insert the SD card into your Pi 5 and boot. Then:

```bash
ssh pi@potato.local    # password: raspberry
```

Open `http://potato.local` in a browser. The starter model downloads automatically on first boot (~5 minutes on a decent connection).

## Cleaning up

Remove build artifacts and caches:

```bash
# Remove output images and build workspace
./bin/clean_image_build_artifacts.sh

# Also remove download cache and pi-gen checkout
./bin/clean_image_build_artifacts.sh --deep
```

## Troubleshooting

**Docker not running:** If you see "Cannot connect to the Docker daemon", run `colima start` or pass `--setup-docker` to auto-start it.

**Stale container:** If the build fails with a container conflict, remove the old container:
```bash
docker rm -f pigen_work potato-pigen-lite potato-pigen-full
```
Or use `--skip-clean-pigen-work` to skip the automatic cleanup attempt.

**Colima VM resources:** For faster builds, give Colima more CPU/RAM:
```bash
colima stop && colima start --cpu 4 --memory 8
```

**Missing runtime:** The build fails if no llama runtime is available. Build one first:
```bash
./bin/build_and_publish_remote.sh --family ik_llama
```

## Linux

The same scripts work on Linux. Docker mode is optional — pi-gen can build natively using chroot (requires `sudo`):

```bash
./bin/build_local_image.sh
```

On Linux, Docker mode is not forced. To explicitly use Docker:

```bash
POTATO_PI_GEN_USE_DOCKER=1 ./bin/build_local_image.sh
```
