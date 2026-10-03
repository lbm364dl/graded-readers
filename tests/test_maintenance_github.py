import json
from pathlib import Path
from types import SimpleNamespace

from pipeline.maintenance_github import sync


def inputs(*, unresolved=3, jobs=3, trigger=True, status="needs_investigation"):
    cluster = {"id": "a1b2c3d4e5f6a7b8", "category": "worker_failure",
               "trigger_worthy": trigger, "distinct_jobs": jobs,
               "unresolved_count": unresolved, "resolved_count": 1,
               "evidence": [{"artifact_path": "/repo/pipeline/maintenance.py",
                             "run_dir": "/runs/ja/current"}]}
    report = {"run_dirs": ["/runs/ja"], "clusters": [cluster]}
    finding = {"cluster_id": cluster["id"], "status": status,
               "diagnosis": "PRIVATE diagnosis must never be published",
               "evidence_paths": ["secret.log"]}
    summary = {"new_diagnoses": [{"finding": finding}]}
    return report, summary


class Gh:
    def __init__(self, existing=None, fail_create=0, fail_writes=0):
        self.issues = list(existing or [])
        self.calls = []
        self.fail_create = fail_create
        self.fail_writes = fail_writes

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if args[1:3] == ["issue", "list"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(self.issues), stderr="")
        if args[1:3] == ["issue", "create"]:
            body = Path(args[args.index("--body-file") + 1]).read_text()
            if self.fail_create or self.fail_writes:
                if self.fail_create:
                    self.fail_create -= 1
                else:
                    self.fail_writes -= 1
                return SimpleNamespace(returncode=1, stdout="", stderr="token=do-not-leak")
            number = 41
            self.issues.append({"number": number, "title": args[args.index("--title") + 1],
                                "body": body, "state": "OPEN"})
            return SimpleNamespace(returncode=0, stdout=f"https://github.com/o/r/issues/{number}\n", stderr="")
        if args[1:3] == ["issue", "edit"]:
            number = int(args[3])
            body = Path(args[args.index("--body-file") + 1]).read_text()
            issue = next(x for x in self.issues if x["number"] == number)
            issue["body"] = body
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(args)


def call(tmp_path, gh, report=None, summary=None, **kwargs):
    report, summary = (report, summary) if report is not None else inputs()
    for cluster in report.get("clusters", []):
        for row in cluster.get("evidence", []):
            artifact = row.get("artifact_path", "")
            if artifact.startswith("/repo/"):
                row["artifact_path"] = str(tmp_path / artifact.removeprefix("/repo/"))
    return sync(report, summary, "owner/repo", repo_root=tmp_path,
                state_path=tmp_path / "github-state.json", runner=gh, **kwargs)


def test_separate_triage_state_republishes_known_diagnosis_after_summary_is_empty(tmp_path):
    report, summary = inputs()
    summary["new_diagnoses"] = []
    finding = inputs()[1]["new_diagnoses"][0]["finding"]
    triage_state = {"clusters": {finding["cluster_id"]: {"finding": finding}}}
    gh = Gh()
    result = call(tmp_path, gh, report, summary, triage_state=triage_state)
    assert result["created"] == 1


def test_language_uses_only_cluster_evidence_and_recognizes_korean_collection_names(tmp_path):
    report, summary = inputs()
    report["run_dirs"] = ["/runs/chinese", "/runs/wagahai", "/runs/korean-topik"]
    report["clusters"][0]["evidence"] = [{"artifact_path": "/outside/private.log",
                                          "run_dir": "/runs/korean-topik/agents/job"}]
    gh = Gh()
    call(tmp_path, gh, report, summary)
    body = gh.issues[0]["body"]
    assert "Korean" in body
    assert "Chinese" not in body
    assert "Japanese" not in body
    assert "outside/private.log" not in body
    assert "PRIVATE diagnosis" not in body
    assert "secret.log" not in body


def test_relative_artifact_is_resolved_against_repo_root(tmp_path):
    report, summary = inputs()
    report["clusters"][0]["evidence"] = [{"artifact_path": "pipeline/maintenance.py",
                                          "run_dir": "/runs/ja/current"}]
    gh = Gh()
    call(tmp_path, gh, report, summary)
    assert "pipeline/maintenance.py" in gh.issues[0]["body"]


def test_invalid_cluster_id_or_category_is_not_rendered_or_published(tmp_path):
    report, summary = inputs()
    report["clusters"][0]["id"] = "bad\n## injected"
    report["clusters"][0]["category"] = "credential leaked"
    gh = Gh()
    result = call(tmp_path, gh, report, summary)
    assert result["considered"] == 0
    assert not any(args[1:3] == ["issue", "create"] for args in gh.calls)


def test_failed_writes_consume_attempt_bound_and_do_not_hide_unattempted_clusters(tmp_path):
    report, summary = inputs()
    extra = dict(report["clusters"][0], id="b1b2c3d4e5f6a7b8")
    report["clusters"].append(extra)
    summary["new_diagnoses"].append({"finding": dict(summary["new_diagnoses"][0]["finding"],
                                                       cluster_id=extra["id"])})
    gh = Gh(fail_create=2)
    result = call(tmp_path, gh, report, summary, max_writes=1)
    assert result["write_attempts"] == 1
    assert len(result["errors"]) == 1
    assert result["skipped"] == 1
    assert not gh.issues


def test_sync_creates_once_then_updates_managed_block_and_preserves_human_text(tmp_path):
    gh = Gh()
    first = call(tmp_path, gh)
    assert first["created"] == 1
    issue = gh.issues[0]
    assert "PRIVATE diagnosis" not in issue["body"]
    assert "Japanese" in issue["body"]
    assert "pipeline/maintenance.py" in issue["body"]

    issue["body"] += "\n\nHuman investigation notes."
    report, summary = inputs(unresolved=4)
    second = call(tmp_path, gh, report, summary)
    assert second["updated"] == 1
    assert "Human investigation notes." in issue["body"]
    assert "4 unresolved" in issue["body"]
    assert sum(args[1:3] == ["issue", "create"] for args in gh.calls) == 1


def test_closed_issue_is_reused_when_cluster_recurs(tmp_path):
    report, summary = inputs()
    cluster_id = report["clusters"][0]["id"]
    old_body = f"Human notes\n\n<!-- graded-readers-maintenance:{cluster_id} -->old<!-- /graded-readers-maintenance:{cluster_id} -->"
    gh = Gh([{"number": 8, "body": old_body, "state": "CLOSED", "title": "old"}])
    result = call(tmp_path, gh, report, summary)
    assert result["updated"] == 1
    assert result["created"] == 0
    assert "Human notes" in gh.issues[0]["body"]


def test_resolved_only_and_single_job_clusters_are_not_published(tmp_path):
    gh = Gh()
    report, summary = inputs(unresolved=0)
    assert call(tmp_path, gh, report, summary)["considered"] == 0
    report, summary = inputs(jobs=1, trigger=False)
    assert call(tmp_path, gh, report, summary)["considered"] == 0
    assert not any(args[1:3] in (["issue", "create"], ["issue", "edit"]) for args in gh.calls)


def test_writes_are_bounded_per_cycle(tmp_path):
    report, summary = inputs()
    extra = dict(report["clusters"][0], id="b1b2c3d4e5f6a7b8")
    report["clusters"].append(extra)
    summary["new_diagnoses"].append({"finding": dict(summary["new_diagnoses"][0]["finding"],
                                                       cluster_id=extra["id"])})
    gh = Gh()
    result = call(tmp_path, gh, report, summary, max_writes=1)
    assert result["created"] == 1
    assert result["skipped"] == 1


def test_failed_create_retries_without_dropping_local_evidence(tmp_path):
    gh = Gh(fail_create=1)
    result = call(tmp_path, gh)
    assert result["created"] == 0
    assert "token=do-not-leak" not in json.dumps(result)
    assert not any("PRIVATE diagnosis" in arg for args in gh.calls for arg in args)
    assert not (tmp_path / "github-state.json").exists()
    result = call(tmp_path, gh)
    assert result["created"] == 1
    assert gh.issues[0]["number"] == 41
