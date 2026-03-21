"""
cache.py - Deterministic cache key computation for build steps.

Cache key is SHA-256 of:
  - Previous layer digest (or base image manifest digest for first layer step)
  - Full instruction text as written
  - Current WORKDIR value
  - Current ENV state (sorted key=value pairs)
  - COPY only: SHA-256 of each source file, sorted by path
"""

import hashlib
import json


def compute_cache_key(prev_digest, instruction_text, workdir, env_state, copy_files_hash=None):
    """
    Compute a deterministic cache key.

    Args:
        prev_digest:       digest of the previous layer (str, e.g. "sha256:abc...")
                           or the base image manifest digest for the first layer step.
        instruction_text:  full raw instruction line from Docksmithfile (str)
        workdir:           current WORKDIR value (str, empty string if not set)
        env_state:         dict of all ENV key=value accumulated so far
        copy_files_hash:   for COPY only — hex digest of source files (str or None)

    Returns:
        hex string (SHA-256) used as cache key
    """
    h = hashlib.sha256()

    # 1. Previous layer digest
    h.update((prev_digest or "").encode())

    # 2. Instruction text
    h.update(instruction_text.encode())

    # 3. WORKDIR
    h.update((workdir or "").encode())

    # 4. ENV state — sorted by key for determinism
    if env_state:
        sorted_env = sorted(env_state.items())
        env_str = "\n".join(f"{k}={v}" for k, v in sorted_env)
    else:
        env_str = ""
    h.update(env_str.encode())

    # 5. COPY source files hash (if applicable)
    if copy_files_hash:
        h.update(copy_files_hash.encode())

    return h.hexdigest()
