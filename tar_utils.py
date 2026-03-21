import tarfile
import io
import os
import fnmatch


def make_layer_tar(src_dir, dest_prefix=""):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        entries = []
        for root, dirs, files in os.walk(src_dir):
            dirs.sort()
            for fname in sorted(files):
                entries.append(os.path.join(root, fname))
            for d in sorted(dirs):
                entries.append(os.path.join(root, d))

        def archive_name(full_path):
            rel = os.path.relpath(full_path, src_dir)
            if dest_prefix:
                return os.path.join(dest_prefix.lstrip("/"), rel)
            return rel

        entries.sort(key=archive_name)

        for full_path in entries:
            arcname = archive_name(full_path)
            info = tar.gettarinfo(full_path, arcname=arcname)
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            if os.path.isfile(full_path):
                with open(full_path, "rb") as f:
                    tar.addfile(info, f)
            else:
                tar.addfile(info)

    return buf.getvalue()


def make_copy_layer_tar(context_dir, src_pattern, dest_path, temp_root):
    import glob
    import shutil

    matched = _resolve_glob(context_dir, src_pattern)
    if not matched:
        raise ValueError(f"COPY: no files matched pattern '{src_pattern}' in context '{context_dir}'")

    dest_abs = os.path.join(temp_root, dest_path.lstrip("/"))

    copied = []
    for src_full in matched:
        if os.path.isfile(src_full):
            if dest_path.endswith("/") or os.path.isdir(dest_abs):
                dst = os.path.join(dest_abs, os.path.basename(src_full))
            else:
                dst = dest_abs
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src_full, dst)
            copied.append(dst)
        elif os.path.isdir(src_full):
            dst = os.path.join(dest_abs, os.path.basename(src_full))
            shutil.copytree(src_full, dst, dirs_exist_ok=True)
            copied.append(dst)

    tar_bytes = _tar_subtree(temp_root, dest_abs)
    return tar_bytes, copied


def _resolve_glob(context_dir, pattern):
    import glob as _glob
    if pattern == ".":
        results = []
        for root, dirs, files in os.walk(context_dir):
            dirs.sort()
            for fname in sorted(files):
                results.append(os.path.join(root, fname))
        return sorted(results)
    full_pattern = os.path.join(context_dir, pattern)
    matches = _glob.glob(full_pattern, recursive=True)
    return sorted(matches)


def _tar_subtree(root, subtree_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        entries = []
        if os.path.isfile(subtree_path):
            entries = [subtree_path]
        else:
            for dirpath, dirs, files in os.walk(subtree_path):
                dirs.sort()
                for fname in sorted(files):
                    entries.append(os.path.join(dirpath, fname))
                for d in sorted(dirs):
                    entries.append(os.path.join(dirpath, d))

        entries.sort(key=lambda p: os.path.relpath(p, root))

        for full_path in entries:
            arcname = os.path.relpath(full_path, root)
            info = tar.gettarinfo(full_path, arcname=arcname)
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            if os.path.isfile(full_path):
                with open(full_path, "rb") as f:
                    tar.addfile(info, f)
            else:
                tar.addfile(info)

    return buf.getvalue()


def extract_layer(tar_bytes, dest_dir):
    buf = io.BytesIO(tar_bytes)
    with tarfile.open(fileobj=buf, mode="r") as tar:
        for member in tar.getmembers():
            member_path = os.path.realpath(os.path.join(dest_dir, member.name))
            if not member_path.startswith(os.path.realpath(dest_dir)):
                raise ValueError(f"Unsafe tar path: {member.name}")
        tar.extractall(path=dest_dir)


def hash_files_for_copy(context_dir, src_pattern):
    import hashlib

    if src_pattern == ".":
        walk_root = context_dir
    else:
        full_path = os.path.join(context_dir, src_pattern)
        walk_root = full_path if os.path.isdir(full_path) else None

    h = hashlib.sha256()

    if walk_root:
        all_files = []
        for root, dirs, files in os.walk(walk_root):
            dirs.sort()
            for fname in sorted(files):
                all_files.append(os.path.join(root, fname))
        for fpath in sorted(all_files):
            rel = os.path.relpath(fpath, context_dir)
            h.update(rel.encode())
            with open(fpath, "rb") as f:
                h.update(f.read())
        return h.hexdigest()

    matched = _resolve_glob(context_dir, src_pattern)
    for fpath in sorted(matched):
        if os.path.isfile(fpath):
            rel = os.path.relpath(fpath, context_dir)
            h.update(rel.encode())
            with open(fpath, "rb") as f:
                h.update(f.read())
    return h.hexdigest()
