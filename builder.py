"""
builder.py - Build engine.

Parses Docksmithfile and executes instructions:
  FROM, COPY, RUN, WORKDIR, ENV, CMD

Each COPY and RUN produces an immutable delta layer (tar).
Layers are content-addressed by SHA-256 of their tar bytes.
"""

import os
import sys
import json
import time
import hashlib
import shutil
import tempfile
from datetime import datetime, timezone

from tar_utils import make_copy_layer_tar, extract_layer, hash_files_for_copy, apply_whiteouts
from cache import compute_cache_key
from runtime import Runtime


VALID_INSTRUCTIONS = {"FROM", "COPY", "RUN", "WORKDIR", "ENV", "CMD"}


def sha256_of_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def compute_manifest_digest(manifest: dict) -> str:
    """
    Compute digest by serializing manifest with digest="" then hashing.
    """
    m = dict(manifest)
    m["digest"] = ""
    serialized = json.dumps(m, separators=(",", ":"), sort_keys=True).encode()
    return "sha256:" + hashlib.sha256(serialized).hexdigest()


class Builder:
    def __init__(self, store, context_dir, no_cache=False):
        self.store = store
        self.context_dir = context_dir
        self.no_cache = no_cache
        self.runtime = Runtime(store)

    def build(self, docksmithfile_path, name, tag):
        instructions = self._parse(docksmithfile_path)

        # Build state
        layers = []           # accumulated layer dicts (digest, size, createdBy)
        base_env_state = {}   # inherited FROM base image
        env_overrides = {}    # ENV updates in current build
        workdir = ""          # current WORKDIR
        cmd = None            # CMD value
        base_manifest = None  # manifest of FROM image
        prev_digest = None    # digest of previous layer (for cache key)
        cache_busted = False  # once True, all remaining steps are CACHE MISS

        total_steps = len(instructions)
        build_start = time.time()

        for step_num, (lineno, instruction, args) in enumerate(instructions, 1):
            print(f"Step {step_num}/{total_steps} : {instruction} {args}", end="", flush=True)

            # ── FROM ────────────────────────────────────────────────────────
            if instruction == "FROM":
                print()  # newline — FROM has no cache status
                image_name, image_tag = self._parse_from_args(args, lineno)
                base_manifest = self.store.get_image(image_name, image_tag)
                if base_manifest is None:
                    print(f"Error (line {lineno}): base image '{image_name}:{image_tag}' not found in local store.")
                    print("Run setup_base_image.py first.")
                    sys.exit(1)

                # Inherit base layers
                layers = list(base_manifest.get("layers", []))
                base_config = base_manifest.get("config", {})
                base_env_state = {}
                env_overrides = {}
                for e in base_config.get("Env", []):
                    if "=" in e:
                        k, v = e.split("=", 1)
                        base_env_state[k] = v
                workdir = base_config.get("WorkingDir", "")
                cmd = base_config.get("Cmd")

                # The "prev_digest" for the first layer-producing step is the base manifest digest
                prev_digest = base_manifest.get("digest", "")
                continue

            # ── WORKDIR ─────────────────────────────────────────────────────
            if instruction == "WORKDIR":
                print()
                workdir = args.strip()
                continue

            # ── ENV ─────────────────────────────────────────────────────────
            if instruction == "ENV":
                print()
                key, _, value = args.partition("=")
                env_overrides[key.strip()] = value.strip()
                continue

            # ── CMD ─────────────────────────────────────────────────────────
            if instruction == "CMD":
                print()
                try:
                    cmd = json.loads(args.strip())
                    if not isinstance(cmd, list):
                        raise ValueError
                except (json.JSONDecodeError, ValueError):
                    print(f"Error (line {lineno}): CMD must be a JSON array, e.g. [\"python\", \"main.py\"]")
                    sys.exit(1)
                continue

            # ── COPY ─────────────────────────────────────────────────────────
            if instruction == "COPY":
                src, dest = self._parse_copy_args(args, lineno)
                full_instruction = f"COPY {args}"

                # Cache key
                files_hash = hash_files_for_copy(self.context_dir, src)
                env_state = _merge_env(base_env_state, env_overrides)
                cache_key = compute_cache_key(prev_digest, full_instruction, workdir, env_state, files_hash)

                step_start = time.time()
                hit = self._cache_lookup(cache_key) if not cache_busted else None

                if hit:
                    print(f" [CACHE HIT] {time.time()-step_start:.2f}s")
                    layer_digest = hit
                    layer_size = os.path.getsize(self.store.layer_path(layer_digest))
                    layers.append({"digest": layer_digest, "size": layer_size, "createdBy": full_instruction})
                    prev_digest = layer_digest
                else:
                    print(f" [CACHE MISS]", end="", flush=True)
                    cache_busted = True

                    # Assemble current rootfs to copy into
                    tmpdir = tempfile.mkdtemp(prefix="docksmith_build_")
                    try:
                        self._assemble_rootfs(layers, tmpdir)
                        self._ensure_workdir(tmpdir, workdir)

                        tar_bytes, _ = make_copy_layer_tar(self.context_dir, src, dest, tmpdir)
                        layer_digest = sha256_of_bytes(tar_bytes)

                        self.store.save_layer(layer_digest, tar_bytes)
                        if not self.no_cache:
                            self.store.cache_set(cache_key, layer_digest)

                        elapsed = time.time() - step_start
                        print(f" {elapsed:.2f}s")

                        layer_size = len(tar_bytes)
                        layers.append({"digest": layer_digest, "size": layer_size, "createdBy": full_instruction})
                        prev_digest = layer_digest
                    finally:
                        shutil.rmtree(tmpdir, ignore_errors=True)
                continue

            # ── RUN ──────────────────────────────────────────────────────────
            if instruction == "RUN":
                full_instruction = f"RUN {args}"
                env_state = _merge_env(base_env_state, env_overrides)
                cache_key = compute_cache_key(prev_digest, full_instruction, workdir, env_state)

                step_start = time.time()
                hit = self._cache_lookup(cache_key) if not cache_busted else None

                if hit:
                    print(f" [CACHE HIT] {time.time()-step_start:.2f}s")
                    layer_digest = hit
                    layer_size = os.path.getsize(self.store.layer_path(layer_digest))
                    layers.append({"digest": layer_digest, "size": layer_size, "createdBy": full_instruction})
                    prev_digest = layer_digest
                else:
                    print(f" [CACHE MISS]", end="", flush=True)
                    cache_busted = True

                    # Assemble rootfs, run command inside isolation, capture delta
                    tmpdir = tempfile.mkdtemp(prefix="docksmith_build_")
                    try:
                        self._assemble_rootfs(layers, tmpdir)
                        self._ensure_workdir(tmpdir, workdir)

                        # Snapshot before
                        snapshot_before = _snapshot_dir(tmpdir)

                        # Run command inside isolated container filesystem
                        run_env = dict(env_state)
                        exit_code = self.runtime.run_for_build(tmpdir, args, workdir, run_env)
                        if exit_code != 0:
                            print(f"\nError: RUN command failed with exit code {exit_code}")
                            sys.exit(exit_code)

                        # Build delta tar of changed/new files
                        tar_bytes = _make_delta_tar(tmpdir, snapshot_before)
                        layer_digest = sha256_of_bytes(tar_bytes)

                        self.store.save_layer(layer_digest, tar_bytes)
                        if not self.no_cache:
                            self.store.cache_set(cache_key, layer_digest)

                        elapsed = time.time() - step_start
                        print(f" {elapsed:.2f}s")

                        layer_size = len(tar_bytes)
                        layers.append({"digest": layer_digest, "size": layer_size, "createdBy": full_instruction})
                        prev_digest = layer_digest
                    finally:
                        shutil.rmtree(tmpdir, ignore_errors=True)
                continue

        # ── Assemble final manifest ──────────────────────────────────────────
        now = datetime.now(timezone.utc).isoformat()

        # If ALL steps were cache hits AND the image already exists, preserve its created timestamp
        existing = self.store.get_image(name, tag)
        if existing and not cache_busted:
            created = existing.get("created", now)
        else:
            created = now

        # Build env list from env_state
        env_state = _merge_env(base_env_state, env_overrides)
        env_list = [f"{k}={v}" for k, v in sorted(env_state.items())]

        manifest = {
            "name": name,
            "tag": tag,
            "digest": "",
            "created": created,
            "config": {
                "Env": env_list,
                "Cmd": cmd,
                "WorkingDir": workdir,
            },
            "layers": layers,
        }

        # Compute and set digest
        manifest["digest"] = compute_manifest_digest(manifest)

        self.store.save_image(manifest)

        total_elapsed = time.time() - build_start
        short_digest = manifest["digest"].replace("sha256:", "")[:12]
        print(f"\nSuccessfully built sha256:{short_digest} {name}:{tag} ({total_elapsed:.2f}s)")

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _parse(self, path):
        """
        Parse Docksmithfile. Returns list of (lineno, INSTRUCTION, args_str).
        """
        instructions = []
        with open(path, "r") as f:
            lines = f.readlines()

        for lineno, raw_line in enumerate(lines, 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split(None, 1)
            instruction = parts[0].upper()
            args = parts[1] if len(parts) > 1 else ""

            if instruction not in VALID_INSTRUCTIONS:
                print(f"Error (line {lineno}): unknown instruction '{instruction}'")
                sys.exit(1)

            instructions.append((lineno, instruction, args))

        return instructions

    def _parse_from_args(self, args, lineno):
        parts = args.strip().split(":")
        if len(parts) == 2:
            return parts[0], parts[1]
        elif len(parts) == 1:
            return parts[0], "latest"
        else:
            print(f"Error (line {lineno}): invalid FROM syntax: '{args}'")
            sys.exit(1)

    def _parse_copy_args(self, args, lineno):
        parts = args.strip().split(None, 1)
        if len(parts) != 2:
            print(f"Error (line {lineno}): COPY requires <src> <dest>")
            sys.exit(1)
        return parts[0], parts[1]

    def _cache_lookup(self, key):
        """Return layer digest on hit (and verify file exists), else None."""
        digest = self.store.cache_get(key)
        if digest and self.store.has_layer(digest):
            return digest
        return None

    def _assemble_rootfs(self, layers, tmpdir):
        """Extract all layers in order into tmpdir."""
        for layer in layers:
            digest = layer["digest"]
            if not self.store.has_layer(digest):
                print(f"Error: layer {digest[:19]} missing from store")
                sys.exit(1)
            tar_bytes = self.store.load_layer(digest)
            extract_layer(tar_bytes, tmpdir)
            apply_whiteouts(tmpdir, tar_bytes)

    def _ensure_workdir(self, rootfs, workdir):
        """Create WORKDIR inside rootfs if it doesn't exist."""
        if workdir:
            full_path = os.path.join(rootfs, workdir.lstrip("/"))
            os.makedirs(full_path, exist_ok=True)


# ── Delta tar helpers ─────────────────────────────────────────────────────────

def _snapshot_dir(path):
    """
    Take a snapshot of all files in path: {rel_path: (mtime, size)}.
    Used to detect what changed after a RUN command.
    """
    snapshot = {}
    for root, dirs, files in os.walk(path):
        for fname in files:
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, path)
            try:
                st = os.stat(full)
                snapshot[rel] = (st.st_mtime, st.st_size)
            except OSError:
                pass
    return snapshot


def _make_delta_tar(rootfs, snapshot_before):
    """
    Create a tar of files that changed, were added, or were deleted since snapshot_before.
    Deletions are represented as whiteout files (.wh.<name>) in the same directory.
    Returns tar bytes.
    """
    import tarfile
    import io

    changed = []
    current_snapshot = {}
    for root, dirs, files in os.walk(rootfs):
        dirs.sort()
        for fname in sorted(files):
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, rootfs)
            try:
                st = os.stat(full)
                current_snapshot[rel] = (st.st_mtime, st.st_size)
                before = snapshot_before.get(rel)
                if before is None or before != (st.st_mtime, st.st_size):
                    changed.append((rel, full))
            except OSError:
                pass

    deleted = sorted(set(snapshot_before.keys()) - set(current_snapshot.keys()))

    # Sort for reproducibility
    changed.sort(key=lambda x: x[0])

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for rel, full in changed:
            info = tar.gettarinfo(full, arcname=rel)
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            with open(full, "rb") as f:
                tar.addfile(info, f)

        for rel in deleted:
            parent = os.path.dirname(rel)
            name = os.path.basename(rel)
            wh_name = os.path.join(parent, f".wh.{name}") if parent else f".wh.{name}"
            info = tarfile.TarInfo(name=wh_name)
            info.size = 0
            info.mode = 0o644
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            tar.addfile(info, io.BytesIO(b""))

    return buf.getvalue()


def _merge_env(base_env_state, env_overrides):
    merged = dict(base_env_state)
    merged.update(env_overrides)
    return merged
