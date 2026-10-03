"""Fail-closed path helpers for coordinator-owned worker workspace files."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile


def _absolute_without_traversal(path: str | os.PathLike[str]) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ValueError(f"Worker path must be absolute: {candidate}")
    if ".." in candidate.parts:
        raise ValueError(f"Worker path contains traversal: {candidate}")
    return candidate


def checked_directory(path: str | os.PathLike[str], *, create: bool = False) -> Path:
    """Return an absolute directory after rejecting symlink path components.

    With ``create=True``, missing components are created one at a time and
    checked immediately. This is for coordinator setup before a worker starts.
    """
    target = _absolute_without_traversal(path)
    current = Path(target.anchor)
    for part in target.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if not create:
                raise ValueError(f"Worker directory does not exist: {current}")
            try:
                current.mkdir()
            except FileExistsError:
                pass
            try:
                info = current.lstat()
            except FileNotFoundError as exc:
                raise ValueError(f"Worker directory disappeared: {current}") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"Worker directory contains a non-directory or symlink: {current}")
    return target


def checked_regular_file(path: str | os.PathLike[str]) -> Path:
    """Require an existing single-link regular file beneath safe directories."""
    target = _absolute_without_traversal(path)
    checked_directory(target.parent)
    try:
        info = target.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"Worker output file does not exist: {target}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError(f"Worker output is not a single-link regular file: {target}")
    return target


def _relative_parts(relative: str | os.PathLike[str]) -> tuple[str, ...]:
    value = Path(relative)
    if value.is_absolute() or not value.parts or any(part in {"", ".", ".."} for part in value.parts):
        raise ValueError(f"Managed workspace path must be a safe relative path: {value}")
    return value.parts


def atomic_write_managed(root: str | os.PathLike[str], relative: str | os.PathLike[str],
                         content: bytes, *, mode: int = 0o644) -> Path:
    """Atomically replace a managed file without following a leaf alias.

    Parent directories are coordinator-owned and may not contain symlinks.
    An existing leaf symlink or hardlink is replaced as a directory entry;
    its target is never opened or modified.
    """
    root_path = checked_directory(root)
    parts = _relative_parts(relative)
    parent = checked_directory(root_path.joinpath(*parts[:-1]), create=True) if len(parts) > 1 else root_path
    target = parent / parts[-1]
    try:
        previous = target.lstat()
    except FileNotFoundError:
        previous = None
    if previous is not None and stat.S_ISDIR(previous.st_mode):
        raise ValueError(f"Managed output is a directory: {target}")

    fd, temporary_name = tempfile.mkstemp(prefix=".worker-input-", dir=parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        # Recheck the parent immediately before replacing the directory entry.
        checked_directory(parent)
        os.replace(temporary, target)
        return checked_regular_file(target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
