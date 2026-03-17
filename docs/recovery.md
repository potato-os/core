# Recovery, Uninstall, and Rollback

Potato OS is still experimental. For MVP, the recovery story is simple: keep backups and be ready to reflash back to Raspberry Pi OS, another known-good image, or a newer Potato OS image.

## Before You Start

Back up anything you care about before you flash the image.

At minimum, back up:

- your current microSD card if you may want to return to it later
- any local files on the Pi you do not want to lose
- any files on the card that only exist on the current system image

Potato OS does not currently provide OTA updates, a one-click rollback flow, or a built-in backup feature.

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

This is also the expected way to move between MVP releases until there is a supported in-place update path.

### If the first boot of Potato seems stuck

Give first boot a few minutes, especially on the first model download. If the web UI never comes up:

- confirm the Pi has power and network access
- try `http://potato.local` again after a few minutes
- if it still does not recover, reflash the card and start clean or return to your previous image

There is no in-place uninstall flow for the flashed image path today, and there is no promise that one MVP Potato OS image can upgrade another in place.

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
- in-place upgrades between Potato OS MVP images
- automatic rollback of system configuration changes
- recovery of data that was not backed up first

If you want the safest escape hatch, keep a backup of your working SD card and expect reflashing to be the normal recovery and upgrade path for now.
