"""Publish conservative recurring-incident summaries to GitHub issues.

This module deliberately publishes aggregate evidence only. Triage diagnoses
remain local and are never copied into issue text.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Callable

START = "<!-- graded-readers-maintenance:{cluster_id} -->"
END = "<!-- /graded-readers-maintenance:{cluster_id} -->"
LANGUAGE_HINTS = (
    ("zh", "Chinese", ("zh", "chinese", "sanguoyanyi")),
    ("ja", "Japanese", ("ja", "japanese", "wagahai")),
    ("ko", "Korean", ("ko", "korean")),
)
CATEGORIES = {"launch_timeout", "process_timeout", "worker_failure", "validator_failure", "review_rejection"}
CLUSTER_ID = re.compile(r"[a-f0-9]{8,64}\Z")


def _load(value: Any) -> dict[str, Any]:
    if isinstance(value, (str, Path)):
        value = json.loads(Path(value).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("maintenance GitHub input must be a JSON object")
    return value


def _run_gh(args: list[str], *, runner: Callable[..., Any]) -> str:
    try:
        result = runner(["gh", *args], capture_output=True, text=True, check=False, timeout=30)
    except Exception as exc:
        # Do not propagate subprocess exception strings: they can include args,
        # environment details, or other sensitive material.
        raise RuntimeError("GitHub CLI invocation failed") from None
    if getattr(result, "returncode", 1) != 0:
        raise RuntimeError("GitHub CLI returned an error")
    return str(getattr(result, "stdout", ""))


def _languages(cluster: dict[str, Any], report: dict[str, Any]) -> list[str]:
    del report  # Only cluster-local evidence can support a language attribution.
    hints: list[str] = []
    for row in cluster.get("evidence", []):
        if not isinstance(row, dict):
            continue
        hints.extend(str(row.get(key, "")) for key in ("language", "run_dir", "artifact_path"))
    paths = "/" + "/".join(hints).lower().replace("\\", "/") + "/"
    found = []
    for _code, name, tokens in LANGUAGE_HINTS:
        if any(re.search(rf"(?:^|[/_.-]){re.escape(token)}(?:$|[/_.-])", paths) for token in tokens):
            found.append(name)
    return found or ["shared"]


def _artifact_refs(cluster: dict[str, Any], repo_root: Path) -> list[str]:
    refs: list[str] = []
    root = repo_root.resolve()
    for row in cluster.get("evidence", []):
        raw = row.get("artifact_path")
        if not isinstance(raw, str):
            continue
        path = Path(raw)
        if not path.is_absolute():
            path = root / path
        try:
            relative = path.resolve().relative_to(root)
        except (OSError, ValueError):
            continue
        value = relative.as_posix()
        if value not in refs:
            refs.append(value)
    return refs[:8]


def _finding_for(cluster_id: str, summary: dict[str, Any], triage_state: dict[str, Any]) -> dict[str, Any] | None:
    candidates = list(summary.get("new_diagnoses", []))
    previous = triage_state.get("clusters", {}).get(cluster_id, {})
    if isinstance(previous, dict):
        candidates.append(previous)
    for record in reversed(candidates):
        finding = record.get("finding") if isinstance(record, dict) else None
        if isinstance(finding, dict) and finding.get("cluster_id") == cluster_id:
            return finding
    return None


def _managed_block(cluster: dict[str, Any], report: dict[str, Any], finding: dict[str, Any],
                   repo_root: Path) -> str:
    cluster_id = str(cluster["id"])
    start, end = START.format(cluster_id=cluster_id), END.format(cluster_id=cluster_id)
    unresolved = max(0, int(cluster.get("unresolved_count", 0)))
    resolved = max(0, int(cluster.get("resolved_count", 0)))
    diagnostic = finding.get("status", "unknown")
    allowed = {"needs_investigation", "proposed_fix", "already_addressed"}
    if diagnostic not in allowed:
        diagnostic = "unknown"
    lines = [start, "## Recurring pipeline incident", "",
             f"- Cluster: `{cluster_id}` ({cluster.get('category', 'unknown')})",
             f"- Evidence: {int(cluster.get('distinct_jobs', 0))} distinct jobs; {unresolved} unresolved and {resolved} resolved observations.",
             f"- Languages inferred from run paths: {', '.join(_languages(cluster, report))}.",
             "- Counts describe recorded observations; an active job is not assumed failed.",
             f"- Local triage status: `{diagnostic}`; diagnosis is not independently verified and no fix is asserted."]
    refs = _artifact_refs(cluster, repo_root)
    if refs:
        lines.extend(["- Repository evidence references:", *[f"  - `{path}`" for path in refs]])
    else:
        lines.append("- Repository evidence references: none (run artifacts are outside this checkout).")
    lines.extend(["", "This issue tracks recurring evidence. Review and verify any proposed change before treating the incident as fixed.", end])
    return "\n".join(lines)


def _replace_block(body: str, cluster_id: str, block: str) -> str:
    start, end = START.format(cluster_id=cluster_id), END.format(cluster_id=cluster_id)
    pattern = re.compile(re.escape(start) + r"[\s\S]*?" + re.escape(end))
    if pattern.search(body):
        return pattern.sub(lambda _: block, body, count=1)
    return (body.rstrip() + "\n\n" if body.strip() else "") + block


def _run_body_command(args: list[str], body: str, *, runner: Callable[..., Any]) -> str:
    # Keep multiline bodies out of process arguments and use the exact same
    # temporary-file behavior for create and edit.
    with tempfile.TemporaryDirectory(prefix="maintenance-github-") as directory:
        body_file = Path(directory) / "issue-body.md"
        body_file.write_text(body, encoding="utf-8")
        return _run_gh([*args, "--body-file", str(body_file)], runner=runner)


def sync(report: Any, triage_summary: Any, repository: str, *, repo_root: str | Path,
         triage_state: Any = None,
         state_path: str | Path | None = None, max_writes: int = 3,
         runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """Create/update at most ``max_writes`` issues for recurring unresolved findings.

    ``repository`` is an explicit ``owner/name``; it is never inferred from git
    config. Failed writes remain eligible on the next call because local state
    records only successful issue numbers.
    """
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("repository must be an explicit owner/name")
    if max_writes < 0:
        raise ValueError("max_writes must be nonnegative")
    report_data, summary = _load(report), _load(triage_summary)
    diagnosis_state = _load(triage_state) if triage_state is not None else {"clusters": {}}
    root = Path(repo_root).resolve()
    state_file = Path(state_path) if state_path else None
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file and state_file.exists() else {"issues": {}}
    if not isinstance(state, dict) or not isinstance(state.get("issues", {}), dict):
        state = {"issues": {}}
    try:
        raw = _run_gh(["issue", "list", "--repo", repository, "--state", "all", "--limit", "100000",
                       "--json", "number,title,body,state"], runner=runner)
        issues = json.loads(raw)
        if not isinstance(issues, list):
            raise RuntimeError("GitHub issue listing had an invalid format")
    except Exception as exc:
        # Return a non-sensitive failure summary and leave all evidence untouched.
        return {"created": 0, "updated": 0, "skipped": 0, "errors": [str(exc)]}

    by_cluster: dict[str, dict[str, Any]] = {}
    marker_re = re.compile(r"<!-- graded-readers-maintenance:([a-f0-9]{8,64}) -->")
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        body = str(issue.get("body") or "")
        marker = marker_re.search(body)
        if marker:
            by_cluster.setdefault(marker.group(1), issue)

    candidates = []
    for cluster in report_data.get("clusters", []):
        if not isinstance(cluster, dict) or not cluster.get("trigger_worthy"):
            continue
        if int(cluster.get("unresolved_count", 0)) <= 0:
            continue
        cluster_id = str(cluster.get("id", ""))
        if not CLUSTER_ID.fullmatch(cluster_id) or cluster.get("category") not in CATEGORIES:
            continue
        finding = _finding_for(cluster_id, summary, diagnosis_state)
        if finding is None:
            continue
        candidates.append((cluster, finding))
    candidates.sort(key=lambda pair: (-int(pair[0].get("distinct_jobs", 0)),
                                      -int(pair[0].get("unresolved_count", 0)), str(pair[0].get("id"))))

    created = updated = skipped = 0
    errors: list[str] = []
    attempts = 0
    for index, (cluster, finding) in enumerate(candidates):
        cluster_id = str(cluster["id"])
        issue = by_cluster.get(cluster_id)
        block = _managed_block(cluster, report_data, finding, root)
        category = str(cluster["category"])
        title = f"[Pipeline maintenance] Recurring {category} ({cluster_id})"
        if issue is not None:
            number = issue.get("number")
            if not isinstance(number, int):
                errors.append(f"{cluster_id}: GitHub issue record has no valid number")
                continue
            body = _replace_block(str(issue.get("body") or ""), cluster_id, block)
            if body == str(issue.get("body") or ""):
                skipped += 1
                state.setdefault("issues", {})[cluster_id] = number
                continue
        else:
            body = block
        if attempts >= max_writes:
            skipped += len(candidates) - index
            break
        attempts += 1
        try:
            if issue is None:
                output = _run_body_command(["issue", "create", "--repo", repository, "--title", title],
                                           body, runner=runner)
                match = re.search(r"/issues/(\d+)", output)
                number = int(match.group(1)) if match else None
                created += 1
                if number is not None:
                    state.setdefault("issues", {})[cluster_id] = number
                    by_cluster[cluster_id] = {"number": number, "body": body, "state": "OPEN"}
            else:
                _run_body_command(["issue", "edit", str(number), "--repo", repository], body, runner=runner)
                updated += 1
                state.setdefault("issues", {})[cluster_id] = number
        except Exception as exc:
            # Do not include CLI stderr, request bodies, or model-authored text.
            errors.append(f"{cluster_id}: {str(exc)}")

    if state_file and (created or updated):
        state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = state_file.with_suffix(state_file.suffix + ".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(state_file)
    return {"created": created, "updated": updated, "skipped": skipped,
            "errors": errors, "considered": len(candidates), "write_attempts": attempts}
