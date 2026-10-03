"""Read-only incident inventory for completed pipeline worker runs.

The report separates repeated evidence from root-cause conclusions. It does not
change worker outputs, reviews, publication state, or source data.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable


def _read_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _safe_path(path: Path, run_dir: Path, skipped: list[dict[str, str]]) -> bool:
    """Reject symlinks and paths that escape the resolved run root."""
    try:
        if path.is_symlink():
            skipped.append({"path": str(path), "reason": "symlinked run artifact refused"})
            return False
        resolved = path.resolve(strict=False)
    except OSError as exc:
        skipped.append({"path": str(path), "reason": f"{type(exc).__name__}: {exc}"})
        return False
    if not resolved.is_relative_to(run_dir):
        skipped.append({"path": str(path), "reason": "path escapes supplied run directory"})
        return False
    return True


def _job_name(job_dir: Path, meta: Any) -> str:
    if isinstance(meta, dict) and isinstance(meta.get("job"), str):
        return meta["job"]
    return job_dir.name


def _failure_category(message: str, *, timeout_hint: str = "") -> str:
    text = f"{timeout_hint} {message}".lower()
    if "launch_timeout" in text or "launch timed out" in text or "subprocess_exec timed out" in text:
        return "launch_timeout"
    if "process_timeout" in text or "process timed out" in text or "agent timed out" in text:
        return "process_timeout"
    return "worker_failure"


def _normal_signature(category: str, message: str) -> str:
    normalized = message.strip().lower()
    if category == "validator_failure":
        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        exception = next((line for line in reversed(lines)
                          if re.search(r"\b[a-z_]*(?:error|exception)\s*:", line)), None)
        if exception:
            normalized = exception
            normalized = re.sub(r"^[a-z_][a-z0-9_.]*:\s*", "", normalized)
            normalized = re.split(r";|:\s*\[", normalized, maxsplit=1)[0]
        normalized = re.sub(r"(['\"]).*?\1", "<value>", normalized)
        normalized = re.sub(r"\b(?:segment|index|offset|position|line)\s*\d+\b",
                            lambda match: re.sub(r"\d+", "#", match.group()), normalized)
        normalized = re.sub(r"\b\d+\b", "#", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    if category in {"launch_timeout", "process_timeout", "worker_failure", "validator_failure"}:
        normalized = re.sub(r"(?:/[^\s:'\"]+)+", "<path>", normalized)
        normalized = re.sub(r"\bline \d+\b", "line #", normalized)
        normalized = re.sub(r"\b0x[0-9a-f]+\b", "<address>", normalized)
    return normalized or category


def _review_status(value: Any) -> tuple[str | None, list[str]]:
    if not isinstance(value, dict):
        return None, []
    issues = value.get("issues", [])
    if not isinstance(issues, list):
        issues = [str(issues)]
    messages = [str(issue) for issue in issues if str(issue).strip()]
    rejected = value.get("approved") is False or value.get("verdict") == "revise"
    if rejected or messages:
        return "rejected", messages or ["Reviewer returned a rejected result without an issue message."]
    if value.get("approved") is True or value.get("verdict") == "pass":
        return "passed", []
    return None, []


def _review_family(job: str) -> str:
    # Strip only a plain numbered review-attempt suffix. Content-addressed
    # review jobs (for example per-lexeme research reviews) stay distinct.
    match = re.fullmatch(r"(.+?)[-_]review[-_](\d+)(?:[-_]adjudication)?", job,
                         flags=re.IGNORECASE)
    return match.group(1) if match else job


def _review_rank(job: str) -> tuple[int, int]:
    match = re.search(r"[-_]review[-_](\d+)", job, re.IGNORECASE)
    return (int(match.group(1)) if match else -1, int(job.endswith("-adjudication")))


def _review_scope(job: str, review_path: Path, run_dir: Path,
                  skipped: list[dict[str, str]]) -> tuple[str, str]:
    family = _review_family(job)
    input_path = review_path.parent / "review-input.json"
    if not _safe_path(input_path, run_dir, skipped):
        return family, ""
    if not input_path.exists():
        return family, ""
    inputs, error = _read_json(input_path)
    if error or not isinstance(inputs, dict):
        skipped.append({"path": str(input_path), "reason": error or "review inputs are not a JSON object"})
        return family, ""
    context = inputs.get("context", {})
    if not isinstance(context, dict) or "source_start" not in context:
        return family, ""
    scope = json.dumps({key: context.get(key) for key in ("chapter_text", "source_start", "target_level")},
                       ensure_ascii=False, sort_keys=True)
    return "annotation-local-review", scope


def _events(path: Path, run_dir: Path, skipped: list[dict[str, str]]) -> list[dict[str, Any]]:
    if not _safe_path(path, run_dir, skipped):
        return []
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        skipped.append({"path": str(path), "reason": f"{type(exc).__name__}: {exc}"})
        return []
    # The final row can be partial while a worker is still writing the stream.
    # Ignore only malformed rows and retain the rest as inspectable evidence.
    items: dict[str, dict[str, Any]] = {}
    for number, line in enumerate(raw.split("\n"), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            skipped.append({"path": str(path), "reason": f"incomplete or invalid JSONL row {number}: {exc}"})
            continue
        if (isinstance(row, dict) and "item" not in row
                and row.get("type") in {"thread.started", "thread.completed", "turn.started", "turn.completed"}):
            continue
        if not isinstance(row, dict) or not isinstance(row.get("item"), dict):
            skipped.append({"path": str(path), "reason": f"JSONL row {number} has no item object"})
            continue
        item = row["item"]
        identity = str(item.get("id", f"row-{number}"))
        # Event logs record repeated snapshots of an item. Keep its latest
        # state, so an in-progress command is not called a failed validation.
        items[identity] = {**items.get(identity, {}), **item, "_maintenance_line": number}
    return sorted(items.values(), key=lambda item: item.get("_maintenance_line", 0))


def _file_incident(job: str, artifact: Path, category: str, message: str,
                   *, resolved: bool, event: str) -> dict[str, Any]:
    job_dir = artifact.parent.resolve()
    run_dir = job_dir.parent.parent
    return {
        "job": job,
        "job_dir": str(job_dir),
        "run_dir": str(run_dir),
        "artifact_path": str(artifact),
        "category": category,
        "event": event,
        "message": message,
        "resolved": bool(resolved),
        "signature": _normal_signature(category, message),
    }


def _scan_job(job_dir: Path, run_dir: Path, skipped: list[dict[str, str]]) -> list[dict[str, Any]]:
    meta_path = job_dir / "meta.json"
    if not _safe_path(meta_path, run_dir, skipped):
        meta, error, meta_present = {}, None, False
    else:
        meta_present = meta_path.exists()
        meta, error = _read_json(meta_path) if meta_present else ({}, None)
    if error:
        skipped.append({"path": str(meta_path), "reason": error})
        meta = {}
    if not isinstance(meta, dict):
        skipped.append({"path": str(meta_path), "reason": "metadata is not a JSON object"})
        meta = {}
    job = _job_name(job_dir, meta)
    incidents: list[dict[str, Any]] = []

    attempts_path = job_dir / "attempts.json"
    if _safe_path(attempts_path, run_dir, skipped) and attempts_path.exists():
        attempts, attempts_error = _read_json(attempts_path)
    else:
        attempts, attempts_error = [], None
    if attempts_error:
        skipped.append({"path": str(attempts_path), "reason": attempts_error})
        attempts = []
    if not isinstance(attempts, list):
        skipped.append({"path": str(attempts_path), "reason": "attempts are not a JSON array"})
        attempts = []
    meta_attempts = meta.get("attempts", [])
    if isinstance(meta_attempts, list) and not attempts:
        attempts = meta_attempts

    for index, attempt in enumerate(attempts):
        if not isinstance(attempt, dict):
            continue
        timed_out = bool(attempt.get("launch_timeout") or attempt.get("process_timeout"))
        nonzero = isinstance(attempt.get("return_code"), int) and attempt.get("return_code") != 0
        if not (timed_out or nonzero):
            continue
        message = str(attempt.get("error") or (
            f"worker attempt exited with return code {attempt.get('return_code')}"
            if nonzero else "worker attempt timed out"
        ))
        category = _failure_category(message, timeout_hint="launch_timeout" if attempt.get("launch_timeout") else
                                     "process_timeout" if attempt.get("process_timeout") else "")
        successful_later = any(
            isinstance(later, dict) and later.get("return_code") == 0
            for later in attempts[index + 1:]
        ) or (meta.get("return_code") == 0 and (
            index < len(attempts) - 1 or attempt.get("recovered_completed_result") is True
        ))
        incidents.append(_file_incident(job, attempts_path if attempts_path.exists() else meta_path,
            category, message, resolved=successful_later, event=f"attempt {attempt.get('attempt', index + 1)}"))

    # Some terminal failures have no attempts array (for example a submission
    # error). Avoid duplicating a failure already represented above.
    terminal_failed = meta_present and (meta.get("return_code") not in (None, 0) or (
        meta.get("return_code") is None and bool(meta.get("error"))
    ))
    if terminal_failed and not any(item["category"] in {"worker_failure", "launch_timeout", "process_timeout"}
                                   for item in incidents):
        message = str(meta.get("submission_error") or meta.get("error") or
                      f"worker metadata reports return_code={meta.get('return_code')}")
        category = _failure_category(message)
        incidents.append(_file_incident(job, meta_path, category, message,
            resolved=False, event="terminal metadata failure"))

    # Capture failures from the actual local workspace validator command.
    validator_failures: list[dict[str, Any]] = []
    event_files = sorted(job_dir.glob("events.attempt-*.jsonl"))
    parsed_events = [(path, _events(path, run_dir, skipped)) for path in event_files]
    for event_path, items in parsed_events:
        for item in items:
            if item.get("type") != "command_execution":
                continue
            command = str(item.get("command", ""))
            if "worker_workspace validate" not in command:
                continue
            code = item.get("exit_code")
            if not isinstance(code, int) or code == 0:
                continue
            message = str(item.get("aggregated_output") or item.get("status") or
                          f"local validator exited with return code {code}")
            validator_failures.append({
                "path": event_path,
                "attempt": event_path.stem.rsplit("-", 1)[-1],
                "message": message,
                "event_id": str(item.get("id", "")),
                "line": int(item.get("_maintenance_line", 0)),
            })
    for failure in validator_failures:
        attempt_num = int(failure["attempt"]) if failure["attempt"].isdigit() else -1
        later_pass = False
        for event_path, items in parsed_events:
            suffix = event_path.stem.rsplit("-", 1)[-1]
            if not suffix.isdigit() or int(suffix) < attempt_num:
                continue
            for item in items:
                if (item.get("type") == "command_execution"
                        and "worker_workspace validate" in str(item.get("command", ""))
                        and item.get("exit_code") == 0
                        and (int(suffix) > attempt_num
                             or int(item.get("_maintenance_line", 0)) > failure["line"])):
                    later_pass = True
                    break
            if later_pass:
                break
        incidents.append(_file_incident(job, failure["path"], "validator_failure",
            failure["message"], resolved=later_pass,
            event=f"worker_workspace validate attempt {failure['attempt']}"))

    # Reviewer outputs are evidence even when the enclosing worker process
    # returned zero. A later accepted review in the same family resolves the
    # rejection as a workflow event, while retaining it in the audit history.
    review_rows: list[tuple[str, Path, dict[str, Any], str, list[str]]] = []
    result_path = job_dir / "result.json"
    if _safe_path(result_path, run_dir, skipped) and result_path.exists():
        result, result_error = _read_json(result_path)
    else:
        result, result_error = None, None
    if result_error:
        skipped.append({"path": str(job_dir / "result.json"), "reason": result_error})
    review_state, review_messages = _review_status(result)
    if review_state:
        review_rows.append((job, result_path, result, review_state, review_messages))
    for review_job, review_path, review, state, messages in review_rows:
        if state != "rejected":
            continue
        family, scope = _review_scope(review_job, review_path, run_dir, skipped)
        try:
            review_time = review_path.stat().st_mtime
        except OSError:
            review_time = 0.0
        later_pass = False
        other_paths = []
        for other_dir in sorted(job_dir.parent.iterdir()):
            if "-review-" not in other_dir.name:
                continue
            if not _safe_path(other_dir, run_dir, skipped) or not other_dir.is_dir():
                continue
            candidate_path = other_dir / "result.json"
            if _safe_path(candidate_path, run_dir, skipped) and candidate_path.is_file():
                other_paths.append(candidate_path)
        for other_path in other_paths:
            other_job = other_path.parent.name
            other_family, other_scope = _review_scope(other_job, other_path, run_dir, skipped)
            if other_family != family:
                continue
            if family == "annotation-local-review":
                if other_scope != scope:
                    continue
                try:
                    if other_path.stat().st_mtime <= review_time:
                        continue
                except OSError:
                    continue
            elif _review_rank(other_job) <= _review_rank(review_job):
                continue
            other, other_error = _read_json(other_path)
            if other_error:
                continue
            other_state, _ = _review_status(other)
            if other_state == "passed":
                later_pass = True
                break
        for message in messages:
            incidents.append(_file_incident(review_job, review_path, "review_rejection", message,
                resolved=later_pass, event="review result"))

    return incidents


def scan_runs(run_dirs: Iterable[str | Path], *, minimum_jobs: int = 3,
              since: float | None = None) -> dict[str, Any]:
    """Scan only supplied run roots; ``since`` is a Unix timestamp cutoff."""
    if minimum_jobs < 1:
        raise ValueError("minimum_jobs must be positive")
    skipped: list[dict[str, str]] = []
    incidents: list[dict[str, Any]] = []
    filtered_job_count = 0
    roots = [Path(value).expanduser().resolve() for value in run_dirs]
    for run_dir in roots:
        agents = run_dir / "agents"
        if not _safe_path(agents, run_dir, skipped) or not agents.is_dir():
            skipped.append({"path": str(agents), "reason": "agents directory is missing"})
            continue
        for job_dir in sorted(agents.iterdir()):
            if not _safe_path(job_dir, run_dir, skipped) or not job_dir.is_dir():
                continue
            if since is not None:
                relevant = [path for pattern in ("meta.json", "attempts.json", "result.json", "events.attempt-*.jsonl")
                            for path in job_dir.glob(pattern)]
                mtimes = []
                for path in relevant:
                    if not _safe_path(path, run_dir, skipped):
                        continue
                    try:
                        mtimes.append(path.stat().st_mtime)
                    except OSError:
                        continue
                if not mtimes or max(mtimes) < since:
                    filtered_job_count += 1
                    continue
            incidents.extend(_scan_job(job_dir, run_dir, skipped))

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for incident in incidents:
        grouped[(incident["category"], incident["signature"])].append(incident)
    clusters = []
    for (category, signature), evidence in sorted(grouped.items()):
        jobs = sorted({row["job_dir"] for row in evidence})
        resolved_count = sum(row["resolved"] for row in evidence)
        unresolved_count = len(evidence) - resolved_count
        identity = hashlib.sha256(f"{category}\0{signature}".encode()).hexdigest()[:16]
        clusters.append({
            "id": identity,
            "category": category,
            "signature": signature,
            "incident_count": len(evidence),
            "distinct_jobs": len(jobs),
            "jobs": jobs,
            "resolved_count": resolved_count,
            "unresolved_count": unresolved_count,
            "trigger_worthy": len(jobs) >= minimum_jobs,
            "assessment": "recurring_incident" if len(jobs) >= minimum_jobs else "observed_incident",
            "root_cause_status": "unproven",
            "evidence": evidence,
        })
    skipped = list({(row["path"], row["reason"]): row for row in skipped}.values())
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_dirs": [str(path) for path in roots],
        "minimum_jobs": minimum_jobs,
        "since_epoch": since,
        "filtered_job_count": filtered_job_count,
        "skipped_count": len(skipped),
        "skipped": skipped,
        "incident_count": len(incidents),
        "clusters": clusters,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", action="append", required=True,
                        help="Pipeline run directory to scan; repeat for multiple runs")
    parser.add_argument("--output", required=True, help="Write the JSON report here")
    parser.add_argument("--minimum-jobs", type=int, default=3,
                        help="Distinct job threshold for a recurring incident (default: 3)")
    parser.add_argument("--since", type=float, default=None,
                        help="Only scan job artifacts modified at or after this Unix timestamp")
    args = parser.parse_args(argv)
    try:
        report = scan_runs(args.run_dir, minimum_jobs=args.minimum_jobs, since=args.since)
        target = Path(args.output).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"maintenance scan failed: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {len(report['clusters'])} incident clusters from {report['incident_count']} incidents")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
