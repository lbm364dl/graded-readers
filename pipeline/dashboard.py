"""Read-only local dashboard for graded-reader pipeline runs.

Run from the repository root with::

    .venv/bin/python -m pipeline.dashboard [--run-dir PATH ...]

The server binds only to loopback and exposes aggregate state, never raw logs,
prompts, environment data, or worker event streams.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = Path(__file__).with_name("dashboard.html")
MAX_JSON_BYTES = 8 * 1024 * 1024
SNAPSHOT_CACHE_SECONDS = 4.0
# These are worker payload/runtime trees, not job namespaces. They may contain
# thousands of generated files and must not be walked to find job metadata.
_AGENT_PAYLOAD_DIRS = frozenset({"workspace", "workspaces", "runtime", "inputs", "references",
                                "logs", "tmp", "cache", "state"})


def _json(path: Path, *, max_bytes: int = MAX_JSON_BYTES) -> Any | None:
    try:
        if not path.is_file() or path.stat().st_size > max_bytes:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None


def _sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(128 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def _iso_epoch(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return path.name


def _infer_run(path: Path, root: Path) -> tuple[str, str, int | None]:
    text = path.as_posix().lower()
    patterns = (
        (r"(?:^|[/_-])korean(?:-topik)?[-_]?([1-6])(?:[/_-]|$)", "Korean", "TOPIK"),
        (r"(?:^|[/_-])topik[-_]?([1-6])(?:[/_-]|$)", "Korean", "TOPIK"),
        (r"(?:^|[/_-])japanese(?:-n)?[-_]?([1-5])(?:[/_-]|$)", "Japanese", "JLPT N"),
        (r"(?:^|[/_-])n[-_]?([1-5])(?:[/_-]|$)", "Japanese", "JLPT N"),
        (r"(?:^|[/_-])chinese(?:-hsk)?[-_]?([1-9])(?:[/_-]|$)", "Chinese", "HSK"),
        (r"(?:^|[/_-])hsk[-_]?([1-9])(?:[/_-]|$)", "Chinese", "HSK"),
    )
    for pattern, language, label in patterns:
        match = re.search(pattern, text)
        if match:
            number = int(match.group(1))
            return language, f"{label} {number}", number
    rel = _relative(path, root)
    first = rel.split("/", 1)[0]
    return first.title() if first else "Run", "Level unknown", None


def _processes() -> list[dict[str, Any]]:
    """Read process evidence. Keep argv private; return only extracted fields."""
    try:
        result = subprocess.run(["ps", "-eo", "pid=,stat=,etimes=,args="], capture_output=True,
                                text=True, check=False, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return []
    found: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        match = re.match(r"\s*(\d+)\s+(\S+)\s+(\d+)\s+(.*)$", line)
        if not match:
            continue
        try:
            argv = shlex.split(match.group(4))
        except ValueError:
            continue
        if not argv:
            continue
        pid = int(match.group(1))
        proc_stat = match.group(2)
        # A SIGSTOP/SIGTSTP process still exists in /proc and ps, but cannot
        # make progress. Zombies have exited and must not look live either.
        proc_state = "suspended" if proc_stat[:1] in {"T", "t"} else "running"
        if proc_stat[:1] == "Z":
            continue
        try:
            cwd = Path(os.readlink(f"/proc/{pid}/cwd"))
        except OSError:
            cwd = None
        found.append({"pid": pid, "elapsed_seconds": int(match.group(3)), "argv": argv,
                      "cwd": cwd, "process_state": proc_state, "proc_stat": proc_stat})
    return found


def _flag_value(argv: list[str], flag: str) -> str | None:
    for index, value in enumerate(argv[:-1]):
        if value == flag:
            return argv[index + 1]
    return None


def _output_result_path(argv: list[str]) -> str | None:
    for index, value in enumerate(argv):
        if value in {"-o", "--output-last-message"} and index + 1 < len(argv):
            return argv[index + 1]
        for flag in ("--output-last-message=", "-o="):
            if value.startswith(flag):
                return value[len(flag):]
    return None


def _stage_for_job(name: str) -> str:
    normalized = name.lower().replace("_", "-")
    if normalized.startswith("annotations/"):
        if "semantic-patch" in normalized or "repair" in normalized:
            return "annotation semantic patch"
        if "review" in normalized:
            return "annotation review"
        return "annotation work"
    canonical = ("sentence-help", "lexical-plan", "annotation", "dictionary", "curriculum", "prose", "plan")
    base = next((stage for stage in canonical if normalized.startswith(stage)), normalized.split("-", 1)[0])
    if "review" in normalized:
        return f"{base} review"
    if base == "annotation" and ("chunk" in normalized or re.match(r"annotation-\d", normalized)):
        return "annotation generation"
    if normalized.endswith("assembly") or "-assembly-" in normalized:
        return f"{base} assembly"
    return f"{base} work"


def _review_summary(result: Any) -> tuple[str | None, int, list[str]]:
    if not isinstance(result, dict):
        return None, 0, []
    issues = result.get("issues")
    count = len(issues) if isinstance(issues, list) else 0
    summaries = []
    if isinstance(issues, list):
        for item in issues[:3]:
            if not isinstance(item, str):
                continue
            clean = re.sub(r"\s+", " ", "".join(ch for ch in item if ch >= " " and ch != "\x7f")).strip()
            if clean:
                summaries.append(clean[:240])
    if result.get("approved") is True and isinstance(issues, list) and not issues:
        outcome = "approved"
    elif result.get("approved") is False or isinstance(issues, list) and bool(issues):
        outcome = "rejected"
    else:
        outcome = "unknown"
    return outcome, count, summaries


def _safe_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    clean = re.sub(r"\s+", " ", "".join(ch for ch in value if ch >= " " and ch != "\x7f")).strip()
    return clean[:limit] if clean else None


def _terminal_failure(run_dir: Path) -> dict[str, str] | None:
    """Interpret only allowlisted categories from a completed traceback.

    The log and free-form exception message never leave the backend. Prepared
    checkpoints without a recognized terminal failure remain idle.
    """
    path = run_dir / "resume-detached.log"
    try:
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - 65536))
            tail = handle.read(65536).decode("utf-8", errors="replace")
    except OSError:
        return None
    marker = "Traceback (most recent call last):"
    if marker not in tail:
        return None
    traceback = tail[tail.rfind(marker):]
    final_line = next((line.strip() for line in reversed(traceback.splitlines()) if line.strip()), "")
    final_categories = (
        (r"^ValueError: Korean annotation chunk \d+ failed independent review:",
         "Independent review did not approve an annotation chunk"),
        (r"^ValueError: Korean annotation chunk \d+ failed reconstruction:",
         "Annotation chunk failed reconstruction checks"),
    )
    for pattern, category in final_categories:
        if re.match(pattern, final_line):
            return {"category": category, "evidence": "resume-detached.log traceback"}
    # jsonschema's ValidationError is followed by its structured validator
    # name and instance path; require those markers so arbitrary exception text
    # that happens to contain a class name cannot create a failure status.
    if (re.search(r"(?:^|\n)jsonschema\.exceptions\.ValidationError:", traceback)
            and "\nFailed validating " in traceback
            and "\nOn instance" in traceback):
        return {"category": "Worker output failed schema validation",
                "evidence": "resume-detached.log traceback"}
    return None


def _job_part(name: str) -> str | None:
    match = re.search(r"(?:^|[-_/])(?:chunk|part)[-_]?0*(\d+)(?:[-_/]|$)", name, re.IGNORECASE)
    return f"chunk {int(match.group(1))}" if match else None


def _worker_processes(run_dir: Path, root: Path, processes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prove and deduplicate live workers by explicit result output path."""
    agents_root = (run_dir / "agents").resolve()
    matches: dict[str, dict[str, Any]] = {}
    for process in processes:
        argv = process.get("argv", [])
        if not argv or Path(argv[0]).name != "codex":
            continue
        output = _output_result_path(argv)
        if not output:
            continue
        candidate = Path(output)
        if not candidate.is_absolute():
            candidate = process.get("cwd") or root
            candidate = candidate / output
        try:
            resolved = candidate.resolve()
            relative = resolved.relative_to(agents_root)
        except (OSError, ValueError):
            continue
        if (len(relative.parts) < 2 or len(relative.parts) > 10
                or relative.parts[-1] not in {"result.json", "receipt.json"}
                or any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", part) or part in {".", ".."}
                       for part in relative.parts)):
            continue
        job_parts = relative.parts[:-1]
        # Workspace-profile Codex output is written under the exact per-job
        # runtime directory. Strip that one protocol component so it matches
        # the coordinator's job metadata. A legacy/nested job literally named
        # "runtime" still maps correctly because it has no parent component.
        if (relative.parts[-1] == "receipt.json" and len(job_parts) >= 2
                and job_parts[-1] == "runtime"):
            job_parts = job_parts[:-1]
        if not job_parts:
            continue
        job = "/".join(job_parts)
        candidate_match = {"pid": process["pid"], "elapsed_seconds": process["elapsed_seconds"],
                           "process_state": process.get("process_state", "running"),
                           "role": "worker", "job": job, "stage": _stage_for_job(job),
                           "started_epoch": time.time() - process["elapsed_seconds"]}
        # A CLI wrapper and its native child can both advertise the same output
        # file. Count that one model task once and retain its earliest start.
        key = str(resolved)
        previous = matches.get(key)
        if (previous is None
                or (previous["process_state"] == "suspended" and candidate_match["process_state"] == "running")
                or (previous["process_state"] == candidate_match["process_state"]
                    and candidate_match["elapsed_seconds"] > previous["elapsed_seconds"])):
            matches[key] = candidate_match
    return list(matches.values())


def _matching_processes(path: Path, root: Path, processes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    target = path.resolve()
    matches = []
    for process in processes:
        argv = process["argv"]
        module = any(name in argv for name in ("pipeline.korean_agent_harness", "pipeline.japanese_agent_harness",
                                               "pipeline.agent_harness"))
        run_dir = _flag_value(argv, "--run-dir")
        output_dir = _flag_value(argv, "--output")
        chosen = run_dir if module else output_dir if "pipeline.maintenance_loop" in argv else None
        if not chosen:
            continue
        candidate = Path(chosen)
        if not candidate.is_absolute():
            candidate = process.get("cwd") or root
            candidate = candidate / run_dir if module else candidate / output_dir
        try:
            if candidate.resolve() != target:
                continue
        except OSError:
            continue
        if module:
            role = "pipeline harness"
        else:
            role = "maintenance loop"
        matches.append({"pid": process["pid"], "elapsed_seconds": process["elapsed_seconds"],
                        "process_state": process.get("process_state", "running"),
                        "role": role, "started_epoch": time.time() - process["elapsed_seconds"]})
    return matches


class Dashboard:
    def __init__(self, repo_root: str | Path = ROOT, run_dirs: list[str | Path] | None = None,
                 maintenance_dir: str | Path | None = None):
        self.root = Path(repo_root).resolve()
        defaults = [self.root / "runs" / f"korean-topik{level}" / "chapter-001" for level in range(1, 7)]
        supplied = [Path(item) for item in (run_dirs or [])]
        resolved: dict[str, Path] = {}
        for path in [*defaults, *supplied]:
            if not path.is_absolute():
                path = self.root / path
            try:
                resolved[str(path.resolve())] = path.resolve()
            except OSError:
                resolved[str(path)] = path
        self.run_dirs = list(resolved.values())
        maint = Path(maintenance_dir) if maintenance_dir else self.root / "runs/maintenance/20261003"
        self.maintenance_dir = (self.root / maint).resolve() if not maint.is_absolute() else maint.resolve()
        self._file_cache: dict[str, tuple[int, int, Any]] = {}
        self._snapshot_lock = threading.Lock()
        self._snapshot_cache: dict[str, Any] | None = None
        self._snapshot_cached_at = 0.0
        self._origin_repository = self._read_origin_repository()

    def _read_origin_repository(self) -> str | None:
        try:
            result = subprocess.run(["git", "-C", str(self.root), "remote", "get-url", "origin"],
                                    capture_output=True, text=True, check=False, timeout=2)
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        remote = result.stdout.strip()
        match = re.search(r"(?:github\.com[:/])([^/\s]+/[^/\s]+?)(?:\.git)?$", remote)
        repository = match.group(1) if match else None
        return repository if repository and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) else None

    def _cached_json(self, path: Path, max_bytes: int = MAX_JSON_BYTES) -> Any | None:
        try:
            stat = path.stat()
            key = str(path)
            fingerprint = (stat.st_mtime_ns, stat.st_size)
            cached = self._file_cache.get(key)
            if cached and cached[:2] == fingerprint:
                return cached[2]
            value = _json(path, max_bytes=max_bytes)
            self._file_cache[key] = (*fingerprint, value)
            return value
        except OSError:
            return None

    def _job_summaries(self, run_dir: Path, workers: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, dict[str, Any] | None]:
        jobs: list[dict[str, Any]] = []
        rejected_attempts = 0
        rejected_rows: list[dict[str, Any]] = []
        worker_by_job = {item["job"]: item for item in workers}
        agents = run_dir / "agents"
        try:
            # Agent workspaces can be gigabytes and contain many deeply nested
            # files. Metadata is stored at job roots; walk only the job
            # namespace, pruning known payload trees before descending.
            for current, dirnames, filenames in os.walk(agents, topdown=True, followlinks=False):
                current_path = Path(current)
                try:
                    relative_dir = current_path.relative_to(agents)
                except ValueError:
                    dirnames[:] = []
                    continue
                if len(relative_dir.parts) >= 9:
                    dirnames[:] = []
                else:
                    kept_dirs = []
                    for name in dirnames:
                        if (not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", name)
                                or name in {".", "..", "history"}):
                            continue
                        child = current_path / name
                        # Reserved scratch names are still valid job names at
                        # namespace nodes when they contain their own meta.
                        # Once inside a coordinator job, these names always
                        # identify payload subtrees and can be pruned safely.
                        child_is_job = (child / "meta.json").is_file()
                        at_namespace = (not relative_dir.parts
                                       or relative_dir.parts[0] == "annotations" and "meta.json" not in filenames)
                        if name in _AGENT_PAYLOAD_DIRS and (not at_namespace or not child_is_job):
                            continue
                        kept_dirs.append(name)
                    dirnames[:] = kept_dirs
                if "meta.json" not in filenames:
                    continue
                meta_path = current_path / "meta.json"
                try:
                    relative_job = meta_path.parent.relative_to(agents)
                except ValueError:
                    continue
                # Archived quarantined attempts are evidence, not current jobs
                # or additional review attempts in the live run summary.
                if "history" in relative_job.parts:
                    continue
                if (len(relative_job.parts) > 9 or any(
                        not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", part) or part in {".", ".."}
                        for part in relative_job.parts)):
                    continue
                meta = self._cached_json(meta_path, 512 * 1024)
                if not isinstance(meta, dict):
                    continue
                name = "/".join(relative_job.parts)[:240]
                started = _iso_epoch(meta.get("started_at"))
                ended = _iso_epoch(meta.get("ended_at"))
                rc = meta.get("return_code")
                active_worker = worker_by_job.get(name)
                active = active_worker is not None
                result = None
                if "review" in name.lower():
                    result_path = meta_path.parent / "result.json"
                    result = self._cached_json(result_path, 1024 * 1024)
                    if not active and ended is not None and rc == 0 and isinstance(result, dict):
                        review_state, issue_count, review_issues = _review_summary(result)
                        if review_state == "rejected":
                            rejected_attempts += 1
                            rejected_rows.append({"job": name, "stage": _stage_for_job(name),
                                "review_state": review_state, "review_issue_count": issue_count,
                                "review_issues": review_issues,
                                "ended_at": meta.get("ended_at") if isinstance(meta.get("ended_at"), str) else None})
                    else:
                        review_state, issue_count, review_issues = None, 0, []
                else:
                    review_state, issue_count, review_issues = None, 0, []
                if active or ended is not None:
                    stage = _stage_for_job(name)
                    process_state = (active_worker.get("process_state", "running") if active
                                     else ("succeeded" if rc == 0 else "failed" if rc is not None else "unknown"))
                    jobs.append({"job": name, "stage": stage,
                                 "state": process_state, "process_state": process_state, "review_state": review_state,
                                 "review_issue_count": issue_count, "review_issues": review_issues,
                                 "submission_state": ("running" if process_state == "running" else "suspended") if active else "submitted" if rc == 0 else "process failed" if rc is not None else "unknown",
                                 "part": _job_part(name),
                                 "elapsed_seconds": worker_by_job.get(name, {}).get("elapsed_seconds") if active else None,
                                 "started_at": meta.get("started_at") if isinstance(meta.get("started_at"), str) else None,
                                 "ended_at": meta.get("ended_at") if isinstance(meta.get("ended_at"), str) else None})
        except OSError:
            pass
        known = {job["job"] for job in jobs}
        for worker in workers:
            if worker["job"] in known:
                continue
            from datetime import datetime, timezone
            started_at = datetime.fromtimestamp(worker["started_epoch"], timezone.utc).isoformat()
            process_state = worker.get("process_state", "running")
            jobs.append({"job": worker["job"], "stage": worker["stage"], "state": process_state, "process_state": process_state,
                         "review_state": None, "review_issue_count": 0, "review_issues": [],
                         "submission_state": process_state, "part": _job_part(worker["job"]),
                         "elapsed_seconds": worker["elapsed_seconds"],
                         "started_at": started_at, "ended_at": None})
        jobs.sort(key=lambda row: _iso_epoch(row.get("ended_at") or row.get("started_at")) or 0, reverse=True)
        rejected_rows.sort(key=lambda row: _iso_epoch(row.get("ended_at")) or 0, reverse=True)
        return jobs[:12], rejected_attempts, rejected_rows[0] if rejected_rows else None

    def _publication(self, language: str, number: int | None, run_dir: Path) -> tuple[str, str | None]:
        report = self._cached_json(run_dir / "report.json")
        chapter = run_dir / "chapter.json"
        if isinstance(report, dict) and report.get("status") == "complete" and chapter.is_file():
            expected = report.get("artifacts", {}).get("chapter.json") if isinstance(report.get("artifacts"), dict) else None
            actual = _sha256(chapter)
            artifacts_ok = isinstance(expected, str) and expected == actual
            all_artifacts_ok = bool(report.get("artifacts")) and artifacts_ok
            if all_artifacts_ok:
                for rel, digest in report["artifacts"].items():
                    artifact_path = run_dir / rel if isinstance(rel, str) else run_dir / "__invalid__"
                    try:
                        artifact_path.resolve().relative_to(run_dir.resolve())
                    except (OSError, ValueError):
                        all_artifacts_ok = False
                        break
                    if not isinstance(rel, str) or not isinstance(digest, str) or _sha256(artifact_path) != digest:
                        all_artifacts_ok = False
                        break
            stages = report.get("stages")
            approved = isinstance(stages, dict) and bool(stages) and all(
                isinstance(stage, dict) and stage.get("approved") is True for stage in stages.values())
            if all_artifacts_ok and approved:
                local = "Complete report · recorded approvals and artifact hashes match"
            else:
                local = "Draft / gate evidence incomplete · report present, review or artifact evidence not fully verified"
        else:
            prep = self._cached_json(run_dir / "preparation.json")
            if isinstance(prep, dict) and isinstance(prep.get("stages"), dict):
                approvals = [v.get("approved") for v in prep["stages"].values() if isinstance(v, dict)]
                local = "Preparation review only · chapter review gate not verified" if approvals and all(x is True for x in approvals) else "Draft / in preparation"
            else:
                local = "Unknown · required report/preparation artifacts absent"

        if language == "Korean" and number is not None:
            metadata = self._cached_json(self.root / "content/korean/honggildong/metadata.json")
            public_file = self.root / f"content/korean/honggildong/l{number}.md"
            review = self._cached_json(self.root / f"content/korean/honggildong/l{number}.review.json")
            review_rows = review.get("chapters") if isinstance(review, dict) else None
            run_chapter_data = self._cached_json(chapter)
            run_chapter_digest = hashlib.sha256(json.dumps(run_chapter_data, ensure_ascii=False,
                                                            sort_keys=True).encode()).hexdigest() if isinstance(run_chapter_data, dict) else None
            review_has_chapter = isinstance(review_rows, list) and any(
                isinstance(row, dict) and row.get("number") == 1 and isinstance(row.get("reviews"), dict)
                and row.get("chapter_digest") == run_chapter_digest
                for row in review_rows)
            enabled = isinstance(metadata, dict) and f"l{number}" in metadata.get("enabled_levels", [])
            if enabled and public_file.is_file() and review_has_chapter:
                return "Published · public file, enabled metadata, and matching chapter review digest observed", local
        return "Not observed as published", local

    def _chapter(self, run_dir: Path, processes: list[dict[str, Any]]) -> dict[str, Any]:
        language, level, number = _infer_run(run_dir, self.root)
        coordinators = _matching_processes(run_dir, self.root, processes)
        workers = _worker_processes(run_dir, self.root, processes)
        live = [{**item, "process_type": "coordinator"} for item in coordinators]
        live.extend({**item, "process_type": "worker"} for item in workers)
        report = self._cached_json(run_dir / "report.json")
        prep = self._cached_json(run_dir / "preparation.json")
        publication, local = self._publication(language, number, run_dir)
        jobs, rejected, latest_rejected = self._job_summaries(run_dir, workers)
        terminal_failure = _terminal_failure(run_dir)
        running_processes = [item for item in [*coordinators, *workers]
                             if item.get("process_state", "running") == "running"]
        suspended_processes = [item for item in [*coordinators, *workers]
                               if item.get("process_state") == "suspended"]
        if running_processes:
            state = "running"
            current = "stage unknown"
            active_jobs = [job for job in jobs if job["state"] == "running"]
            if active_jobs:
                current = ", ".join(sorted({job["stage"] for job in active_jobs}))
            elif any(item.get("process_state", "running") == "running" for item in coordinators):
                current = "harness running; current substage not recorded"
            state = f"Running · {current}"
        elif suspended_processes:
            state = "Suspended · no agents working"
        elif isinstance(report, dict) and report.get("status") == "complete" and (run_dir / "chapter.json").is_file():
            state = "Complete report · recorded approvals and artifact hashes match" if local.startswith("Complete report") else "Report says complete · review or artifact integrity unverified"
        elif isinstance(prep, dict) and prep.get("status") == "prepared":
            state = "Prepared; downstream chapter status unknown"
        elif (run_dir / "chapter.json").is_file() and report is None:
            state = "Chapter artifact exists; completion report absent"
        elif run_dir.exists():
            state = "No verified live process; incomplete or unknown"
        else:
            state = "Run directory absent"
        relative = _relative(run_dir, self.root)
        chapter_number = next((value.get('number') for value in (report, prep)
            if isinstance(value, dict) and type(value.get('number')) is int and value['number'] > 0), None)
        details = []
        for process in live:
            details.append({"role": process["role"], "process_type": process["process_type"],
                            "job": process.get("job"), "pid": process["pid"],
                            "process_state": process.get("process_state", "running"),
                            "stage": process.get("stage"), "part": _job_part(process.get("job") or ""),
                            "elapsed_seconds": process["elapsed_seconds"]})
        row = {"id": relative, "path": relative, "language": language, "level": level, "level_number": number,
                "chapter_number": chapter_number,
                "state": state, "publication": publication, "local_status": local, "active_processes": details,
                "running_process_count": len(running_processes),
                "suspended_process_count": len(suspended_processes),
                "rejected_review_attempts": rejected, "latest_rejected_review": latest_rejected,
                "terminal_failure": terminal_failure, "recent_jobs": jobs,
                "artifact_notes": self._artifact_notes(run_dir, report, prep)}
        row['display'] = self._plain_progress(row, report, prep)
        return row

    @staticmethod
    def _plain_progress(row, report, prep):
        """Explain the evidence in terms of reader work, not worker artifacts."""
        workers = [p for p in row['active_processes']
                   if p['process_type'] == 'worker' and p.get('process_state', 'running') == 'running']
        suspended_workers = [p for p in row['active_processes']
                             if p['process_type'] == 'worker' and p.get('process_state') == 'suspended']
        adding_help = any(_stage_for_job(p.get('job') or '').startswith('annotation') for p in workers)
        live = any(p.get('process_state', 'running') == 'running' for p in row['active_processes'])
        suspended = any(p.get('process_state') == 'suspended' for p in row['active_processes'])
        evidence = report if isinstance(report, dict) and report.get('status') == 'complete' else prep
        stages = evidence.get('stages', {}) if isinstance(evidence, dict) else {}
        passed = lambda name: isinstance(stages.get(name), dict) and stages[name].get('approved') is True
        text_ready = passed('prose')
        help_ready = all(passed(name) for name in ('annotation', 'dictionary', 'sentence-help'))
        reviewed = row['local_status'].startswith('Complete report')
        published = row['publication'].startswith('Published')
        if published:
            status, step, explanation = 'Ready', 'Available to read', 'Chapter 1 is published.'
        elif live:
            status = 'Working'
            step = 'Running final checks' if help_ready else 'Adding word and grammar help' if text_ready or adding_help else 'Writing the chapter'
            explanation = ('Annotations are being prepared and checked.' if adding_help and not help_ready
                           else 'The text is written. Annotations are being prepared and checked.' if text_ready and not help_ready
                           else 'The complete chapter is being checked before publication.' if help_ready
                           else 'The story and learner-level text are being prepared.')
            if not workers:
                explanation += ' The pipeline is preparing its next step.'
        elif suspended:
            status, step = 'Paused', 'Paused before approval'
            explanation = ('The chapter process is suspended; no agents are working now. Saved submissions and review attempts remain in the history below. This pause does not mean you need to approve the chapter.')
        elif reviewed:
            status, step, explanation = 'Needs attention', 'Ready for publication', 'The chapter checks are complete. Publication is still pending.'
        elif row.get('terminal_failure'):
            status, step = 'Checks failed', 'Checks failed'
            explanation = f"The last run stopped because checks failed: {row['terminal_failure']['category']}. No chapter agents are running."
        elif row['language'] == 'Korean' and text_ready:
            status, step, explanation = 'Waiting for checks', 'Waiting for checks', 'The text is written, but no terminal failure or live chapter process is recorded.'
        elif row['state'] == 'Run directory absent':
            status, step, explanation = 'Unknown', 'No run found', 'No chapter run has been recorded yet.'
        else:
            status, step, explanation = 'Idle', 'Not running', 'Saved run history is available. No workers are active for this run.'
        activity = {}
        for worker in workers:
            label = _stage_for_job(worker.get('job') or '')
            if label == 'annotation generation':
                label = 'Adding word and grammar help'
            elif label == 'annotation review':
                label = 'Checking annotations'
            elif 'lexical' in label or 'research' in label:
                label = 'Researching words'
            elif 'review' in label:
                label = 'Checking the chapter'
            else:
                label = 'Preparing chapter material'
            activity[label] = activity.get(label, 0) + 1
        steps = [{'label': 'Text', 'state': 'done' if text_ready or published else 'working' if live else 'waiting'},
                 {'label': 'Word & grammar help', 'state': 'done' if help_ready or published else 'working' if live and text_ready else 'attention' if status == 'Checks failed' else 'waiting'},
                 {'label': 'Final checks', 'state': 'done' if reviewed or published else 'working' if live and help_ready else 'waiting'},
                 {'label': 'Ready', 'state': 'done' if published else 'waiting'}]
        return {'title': row['level'], 'status': status, 'step': step, 'explanation': explanation,
                'workers': len(workers), 'suspended_workers': len(suspended_workers),
                'running_processes': row.get('running_process_count', 0),
                'suspended_processes': row.get('suspended_process_count', 0),
                'activity': [{'label': label, 'count': count} for label, count in activity.items()],
                'steps': steps}

    def _artifact_notes(self, run_dir: Path, report: Any, prep: Any) -> list[str]:
        notes = []
        for filename, obj in (("report.json", report), ("preparation.json", prep)):
            path = run_dir / filename
            if path.exists() and obj is None:
                notes.append(f"{_relative(path, self.root)} exists but is unreadable or changing; status is unknown")
        if not (run_dir / "report.json").exists() and not (run_dir / "preparation.json").exists():
            notes.append("No report.json or preparation.json was observed")
        return notes

    def _maintenance(self, processes: list[dict[str, Any]]) -> dict[str, Any]:
        base = self.maintenance_dir
        status = self._cached_json(base / "status.json")
        report = self._cached_json(base / "incidents.json")
        summary = self._cached_json(base / "triage/summary.json")
        triage = self._cached_json(base / "triage/triage-state.json")
        github = self._cached_json(base / "github-state.json")
        verified_fixes = self._cached_json(base / "verified-fixes.json")
        all_processes = _matching_processes(base, self.root, processes)
        live = [item for item in all_processes if item.get("process_state", "running") == "running"]
        suspended = [item for item in all_processes if item.get("process_state") == "suspended"]
        clusters = report.get("clusters", []) if isinstance(report, dict) else []
        findings: dict[str, dict[str, Any]] = {}
        if isinstance(triage, dict) and isinstance(triage.get("clusters"), dict):
            for cluster_id, record in triage["clusters"].items():
                finding = record.get("finding") if isinstance(record, dict) else None
                if isinstance(finding, dict):
                    findings[str(cluster_id)] = finding
        if isinstance(summary, dict):
            for row in summary.get("new_diagnoses", []):
                finding = row.get("finding") if isinstance(row, dict) else None
                if isinstance(finding, dict) and finding.get("cluster_id"):
                    findings[str(finding["cluster_id"])] = finding
        verified_rows = self._verified_fix_rows(verified_fixes)
        verified = len(verified_rows)
        verified_ids = {row["cluster_id"] for row in verified_rows}
        proposed = sum(1 for cluster_id in findings if cluster_id not in verified_ids)
        implementation_tested = sum(1 for row in verified_fixes.get("fixes", [])
                                     if isinstance(row, dict) and self._implementation_tests_passed(row)) if isinstance(verified_fixes, dict) else 0
        reviewed_pending_production = sum(1 for row in verified_fixes.get("fixes", [])
                                          if isinstance(row, dict) and self._reviewed_pending_production(row)) if isinstance(verified_fixes, dict) else 0
        gh_issues = github.get("issues", {}) if isinstance(github, dict) else {}
        issue_count = sum(1 for value in gh_issues.values() if isinstance(value, (str, int, dict))) if isinstance(gh_issues, dict) else 0
        github_status = status.get("github", {}) if isinstance(status, dict) and isinstance(status.get("github"), dict) else {}
        repository = github_status.get("repository") if isinstance(github_status.get("repository"), str) else None
        if not repository and isinstance(github, dict):
            repository = github.get("repository") if isinstance(github.get("repository"), str) else None
        if not repository:
            repository = self._origin_repository
        incidents_count = report.get("incident_count") if isinstance(report, dict) else None
        incident_jobs = {job for cluster in clusters if isinstance(cluster, dict)
                         for job in cluster.get('jobs', []) if isinstance(job, str)}
        github_state_label = self._github_state_label(github, github_status, status)
        return {"path": _relative(base, self.root),
                "state": "Running" if live else "Suspended · no maintenance work is executing" if suspended else "Stopped / no verified live process",
                "running_process_count": len(live), "suspended_process_count": len(suspended),
                "active_processes": [{"pid": item["pid"], "process_state": item.get("process_state", "running"),
                                      "elapsed_seconds": item["elapsed_seconds"]}
                                     for item in [*live, *suspended]],
                "phase": status.get("phase") if isinstance(status, dict) else None,
                "updated_at": status.get("updated_at") if isinstance(status, dict) else None,
                "incidents": incidents_count if isinstance(incidents_count, int) else None,
                "incident_clusters": len(clusters) if isinstance(clusters, list) else None,
                "jobs_with_incidents": len(incident_jobs) if isinstance(report, dict) else None,
                "proposed_fixes": proposed, "implementation_tested_fixes": implementation_tested,
                "reviewed_pending_production": reviewed_pending_production,
                "verified_fixes": verified,
                "fix_note": "Triage labels, including ‘already addressed’, are unverified proposals. A verified fix needs recorded implementation tests and independent review; production re-verification is tracked separately.",
                "github": {"state_file": github_state_label,
                           "issue_records": issue_count, "repository": repository,
                           "issue2": self._issue_by_number(github, gh_issues, 2, repository),
                           "pr1": self._pr_link(github, repository)},
                "recurrent": self._top_clusters(clusters, findings, gh_issues, repository)}

    @staticmethod
    def _github_state_label(github: Any, github_status: Any, status: Any) -> str:
        if github is None:
            return "offline / local GitHub state file not observed"
        if not isinstance(github_status, dict):
            return "partial or stale / issue map exists, current sync status absent"
        if github_status.get("enabled") is False:
            return "offline / GitHub sync disabled"
        if github_status.get("errors"):
            return "stale or partial / latest sync reported errors"
        updated = status.get("updated_at") if isinstance(status, dict) else None
        return f"sync status observed{'; maintenance updated '+updated if isinstance(updated, str) else ''}"

    def _verified_fix_rows(self, data: Any) -> list[dict[str, Any]]:
        """Accept verification only from explicit local evidence, never triage labels."""
        rows = data.get("fixes", []) if isinstance(data, dict) else []
        if not isinstance(rows, list):
            return []
        valid = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            review = row.get("independent_review")
            if not self._implementation_tests_passed(row) or not isinstance(review, dict):
                continue
            if review.get("status") != "passed":
                continue
            implementation = row.get("implementation_tests")
            evidence_paths = [implementation.get("evidence_path"), review.get("evidence_path")]
            if not all(isinstance(item, str) and self._evidence_path_exists(item) for item in evidence_paths):
                continue
            if row.get("production_verification") != "verified":
                continue
            valid.append({"cluster_id": str(row.get("cluster_id", "")),
                          "production_verification": row["production_verification"]})
        return valid

    @staticmethod
    def _implementation_tests_passed(row: dict[str, Any]) -> bool:
        evidence = row.get("implementation_tests")
        return isinstance(evidence, dict) and evidence.get("status") == "passed"

    def _reviewed_pending_production(self, row: dict[str, Any]) -> bool:
        implementation = row.get("implementation_tests")
        review = row.get("independent_review")
        if not self._implementation_tests_passed(row) or not isinstance(review, dict):
            return False
        if review.get("status") != "passed" or row.get("production_verification") != "pending":
            return False
        return all(isinstance(item, str) and self._evidence_path_exists(item) for item in
                   (implementation.get("evidence_path"), review.get("evidence_path")))

    def _evidence_path_exists(self, value: str) -> bool:
        path = Path(value)
        if not path.is_absolute():
            path = self.root / path
        try:
            path.resolve().relative_to(self.root)
            return path.is_file()
        except (OSError, ValueError):
            return False

    @staticmethod
    def _github_url(url: Any) -> str | None:
        if not isinstance(url, str):
            return None
        try:
            from urllib.parse import urlsplit
            parsed = urlsplit(url)
            return url if parsed.scheme == "https" and parsed.hostname == "github.com" else None
        except ValueError:
            return None

    @classmethod
    def _issue_by_number(cls, github: Any, issues: Any, wanted: int, repository: str | None) -> dict[str, Any] | None:
        if isinstance(github, dict):
            parent_issue = github.get("parent_issue")
            if isinstance(parent_issue, dict) and parent_issue.get("number") == wanted:
                url = cls._github_url(parent_issue.get("url")) or (f"https://github.com/{repository}/issues/{wanted}" if repository else None)
                return {"number": wanted, "url": url}
        if not isinstance(issues, dict):
            return None
        for value in issues.values():
            number = value.get("number") if isinstance(value, dict) else value
            if isinstance(number, (int, str)) and str(number).isdigit() and int(number) == wanted:
                url = cls._github_url(value.get("url")) if isinstance(value, dict) else None
                return {"number": wanted, "url": url or (f"https://github.com/{repository}/issues/{wanted}" if repository else None)}
        return None

    @staticmethod
    def _pr_link(github: Any, repository: str | None) -> dict[str, Any] | None:
        if not isinstance(github, dict):
            return None
        direct = github.get("pull_request")
        if isinstance(direct, dict) and direct.get("number") == 1:
            url = Dashboard._github_url(direct.get("url"))
            if not url and repository:
                url = f"https://github.com/{repository}/pull/1"
            return {"url": url, "number": 1}
        prs = github.get("pull_requests", github.get("prs", {}))
        if isinstance(prs, dict):
            for value in prs.values():
                if isinstance(value, dict) and value.get("number") == 1:
                    url = Dashboard._github_url(value.get("url"))
                    if not url and repository:
                        url = f"https://github.com/{repository}/pull/1"
                    return {"url": url, "number": 1}
        return None

    @staticmethod
    def _top_clusters(clusters: Any, findings: dict[str, dict[str, Any]], issues: Any,
                      repository: str | None) -> list[dict[str, Any]]:
        if not isinstance(clusters, list):
            return []
        issue_map = issues.get("issues", {}) if isinstance(issues, dict) else {}
        ordered = sorted((c for c in clusters if isinstance(c, dict)),
                         key=lambda c: int(c.get("incident_count", 0)), reverse=True)
        rows = []
        for cluster in ordered[:8]:
            cluster_id = str(cluster.get("id", ""))
            issue_value = issue_map.get(cluster_id) if isinstance(issue_map, dict) else None
            number = issue_value.get("number") if isinstance(issue_value, dict) else issue_value
            rows.append({"id": cluster_id, "category": cluster.get("category", "unknown"),
                         "signature": _safe_text(cluster.get("signature"), 180),
                         "attempts": cluster.get("incident_count"), "distinct_jobs": cluster.get("distinct_jobs"),
                         "unresolved": cluster.get("unresolved_count"), "triage": findings.get(cluster_id, {}).get("status", "not diagnosed"),
                         "diagnosis": _safe_text(findings.get(cluster_id, {}).get("diagnosis"), 1800),
                         "issue_number": number if isinstance(number, (str, int)) else None,
                         "issue_url": f"https://github.com/{repository}/issues/{number}" if repository and isinstance(number, (str, int)) and str(number).isdigit() else None})
        return rows

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        cached = self._snapshot_cache
        if cached is not None and now - self._snapshot_cached_at < SNAPSHOT_CACHE_SECONDS:
            return cached
        # Browser refreshes can overlap while a large run tree is being read.
        # Let those requests reuse the last complete snapshot instead of
        # launching duplicate recursive scans.
        if not self._snapshot_lock.acquire(blocking=False):
            if cached is not None:
                return cached
            self._snapshot_lock.acquire()
        try:
            now = time.monotonic()
            if self._snapshot_cache is not None and now - self._snapshot_cached_at < SNAPSHOT_CACHE_SECONDS:
                return self._snapshot_cache
            processes = _processes()
            chapters = [self._chapter(path, processes) for path in self.run_dirs]
            snapshot = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "refresh_seconds": 5, "chapters": chapters, "maintenance": self._maintenance(processes),
                        "definitions": {"published": "Observed public chapter file plus enabled metadata and chapter review record.",
                                        "locally_reviewed": "Run report is complete, all recorded artifact hashes match, and stage approvals are recorded.",
                                        "draft": "Artifacts are absent or the complete local review evidence is not verified.",
                                        "rejected_reviews": "Count of persisted review result artifacts with approved=false; these are review attempts, not chunks."}}
            self._snapshot_cache = snapshot
            self._snapshot_cached_at = time.monotonic()
            return snapshot
        finally:
            self._snapshot_lock.release()


def make_handler(dashboard: Dashboard):
    class Handler(BaseHTTPRequestHandler):
        server_version = "GradedReadersDashboard/1"

        def log_message(self, _format: str, *args: Any) -> None:
            # Avoid echoing query strings or other request material to logs.
            return

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path == "/api/state":
                payload = json.dumps(dashboard.snapshot(), ensure_ascii=False, separators=(",", ":")).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            elif path in {"/", "/index.html"}:
                try:
                    payload = HTML_PATH.read_bytes()
                except OSError:
                    self.send_error(503, "Dashboard interface is unavailable")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            else:
                self.send_error(404)

    return Handler


def serve(dashboard: Dashboard, host: str = "127.0.0.1", port: int = 8771) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("dashboard must bind to loopback")
    server = ThreadingHTTPServer((host, port), make_handler(dashboard))
    print(f"Pipeline dashboard: http://{host}:{server.server_port}/", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--run-dir", action="append", default=[], help="run directory to monitor; repeatable")
    parser.add_argument("--maintenance-dir", default=None)
    parser.add_argument("--host", default="127.0.0.1", help="loopback only")
    parser.add_argument("--port", type=int, default=8771)
    args = parser.parse_args(argv)
    dashboard = Dashboard(args.repo_root, args.run_dir, args.maintenance_dir)
    serve(dashboard, args.host, args.port)


if __name__ == "__main__":
    main()
