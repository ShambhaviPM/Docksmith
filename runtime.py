"""
runtime.py - Container runtime.

Assembles the image filesystem from layers, then runs a process in isolation.
Isolation uses Linux namespaces via 'unshare' + chroot.

The SAME isolation is used for:
  - docksmith run (user-facing)
  - RUN instructions during build
"""

import os
import sys
import shutil
import tempfile
import subprocess
import shlex

from tar_utils import extract_layer


class Runtime:
    def __init__(self, store):
        self.store = store

    def assemble_rootfs(self, manifest, tmpdir):
        """
        Extract all layers in order into tmpdir.
        Later layers overwrite earlier ones (union-like).
        """
        for layer in manifest.get("layers", []):
            digest = layer["digest"]
            if not self.store.has_layer(digest):
                print(f"Error: layer {digest[:19]} is missing from the layer store.")
                sys.exit(1)
            tar_bytes = self.store.load_layer(digest)
            extract_layer(tar_bytes, tmpdir)

    def run(self, manifest, cmd_override=None, env_overrides=None):
        """
        Run a container from a manifest.
        cmd_override: list of strings, overrides image CMD
        env_overrides: dict of KEY->VALUE that override image ENV
        """
        config = manifest.get("config", {})

        # Resolve command
        cmd = cmd_override if cmd_override else config.get("Cmd")
        if not cmd:
            print("Error: no CMD defined in image and no command given at runtime.")
            sys.exit(1)

        # Resolve working directory
        workdir = config.get("WorkingDir", "/")
        if not workdir:
            workdir = "/"

        # Resolve environment
        env = {}
        for e in config.get("Env", []):
            if "=" in e:
                k, v = e.split("=", 1)
                env[k] = v
        if env_overrides:
            env.update(env_overrides)

        # Assemble rootfs in a temp directory
        tmpdir = tempfile.mkdtemp(prefix="docksmith_run_")
        try:
            self.assemble_rootfs(manifest, tmpdir)
            exit_code = self._run_isolated(tmpdir, cmd, workdir, env)
            print(f"\nContainer exited with code {exit_code}")
            sys.exit(exit_code)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def run_for_build(self, rootfs_dir, cmd_str, workdir, env):
        """
        Run a shell command inside rootfs_dir during build (for RUN instruction).
        Uses the same isolation as `docksmith run`.

        Returns exit code.
        """
        cmd = ["/bin/sh", "-c", cmd_str]
        return self._run_isolated(rootfs_dir, cmd, workdir, env)

    def _run_isolated(self, rootfs, cmd, workdir, env):
        """
        Run cmd inside rootfs with Linux namespace isolation.

        Strategy:
          We use a Python re-exec trick:
          1. This process calls itself with a special env var set
          2. The child enters new namespaces (mount + pid) via unshare(2)
             then does chroot + exec.

        We use 'unshare' (from util-linux) as a wrapper because calling
        unshare(2) + pivot_root from Python directly requires careful
        fork/exec ordering. Using the 'unshare' binary is simpler and
        equally correct.

        The child script:
          - chroots into rootfs
          - sets workdir
          - execs cmd
        """
        # Build the environment for the container process
        container_env = dict(env)
        container_env["PATH"] = container_env.get(
            "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        )

        # Write a small bootstrap script that will run INSIDE the namespace
        # before chrooting. We pass it via a temp file inside rootfs so it's
        # accessible after chroot.
        bootstrap = self._make_bootstrap_script(cmd, workdir, container_env)

        # Write bootstrap to a temp location on the HOST (not inside rootfs)
        # We'll pass the rootfs path and script content via env vars to the
        # helper process.
        helper_env = os.environ.copy()
        helper_env["_DOCKSMITH_ROOTFS"] = rootfs
        helper_env["_DOCKSMITH_CMD"] = " ".join(_shell_quote(c) for c in cmd) if isinstance(cmd, list) else cmd
        helper_env["_DOCKSMITH_WORKDIR"] = workdir
        helper_env["_DOCKSMITH_ENV"] = "\n".join(f"{k}={v}" for k, v in container_env.items())
        helper_env["_DOCKSMITH_REEXEC"] = "1"

        # Use 'unshare' to create new mount + pid + uts namespaces
        # --mount: new mount namespace (so mounts don't leak to host)
        # --pid --fork: new PID namespace
        # --uts: new UTS namespace (hostname isolation)
        # --map-root-user: map current user to root inside namespace (for rootless)
        unshare_cmd = [
            "unshare",
            "--mount",
            "--pid",
            "--fork",
            "--uts",
            "--map-root-user",
            sys.executable,           # re-exec ourselves
            os.path.abspath(__file__), # this file
            "--_reexec_container",
        ]

        try:
            result = subprocess.run(unshare_cmd, env=helper_env)
            return result.returncode
        except FileNotFoundError:
            print("Error: 'unshare' not found. Install it with: sudo apt install util-linux")
            sys.exit(1)

    def _make_bootstrap_script(self, cmd, workdir, env):
        # Not used directly — kept for reference
        pass


def _shell_quote(s):
    """Simple shell quoting."""
    return shlex.quote(s)


def _reexec_container():
    """
    Called when we are re-exec'd inside the new namespace.
    At this point we are 'root' in the new user namespace.
    We:
      1. Mount /proc inside the new rootfs
      2. chroot into rootfs
      3. Set working directory
      4. Set environment
      5. exec the command
    """
    rootfs  = os.environ.pop("_DOCKSMITH_ROOTFS")
    cmd_str = os.environ.pop("_DOCKSMITH_CMD")
    workdir = os.environ.pop("_DOCKSMITH_WORKDIR", "/")
    env_str = os.environ.pop("_DOCKSMITH_ENV", "")

    # Build clean environment for container
    env = {}
    for line in env_str.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            env[k] = v

    env["PATH"] = env.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")

    # Mount /proc inside rootfs so processes work correctly
    proc_dir = os.path.join(rootfs, "proc")
    os.makedirs(proc_dir, exist_ok=True)

    try:
        # Mount proc (requires new mount namespace, which unshare --mount gave us)
        subprocess.run(
            ["mount", "-t", "proc", "proc", proc_dir],
            check=False  # Don't fail if proc already mounted
        )
    except Exception:
        pass

    # chroot into rootfs
    try:
        os.chroot(rootfs)
    except PermissionError:
        print("Error: chroot failed. Are you running with --map-root-user in unshare?")
        sys.exit(1)

    # Set working directory inside the container
    if not workdir:
        workdir = "/"
    try:
        os.chdir(workdir)
    except FileNotFoundError:
        os.chdir("/")

    # exec the command
    if isinstance(cmd_str, str):
        final_cmd = ["/bin/sh", "-c", cmd_str]
    else:
        final_cmd = cmd_str

    try:
        os.execvpe(final_cmd[0], final_cmd, env)
    except FileNotFoundError:
        # Try with sh -c as fallback
        os.execvpe("/bin/sh", ["/bin/sh", "-c", cmd_str], env)


# ── Entry point when re-exec'd inside namespace ──────────────────────────────

if __name__ == "__main__" or (len(sys.argv) > 1 and sys.argv[-1] == "--_reexec_container"):
    if os.environ.get("_DOCKSMITH_REEXEC") == "1":
        import subprocess
        _reexec_container()
