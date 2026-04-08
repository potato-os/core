# Recovery, Uninstall, and Rollback

Potato OS is still experimental. For MVP, the recovery story is simple: keep backups and be ready to reflash back to Raspberry Pi OS, another known-good image, or a newer Potato OS image.

## Before You Start

Back up anything you care about before you flash the image.

At minimum, back up:

- your current microSD card if you may want to return to it later
- any local files on the Pi you do not want to lose
- any files on the card that only exist on the current system image

Potato OS provides app-level OTA updates (see below) but does not yet offer a one-click rollback flow or a built-in backup feature.

## OTA Update Recovery

Potato OS supports app-level OTA updates that replace `core/` and `bin/` on the device. The updater creates a backup before applying changes and attempts automatic rollback on failure.

### What the updater backs up

Before overwriting live files, the updater copies the current `core/` and `bin/` directories (plus `core/requirements.txt`) to a staging backup at `/opt/potato/.update_staging/_backup/`. This backup is used for automatic rollback if the apply or pip install step fails.

### Auto-recovered failures (no action needed)

These failures are handled automatically — the updater rolls back to the previous code and reports `failed` state in the UI:

- **Download failures** (network error, timeout, rate limit): no files changed, safe to retry
- **Extract failures** (corrupt tarball, disk full during extract): no files changed, safe to retry
- **Apply failures** (permission error, disk full during copy): backup restored automatically
- **Pip install failures** (missing dependency, venv error): backup restored automatically

After automatic rollback the Pi continues running the previous version. Click "Retry" or "Check for updates" to try again.

### Manual recovery via SSH

If the service won't start after an update (e.g., the backup restore was incomplete or a new dependency is missing):

```bash
# Check service status and logs
ssh pi@potato.local
echo raspberry | sudo -S systemctl status potato --no-pager
echo raspberry | sudo -S journalctl -u potato -n 50 --no-pager
```

To re-deploy the working code from your dev machine:

```bash
export SSHPASS=raspberry
# Fix ownership if needed
sshpass -e ssh -o StrictHostKeyChecking=accept-new pi@potato.local \
  "echo raspberry | sudo -S chown -R pi:pi /opt/potato/core"

# Rsync known-good core/ from your checkout
sshpass -e rsync -az --delete \
  -e "ssh -o StrictHostKeyChecking=accept-new" \
  core/ pi@potato.local:/opt/potato/core/

# Install dependencies (required — some packages live outside core/)
sshpass -e ssh -o StrictHostKeyChecking=accept-new pi@potato.local \
  "echo raspberry | sudo -S /opt/potato/venv/bin/pip install -r /opt/potato/core/requirements.txt"

# Restart the service
sshpass -e ssh -o StrictHostKeyChecking=accept-new pi@potato.local \
  "echo raspberry | sudo -S systemctl restart potato"
```

### When to reflash instead

Reflash the SD card if:

- Both the update apply and the automatic backup restore failed (system in unknown state)
- The service repeatedly fails to start after manual re-deploy attempts
- You need to roll back to a clean baseline with no risk of leftover state
- The update changed system-level files outside `core/` and `bin/` (not currently supported, but guard against future changes)

### Known limitations

- There is no one-click rollback button in the UI. If automatic rollback fails, recovery requires SSH access.
- The updater only replaces `core/` and `bin/`. System packages, kernel, firmware, nginx config, and systemd units are not updated by OTA.
- If the Pi loses power during the apply phase (after backup, before restart), the system may be left with partially applied code. SSH re-deploy or reflash is the recovery path.
- The staging backup is removed after a successful restart. There is no persistent rollback snapshot.

## Reflash the Card

Potato OS is currently intended to be used by flashing the SD card image. The expected MVP recovery path is to reflash the card.

Use one of these targets:

- a backup image of your previous Raspberry Pi OS card
- a fresh Raspberry Pi OS image
- another known-good Linux image for the Pi
- a newer Potato OS image

### Recommended path

1. Power off the Pi cleanly.
2. Remove the microSD card.
3. Reflash it with the image you want to return to.
4. Boot again and restore any files you backed up separately.

For full-system changes (kernel, firmware, system packages), reflashing remains the expected upgrade path. App-level changes are handled by OTA updates.

### If the first boot of Potato seems stuck

Give first boot a few minutes, especially on the first model download. If the web UI never comes up:

- confirm the Pi has power and network access
- try `http://potato.local` again after a few minutes
- if it still does not recover, reflash the card and start clean or return to your previous image

There is no in-place uninstall flow for the flashed image path today.

## Quick Troubleshooting Before You Reflash

If Potato installed but the UI is not reachable or the service seems unhealthy, check:

```bash
systemctl status potato --no-pager
journalctl -u potato -e
```

These are the same commands surfaced by the installer after completion. If you are trying to get back to a known-good setup quickly, checking these briefly and then reflashing is a reasonable MVP workflow.

If `potato.local` does not open:

- make sure the Pi finished booting
- verify the Pi and your browser are on the same network
- try the Pi's IP address directly if mDNS name resolution is not working on your network
- check the service status and logs above

## What This Guide Does Not Promise

This guide is intentionally lightweight for MVP.

It does not provide:

- full disaster recovery for every failure mode
- automatic rollback of system configuration changes
- recovery of data that was not backed up first

App-level OTA updates are now supported for routine `core/` and `bin/` changes. For full-system upgrades (kernel, firmware, system packages), reflashing remains the expected path.
