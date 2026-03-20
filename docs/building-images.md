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
2. Click the **OS** button → scroll to bottom → **Other general-purpose OS** → **Use custom**? No — instead, load the manifest as a **Content Repository**:
   - On macOS: **Raspberry Pi Imager** → **Settings** (gear icon) → under **Content Repository**, select the `.rpi-imager-manifest` file
   - The manifest registers Potato OS as a selectable OS entry
3. Back on the main screen, click **Choose OS** → select **Potato OS (lite, Raspberry Pi 5)**
4. Choose your SD card and flash

The manifest file is at `output/images/potato-lite.rpi-imager-manifest`.

### After flashing

Insert the SD card into your Pi 5 and boot. Then:

```bash
ssh pi@potato.local    # password: raspberry
```

Open `http://potato.local` in a browser. The starter model downloads automatically on first boot (~5 minutes on a decent connection).

## Publishing a release

After building, publish the image to GitHub Releases so users can flash via Raspberry Pi Imager:

```bash
# Dry run — validate the bundle without publishing
./bin/publish_image_release.sh --version v0.3 --dry-run

# Publish for real
./bin/publish_image_release.sh --version v0.3
```

This tags the repo, uploads the image + manifest + icon + checksums, and generates release notes with flashing instructions.

Once published, users paste this URL into Raspberry Pi Imager (**OS** → **Content Repository** → **Use custom URL**):

```
https://github.com/slomin/potato-os/releases/download/stable/potato-lite.rpi-imager-manifest
```

The script auto-detects the latest bundle in `output/images/`. To target a specific bundle:

```bash
./bin/publish_image_release.sh --version v0.3 --bundle-dir output/images/local-test-lite-*/
```

## Disk space requirements

A pi-gen Docker build needs at least **8 GB free** inside the Docker filesystem. The build script checks this automatically and fails early with recovery instructions if space is too low.

### Checking Docker disk space manually

```bash
docker run --rm alpine df -h /
```

### Recovering disk space

**Prune unused Docker artifacts:**
```bash
docker system prune --volumes
```

**Or use the built-in cleanup script:**
```bash
./bin/clean_image_build_artifacts.sh --docker-prune
```

**Increase Colima disk size (macOS):**
```bash
colima stop && colima start --disk 100
```

If Colima disk is nearly full, you may need to delete and recreate:
```bash
colima stop && colima delete && colima start --disk 100
```

**Automatic post-build cleanup:**
```bash
./bin/build_local_image.sh --setup-docker --post-cleanup
```

This removes unused Docker images, build cache, and volumes older than 24 hours after the build completes. Active containers and in-use images are never removed.

### Skipping the preflight check

```bash
POTATO_SKIP_SPACE_PREFLIGHT=1 ./bin/build_local_image.sh --setup-docker
```

### Adjusting thresholds

The default minimum is 8 GB (hard fail) and 12 GB (warning). Override with:
```bash
POTATO_DOCKER_MIN_SPACE_GB=6 POTATO_DOCKER_WARN_SPACE_GB=10 ./bin/build_local_image.sh --setup-docker
```

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

**Low disk space in Docker:** The build checks Docker filesystem space before starting. If it fails with a space error, see the [Disk space requirements](#disk-space-requirements) section above.

**Stale container:** If the build fails with a container conflict, remove the old container:
```bash
docker rm -f pigen_work potato-pigen-lite potato-pigen-full
```
The build script normally cleans these automatically. If you see repeated conflicts, check that Docker is responding (`docker info`) before retrying.

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
