import tarfile
import io
import os
import fnmatch


def _iter_files_recursive(path):
    files = []
    for root, dirs, filenames in os.walk(path):
        dirs.sort()
        for fname in sorted(filenames):
            files.append(os.path.join(root, fname))
    return files


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
        return sorted(_iter_files_recursive(context_dir))

    # Plain path (no glob chars) should resolve deterministically to existing path.
    has_glob = any(ch in pattern for ch in "*?[")
    if not has_glob:
        candidate = os.path.join(context_dir, pattern)
        if os.path.exists(candidate):
            return [candidate]
        return []

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

    h = hashlib.sha256()

    file_set = set()
    matched = _resolve_glob(context_dir, src_pattern)
    for path in matched:
        if os.path.isfile(path):
            file_set.add(path)
        elif os.path.isdir(path):
            for fpath in _iter_files_recursive(path):
                file_set.add(fpath)

    for fpath in sorted(file_set):
        if os.path.isfile(fpath):
            rel = os.path.relpath(fpath, context_dir)
            h.update(rel.encode())
            with open(fpath, "rb") as f:
                h.update(f.read())
    return h.hexdigest()


def apply_whiteouts(root_dir, tar_bytes):
    """
    Apply OCI-style whiteout entries from a layer tar.
    A whiteout is encoded as a file named '.wh.<name>' and means
    the sibling path '<name>' should be removed from lower layers.
    """
    buf = io.BytesIO(tar_bytes)
    with tarfile.open(fileobj=buf, mode="r") as tar:
        for member in tar.getmembers():
            base = os.path.basename(member.name)
            if not base.startswith(".wh."):
                continue

            dir_part = os.path.dirname(member.name)
            target_name = base[4:]
            if not target_name:
                continue

            target_rel = os.path.normpath(os.path.join(dir_part, target_name))
            target_abs = os.path.realpath(os.path.join(root_dir, target_rel))
            root_abs = os.path.realpath(root_dir)
            if not target_abs.startswith(root_abs):
                continue

            if os.path.isdir(target_abs):
                import shutil
                shutil.rmtree(target_abs, ignore_errors=True)
            elif os.path.exists(target_abs):
                os.remove(target_abs)
