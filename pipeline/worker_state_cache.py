"""Coordinator-managed, versioned snapshots for Codex's isolated SQLite state.

This cache avoids repeating Codex's historical rollout backfill for each
tool-enabled worker. It stores only a verified SQLite snapshot and non-content
provenance metadata. Every worker receives a private writable copy.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import tempfile
import time
from typing import Any

from pipeline.worker_paths import atomic_write_managed, checked_directory, checked_regular_file


FORMAT_VERSION = 1
CACHE_RELATIVE = Path("runs/.worker-state-cache")
ACTIVE_MANIFEST = "active.json"
STATE_FILENAME = "state_5.sqlite"
SEED_LOCK = ".worker-state-seed.lock"
SEED_TEMP_PREFIX = ".state_5.sqlite.seed-"
_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
POLICY_VERSION = "landlock-write-scope-v1"
_VERSION = re.compile(r"^codex-cli [0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")


class StateCacheError(ValueError):
    """A cache or source is not safe to install or use."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _schema_fingerprint(db: sqlite3.Connection) -> str:
    rows = db.execute(
        "SELECT type, name, tbl_name, COALESCE(sql, '') FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name, tbl_name"
    ).fetchall()
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _db_metadata(path: Path, *, quick_check: bool = True) -> dict[str, Any]:
    checked_regular_file(path)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        connection.execute("PRAGMA query_only=ON")
        check = connection.execute("PRAGMA quick_check").fetchone()[0] if quick_check else "not-run"
        migrations = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(CASE WHEN success THEN 1 ELSE 0 END), 0), "
            "COALESCE(MAX(version), 0) FROM _sqlx_migrations"
        ).fetchone()
        backfill_rows = connection.execute(
            "SELECT status, typeof(last_watermark), "
            "CASE WHEN last_watermark IS NULL THEN 0 ELSE length(CAST(last_watermark AS TEXT)) END "
            "FROM backfill_state"
        ).fetchall()
        if len(backfill_rows) != 1:
            raise StateCacheError("SQLite backfill metadata is absent or ambiguous")
        backfill_status, watermark_type, watermark_length = backfill_rows[0]
        return {
            "quick_check": check,
            "schema_fingerprint": _schema_fingerprint(connection),
            "schema_version": connection.execute("PRAGMA schema_version").fetchone()[0],
            "user_version": connection.execute("PRAGMA user_version").fetchone()[0],
            "page_count": connection.execute("PRAGMA page_count").fetchone()[0],
            "freelist_count": connection.execute("PRAGMA freelist_count").fetchone()[0],
            "migration_count": migrations[0],
            "migration_success_count": migrations[1],
            "migration_max_version": migrations[2],
            "backfill_status": backfill_status,
            "watermark_type": watermark_type,
            "watermark_length": watermark_length,
            "thread_count": connection.execute("SELECT COUNT(*) FROM threads").fetchone()[0],
        }
    except sqlite3.Error as exc:
        raise StateCacheError("SQLite metadata could not be verified") from exc
    finally:
        connection.close()


def _check_db_metadata(metadata: dict[str, Any], *, require_quick_check: bool = True) -> None:
    if require_quick_check and metadata.get("quick_check") != "ok":
        raise StateCacheError("SQLite integrity check did not pass")
    if metadata.get("backfill_status") != "complete":
        raise StateCacheError("SQLite history backfill is not complete")
    migration_count = metadata.get("migration_count")
    if (not isinstance(migration_count, int) or migration_count < 1
            or metadata.get("migration_success_count") != migration_count):
        raise StateCacheError("SQLite schema migrations are incomplete")
    if not isinstance(metadata.get("schema_fingerprint"), str) or len(metadata["schema_fingerprint"]) != 64:
        raise StateCacheError("SQLite schema fingerprint is invalid")


def _safe_version(version: str) -> str:
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise StateCacheError("Codex CLI version is not recognized")
    return version.removeprefix("codex-cli ")


def _cache_root(repository: Path, explicit: str | os.PathLike[str] | None = None,
                *, create: bool = False) -> Path:
    if explicit is not None and not Path(explicit).is_absolute():
        raise StateCacheError("Worker state cache override must be absolute")
    raw = Path(explicit) if explicit is not None else repository / CACHE_RELATIVE
    if ".." in raw.parts or not raw.is_absolute() or not raw.is_relative_to(repository):
        raise StateCacheError("Worker state cache must be a safe repository-relative path")
    root = checked_directory(raw, create=create)
    return root


def _read_json_regular(path: Path) -> dict[str, Any]:
    checked_regular_file(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateCacheError("State cache manifest is unreadable") from exc
    if not isinstance(value, dict):
        raise StateCacheError("State cache manifest is malformed")
    return value


SEED_CONTENT_POLICY = "schema-and-backfill-v1"
MAX_SEED_BYTES = 8 * 1024 * 1024


def _minimal_snapshot(source: Path, destination: Path) -> None:
    """Recreate schema and migration/backfill sentinels without historical rows."""
    original = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=10)
    output = sqlite3.connect(destination)
    try:
        original.execute("PRAGMA query_only=ON")
        objects = original.execute("SELECT type, name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL "
            "ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 WHEN 'view' THEN 2 ELSE 3 END, name").fetchall()
        for kind, name, sql in objects:
            output.execute(sql)
        for table in ("_sqlx_migrations", "backfill_state"):
            rows = original.execute(f'SELECT * FROM "{table}"').fetchall()
            if rows:
                slots = ','.join('?' for _ in rows[0])
                output.executemany(f'INSERT INTO "{table}" VALUES ({slots})', rows)
        output.execute(f"PRAGMA user_version={original.execute('PRAGMA user_version').fetchone()[0]}")
        output.commit()
        if _schema_fingerprint(output) != _schema_fingerprint(original):
            raise StateCacheError("Minimal state schema differs from its verified source")
    finally:
        original.close()
        output.close()
    if destination.stat().st_size > MAX_SEED_BYTES:
        raise StateCacheError("Minimal state schema exceeds the bounded seed size")


def install_cache(repository: str | os.PathLike[str], source_db: str | os.PathLike[str],
                  *, codex_version: str,
                  cache_root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Install a verified backup from a completed, guarded worker database.

    The source is opened read-only. Schema projection creates an immutable
    coordinator-owned schema template without historical rows; workers receive private seeds.
    """
    repo = checked_directory(Path(repository).absolute())
    version = _safe_version(codex_version)
    source = Path(source_db).absolute()
    source = checked_regular_file(source)
    if not source.is_relative_to(repo):
        raise StateCacheError("Source state database must be within the repository")
    parts = source.relative_to(repo).parts
    if (len(parts) < 5 or parts[0] != "runs"
            or not any(parent.name == "agents" for parent in source.parents)
            or parts[-3:] != ("runtime", "state", STATE_FILENAME)):
        raise StateCacheError("Source database is not in a pipeline worker runtime")
    job_dir = source.parents[2]
    meta_path = checked_regular_file(job_dir / "meta.json")
    meta = _read_json_regular(meta_path)
    if (meta.get("return_code") != 0 or meta.get("tool_profile") != "workspace"
            or meta.get("filesystem_policy") != POLICY_VERSION
            or meta.get("model") != "gpt-6-luna" or meta.get("effort") != "low"
            or not meta.get("ended_at")):
        raise StateCacheError("Source worker is not a completed guarded Luna-low run")
    wal_path = Path(str(source) + "-wal")
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(source) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            checked_regular_file(sidecar)
            if suffix == "-wal" and sidecar.stat().st_size:
                raise StateCacheError("Source SQLite WAL is not checkpointed")
    source_hash_before = _sha256(source)
    source_metadata = _db_metadata(source)
    _check_db_metadata(source_metadata)

    cache_root = _cache_root(repo, cache_root, create=True)
    version_root = checked_directory(cache_root / version, create=True)
    schema_key = source_metadata["schema_fingerprint"]
    schema_root = checked_directory(version_root / schema_key, create=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=".install-", dir=schema_root))
    try:
        temp_db = temp_dir / STATE_FILENAME
        _minimal_snapshot(source, temp_db)
        os.chmod(temp_db, 0o600)
        snapshot_metadata = _db_metadata(temp_db)
        _check_db_metadata(snapshot_metadata)
        if snapshot_metadata["schema_fingerprint"] != schema_key:
            raise StateCacheError("SQLite backup schema differs from its source")
        if _sha256(source) != source_hash_before:
            raise StateCacheError("Source SQLite database changed during snapshot creation")
        snapshot_hash = _sha256(temp_db)
        final_dir = checked_directory(schema_root / snapshot_hash, create=False) if (schema_root / snapshot_hash).exists() else schema_root / snapshot_hash
        if not final_dir.exists():
            os.replace(temp_dir, final_dir)
            temp_dir = Path()
            final_db = final_dir / STATE_FILENAME
        else:
            final_dir = checked_directory(final_dir)
            final_db = checked_regular_file(final_dir / STATE_FILENAME)
            if _sha256(final_db) != snapshot_hash:
                raise StateCacheError("Existing cache snapshot failed its digest check")
        manifest = {
            "format_version": FORMAT_VERSION,
            "seed_content_policy": SEED_CONTENT_POLICY,
            "codex_version": codex_version,
            "schema_key": schema_key,
            "snapshot_relative_path": final_db.relative_to(cache_root).as_posix(),
            "snapshot_sha256": snapshot_hash,
            "snapshot_bytes": final_db.stat().st_size,
            "snapshot_metadata": snapshot_metadata,
            "source_job": job_dir.relative_to(repo).as_posix(),
            "source_db_sha256": source_hash_before,
            "source_db_bytes": source.stat().st_size,
            "source_metadata": source_metadata,
            "source_provenance": {key: meta.get(key) for key in (
                "job", "model", "effort", "return_code", "started_at", "ended_at",
                "tool_profile", "filesystem_policy", "workspace_digest", "artifact_digest",
            )},
            "installed_at": datetime.now(timezone.utc).isoformat(),
        }
        active = {
            "format_version": FORMAT_VERSION,
            "codex_version": codex_version,
            "schema_key": schema_key,
            "manifest_path": (Path(version) / schema_key / snapshot_hash / "manifest.json").as_posix(),
        }
        atomic_write_managed(final_dir, "manifest.json",
                             (json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
        manifest_sha = _sha256(final_dir / "manifest.json")
        active["manifest_sha256"] = manifest_sha
        atomic_write_managed(cache_root, ACTIVE_MANIFEST,
                             (json.dumps(active, indent=2) + "\n").encode("utf-8"))
        return {
            "status": "installed",
            "codex_version": codex_version,
            "schema_key": schema_key,
            "snapshot_sha256": snapshot_hash,
            "snapshot_relative_path": final_db.relative_to(cache_root).as_posix(),
            "snapshot_bytes": final_db.stat().st_size,
            "source_job": manifest["source_job"],
            "snapshot_metadata": snapshot_metadata,
        }
    finally:
        if temp_dir != Path() and temp_dir.exists():
            import shutil
            shutil.rmtree(temp_dir)


def _load_active_manifest(repository: Path, codex_version: str,
                          cache_root_override: str | os.PathLike[str] | None = None) -> tuple[Path, dict[str, Any]]:
    cache_override = cache_root_override or os.environ.get("GRADED_READERS_WORKER_STATE_CACHE_ROOT")
    cache_root = _cache_root(repository, cache_override)
    active_path = checked_regular_file(cache_root / ACTIVE_MANIFEST)
    active = _read_json_regular(active_path)
    if active.get("format_version") != FORMAT_VERSION or active.get("codex_version") != codex_version:
        raise StateCacheError("No cache snapshot matches this Codex CLI version")
    schema_key = active.get("schema_key")
    manifest_rel = active.get("manifest_path")
    if (not isinstance(schema_key, str) or not re.fullmatch(r"[0-9a-f]{64}", schema_key)
            or not isinstance(manifest_rel, str)):
        raise StateCacheError("Active state cache index is malformed")
    relative = Path(manifest_rel)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise StateCacheError("Active state cache index has unsafe path")
    manifest_path = cache_root / relative
    current = cache_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise StateCacheError("State cache path contains a symlink")
    checked_regular_file(manifest_path)
    if _sha256(manifest_path) != active.get("manifest_sha256"):
        raise StateCacheError("State cache manifest digest differs from its index")
    manifest = _read_json_regular(manifest_path)
    snapshot_metadata = manifest.get("snapshot_metadata")
    source_metadata = manifest.get("source_metadata")
    provenance = manifest.get("source_provenance")
    if not isinstance(snapshot_metadata, dict) or not isinstance(source_metadata, dict):
        raise StateCacheError("State cache database metadata is missing")
    if not isinstance(provenance, dict):
        raise StateCacheError("State cache source provenance is missing")
    if (manifest.get("format_version") != FORMAT_VERSION
            or manifest.get("codex_version") != codex_version
            or manifest.get("schema_key") != schema_key
            or snapshot_metadata.get("schema_fingerprint") != schema_key):
        raise StateCacheError("State cache provenance does not match its index")
    snapshot_hash = manifest.get("snapshot_sha256")
    source_hash = manifest.get("source_db_sha256")
    if (not isinstance(snapshot_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", snapshot_hash)
            or not isinstance(source_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", source_hash)):
        raise StateCacheError("State cache digests are missing or malformed")
    if (not isinstance(manifest.get("snapshot_bytes"), int)
            or manifest["snapshot_bytes"] < 1
            or not isinstance(manifest.get("source_db_bytes"), int)
            or manifest["source_db_bytes"] < 1):
        raise StateCacheError("State cache sizes are missing or malformed")
    source_job = manifest.get("source_job")
    if (not isinstance(source_job, str) or not source_job.startswith("runs/")
            or ".." in Path(source_job).parts or Path(source_job).is_absolute()):
        raise StateCacheError("State cache source job is missing or unsafe")
    snapshot_rel = manifest.get("snapshot_relative_path")
    if not isinstance(snapshot_rel, str):
        raise StateCacheError("State cache snapshot path is missing")
    snapshot_parts = Path(snapshot_rel)
    if snapshot_parts.is_absolute() or any(part in {"", ".", ".."} for part in snapshot_parts.parts):
        raise StateCacheError("State cache snapshot path is unsafe")
    snapshot_path = cache_root / snapshot_parts
    expected_snapshot_rel = (Path(codex_version.removeprefix("codex-cli ")) / schema_key
                             / snapshot_hash / STATE_FILENAME)
    if snapshot_parts != expected_snapshot_rel:
        raise StateCacheError("State cache snapshot path does not match its index")
    current = cache_root
    for part in snapshot_parts.parts:
        current = current / part
        if current.is_symlink():
            raise StateCacheError("State cache snapshot uses a symlink")
    checked_regular_file(snapshot_path)
    if snapshot_path.stat().st_size != manifest.get("snapshot_bytes"):
        raise StateCacheError("State cache snapshot size differs from its manifest")
    _check_db_metadata(snapshot_metadata)
    _check_db_metadata(source_metadata)
    return snapshot_path, manifest


def _remove_interrupted_temps(state: Path, target: Path) -> None:
    """Remove only private temp entries left by a dead seeding wrapper."""
    try:
        target_info = target.lstat()
    except FileNotFoundError:
        target_info = None
    for entry in state.iterdir():
        if not entry.name.startswith(SEED_TEMP_PREFIX):
            continue
        info = entry.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise StateCacheError("Interrupted SQLite seed temp is not a regular file")
        if target_info is not None and (info.st_dev, info.st_ino) == (target_info.st_dev, target_info.st_ino):
            if info.st_nlink != 2 or target_info.st_nlink != 2:
                raise StateCacheError("Published SQLite seed has unexpected aliases")
        elif info.st_nlink != 1:
            raise StateCacheError("Interrupted SQLite seed temp has unexpected aliases")
        entry.unlink()


def _copy_verified_snapshot(snapshot: Path, temp: Path, output_handle,
                            manifest: dict[str, Any]) -> None:
    # Existing immutable caches may contain hundreds of MB of historical
    # threads. Verify their source bytes, then project a tiny private schema;
    # never byte-copy that history merely to discard it afterwards.
    output_handle.close()
    if _sha256(snapshot) != manifest.get("snapshot_sha256"):
        raise StateCacheError("Seed source digest differs from verified snapshot")
    if snapshot.stat().st_size != manifest.get("snapshot_bytes"):
        raise StateCacheError("Seed source size differs from verified snapshot")
    _minimal_snapshot(snapshot, temp)
    if _sha256(snapshot) != manifest.get("snapshot_sha256"):
        raise StateCacheError("Seed source changed while projecting its schema")
    os.chmod(temp, 0o600)
    actual = _db_metadata(temp, quick_check=False)
    _check_db_metadata(actual, require_quick_check=False)
    for key in ("schema_fingerprint", "migration_count", "migration_success_count",
                "migration_max_version", "backfill_status"):
        if actual.get(key) != manifest.get("snapshot_metadata", {}).get(key):
            raise StateCacheError("Private worker schema differs from its manifest")
    if actual["thread_count"] != 0:
        raise StateCacheError("Private worker seed contains historical threads")
    # SQLite may create temporary WAL/shared-memory sidecars even for a
    # read-only metadata check. The seed itself is a complete checkpoint, so
    # close over those temp-named files before moving the database into place.
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(temp) + suffix)
        try:
            checked_regular_file(sidecar).unlink()
        except ValueError:
            if sidecar.exists() or sidecar.is_symlink():
                raise StateCacheError("Private SQLite seed created an unsafe sidecar")
        except FileNotFoundError:
            pass


def _publish_no_replace(temp: Path, target: Path) -> None:
    """Atomically publish a complete seed without replacing existing state.

    Linux renameat2(RENAME_NOREPLACE) avoids the hard-link/unlink crash window:
    a process killed after publication leaves one valid target with link count
    one, which the normal worker preflight accepts.
    """
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOSYS, "atomic no-replace rename unavailable")
    renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                          ctypes.c_uint)
    renameat2.restype = ctypes.c_int
    if renameat2(_AT_FDCWD, os.fsencode(temp), _AT_FDCWD, os.fsencode(target),
                 _RENAME_NOREPLACE) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(target))


def seed_runtime_state(repository: str | os.PathLike[str], runtime_root: str | os.PathLike[str],
                       *, codex_version: str,
                       cache_root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Copy a verified immutable snapshot into one empty per-job runtime.

    This function is intended to run after Landlock is enforced. Failures are
    represented as skip receipts so callers can safely fall back to a fresh
    Codex state DB. Existing job state is never replaced.
    """
    started = time.monotonic()
    receipt: dict[str, Any] = {"format_version": FORMAT_VERSION, "status": "skipped",
                               "reason": "cache-unavailable", "codex_version": codex_version}
    repository_path = Path(repository)
    runtime = Path(runtime_root)
    lock_fd: int | None = None
    temp_path: Path | None = None
    temp_fd: int | None = None
    try:
        checked_directory(repository_path)
        checked_directory(runtime)
        state = checked_directory(runtime / "state", create=True)
        cache_override = cache_root or os.environ.get("GRADED_READERS_WORKER_STATE_CACHE_ROOT")
        cache_candidate = Path(cache_override) if cache_override else repository_path / CACHE_RELATIVE
        workspace = runtime.parent / "workspace"
        if any(cache_candidate.is_relative_to(write_root) for write_root in (runtime, workspace)):
            raise StateCacheError("Worker state cache cannot be inside a writable job root")
        lock_path = state / SEED_LOCK
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        lock_info = os.fstat(lock_fd)
        if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_nlink != 1:
            raise StateCacheError("Worker state seed lock is aliased")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            receipt["reason"] = "seed-in-progress"
            return receipt
        target = state / STATE_FILENAME
        _remove_interrupted_temps(state, target)
        remaining = [entry for entry in state.iterdir() if entry.name != SEED_LOCK]
        if remaining:
            receipt["reason"] = "existing-state-preserved"
            return receipt
        snapshot, manifest = _load_active_manifest(repository_path, codex_version, cache_root)
        temp_fd, temp_name = tempfile.mkstemp(prefix=SEED_TEMP_PREFIX, dir=state)
        temp_path = Path(temp_name)
        os.fchmod(temp_fd, 0o600)
        output_handle = os.fdopen(temp_fd, "wb")
        temp_fd = None
        _copy_verified_snapshot(snapshot, temp_path, output_handle, manifest)
        checked_regular_file(temp_path)
        # Atomic no-clobber publication: a preexisting or racing state DB is
        # never replaced with a partial copy, and a kill at the publication
        # boundary cannot leave a multiply-linked target.
        _publish_no_replace(temp_path, target)
        directory_fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        temp_path = None
        checked_regular_file(target)
        receipt.update({"status": "seeded", "reason": None,
                        "schema_key": manifest["schema_key"],
                        "snapshot_sha256": manifest["snapshot_sha256"],
                        "copy_bytes": target.stat().st_size,
                        "seed_content_policy": SEED_CONTENT_POLICY,
                        "seed_sha256": _sha256(target)})
    except (OSError, sqlite3.Error, StateCacheError, ValueError, KeyError, TypeError):
        # Incomplete bytes live only under a managed temp name. The final DB
        # appears only after verification and atomic no-clobber publication.
        if temp_fd is not None:
            try:
                os.close(temp_fd)
            except OSError:
                pass
        if temp_path is not None:
            try:
                state = checked_directory(runtime / "state")
                checked_regular_file(state / temp_path.name).unlink()
            except (OSError, ValueError):
                pass
        receipt["reason"] = "cache-invalid-or-copy-failed"
    finally:
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except OSError:
                pass
        receipt["elapsed_seconds"] = round(time.monotonic() - started, 4)
    return receipt


def write_setup_receipt(runtime_root: str | os.PathLike[str], receipt: dict[str, Any]) -> None:
    """Write non-content cache setup diagnostics inside the per-job runtime."""
    runtime = checked_directory(runtime_root)
    payload = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    atomic_write_managed(runtime, "state-cache-setup.json", payload)


def cache_configured(repository: str | os.PathLike[str]) -> bool:
    """Whether a coordinator cache directory is present (no DB reads)."""
    repo = Path(repository)
    override = os.environ.get("GRADED_READERS_WORKER_STATE_CACHE_ROOT")
    candidate = Path(override) if override else repo / CACHE_RELATIVE
    return candidate.exists() or candidate.is_symlink()


def _codex_version_from_command(command: list[str]) -> str | None:
    if not command:
        return None
    try:
        result = subprocess.run([command[0], "--version"], capture_output=True,
                                text=True, timeout=8, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value if _VERSION.fullmatch(value) else None


def prepare_runtime_state(repository: str | os.PathLike[str], runtime_root: str | os.PathLike[str],
                          command: list[str]) -> dict[str, Any] | None:
    """Probe and seed the cache from a post-Landlock wrapper process.

    No subprocess is started unless a coordinator cache path exists. CLI
    version discovery and all snapshot reads/copies happen under the caller's
    already-enforced filesystem policy.
    """
    if not cache_configured(repository):
        return None
    version = _codex_version_from_command(command)
    if version is None:
        receipt = {"format_version": FORMAT_VERSION, "status": "skipped",
                   "reason": "codex-version-unavailable"}
        return receipt
    return seed_runtime_state(repository, runtime_root, codex_version=version)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    install = sub.add_parser("install", help="install a verified completed worker DB snapshot")
    install.add_argument("--repository-root", type=Path, required=True)
    install.add_argument("--source-db", type=Path, required=True)
    install.add_argument("--codex-version", required=True)
    install.add_argument("--cache-root", type=Path,
                         help="coordinator-owned cache root (defaults to runs/.worker-state-cache)")
    args = parser.parse_args(argv)
    try:
        result = install_cache(args.repository_root, args.source_db,
                               codex_version=args.codex_version, cache_root=args.cache_root)
    except StateCacheError as exc:
        parser.exit(2, f"worker_state_cache: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
