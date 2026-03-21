#!/usr/bin/env python3
"""
setup_base_image.py - One-time setup script.

Downloads a minimal Alpine Linux rootfs and imports it into ~/.docksmith/
as a base image. After this runs, builds work fully offline.

Usage:
  python3 setup_base_image.py

What it does:
  1. Downloads alpine-minirootfs tar.gz from alpinelinux.org
  2. Splits it into a single layer
  3. Writes the manifest to ~/.docksmith/images/alpine_3.18.json
"""

import os
import sys
import json
import hashlib
import tarfile
import io
import urllib.request
import shutil
import tempfile
from datetime import datetime, timezone


DOCKSMITH_DIR = os.path.expanduser("~/.docksmith")
IMAGES_DIR = os.path.join(DOCKSMITH_DIR, "images")
LAYERS_DIR = os.path.join(DOCKSMITH_DIR, "layers")
CACHE_DIR  = os.path.join(DOCKSMITH_DIR, "cache")

# Alpine minirootfs — a tiny (~2.7MB) rootfs, perfect as a base image
ALPINE_URL = "https://dl-cdn.alpinelinux.org/alpine/v3.18/releases/x86_64/alpine-minirootfs-3.18.4-x86_64.tar.gz"
ALPINE_NAME = "alpine"
ALPINE_TAG  = "3.18"


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def compute_manifest_digest(manifest: dict) -> str:
    m = dict(manifest)
    m["digest"] = ""
    serialized = json.dumps(m, separators=(",", ":"), sort_keys=True).encode()
    return "sha256:" + hashlib.sha256(serialized).hexdigest()


def make_reproducible_tar(input_tar_gz_bytes: bytes) -> bytes:
    """
    Re-pack the alpine tar.gz into a reproducible tar:
      - All entries sorted by name
      - All timestamps zeroed
      - uid/gid zeroed, uname/gname cleared
    Returns raw tar bytes (not gzipped).
    """
    src_buf = io.BytesIO(input_tar_gz_bytes)
    out_buf = io.BytesIO()

    with tarfile.open(fileobj=src_buf, mode="r:gz") as src_tar:
        members = src_tar.getmembers()
        # Sort for reproducibility
        members.sort(key=lambda m: m.name)

        with tarfile.open(fileobj=out_buf, mode="w") as out_tar:
            for member in members:
                member.mtime  = 0
                member.uid    = 0
                member.gid    = 0
                member.uname  = ""
                member.gname  = ""

                if member.isfile():
                    f = src_tar.extractfile(member)
                    out_tar.addfile(member, f)
                else:
                    out_tar.addfile(member)

    return out_buf.getvalue()


def main():
    # Check if already imported
    image_path = os.path.join(IMAGES_DIR, f"{ALPINE_NAME}_{ALPINE_TAG}.json")
    if os.path.isfile(image_path):
        print(f"Base image {ALPINE_NAME}:{ALPINE_TAG} already imported. Nothing to do.")
        print(f"To re-import, delete: {image_path}")
        return

    print(f"Setting up docksmith directories...")
    for d in [IMAGES_DIR, LAYERS_DIR, CACHE_DIR]:
        os.makedirs(d, exist_ok=True)

    print(f"Downloading Alpine {ALPINE_TAG} minirootfs (~2.7MB)...")
    print(f"  URL: {ALPINE_URL}")

    try:
        with urllib.request.urlopen(ALPINE_URL, timeout=60) as resp:
            raw_gz = resp.read()
    except Exception as e:
        print(f"Error downloading Alpine: {e}")
        print("\nIf you have no internet access, manually download the file and run:")
        print(f"  python3 setup_base_image.py --local <path-to-alpine-minirootfs.tar.gz>")
        sys.exit(1)

    print(f"Downloaded {len(raw_gz):,} bytes. Repacking as reproducible tar...")
    tar_bytes = make_reproducible_tar(raw_gz)
    layer_digest = sha256_bytes(tar_bytes)
    layer_size = len(tar_bytes)

    print(f"Layer digest: {layer_digest[:26]}...")

    # Save layer
    layer_filename = layer_digest.replace("sha256:", "sha256_")
    layer_path = os.path.join(LAYERS_DIR, layer_filename)
    with open(layer_path, "wb") as f:
        f.write(tar_bytes)
    print(f"Saved layer to {layer_path}")

    # Build manifest
    now = datetime.now(timezone.utc).isoformat()
    manifest = {
        "name": ALPINE_NAME,
        "tag": ALPINE_TAG,
        "digest": "",
        "created": now,
        "config": {
            "Env": ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"],
            "Cmd": ["/bin/sh"],
            "WorkingDir": "",
        },
        "layers": [
            {
                "digest": layer_digest,
                "size": layer_size,
                "createdBy": f"alpine:{ALPINE_TAG} base layer",
            }
        ],
    }

    manifest["digest"] = compute_manifest_digest(manifest)

    # Save manifest
    with open(image_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"\n✓ Successfully imported {ALPINE_NAME}:{ALPINE_TAG}")
    print(f"  Manifest: {image_path}")
    print(f"  Digest:   {manifest['digest'][:26]}...")
    print(f"\nYou can now build images with: FROM alpine:3.18")


def main_local(gz_path):
    """Import from a local .tar.gz file instead of downloading."""
    image_path = os.path.join(IMAGES_DIR, f"{ALPINE_NAME}_{ALPINE_TAG}.json")

    for d in [IMAGES_DIR, LAYERS_DIR, CACHE_DIR]:
        os.makedirs(d, exist_ok=True)

    with open(gz_path, "rb") as f:
        raw_gz = f.read()

    print(f"Repacking {gz_path} as reproducible tar...")
    tar_bytes = make_reproducible_tar(raw_gz)
    layer_digest = sha256_bytes(tar_bytes)
    layer_size = len(tar_bytes)

    layer_filename = layer_digest.replace("sha256:", "sha256_")
    layer_path = os.path.join(LAYERS_DIR, layer_filename)
    with open(layer_path, "wb") as f:
        f.write(tar_bytes)

    now = datetime.now(timezone.utc).isoformat()
    manifest = {
        "name": ALPINE_NAME,
        "tag": ALPINE_TAG,
        "digest": "",
        "created": now,
        "config": {
            "Env": ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"],
            "Cmd": ["/bin/sh"],
            "WorkingDir": "",
        },
        "layers": [
            {
                "digest": layer_digest,
                "size": layer_size,
                "createdBy": f"alpine:{ALPINE_TAG} base layer",
            }
        ],
    }
    manifest["digest"] = compute_manifest_digest(manifest)

    with open(image_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"✓ Imported {ALPINE_NAME}:{ALPINE_TAG} from local file")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--local":
        main_local(sys.argv[2])
    else:
        main()
