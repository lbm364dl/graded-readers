import json
import os
from pathlib import Path
import sqlite3

import pytest

from pipeline import worker_state_cache as cache


CLI_VERSION = "codex-cli 0.160.0"


def _completed_guarded_job(root: Path, *, nested_job: bool = False) -> tuple[Path, Path]:
    parts = ["runs", "cache-fixture", "chapter-001", "agents"]
    if nested_job:
        parts += ["annotations", "chunk_0001", "semantic_patch", "job-1"]
    else:
        parts += ["job-1"]
    job_dir = root.joinpath(*parts)
    state = job_dir / "runtime" / "state"
    state.mkdir(parents=True)
    db_path = state / "state_5.sqlite"
    db = sqlite3.connect(db_path)
    db.executescript("""
        CREATE TABLE _sqlx_migrations(version INTEGER PRIMARY KEY, success BOOLEAN NOT NULL);
        CREATE TABLE backfill_state(id INTEGER PRIMARY KEY, status TEXT, last_watermark TEXT,
            last_success_at INTEGER, updated_at INTEGER);
        CREATE TABLE threads(id INTEGER PRIMARY KEY, test_only_count INTEGER NOT NULL);
    """)
    db.executemany("INSERT INTO _sqlx_migrations(version, success) VALUES(?, 1)",
                   [(i,) for i in range(1, 59)])
    db.execute("INSERT INTO backfill_state VALUES(1, 'complete', 'fixture-watermark', 1, 1)")
    db.executemany("INSERT INTO threads(id, test_only_count) VALUES(?, ?)", [(1, 11), (2, 22)])
    db.commit()
    db.close()
    (job_dir / "meta.json").write_text(json.dumps({
        "job": job_dir.relative_to(root / "runs/cache-fixture/chapter-001/agents").as_posix(),
        "model": "gpt-6-luna",
        "effort": "low",
        "return_code": 0,
        "started_at": "2026-10-03T00:00:00+00:00",
        "ended_at": "2026-10-03T00:00:01+00:00",
        "tool_profile": "workspace",
        "filesystem_policy": "landlock-write-scope-v1",
        "workspace_digest": "a" * 64,
        "artifact_digest": "b" * 64,
    }) + "\n")
    return job_dir, db_path


def _install(root: Path, *, nested_job: bool = False, cache_root: Path | None = None):
    _job, source_db = _completed_guarded_job(root, nested_job=nested_job)
    return cache.install_cache(root, source_db, codex_version=CLI_VERSION,
                               cache_root=cache_root), source_db


def _read_count(path: Path) -> int:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.execute("PRAGMA query_only=ON")
    value = db.execute("SELECT SUM(test_only_count) FROM threads").fetchone()[0]
    db.close()
    return value


def _user_state_entries(runtime: Path) -> list[str]:
    return sorted(path.name for path in (runtime / "state").iterdir()
                  if path.name != cache.SEED_LOCK)


def test_install_and_seed_use_private_verified_copy_for_nested_job(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    cache_root = root / "runs" / ".worker-state-cache-test"
    installed, source_db = _install(root, nested_job=True, cache_root=cache_root)
    assert installed["status"] == "installed"
    assert installed["snapshot_metadata"]["quick_check"] == "ok"
    assert installed["snapshot_metadata"]["backfill_status"] == "complete"
    assert installed["snapshot_metadata"]["migration_success_count"] == 58

    runtime = root / "runs" / "future" / "agents" / "job-2" / "runtime"
    runtime.mkdir(parents=True)
    seeded = cache.seed_runtime_state(root, runtime, codex_version=CLI_VERSION,
                                      cache_root=cache_root)
    target = runtime / "state" / "state_5.sqlite"
    assert seeded["status"] == "seeded"
    assert target.is_file() and target.stat().st_nlink == 1
    assert target.stat().st_mode & 0o777 == 0o600
    assert not any(entry.name.startswith(cache.SEED_TEMP_PREFIX)
                   for entry in (runtime / "state").iterdir())
    assert _read_count(target) == 33

    with sqlite3.connect(target) as db:
        db.execute("UPDATE threads SET test_only_count=0 WHERE id=1")
    snapshot = cache_root / installed["snapshot_relative_path"]
    assert _read_count(snapshot) == 33
    assert _read_count(source_db) == 33


def test_stale_cli_version_falls_back_without_creating_state(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    cache_root = root / "runs" / ".worker-state-cache-test"
    _install(root, cache_root=cache_root)
    runtime = root / "runs" / "future" / "agents" / "job" / "runtime"
    (runtime / "state").mkdir(parents=True)
    receipt = cache.seed_runtime_state(root, runtime, codex_version="codex-cli 0.159.9",
                                       cache_root=cache_root)
    assert receipt["status"] == "skipped"
    assert _user_state_entries(runtime) == []


def test_existing_job_state_is_never_overwritten(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    cache_root = root / "runs" / ".worker-state-cache-test"
    _install(root, cache_root=cache_root)
    runtime = root / "runs" / "future" / "agents" / "job" / "runtime"
    state = runtime / "state"
    state.mkdir(parents=True)
    preexisting = state / "state_5.sqlite"
    preexisting.write_bytes(b"do-not-replace")
    before = preexisting.read_bytes()
    receipt = cache.seed_runtime_state(root, runtime, codex_version=CLI_VERSION,
                                       cache_root=cache_root)
    assert receipt["reason"] == "existing-state-preserved"
    assert preexisting.read_bytes() == before


@pytest.mark.parametrize("alias_kind", ["symlink", "hardlink"])
def test_cache_snapshot_alias_is_rejected_without_touching_alias_target(tmp_path, alias_kind):
    root = tmp_path / "repo"
    root.mkdir()
    cache_root = root / "runs" / ".worker-state-cache-test"
    installed, _ = _install(root, cache_root=cache_root)
    snapshot = cache_root / installed["snapshot_relative_path"]
    external = tmp_path / "outside.sqlite"
    external.write_bytes(snapshot.read_bytes())
    if alias_kind == "symlink":
        snapshot.unlink()
        snapshot.symlink_to(external)
    else:
        snapshot.unlink()
        os.link(external, snapshot)
    external_before = external.read_bytes()

    runtime = root / "runs" / "future" / "agents" / "job" / "runtime"
    (runtime / "state").mkdir(parents=True)
    receipt = cache.seed_runtime_state(root, runtime, codex_version=CLI_VERSION,
                                       cache_root=cache_root)
    assert receipt["status"] == "skipped"
    assert _user_state_entries(runtime) == []
    assert external.read_bytes() == external_before


def test_active_manifest_traversal_is_rejected(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    cache_root = root / "runs" / ".worker-state-cache-test"
    _install(root, cache_root=cache_root)
    active_path = cache_root / "active.json"
    active = json.loads(active_path.read_text())
    active["manifest_path"] = "../outside.json"
    active_path.write_text(json.dumps(active))
    runtime = root / "runs" / "future" / "agents" / "job" / "runtime"
    (runtime / "state").mkdir(parents=True)
    receipt = cache.seed_runtime_state(root, runtime, codex_version=CLI_VERSION,
                                       cache_root=cache_root)
    assert receipt["status"] == "skipped"
    assert _user_state_entries(runtime) == []


def test_manifest_symlink_is_rejected_without_reading_target(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    cache_root = root / "runs" / ".worker-state-cache-test"
    _install(root, cache_root=cache_root)
    active = json.loads((cache_root / "active.json").read_text())
    manifest = cache_root / active["manifest_path"]
    outside = tmp_path / "outside.json"
    outside.write_text(manifest.read_text())
    original = manifest.read_bytes()
    manifest.unlink()
    manifest.symlink_to(outside)
    runtime = root / "runs" / "future" / "agents" / "job" / "runtime"
    (runtime / "state").mkdir(parents=True)
    receipt = cache.seed_runtime_state(root, runtime, codex_version=CLI_VERSION,
                                       cache_root=cache_root)
    assert receipt["status"] == "skipped"
    assert _user_state_entries(runtime) == []
    assert outside.read_bytes() == original


def test_source_must_be_completed_guarded_low_worker(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    job_dir, source = _completed_guarded_job(root)
    meta_path = job_dir / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["return_code"] = 1
    meta_path.write_text(json.dumps(meta))
    with pytest.raises(cache.StateCacheError, match="completed guarded"):
        cache.install_cache(root, source, codex_version=CLI_VERSION)


def test_source_backfill_must_be_complete(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _job, source = _completed_guarded_job(root)
    with sqlite3.connect(source) as db:
        db.execute("UPDATE backfill_state SET status='running'")
    with pytest.raises(cache.StateCacheError, match="backfill"):
        cache.install_cache(root, source, codex_version=CLI_VERSION)


def test_cache_override_must_be_absolute_and_inside_repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _job, source = _completed_guarded_job(root)
    with pytest.raises(cache.StateCacheError, match="absolute"):
        cache.install_cache(root, source, codex_version=CLI_VERSION, cache_root=Path("relative-cache"))
    outside = tmp_path / "outside"
    with pytest.raises(cache.StateCacheError, match="repository"):
        cache.install_cache(root, source, codex_version=CLI_VERSION, cache_root=outside)
    assert not outside.exists()


def test_cache_inside_worker_writable_root_is_rejected(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    cache_root = root / "runs" / ".worker-state-cache-test"
    _install(root, cache_root=cache_root)
    runtime = root / "runs" / "future" / "agents" / "job" / "runtime"
    (runtime / "state").mkdir(parents=True)
    mutable_cache = runtime / "cache"
    mutable_cache.mkdir()
    receipt = cache.seed_runtime_state(root, runtime, codex_version=CLI_VERSION,
                                       cache_root=mutable_cache)
    assert receipt["status"] == "skipped"
    assert _user_state_entries(runtime) == []


@pytest.mark.parametrize("missing_field", [
    "snapshot_sha256", "snapshot_bytes", "snapshot_metadata", "source_db_sha256",
    "source_db_bytes", "source_metadata", "source_provenance", "source_job",
])
def test_malformed_manifest_falls_back_without_partial_final_db(tmp_path, missing_field):
    root = tmp_path / "repo"
    root.mkdir()
    cache_root = root / "runs" / ".worker-state-cache-test"
    _install(root, cache_root=cache_root)
    active_path = cache_root / "active.json"
    active = json.loads(active_path.read_text())
    manifest_path = cache_root / active["manifest_path"]
    manifest = json.loads(manifest_path.read_text())
    del manifest[missing_field]
    manifest_path.write_text(json.dumps(manifest))
    active["manifest_sha256"] = cache._sha256(manifest_path)
    active_path.write_text(json.dumps(active))

    runtime = root / "runs" / "future" / "agents" / "job" / "runtime"
    state = runtime / "state"
    state.mkdir(parents=True)
    receipt = cache.seed_runtime_state(root, runtime, codex_version=CLI_VERSION,
                                       cache_root=cache_root)
    assert receipt["status"] == "skipped"
    assert not (state / "state_5.sqlite").exists()
    assert _user_state_entries(runtime) == []
    # This is the state shape Codex can populate itself on its normal fresh path.
    with sqlite3.connect(state / "state_5.sqlite") as db:
        db.execute("CREATE TABLE fresh_fallback(marker TEXT)")
        db.execute("INSERT INTO fresh_fallback VALUES('usable')")
    with sqlite3.connect(f"file:{state / 'state_5.sqlite'}?mode=ro", uri=True) as db:
        assert db.execute("SELECT marker FROM fresh_fallback").fetchone() == ("usable",)


def test_interrupted_seed_temp_is_cleaned_before_retry(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    cache_root = root / "runs" / ".worker-state-cache-test"
    _install(root, cache_root=cache_root)
    runtime = root / "runs" / "future" / "agents" / "job" / "runtime"
    state = runtime / "state"
    state.mkdir(parents=True)
    interrupted = state / f"{cache.SEED_TEMP_PREFIX}interrupted"
    interrupted.write_bytes(b"partial database from interrupted copy")

    receipt = cache.seed_runtime_state(root, runtime, codex_version=CLI_VERSION,
                                       cache_root=cache_root)
    assert receipt["status"] == "seeded"
    assert (state / "state_5.sqlite").is_file()
    assert not interrupted.exists()
    assert (state / "state_5.sqlite").stat().st_nlink == 1


def test_copy_failure_never_publishes_partial_db_and_retry_succeeds(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    cache_root = root / "runs" / ".worker-state-cache-test"
    _install(root, cache_root=cache_root)
    runtime = root / "runs" / "future" / "agents" / "job" / "runtime"
    state = runtime / "state"
    state.mkdir(parents=True)

    def fail_after_partial_copy(snapshot, temp, output, manifest):
        with output:
            output.write(b"partial")
            output.flush()
        raise OSError("test-only simulated interruption")

    monkeypatch.setattr(cache, "_copy_verified_snapshot", fail_after_partial_copy)
    failed = cache.seed_runtime_state(root, runtime, codex_version=CLI_VERSION,
                                      cache_root=cache_root)
    target = state / "state_5.sqlite"
    assert failed["status"] == "skipped"
    assert not target.exists()
    assert _user_state_entries(runtime) == []

    monkeypatch.undo()
    succeeded = cache.seed_runtime_state(root, runtime, codex_version=CLI_VERSION,
                                         cache_root=cache_root)
    assert succeeded["status"] == "seeded"
    assert target.is_file()
    assert target.stat().st_nlink == 1


def test_kill_after_atomic_publication_leaves_valid_single_link_state(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    cache_root = root / "runs" / ".worker-state-cache-test"
    _install(root, cache_root=cache_root)
    runtime = root / "runs" / "future" / "agents" / "job" / "runtime"
    state = runtime / "state"
    state.mkdir(parents=True)
    publish = cache._publish_no_replace

    def publish_then_die(temp, target):
        publish(temp, target)
        raise OSError("test-only simulated kill after atomic publication")

    monkeypatch.setattr(cache, "_publish_no_replace", publish_then_die)
    interrupted = cache.seed_runtime_state(root, runtime, codex_version=CLI_VERSION,
                                           cache_root=cache_root)
    target = state / "state_5.sqlite"
    assert interrupted["status"] == "skipped"
    assert target.is_file() and target.stat().st_nlink == 1

    # This is the important wrapper contrast: its preflight accepts the
    # published single-link DB, then seeding recognizes it as existing state.
    monkeypatch.undo()
    retried = cache.seed_runtime_state(root, runtime, codex_version=CLI_VERSION,
                                       cache_root=cache_root)
    assert retried["reason"] == "existing-state-preserved"
    assert target.is_file() and target.stat().st_nlink == 1
