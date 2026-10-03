"""Run a child under a fail-closed, write-scoped Landlock filesystem policy.

This intentionally leaves network syscalls unhandled. The child keeps its
normal executable/tool environment and read access. Filesystem mutation is
allowed beneath explicit per-job roots, for explicitly named existing runtime
files (write/truncate only), and to the exact `/dev/null` device (write only).

Example::

    python -m pipeline.landlock_exec \
      --repository-root /work/graded-readers \
      --writable-root /work/graded-readers/runs/job-123/workspace \
      --cwd /work/graded-readers/runs/job-123/workspace \
      --stdout-log /work/graded-readers/runs/job-123/workspace/worker.stdout \
      --stderr-log /work/graded-readers/runs/job-123/workspace/worker.stderr \
      -- codex exec ...

The log files are opened only after enforcement.  This matters because
Landlock does not revoke access through descriptors opened before the policy.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import os
import platform
import stat
import sys
from pathlib import Path
from typing import Sequence


_LANDLOCK_CREATE_RULESET_VERSION = 1
_LANDLOCK_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38
_O_PATH = getattr(os, "O_PATH", 0o10000000)

# Linux UAPI filesystem access bits.  Rights introduced after ABI 3 are
# omitted; the policy requires ABI 3 for REFER and TRUNCATE protections.
_EXECUTE = 1 << 0
_WRITE_FILE = 1 << 1
_READ_FILE = 1 << 2
_READ_DIR = 1 << 3
_REMOVE_DIR = 1 << 4
_REMOVE_FILE = 1 << 5
_MAKE_CHAR = 1 << 6
_MAKE_DIR = 1 << 7
_MAKE_REG = 1 << 8
_MAKE_SOCK = 1 << 9
_MAKE_FIFO = 1 << 10
_MAKE_BLOCK = 1 << 11
_MAKE_SYM = 1 << 12
_REFER = 1 << 13
_TRUNCATE = 1 << 14
_CLOSE_RANGE_MAX = (1 << 32) - 1

_READ_EXECUTE = _EXECUTE | _READ_FILE | _READ_DIR
_WRITE_RIGHTS = (
    _WRITE_FILE
    | _REMOVE_DIR
    | _REMOVE_FILE
    | _MAKE_CHAR
    | _MAKE_DIR
    | _MAKE_REG
    | _MAKE_SOCK
    | _MAKE_FIFO
    | _MAKE_BLOCK
    | _MAKE_SYM
    | _REFER
    | _TRUNCATE
)
_HANDLED_FS = _READ_EXECUTE | _WRITE_RIGHTS


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _PathBeneathAttr(ctypes.Structure):
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


class LandlockError(RuntimeError):
    """A policy setup or validation error that must stop child execution."""


def _syscall_numbers() -> tuple[int, int, int]:
    # These generic syscall numbers are shared by the supported Linux ABIs.
    # Refuse unknown platforms instead of guessing and risking a false sandbox.
    machine = platform.machine().lower()
    if machine not in {"x86_64", "amd64", "aarch64", "arm64", "riscv64", "ppc64le", "s390x"}:
        raise LandlockError("unsupported Linux architecture")
    if sys.platform != "linux":
        raise LandlockError("Landlock is available only on Linux")
    return 444, 445, 446


def _libc_syscall(number: int, *args: object) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.syscall(ctypes.c_long(number), *args)
    if result < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))
    return int(result)


def landlock_abi() -> int:
    create_nr, _, _ = _syscall_numbers()
    return _libc_syscall(create_nr, ctypes.c_void_p(), ctypes.c_size_t(0), ctypes.c_uint32(_LANDLOCK_CREATE_RULESET_VERSION))


def _canonical_directory(raw: str, *, label: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        raise LandlockError(f"{label} must be an absolute path")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise LandlockError(f"{label} must exist") from exc
    if path != resolved:
        raise LandlockError(f"{label} must not use symlink components")
    if not resolved.is_dir():
        raise LandlockError(f"{label} must be a directory")
    return resolved


def _validate_write_roots(repository_root: str, raw_roots: Sequence[str]) -> tuple[Path, list[Path]]:
    if not raw_roots:
        raise LandlockError("at least one writable root is required")
    repo = _canonical_directory(repository_root, label="repository root")
    roots: list[Path] = []
    for raw in raw_roots:
        root = _canonical_directory(raw, label="writable root")
        try:
            relative = root.relative_to(repo)
        except ValueError as exc:
            raise LandlockError("writable roots must be inside the repository") from exc
        if not relative.parts:
            raise LandlockError("repository root cannot be writable")
        if root not in roots:
            roots.append(root)
    return repo, roots


def _reject_preexisting_hardlinks(roots: Sequence[Path]) -> None:
    """Prevent a preexisting in-root hardlink from aliasing a protected file."""
    for root in roots:
        for current, dirs, files in os.walk(root, followlinks=False):
            # Symlink directories are not traversed, but are valid workspace
            # data; Landlock still checks the resolved target hierarchy.
            dirs[:] = [name for name in dirs if not (Path(current) / name).is_symlink()]
            for name in files:
                item = Path(current) / name
                try:
                    st = item.lstat()
                except OSError as exc:
                    raise LandlockError("could not inspect writable root") from exc
                if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
                    raise LandlockError("writable root contains a preexisting multiply-linked file")


def _open_directory_no_symlinks(path: Path) -> int:
    """Open a canonical directory by walking from / without following links."""
    flags = _O_PATH | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    current_fd = os.open("/", flags)
    try:
        for component in path.parts[1:]:
            next_fd = os.open(component, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        if not stat.S_ISDIR(os.fstat(current_fd).st_mode):
            raise LandlockError("expected directory while pinning writable root")
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _open_path_no_symlinks(path: Path) -> int:
    """Open an existing file through no-follow directory components."""
    if not path.is_absolute() or len(path.parts) < 2:
        raise LandlockError("runtime file must be an absolute file path")
    current_fd = _open_directory_no_symlinks(path.parent)
    try:
        return os.open(path.name, _O_PATH | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=current_fd)
    finally:
        os.close(current_fd)


def _validate_runtime_files(repository: Path, raw_files: Sequence[str]) -> list[tuple[Path, int]]:
    """Pin explicitly named external regular files for write/truncate only."""
    pinned: list[tuple[Path, int]] = []
    seen: set[Path] = set()
    try:
        for raw in raw_files:
            path = Path(raw)
            if not path.is_absolute():
                raise LandlockError("runtime files must be absolute paths")
            try:
                canonical = path.resolve(strict=True)
            except OSError as exc:
                raise LandlockError("runtime file must already exist") from exc
            if path != canonical:
                raise LandlockError("runtime file must not use symlink components")
            try:
                canonical.relative_to(repository)
            except ValueError:
                pass
            else:
                raise LandlockError("runtime files must be outside the repository")
            if canonical in seen:
                continue
            fd = _open_path_no_symlinks(canonical)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                os.close(fd)
                raise LandlockError("runtime file must be a single-link regular file")
            pinned.append((canonical, fd))
            seen.add(canonical)
        return pinned
    except BaseException:
        for _, fd in pinned:
            os.close(fd)
        raise


def _open_null_device() -> int:
    """Pin only the conventional /dev/null character device for shell sinks."""
    path = Path("/dev/null")
    try:
        canonical = path.resolve(strict=True)
    except OSError as exc:
        raise LandlockError("/dev/null is required for confined tool startup") from exc
    if canonical != path:
        raise LandlockError("/dev/null must be the canonical character device")
    try:
        fd = _open_path_no_symlinks(path)
    except OSError as exc:
        raise LandlockError("could not pin /dev/null") from exc
    info = os.fstat(fd)
    if (not stat.S_ISCHR(info.st_mode) or os.major(info.st_rdev) != 1
            or os.minor(info.st_rdev) != 3):
        os.close(fd)
        raise LandlockError("/dev/null is not the expected character device")
    return fd


def enforce_landlock(repository_root: str, writable_roots: Sequence[str], *,
                     pinned_root_fds: Sequence[int] | None = None,
                     runtime_file_fds: Sequence[int] = (),
                     null_device_fds: Sequence[int] = ()) -> list[Path]:
    """Apply read/execute access globally and mutation access only in roots."""
    _, roots = _validate_write_roots(repository_root, writable_roots)
    _reject_preexisting_hardlinks(roots)
    try:
        abi = landlock_abi()
    except (OSError, LandlockError) as exc:
        raise LandlockError("Landlock is unavailable") from exc
    if abi < 3:
        raise LandlockError("Landlock ABI 3 or newer is required")

    create_nr, add_nr, restrict_nr = _syscall_numbers()
    attr = _RulesetAttr(_HANDLED_FS)
    try:
        ruleset_fd = _libc_syscall(
            create_nr,
            ctypes.byref(attr),
            ctypes.c_size_t(ctypes.sizeof(attr)),
            ctypes.c_uint32(0),
        )
    except OSError as exc:
        raise LandlockError("could not create Landlock ruleset") from exc

    path_fds: list[int] = []
    owned_root_fds: list[int] = []
    try:
        root_fd = os.open("/", _O_PATH | os.O_CLOEXEC)
        path_fds.append(root_fd)
        _add_path_rule(add_nr, ruleset_fd, root_fd, _READ_EXECUTE)
        if pinned_root_fds is not None and len(pinned_root_fds) != len(roots):
            raise LandlockError("pinned writable roots do not match validated roots")
        for index, root in enumerate(roots):
            if pinned_root_fds is None:
                fd = _open_directory_no_symlinks(root)
                owned_root_fds.append(fd)
            else:
                fd = pinned_root_fds[index]
            _add_path_rule(add_nr, ruleset_fd, fd, _READ_EXECUTE | _WRITE_RIGHTS)
        for fd in runtime_file_fds:
            _add_path_rule(add_nr, ruleset_fd, fd, _WRITE_FILE | _TRUNCATE)
        for fd in null_device_fds:
            _add_path_rule(add_nr, ruleset_fd, fd, _WRITE_FILE)
        _prctl_no_new_privs()
        _libc_syscall(restrict_nr, ctypes.c_int(ruleset_fd), ctypes.c_uint32(0))
    except OSError as exc:
        raise LandlockError("could not enforce Landlock ruleset") from exc
    finally:
        for fd in path_fds:
            os.close(fd)
        for fd in owned_root_fds:
            os.close(fd)
        os.close(ruleset_fd)
    return roots


def _add_path_rule(add_nr: int, ruleset_fd: int, parent_fd: int, rights: int) -> None:
    rule = _PathBeneathAttr(rights, parent_fd)
    _libc_syscall(add_nr, ctypes.c_int(ruleset_fd), ctypes.c_int(_LANDLOCK_RULE_PATH_BENEATH), ctypes.byref(rule), ctypes.c_uint32(0))


def _prctl_no_new_privs() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.restype = ctypes.c_int
    if prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))


def _validate_inherited_stdin() -> None:
    """Refuse an inherited writable regular file descriptor outside policy."""
    try:
        mode = os.fstat(0).st_mode
        flags = fcntl.fcntl(0, fcntl.F_GETFL)
    except OSError:
        return
    if stat.S_ISREG(mode) and (flags & os.O_ACCMODE) != os.O_RDONLY:
        raise LandlockError("inherited stdin must not be a writable regular file")


def _log_path(raw: str | None, roots: Sequence[Path], *, label: str) -> Path:
    if raw is None:
        raise LandlockError(f"{label} is required")
    path = Path(raw)
    if not path.is_absolute():
        raise LandlockError(f"{label} must be absolute")
    parent = path.parent.resolve(strict=True)
    resolved = parent / path.name
    if not any(resolved == root or root in resolved.parents for root in roots):
        raise LandlockError(f"{label} must be inside a writable root")
    if path.parent != parent:
        raise LandlockError(f"{label} must not use symlink components")
    try:
        info = path.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise LandlockError(f"{label} cannot be inspected") from exc
    else:
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise LandlockError(f"{label} must be a single-link regular file")
    return resolved


def _root_for_log(path: Path, roots: Sequence[Path]) -> int:
    matches = [(len(root.parts), index) for index, root in enumerate(roots)
               if path == root or root in path.parents]
    if not matches:
        raise LandlockError("log file is outside writable roots")
    return max(matches)[1]


def _open_log_file(root_fd: int, root: Path, path: Path) -> int:
    """Open a log relative to a pinned writable root without following links."""
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise LandlockError("log file is outside its writable root") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise LandlockError("log file path is invalid")
    directory_fd = os.dup(root_fd)
    try:
        for component in relative.parts[:-1]:
            next_fd = os.open(component, _O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                              dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        name = relative.parts[-1]
        try:
            probe_fd = os.open(name, _O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
                               dir_fd=directory_fd)
        except FileNotFoundError:
            # O_EXCL ensures a concurrent create cannot swap in a symlink
            # between the absence check and creation.
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND
                         | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
        else:
            try:
                before = os.fstat(probe_fd)
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    raise LandlockError("log target must be a single-link regular file")
                fd = os.open(name, os.O_WRONLY | os.O_APPEND | os.O_CLOEXEC | os.O_NOFOLLOW
                             | os.O_NONBLOCK, dir_fd=directory_fd)
                after = os.fstat(fd)
                if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                    os.close(fd)
                    raise LandlockError("log target changed while being opened")
                if not stat.S_ISREG(after.st_mode) or after.st_nlink != 1:
                    os.close(fd)
                    raise LandlockError("log target must be a single-link regular file")
            finally:
                os.close(probe_fd)
        final_info = os.fstat(fd)
        if not stat.S_ISREG(final_info.st_mode) or final_info.st_nlink != 1:
            os.close(fd)
            raise LandlockError("log target must be a single-link regular file")
        return fd
    finally:
        os.close(directory_fd)


def _close_range(first: int, last: int) -> bool:
    """Call Linux close_range(2), whose upper endpoint is inclusive."""
    libc = ctypes.CDLL(None, use_errno=True)
    close_range = getattr(libc, "close_range", None)
    if close_range is None:
        return False
    close_range.argtypes = (ctypes.c_uint, ctypes.c_uint, ctypes.c_uint)
    close_range.restype = ctypes.c_int
    if close_range(first, last, 0) == 0:
        return True
    err = ctypes.get_errno()
    if err in (errno.ENOSYS, errno.EINVAL):
        return False
    raise OSError(err, os.strerror(err))


def _close_inherited_fds(*, preserve: Sequence[int] = ()) -> None:
    """Close all inherited descriptors >=3, preserving only requested pins."""
    kept = sorted({fd for fd in preserve if fd >= 3})
    if not kept and _close_range(3, _CLOSE_RANGE_MAX):
        return
    if kept:
        start = 3
        for fd in kept:
            if start < fd and _close_range(start, fd - 1):
                pass
            else:
                _close_fd_inventory(keep=set(kept))
                return
            start = fd + 1
        if start <= _CLOSE_RANGE_MAX and _close_range(start, _CLOSE_RANGE_MAX):
            return
        _close_fd_inventory(keep=set(kept))
        return
    _close_fd_inventory(keep=set())


def _close_fd_inventory(*, keep: set[int]) -> None:
    try:
        entries = os.listdir("/proc/self/fd")
    except OSError as exc:
        raise LandlockError("could not enumerate inherited file descriptors") from exc
    for entry in entries:
        try:
            fd = int(entry)
        except ValueError:
            continue
        if fd < 3 or fd in keep:
            continue
        try:
            os.close(fd)
        except OSError as exc:
            if exc.errno != errno.EBADF:
                raise


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--writable-root", action="append", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--stdout-log", required=True)
    parser.add_argument("--stderr-log", required=True)
    parser.add_argument("--runtime-file", action="append", default=[],
                        help="existing external regular file allowed WRITE_FILE/TRUNCATE only")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("a command after -- is required")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    root_fds: list[int] = []
    runtime_fds: list[int] = []
    null_device_fds: list[int] = []
    opened_logs: list[int] = []
    try:
        args = _parse_args(argv)
        repo, roots = _validate_write_roots(args.repository_root, args.writable_root)
        cwd = _canonical_directory(args.cwd, label="working directory")
        if cwd != repo and repo not in cwd.parents:
            raise LandlockError("working directory must be inside the repository")
        stdout_path = _log_path(args.stdout_log, roots, label="stdout log")
        stderr_path = _log_path(args.stderr_log, roots, label="stderr log")
        _validate_inherited_stdin()
        _reject_preexisting_hardlinks(roots)
        for root in roots:
            root_fds.append(_open_directory_no_symlinks(root))
        runtime_files = _validate_runtime_files(repo, args.runtime_file)
        runtime_fds = [fd for _, fd in runtime_files]
        null_device_fds = [_open_null_device()]
        os.chdir(cwd)
        enforce_landlock(str(repo), [str(root) for root in roots],
                         pinned_root_fds=root_fds, runtime_file_fds=runtime_fds,
                         null_device_fds=null_device_fds)
        # Drop all inherited non-stdio descriptors, including event-log files
        # opened by a parent runner before policy enforcement.
        _close_inherited_fds(preserve=root_fds)
        # Open both private logs first. Only the final dup2 calls replace the
        # parent's stdio; no child output is emitted through inherited FDs.
        out_root_index = _root_for_log(stdout_path, roots)
        err_root_index = _root_for_log(stderr_path, roots)
        out_fd = _open_log_file(root_fds[out_root_index], roots[out_root_index], stdout_path)
        opened_logs.append(out_fd)
        err_fd = _open_log_file(root_fds[err_root_index], roots[err_root_index], stderr_path)
        opened_logs.append(err_fd)
        out_info, err_info = os.fstat(out_fd), os.fstat(err_fd)
        if (out_info.st_dev, out_info.st_ino) == (err_info.st_dev, err_info.st_ino):
            raise LandlockError("stdout and stderr logs must be different files")
        os.dup2(out_fd, 1)
        if out_fd != 1:
            os.close(out_fd)
            opened_logs.remove(out_fd)
        os.dup2(err_fd, 2)
        if err_fd != 2:
            os.close(err_fd)
            opened_logs.remove(err_fd)
        os.set_inheritable(1, True)
        os.set_inheritable(2, True)
        # A coordinator-owned SQLite template can be copied into this job's
        # private runtime only after Landlock is active. This keeps cache
        # setup from broadening the wrapper's write authority and leaves the
        # immutable template outside every writable root.
        runtime_roots = [root for root in roots if root.name == "runtime"]
        if len(runtime_roots) == 1:
            runtime_root = runtime_roots[0]
            workspace_root = runtime_root.parent / "workspace"
            if workspace_root in roots:
                try:
                    if str(repo) not in sys.path:
                        sys.path.insert(0, str(repo))
                    from pipeline.worker_state_cache import (
                        prepare_runtime_state,
                        write_setup_receipt,
                    )
                    receipt = prepare_runtime_state(str(repo), str(runtime_root), args.command)
                    if receipt is not None:
                        write_setup_receipt(str(runtime_root), receipt)
                except Exception:
                    # A cache miss or failure must fall back to Codex's normal
                    # isolated fresh-state backfill. Do not expose paths or
                    # helper diagnostics to worker stderr.
                    try:
                        from pipeline.worker_state_cache import write_setup_receipt
                        write_setup_receipt(str(runtime_root), {
                            "format_version": 1,
                            "status": "skipped",
                            "reason": "cache-setup-failed",
                        })
                    except Exception:
                        pass
        os.execvpe(args.command[0], args.command, os.environ.copy())
    except LandlockError as exc:
        # Messages are fixed, sanitized policy diagnostics; don't print child
        # arguments, environment values, command output, or credentials.
        print(f"landlock_exec: {exc}", file=sys.stderr)
        return 125
    except (OSError, ValueError):
        print("landlock_exec: setup or exec failed", file=sys.stderr)
        return 126
    finally:
        for fd in opened_logs:
            try:
                os.close(fd)
            except OSError:
                pass
        for fd in [*root_fds, *runtime_fds, *null_device_fds]:
            try:
                os.close(fd)
            except OSError:
                pass
    return 126


if __name__ == "__main__":
    raise SystemExit(main())
