import json
import os
from pathlib import Path

from pipeline.maintenance import main, scan_runs


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def make_job(run_dir: Path, name: str, *, meta=None, result=None, attempts=None):
    job = run_dir / "agents" / name
    write(job / "meta.json", meta or {"job": name, "return_code": 0})
    if result is not None:
        write(job / "result.json", result)
    if attempts is not None:
        write(job / "attempts.json", attempts)
    return job


def validation_event(job: Path, attempt: int, *, exit_code, output="Bad candidate"):
    event = {"item": {
        "id": f"validator-{attempt}-{exit_code}",
        "type": "command_execution",
        "command": "python -m pipeline.worker_workspace validate --workspace ./workspace --candidate ./candidate.json",
        "exit_code": exit_code,
        "status": "failed" if exit_code else "completed",
        "aggregated_output": output,
    }}
    write(job / f"events.attempt-{attempt:02d}.jsonl", event)


def test_scan_tracks_transient_worker_validator_and_review_failures_as_resolved(tmp_path):
    chinese = tmp_path / "chinese-run"
    japanese = tmp_path / "japanese-run"
    job = make_job(chinese, "annotation-chunk-01", meta={
        "job": "annotation-chunk-01", "return_code": 0,
        "attempts": [{"attempt": 1, "return_code": 1}, {"attempt": 2, "return_code": 0}],
    }, attempts=[{"attempt": 1, "return_code": 1}, {"attempt": 2, "return_code": 0}])
    validation_event(job, 1, exit_code=1, output="validator: invalid span")
    validation_event(job, 2, exit_code=0, output="validation passed")
    make_job(chinese, "annotation-review-0", result={
        "approved": False, "issues": ["Clarify the occurrence meaning."],
    })
    make_job(chinese, "annotation-review-1", result={"approved": True, "issues": []})
    # Japanese-shaped output uses verdict/issues rather than approved/issues.
    make_job(japanese, "scene-review-0", result={
        "verdict": "revise", "issues": ["Clarify this source omission."],
    })
    make_job(japanese, "scene-review-1", result={"verdict": "pass", "issues": []})

    report = scan_runs([chinese, japanese])
    resolved = [incident for cluster in report["clusters"] for incident in cluster["evidence"]
                if incident["resolved"]]
    assert {incident["category"] for incident in resolved} >= {
        "worker_failure", "validator_failure", "review_rejection",
    }
    assert report["incident_count"] == 4
    assert not any(cluster["trigger_worthy"] for cluster in report["clusters"])


def test_repeated_validator_signature_uses_distinct_job_threshold(tmp_path):
    ko_run = tmp_path / "korean"
    repeated_job = make_job(ko_run, "annotation-01-chunk-01", meta={
        "job": "annotation-01-chunk-01", "return_code": 0,
    })
    validation_event(repeated_job, 1, exit_code=1, output="Validator rejected candidate: missing field")
    validation_event(repeated_job, 2, exit_code=1, output="Validator rejected candidate: missing field")
    # Same issue across three additional job identities is trigger-worthy.
    for number in range(2, 5):
        job = make_job(ko_run, f"annotation-01-chunk-{number:02d}", meta={
            "job": f"annotation-01-chunk-{number:02d}", "return_code": 0,
        })
        validation_event(job, 1, exit_code=1, output="Validator rejected candidate: missing field")

    report = scan_runs([ko_run], minimum_jobs=3)
    cluster = next(item for item in report["clusters"] if item["category"] == "validator_failure")
    assert cluster["incident_count"] == 5
    assert cluster["distinct_jobs"] == 4
    assert cluster["trigger_worthy"] is True
    assert cluster["root_cause_status"] == "unproven"
    assert len(cluster["jobs"]) == 4


def test_validator_pass_before_later_failure_does_not_resolve_failure(tmp_path):
    run_dir = tmp_path / "ordered"
    job = make_job(run_dir, "worker", meta={"job": "worker", "return_code": 0})
    validation_event(job, 1, exit_code=0, output="validation passed")
    validation_event(job, 2, exit_code=1, output="ValueError: invalid annotation span")
    report = scan_runs([run_dir])
    cluster = next(item for item in report["clusters"] if item["category"] == "validator_failure")
    assert cluster["unresolved_count"] == 1


def test_validator_pass_resolution_respects_event_order_within_attempt(tmp_path):
    run_dir = tmp_path / "same-attempt"
    command = "python -m pipeline.worker_workspace validate"

    def row(identity, exit_code):
        return {"type": "item.completed", "item": {
            "id": identity, "type": "command_execution", "command": command,
            "exit_code": exit_code, "status": "failed" if exit_code else "completed",
            "aggregated_output": "ValueError: invalid candidate" if exit_code else "validation passed",
        }}

    fixed_job = make_job(run_dir, "fixed-within-attempt", meta={
        "job": "fixed-within-attempt", "return_code": 0,
    })
    (fixed_job / "events.attempt-01.jsonl").write_text(
        json.dumps(row("failed", 1)) + "\n" + json.dumps(row("passed", 0)) + "\n",
        encoding="utf-8")
    failed_job = make_job(run_dir, "failed-after-pass", meta={
        "job": "failed-after-pass", "return_code": 0,
    })
    (failed_job / "events.attempt-01.jsonl").write_text(
        json.dumps(row("passed", 0)) + "\n" + json.dumps(row("failed", 1)) + "\n",
        encoding="utf-8")
    report = scan_runs([run_dir])
    validator_incidents = [row for cluster in report["clusters"] if cluster["category"] == "validator_failure"
                           for row in cluster["evidence"]]
    by_job = {Path(row["job_dir"]).name: row["resolved"] for row in validator_incidents}
    assert by_job == {"fixed-within-attempt": True, "failed-after-pass": False}


def test_local_review_resolution_is_scoped_to_exact_occurrence(tmp_path):
    run_dir = tmp_path / "local-review"

    def local_review(name, source_start, result):
        job = make_job(run_dir, name, result=result)
        write(job / "review-input.json", {"annotation": {}, "text": "가", "context": {
            "chapter_text": "가 나", "source_start": source_start, "target_level": 3,
        }})
        return job

    rejected = local_review("annotation-local-review-a", 0, {
        "approved": False, "issues": ["Correct the local form analysis."],
    })
    local_review("annotation-local-review-b", 5, {"approved": True, "issues": []})
    local_review("annotation-local-review-c", 0, {"approved": True, "issues": []})
    # Make the same-position passing review chronologically later.
    os.utime(rejected / "result.json", (100.0, 100.0))
    os.utime((run_dir / "agents/annotation-local-review-c/result.json"), (200.0, 200.0))
    report = scan_runs([run_dir])
    reviews = [incident for cluster in report["clusters"] for incident in cluster["evidence"]
               if incident["category"] == "review_rejection"]
    assert len(reviews) == 1
    assert reviews[0]["resolved"] is True


def test_unrelated_local_review_pass_does_not_resolve_another_position(tmp_path):
    run_dir = tmp_path / "local-review-unrelated"
    for name, source_start, result in (
        ("annotation-local-review-a", 0, {"approved": False, "issues": ["Fix this occurrence."]}),
        ("annotation-local-review-b", 8, {"approved": True, "issues": []}),
    ):
        job = make_job(run_dir, name, result=result)
        write(job / "review-input.json", {"annotation": {}, "text": "가", "context": {
            "chapter_text": "가 나", "source_start": source_start, "target_level": 3,
        }})
    report = scan_runs([run_dir])
    incident = next(incident for cluster in report["clusters"] for incident in cluster["evidence"]
                     if incident["category"] == "review_rejection")
    assert incident["resolved"] is False


def test_terminal_failures_and_rejected_reviews_remain_unresolved_without_later_pass(tmp_path):
    run_dir = tmp_path / "mixed"
    make_job(run_dir, "worker-timeout", meta={
        "job": "worker-timeout", "return_code": None,
        "error": "process_timeout", "attempts": [{
            "attempt": 1, "return_code": None, "process_timeout": True,
            "error": "codex process timed out",
        }],
    }, attempts=[{"attempt": 1, "return_code": None,
                  "process_timeout": True, "error": "codex process timed out"}])
    make_job(run_dir, "dictionary-review-0", result={
        "approved": False, "issues": ["The dictionary sense is unsupported."],
    })
    make_job(run_dir, "another-live-job", meta={"job": "another-live-job", "return_code": 0})
    live = {"item": {"id": "cmd", "type": "command_execution",
                      "command": "python -m pipeline.worker_workspace validate",
                      "exit_code": None, "status": "in_progress"}}
    (run_dir / "agents" / "another-live-job" / "events.attempt-01.jsonl").write_text(
        json.dumps(live) + "\n", encoding="utf-8")
    active_job = run_dir / "agents" / "active-without-meta"
    active_job.mkdir(parents=True)
    (active_job / "events.attempt-01.jsonl").write_text(
        json.dumps({"type": "turn.started"}) + "\n" + json.dumps(live) + "\n",
        encoding="utf-8")
    partial = run_dir / "agents" / "partial"
    partial.mkdir(parents=True)
    (partial / "meta.json").write_text('{"job":', encoding="utf-8")

    report = scan_runs([run_dir])
    by_category = {cluster["category"]: cluster for cluster in report["clusters"]}
    assert by_category["process_timeout"]["unresolved_count"] == 1
    assert by_category["review_rejection"]["unresolved_count"] == 1
    assert report["skipped_count"] == 1
    assert not any(cluster["category"] == "validator_failure" for cluster in report["clusters"])


def test_review_with_approval_marker_and_outstanding_issues_does_not_resolve_rejection(tmp_path):
    run_dir = tmp_path / "contradictory-review"
    make_job(run_dir, "scene-review-0", result={
        "verdict": "revise", "issues": ["Correct the omitted source detail."],
    })
    # A later pass that still carries issues is internally contradictory and
    # cannot count as evidence that the earlier rejection was fixed.
    make_job(run_dir, "scene-review-1", result={
        "verdict": "pass", "approved": True, "issues": ["The source detail is still omitted."],
    })
    # Contrast with a later clean approval for an independent review family.
    make_job(run_dir, "gloss-review-0", result={
        "approved": False, "issues": ["Clarify the gloss."],
    })
    make_job(run_dir, "gloss-review-1", result={"approved": True, "issues": []})

    report = scan_runs([run_dir])
    rejected = [row for cluster in report["clusters"] if cluster["category"] == "review_rejection"
                for row in cluster["evidence"]]
    by_job = {Path(row["job_dir"]).name: row["resolved"] for row in rejected}
    assert by_job == {
        "scene-review-0": False,
        "scene-review-1": False,
        "gloss-review-0": True,
    }


def test_symlinked_agents_job_dirs_and_artifacts_are_refused(tmp_path):
    outside = tmp_path / "outside"
    external_agents = outside / "agents"
    external_job = external_agents / "external-job"
    external_job.mkdir(parents=True)
    write(external_job / "meta.json", {
        "job": "external-job", "return_code": 9, "error": "external failure",
    })
    write(external_job / "result.json", {"approved": False, "issues": ["external rejection"]})

    # The run-level agents link must not expose any external jobs.
    linked_agents_run = tmp_path / "linked-agents-run"
    linked_agents_run.mkdir()
    (linked_agents_run / "agents").symlink_to(external_agents, target_is_directory=True)
    agents_report = scan_runs([linked_agents_run])
    assert agents_report["incident_count"] == 0
    assert any("symlinked run artifact refused" in row["reason"]
               for row in agents_report["skipped"])

    # A job-directory link under an otherwise valid agents directory is also refused.
    linked_job_run = tmp_path / "linked-job-run"
    (linked_job_run / "agents").mkdir(parents=True)
    (linked_job_run / "agents" / "external-job").symlink_to(external_job, target_is_directory=True)
    job_report = scan_runs([linked_job_run])
    assert job_report["incident_count"] == 0
    assert any(Path(row["path"]).name == "external-job" and "symlinked" in row["reason"]
               for row in job_report["skipped"])

    # Leaf symlinks for metadata, events and review results must not be read.
    artifact_run = tmp_path / "linked-artifact-run"
    artifact_dir = artifact_run / "agents" / "local-job"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "meta.json").symlink_to(external_job / "meta.json")
    external_events = outside / "events.jsonl"
    external_events.write_text(json.dumps({"item": {
        "id": "external-validator", "type": "command_execution",
        "command": "python -m pipeline.worker_workspace validate", "exit_code": 1,
        "aggregated_output": "ValueError: external validator failure",
    }}) + "\n", encoding="utf-8")
    (artifact_dir / "events.attempt-01.jsonl").symlink_to(external_events)
    (artifact_dir / "result.json").symlink_to(external_job / "result.json")
    artifact_report = scan_runs([artifact_run])
    assert artifact_report["incident_count"] == 0
    assert sum("symlinked run artifact refused" in row["reason"]
               for row in artifact_report["skipped"]) == 3


def test_complete_and_live_partial_jobs_remain_scannable_with_contained_paths(tmp_path):
    run_dir = tmp_path / "contained-run"
    complete = make_job(run_dir, "successful", meta={"job": "successful", "return_code": 0})
    (complete / "events.attempt-01.jsonl").write_text(
        json.dumps({"type": "turn.started"}) + "\n" + '{"item":', encoding="utf-8")
    active = run_dir / "agents" / "active"
    active.mkdir()
    (active / "events.attempt-01.jsonl").write_text(
        json.dumps({"type": "turn.started"}) + "\n" + json.dumps({"item": {
            "id": "cmd", "type": "command_execution",
            "command": "python -m pipeline.worker_workspace validate", "exit_code": None,
        }}) + "\n", encoding="utf-8")

    report = scan_runs([run_dir])
    assert report["incident_count"] == 0
    assert report["skipped_count"] == 1  # Only the incomplete trailing JSONL row.
    assert "incomplete or invalid JSONL row" in report["skipped"][0]["reason"]


def test_since_filters_old_jobs_before_parsing_event_streams(tmp_path):
    run_dir = tmp_path / "since-run"
    old = make_job(run_dir, "old-job", meta={"job": "old-job", "return_code": 2,
                                              "error": "old failure"})
    old_time = 100.0
    for path in old.iterdir():
        os.utime(path, (old_time, old_time))
    current = make_job(run_dir, "current-job", meta={"job": "current-job", "return_code": 2,
                                                      "error": "current failure"})
    report = scan_runs([run_dir], since=200.0)
    assert report["filtered_job_count"] == 1
    assert report["incident_count"] == 1
    assert [Path(job).name for job in report["clusters"][0]["jobs"]] == ["current-job"]


def test_same_job_name_in_separate_runs_counts_as_distinct_job_identity(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    for run_dir in (left, right):
        job = make_job(run_dir, "annotation-chunk-01", meta={
            "job": "annotation-chunk-01", "return_code": 0,
        })
        validation_event(job, 1, exit_code=1, output="ValueError: Korean span check failed")
    report = scan_runs([left, right], minimum_jobs=2)
    cluster = next(item for item in report["clusters"] if item["category"] == "validator_failure")
    assert cluster["distinct_jobs"] == 2
    assert len(cluster["jobs"]) == 2


def test_validator_signature_groups_variable_positions_and_surface_values(tmp_path):
    runs = [tmp_path / "ko-a", tmp_path / "ko-b"]
    messages = [
        "ValueError: Korean form review is incomplete: declared inflected taps without form_steps: [(2, '살이')]; duplicate audit indices: 0.",
        "ValueError: Korean form review is incomplete: declared inflected taps without form_steps: [(19, '길동은')]; duplicate audit indices: 4.",
    ]
    for run_dir, message in zip(runs, messages):
        job = make_job(run_dir, "chunk", meta={"job": "chunk", "return_code": 0})
        validation_event(job, 1, exit_code=1, output=message)
    report = scan_runs(runs, minimum_jobs=2)
    clusters = [item for item in report["clusters"] if item["category"] == "validator_failure"]
    assert len(clusters) == 1
    assert clusters[0]["distinct_jobs"] == 2
    assert clusters[0]["trigger_worthy"] is True
    assert all("Korean form review is incomplete" in row["message"]
               for row in clusters[0]["evidence"])


def test_cli_writes_requested_read_only_report(tmp_path):
    run_dir = tmp_path / "cli-run"
    make_job(run_dir, "worker", meta={"job": "worker", "return_code": 3,
                                      "error": "launch_timeout"})
    output = tmp_path / "reports" / "maintenance.json"
    assert main(["--run-dir", str(run_dir), "--output", str(output), "--since", "0"]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["incident_count"] == 1
    assert not (run_dir / "chapter.json").exists()
