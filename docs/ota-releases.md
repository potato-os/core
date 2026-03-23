# OTA Release Artifact Contract

This document defines the release artifacts the on-device OTA updater consumes. Future release changes should preserve this contract intentionally.

## Asset naming

Each OTA-enabled release publishes two assets:

| Asset | Pattern | Example |
|-------|---------|---------|
| App tarball | `potato-os-<version>.tar.gz` | `potato-os-0.5.0.tar.gz` |
| Checksum | `potato-os-<version>.tar.gz.sha256` | `potato-os-0.5.0.tar.gz.sha256` |

These assets live on the `v<version>` GitHub Release alongside any image assets. Either can be published independently.

## Tarball layout

The tarball contains a single top-level directory with `app/`, `bin/`, and `requirements.txt`:

```
potato-os-0.5.0/
  app/              # Python application code + frontend assets
  bin/              # Operational scripts (run.sh, install_dev.sh, etc.)
  requirements.txt  # Python dependencies
```

This is the "single subdir" layout that `_find_update_root()` handles in `app/update_state.py`.

### Included

- `app/` — all Python source and `assets/` (HTML, CSS, JS)
- `bin/` — all shell scripts, `lib/`, and `assets/`
- `requirements.txt` — from repo root

### Excluded

- `__pycache__/`, `*.pyc`, `.DS_Store`
- `tests/`, `node_modules/`, `.git/`, `.venv/`, `output/`, `references/`

## Checksum format

The `.sha256` file contains one line in `sha256sum` format:

```
<sha256hash>  potato-os-<version>.tar.gz
```

Two-space separator between hash and filename, matching BSD/GNU `sha256sum` output.

## How the updater discovers the tarball

`check_for_update()` in `app/update_state.py`:

1. Queries `https://api.github.com/repos/slomin/potato-os/releases/latest`
2. Iterates release assets looking for `name` matching `potato-os-*.tar.gz`
3. Saves the `browser_download_url` as `tarball_url`

The `potato-os-` prefix prevents accidental matches against runtime tarballs (`ik_llama-*.tar.gz`).

## How the updater applies the tarball

`run_update()` in `app/main.py`:

1. Downloads tarball to `.update_staging/update.tar.gz`
2. Extracts to `.update_staging/extracted/`
3. `_find_update_root()` locates the `app/` directory (handles single-subdir layout)
4. Backs up live `app/` and `bin/`
5. Copies new `app/` and `bin/` over the live installation
6. Copies `requirements.txt` to `app/requirements.txt`
7. Sets executable bits on `bin/*.sh`
8. Runs `pip install -r app/requirements.txt`
9. Signals service restart via systemd
10. On next boot, detects version change to confirm success

## Publishing

```bash
# Dry run — build tarball locally without publishing
./bin/publish_ota_release.sh --version v0.5.0 --dry-run

# Publish — attach to existing release or create new one
./bin/publish_ota_release.sh --version v0.5.0
```

If the `v<version>` release already exists (e.g., image was published first), the script uploads the OTA assets with `--clobber`. If it doesn't exist, the script creates the release.

## Version source

`app/__version__.py` is the single source of truth. The tag version should match.
