import json
import errno
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import threading

import pytest

from pipeline import landlock_exec


REPO = Path(__file__).resolve().parents[1]


def _tree(tmp_path):
    repo = tmp_path / "repo"
    source = repo / "pipeline"
    writable = repo / "runs" / "job" / "workspace"
    source.mkdir(parents=True)
    writable.mkdir(parents=True)
    protected = source / "protected.json"
    protected.write_text('{"protected": true}\n', encoding="utf-8")
    return repo, source, writable, protected


def _run(repo, writable, command, *, stdout="stdout.log", stderr="stderr.log", env=None,
         runtime_files=()):
    out = writable / stdout
    err = writable / stderr
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pipeline.landlock_exec",
            "--repository-root",
            str(repo),
            "--writable-root",
            str(writable),
            "--cwd",
            str(repo),
            "--stdout-log",
            str(out),
            "--stderr-log",
            str(err),
            *[part for value in runtime_files for part in ("--runtime-file", str(value))],
            "--",
            *command,
        ],
        cwd=REPO,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(REPO), **(env or {})},
        text=True,
        capture_output=True,
        check=False,
    )
    return proc, out, err


def test_landlock_child_can_read_repo_write_job_and_run_local_validation(tmp_path):
    repo, _, writable, protected = _tree(tmp_path)
    schema = writable / "schema.json"
    context = writable / "validation-context.json"
    schema.write_text('{"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"],"additionalProperties":false}\n', encoding="utf-8")
    context.write_text("[]\n", encoding="utf-8")
    code = r"""
import json, os, subprocess, sys
from pathlib import Path
root = Path.cwd()
workspace = root / 'runs/job/workspace'
assert (root / 'pipeline/protected.json').read_text().startswith('{')
(workspace / 'candidate.json').write_text('{\"ok\":true}\n')
env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
checked = subprocess.run([
    sys.executable, '-m', 'pipeline.worker_workspace', 'validate',
    '--workspace', str(workspace), '--candidate', str(workspace / 'candidate.json')
], env=env, capture_output=True, text=True)
print(json.dumps({'validation_rc': checked.returncode, 'validation': checked.stdout.strip(), 'error': checked.stderr.strip()}))
sys.exit(checked.returncode)
"""
    proc, out, err = _run(repo, writable, [sys.executable, "-c", code])
    assert proc.returncode == 0, f"{err.read_text(encoding='utf-8')} {out.read_text(encoding='utf-8')}"
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["validation_rc"] == 0, report["error"]
    assert "Schema and applicable source/link checks passed" in report["validation"]
    assert protected.read_text(encoding="utf-8") == '{"protected": true}\n'
    assert (writable / "candidate.json").exists()


def test_landlock_blocks_direct_truncate_symlink_and_hardlink_escape(tmp_path):
    repo, source_dir, writable, protected = _tree(tmp_path)
    escape_link = writable / "escape.json"
    escape_link.symlink_to(protected)
    code = r"""
import errno, json, os
from pathlib import Path
root = Path.cwd()
target = root / 'pipeline/protected.json'
workspace = root / 'runs/job/workspace'
outcomes = {}
for name, operation in [
    ('open_write', lambda: open(target, 'w').close()),
    ('truncate', lambda: os.truncate(target, 0)),
    ('symlink_write', lambda: open(workspace / 'escape.json', 'w').close()),
    ('hardlink', lambda: os.link(target, workspace / 'hardlink.json')),
]:
    try:
        operation()
    except OSError as exc:
        outcomes[name] = exc.errno in (errno.EACCES, errno.EPERM, errno.EXDEV)
    else:
        outcomes[name] = False
(workspace / 'own.txt').write_text('allowed')
print(json.dumps(outcomes, sort_keys=True))
"""
    proc, out, err = _run(repo, writable, [sys.executable, "-c", code])
    assert proc.returncode == 0, err.read_text(encoding="utf-8")
    assert json.loads(out.read_text(encoding="utf-8")) == {
        "hardlink": True,
        "open_write": True,
        "symlink_write": True,
        "truncate": True,
    }
    assert protected.read_text(encoding="utf-8") == '{"protected": true}\n'
    assert not (writable / "hardlink.json").exists()
    assert (writable / "own.txt").read_text(encoding="utf-8") == "allowed"


def test_landlock_does_not_remove_network_access(tmp_path):
    repo, _, writable, _ = _tree(tmp_path)
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    server.settimeout(5)
    received = []

    def accept_one():
        conn, _ = server.accept()
        with conn:
            received.append(conn.recv(32))
            conn.sendall(b"ok")

    thread = threading.Thread(target=accept_one, daemon=True)
    thread.start()
    port = server.getsockname()[1]
    code = "import socket; s=socket.create_connection(('127.0.0.1', %d), timeout=3); s.sendall(b'network'); assert s.recv(2)==b'ok'; s.close()" % port
    try:
        proc, _, _ = _run(repo, writable, [sys.executable, "-c", code])
        assert proc.returncode == 0, proc.stderr
        thread.join(timeout=5)
        assert received == [b"network"]
    finally:
        server.close()


def test_parent_log_descriptors_are_not_written_or_copied(tmp_path):
    repo, _, writable, _ = _tree(tmp_path)
    parent_log = repo / "pipeline" / "parent-event.log"
    code = "import sys; print('child stdout'); print('child stderr', file=sys.stderr)"
    with parent_log.open("wb") as inherited:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pipeline.landlock_exec",
                "--repository-root",
                str(repo),
                "--writable-root",
                str(writable),
                "--cwd",
                str(repo),
                "--stdout-log",
                str(writable / "stdout.log"),
                "--stderr-log",
                str(writable / "stderr.log"),
                "--",
                sys.executable,
                "-c",
                code,
            ],
            cwd=REPO,
            env={**os.environ, "PYTHONPATH": str(REPO), "PYTHONDONTWRITEBYTECODE": "1"},
            stdout=inherited,
            stderr=inherited,
            check=False,
        )
    assert proc.returncode == 0
    assert parent_log.read_bytes() == b""
    assert (writable / "stdout.log").read_text(encoding="utf-8") == "child stdout\n"
    assert (writable / "stderr.log").read_text(encoding="utf-8") == "child stderr\n"


def test_preexisting_hardlink_in_allowed_tree_fails_closed(tmp_path):
    repo, _, writable, protected = _tree(tmp_path)
    (writable / "alias").hardlink_to(protected)
    with pytest.raises(landlock_exec.LandlockError, match="multiply-linked"):
        landlock_exec.enforce_landlock(str(repo), [str(writable)])


def test_unsupported_landlock_abi_fails_closed(tmp_path, monkeypatch):
    repo, _, writable, _ = _tree(tmp_path)
    monkeypatch.setattr(landlock_exec, "landlock_abi", lambda: 2)
    with pytest.raises(landlock_exec.LandlockError, match="ABI 3 or newer"):
        landlock_exec.enforce_landlock(str(repo), [str(writable)])


def test_write_roots_cannot_widen_to_repository_or_symlink(tmp_path):
    repo, _, writable, _ = _tree(tmp_path)
    with pytest.raises(landlock_exec.LandlockError, match="repository root cannot be writable"):
        landlock_exec._validate_write_roots(str(repo), [str(repo)])
    link = tmp_path / "workspace-link"
    link.symlink_to(writable, target_is_directory=True)
    with pytest.raises(landlock_exec.LandlockError, match="symlink components"):
        landlock_exec._validate_write_roots(str(repo), [str(link)])


def test_close_inherited_fd_policy_has_no_one_megabyte_ceiling(monkeypatch):
    closed = []
    monkeypatch.setattr(landlock_exec, "_close_range", lambda first, last: False)
    monkeypatch.setattr(landlock_exec.os, "listdir", lambda path: ["3", "1048577"])
    monkeypatch.setattr(landlock_exec.os, "close", lambda fd: closed.append(fd))
    landlock_exec._close_inherited_fds()
    assert closed == [3, 1048577]


def test_pinned_log_open_rejects_dangling_symlink(tmp_path):
    repo, _, writable, _ = _tree(tmp_path)
    root_fd = landlock_exec._open_directory_no_symlinks(writable)
    try:
        dangling = writable / "dangling.log"
        dangling.symlink_to(writable / "not-created.log")
        with pytest.raises(landlock_exec.LandlockError, match="single-link regular file"):
            landlock_exec._open_log_file(root_fd, writable, dangling)
        with pytest.raises(landlock_exec.LandlockError, match="single-link regular file"):
            landlock_exec._log_path(str(dangling), [writable], label="stdout log")
        assert not (writable / "not-created.log").exists()
    finally:
        os.close(root_fd)


def test_explicit_runtime_file_is_exact_external_regular_file_write_allowance(tmp_path):
    repo, _, writable, _ = _tree(tmp_path)
    runtime = tmp_path / "codex" / "installation_id"
    sibling = runtime.parent / "auth.json"
    runtime.parent.mkdir()
    runtime.write_text("old-id", encoding="utf-8")
    sibling.write_text("keep-secret", encoding="utf-8")
    code = r"""
from pathlib import Path
import json
runtime = Path(%r)
sibling = Path(%r)
runtime.write_text('new-id', encoding='utf-8')
try:
    sibling.write_text('changed', encoding='utf-8')
except OSError:
    sibling_blocked = True
else:
    sibling_blocked = False
print(json.dumps({'sibling_blocked': sibling_blocked}))
""" % (str(runtime), str(sibling))
    proc, out, err = _run(repo, writable, [sys.executable, "-c", code], runtime_files=[runtime])
    assert proc.returncode == 0, err.read_text(encoding="utf-8")
    assert json.loads(out.read_text(encoding="utf-8")) == {"sibling_blocked": True}
    assert runtime.read_text(encoding="utf-8") == "new-id"
    assert sibling.read_text(encoding="utf-8") == "keep-secret"


def test_runtime_file_cannot_be_inside_repository_or_follow_symlink(tmp_path):
    repo, _, writable, protected = _tree(tmp_path)
    with pytest.raises(landlock_exec.LandlockError, match="outside the repository"):
        landlock_exec._validate_runtime_files(repo, [str(protected)])
    external = tmp_path / "runtime-id"
    external.write_text("id", encoding="utf-8")
    link = tmp_path / "runtime-link"
    link.symlink_to(external)
    with pytest.raises(landlock_exec.LandlockError, match="symlink components"):
        landlock_exec._validate_runtime_files(repo, [str(link)])


def test_wrapper_rejects_dangling_symlink_as_log_target(tmp_path):
    repo, _, writable, _ = _tree(tmp_path)
    (writable / "stdout.log").symlink_to(writable / "would-be-created.log")
    proc, _, _ = _run(repo, writable, [sys.executable, "-c", "print('should not run')"])
    assert proc.returncode == 125
    assert "stdout log must be a single-link regular file" in proc.stderr
    assert not (writable / "would-be-created.log").exists()


def test_shell_dev_null_redirection_works_while_repository_writes_stay_blocked(tmp_path):
    repo, _, writable, protected = _tree(tmp_path)
    code = (
        "from pathlib import Path; p=Path(" + repr(str(protected)) + "); "
        "\ntry: p.write_text('changed')"
        "\nexcept PermissionError: print('protected-write-blocked')"
        "\nelse: print('protected-write-allowed')"
    )
    shell_script = "printf hidden >/dev/null; " + shlex.quote(sys.executable) + " -c " + shlex.quote(code)
    proc, out, err = _run(
        repo, writable, ["bash", "--noprofile", "--norc", "-c", shell_script]
    )
    assert proc.returncode == 0, err.read_text(encoding="utf-8")
    assert out.read_text(encoding="utf-8").strip() == "protected-write-blocked"
    assert protected.read_text(encoding="utf-8") == '{"protected": true}\n'
