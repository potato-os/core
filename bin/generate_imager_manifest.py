#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import shutil
import subprocess
from datetime import date
from pathlib import Path


DEFAULT_ICON = "potato-imager-icon.svg"
DEFAULT_DISPLAY_NAME = "Potato OS (Raspberry Pi 4 / 5)"
DEFAULT_DESCRIPTION = "Potato OS — local AI, zero cloud dependency."


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_size_from_xz(image_path: Path) -> int | None:
    if shutil.which("xz") is None:
        return None
    proc = subprocess.run(
        ["xz", "--robot", "--list", str(image_path)],
        check=False,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        if not line.startswith("totals\t"):
            continue
        columns = line.split("\t")
        if len(columns) < 5:
            continue
        try:
            return int(columns[4])
        except ValueError:
            return None
    return None


def xz_extract_sha256_and_size(image_path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total_size = 0
    with lzma.open(image_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            total_size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), total_size


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a Raspberry Pi Imager manifest for a built Potato OS image. "
            "Manifest is intentionally restricted to Raspberry Pi 5 (pi5-64bit)."
        )
    )
    parser.add_argument("--image", required=True, help="Path to local image (.img or .img.xz).")
    parser.add_argument("--output", required=True, help="Output .rpi-imager-manifest path.")
    parser.add_argument("--name", default=DEFAULT_DISPLAY_NAME, help="Display name shown in Imager.")
    parser.add_argument("--description", default=DEFAULT_DESCRIPTION, help="Description shown in Imager.")
    parser.add_argument("--icon", default=DEFAULT_ICON, help="OS icon URL/path.")
    parser.add_argument("--website", default="", help="Optional website URL.")
    parser.add_argument(
        "--download-url",
        default="",
        help="Optional image URL. Defaults to file:// URL for local images.",
    )
    parser.add_argument(
        "--release-date",
        default=date.today().isoformat(),
        help="Release date in YYYY-MM-DD format.",
    )
    parser.add_argument("--init-format", default="systemd", help="Imager init_format value.")
    parser.add_argument("--architecture", default="armv8", help="Imager architecture value.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    image_path = Path(args.image).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    if not image_path.is_file():
        raise SystemExit(f"Image not found: {image_path}")

    image_download_size = image_path.stat().st_size
    image_download_sha256 = sha256_file(image_path)

    if image_path.suffix == ".xz":
        extract_sha256, extracted_size = xz_extract_sha256_and_size(image_path)
        extract_size = extract_size_from_xz(image_path) or extracted_size
    else:
        extract_sha256 = image_download_sha256
        extract_size = image_download_size

    image_url = args.download_url.strip() or image_path.as_uri()
    icon_value = args.icon
    icon_path = Path(icon_value)
    if icon_path.is_file():
        # Resolve the icon path relative to the output manifest directory so
        # the manifest stays self-contained within its bundle directory.
        try:
            rel = icon_path.resolve().relative_to(output_path.parent.resolve())
            icon_value = str(rel)
        except ValueError:
            icon_value = icon_path.resolve().as_uri()
    if output_path.suffix not in {".json", ".rpi-imager-manifest"}:
        raise SystemExit("Output must end with .json or .rpi-imager-manifest")

    os_entry: dict[str, object] = {
        "name": args.name,
        "description": args.description,
        "icon": icon_value,
        "url": image_url,
        "extract_size": extract_size,
        "extract_sha256": extract_sha256,
        "image_download_size": image_download_size,
        "image_download_sha256": image_download_sha256,
        "release_date": args.release_date,
        "devices": ["pi5-64bit", "pi4-64bit"],
        "init_format": args.init_format,
        "architecture": args.architecture,
    }
    if args.website.strip():
        os_entry["website"] = args.website.strip()

    payload = {
        "imager": {
            "devices": [
                {
                    "name": "Raspberry Pi 5",
                    "description": "Raspberry Pi 5, 500 / 500+, and Compute Module 5",
                    "tags": ["pi5-64bit", "pi5-32bit"],
                    "matching_type": "exclusive",
                    "capabilities": [],
                },
                {
                    "name": "Raspberry Pi 4",
                    "description": "Raspberry Pi 4 Model B (8 GB)",
                    "tags": ["pi4-64bit", "pi4-32bit"],
                    "matching_type": "exclusive",
                    "capabilities": [],
                },
            ]
        },
        "os_list": [os_entry],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote Pi 5-only manifest: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
