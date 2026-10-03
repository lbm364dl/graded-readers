from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import patch

from pipeline.dashboard import Dashboard, _infer_run, make_handler


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class DashboardStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = __import__("tempfile").TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_dir(self, name: str = "runs/korean-topik3/chapter-001") -> Path:
        path = self.root / name
        path.mkdir(parents=True)
        return path

    def dashboard(self, run: Path, maintenance: Path | None = None) -> Dashboard:
        return Dashboard(self.root, [run], maintenance or self.root / "runs/maintenance/20261003")

    def chapter_row(self, run: Path, maintenance: Path | None = None) -> dict:
        relative = run.resolve().relative_to(self.root.resolve()).as_posix()
        snapshot = self.dashboard(run, maintenance).snapshot()
        return next(row for row in snapshot["chapters"] if row["id"] == relative)

    def test_live_process_requires_exact_run_path_and_old_incomplete_job_is_not_active(self) -> None:
        run = self.run_dir()
        write_json(run / "agents/annotation-0/meta.json", {
            "job": "annotation-0", "started_at": datetime.now(timezone.utc).isoformat(), "return_code": None,
        })
        dashboard = self.dashboard(run)
        process = {"pid": 11, "elapsed_seconds": 30, "argv": [
            ".venv/bin/python", "-m", "pipeline.korean_agent_harness", "--run-dir",
            str(run), "--level", "3",
        ]}
        with patch("pipeline.dashboard._processes", return_value=[process]):
            live = next(row for row in dashboard.snapshot()["chapters"] if row["id"] == "runs/korean-topik3/chapter-001")
        self.assertTrue(live["state"].startswith("Running"))
        self.assertEqual(live["active_processes"][0]["elapsed_seconds"], 30)
        self.assertEqual(live["active_processes"][0]["process_type"], "coordinator")
        self.assertFalse(any(job["state"] == "running" for job in live["recent_jobs"]))
        with patch("pipeline.dashboard._processes", return_value=[]):
            stale = next(row for row in dashboard.snapshot()["chapters"] if row["id"] == "runs/korean-topik3/chapter-001")
        self.assertIn("incomplete or unknown", stale["state"])
        self.assertEqual(stale["active_processes"], [])
        self.assertFalse(any(job["state"] == "running" for job in stale["recent_jobs"]))

    def test_active_worker_uses_output_path_without_meta_and_ignores_abandoned_meta(self) -> None:
        run = self.run_dir()
        write_json(run / "agents/annotation-old/meta.json", {
            "job": "annotation-old", "started_at": "2026-10-02T09:00:00+00:00",
        })
        dashboard = self.dashboard(run)
        coordinator = {"pid": 111, "elapsed_seconds": 80, "cwd": self.root, "argv": [
            ".venv/bin/python", "-m", "pipeline.korean_agent_harness", "--run-dir", str(run),
        ]}
        worker = {"pid": 222, "elapsed_seconds": 35, "cwd": self.root, "argv": [
            "codex", "exec", "--json", "-o", str(run / "agents/annotation-0-chunk-current/receipt.json"), "-",
        ]}
        unrelated = {"pid": 333, "elapsed_seconds": 15, "cwd": self.root, "argv": [
            "codex", "exec", "-o", str(self.root / "elsewhere/agents/annotation-x/result.json"), "-",
        ]}
        with patch("pipeline.dashboard._processes", return_value=[coordinator, worker, unrelated]):
            row = next(row for row in dashboard.snapshot()["chapters"] if row["id"] == "runs/korean-topik3/chapter-001")
        self.assertEqual(sum(p["process_type"] == "coordinator" for p in row["active_processes"]), 1)
        self.assertEqual(sum(p["process_type"] == "worker" for p in row["active_processes"]), 1)
        running = [job for job in row["recent_jobs"] if job["state"] == "running"]
        self.assertEqual([job["job"] for job in running], ["annotation-0-chunk-current"])
        self.assertEqual(running[0]["stage"], "annotation generation")
        self.assertEqual(row['display']['status'], 'Working')
        self.assertEqual(row['display']['step'], 'Adding word and grammar help')
        self.assertEqual(row['display']['workers'], 1)

    def test_plain_progress_distinguishes_saved_text_publication_and_live_help(self) -> None:
        row = {'language': 'Korean', 'level': 'TOPIK 3', 'active_processes': [],
               'publication': 'Not observed as published', 'local_status': 'Preparation review only',
               'state': 'Prepared; downstream chapter status unknown'}
        prep = {'stages': {'prose': {'approved': True}}}
        waiting = Dashboard._plain_progress(row, None, prep)
        self.assertEqual(waiting['status'], 'Needs attention')
        self.assertEqual([stage['state'] for stage in waiting['steps']], ['done', 'attention', 'waiting', 'waiting'])
        self.assertEqual(waiting['workers'], 0)
        live = dict(row, active_processes=[{'process_type': 'worker', 'job': 'annotation-local-review-synthetic'}])
        working = Dashboard._plain_progress(live, None, prep)
        self.assertEqual(working['status'], 'Working')
        self.assertEqual(working['activity'], [{'label': 'Checking annotations', 'count': 1}])
        ready = Dashboard._plain_progress(dict(row, publication='Published'), None, prep)
        self.assertEqual(ready['status'], 'Ready')
        self.assertTrue(all(stage['state'] == 'done' for stage in ready['steps']))

    def test_schema_job_success_does_not_erase_rejected_review_attempt(self) -> None:
        run = self.run_dir()
        write_json(run / "agents/annotation-review-2/meta.json", {
            "job": "annotation-review-2", "started_at": "2026-10-03T09:00:00+00:00",
            "ended_at": "2026-10-03T09:01:00+00:00", "return_code": 0,
        })
        write_json(run / "agents/annotation-review-2/result.json", {"approved": False, "issues": ["example"]})
        write_json(run / "agents/plan-review-3/meta.json", {
            "job": "plan-review-3", "started_at": "2026-10-03T09:04:00+00:00",
            "ended_at": "2026-10-03T09:05:00+00:00", "return_code": 0,
        })
        write_json(run / "agents/plan-review-3/result.json", {"approved": True, "issues": ["unresolved issue"]})
        write_json(run / "agents/annotation-assembly/meta.json", {
            "job": "annotation-assembly", "started_at": "2026-10-03T09:02:00+00:00",
            "ended_at": "2026-10-03T09:03:00+00:00", "return_code": 0,
        })
        write_json(run / "agents/annotation-assembly/result.json", {"schema_valid": True})
        row = self.chapter_row(run)
        self.assertEqual(row["rejected_review_attempts"], 2)
        jobs = {job["job"]: job for job in row["recent_jobs"]}
        self.assertEqual(jobs["annotation-review-2"]["process_state"], "succeeded")
        self.assertEqual(jobs["annotation-review-2"]["review_state"], "rejected")
        self.assertEqual(jobs["annotation-review-2"]["review_issue_count"], 1)
        self.assertEqual(jobs["annotation-review-2"]["review_issues"], ["example"])
        self.assertEqual(jobs["plan-review-3"]["review_state"], "rejected")
        self.assertEqual(jobs["plan-review-3"]["review_issue_count"], 1)
        self.assertEqual(jobs["annotation-assembly"]["state"], "succeeded")

    def test_published_is_distinct_from_verified_local_review(self) -> None:
        run = self.run_dir("runs/korean-topik3/chapter-001")
        chapter_data = {"number": 1}
        chapter = json.dumps(chapter_data).encode()
        (run / "chapter.json").write_bytes(chapter)
        write_json(run / "report.json", {
            "status": "complete", "artifacts": {"chapter.json": hashlib.sha256(chapter).hexdigest()},
            "stages": {"annotation": {"approved": True}},
        })
        write_json(self.root / "content/korean/honggildong/metadata.json", {"enabled_levels": ["l3"]})
        public = self.root / "content/korean/honggildong/l3.md"
        public.parent.mkdir(parents=True, exist_ok=True)
        public.write_text("public chapter", encoding="utf-8")
        write_json(self.root / "content/korean/honggildong/l3.review.json", {
            "chapters": [{"number": 1, "chapter_digest": hashlib.sha256(
                json.dumps(chapter_data, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                "reviews": {"plan": {"approved": True}}}],
        })
        row = self.chapter_row(run)
        self.assertTrue(row["publication"].startswith("Published"))
        self.assertTrue(row["local_status"].startswith("Complete report"))
        report = json.loads((run / "report.json").read_text())
        report["stages"]["annotation"]["approved"] = False
        write_json(run / "report.json", report)
        changed = self.chapter_row(run)
        self.assertTrue(changed["publication"].startswith("Published"))
        self.assertTrue(changed["local_status"].startswith("Draft"))

    def test_incomplete_concurrent_json_is_unknown_with_artifact_note(self) -> None:
        run = self.run_dir()
        (run / "preparation.json").write_text('{"status":"prepared",', encoding="utf-8")
        row = self.chapter_row(run)
        self.assertIn("unknown", row["state"])
        self.assertTrue(any("unreadable or changing" in note for note in row["artifact_notes"]))

    def test_language_inference_for_explicit_chinese_and_japanese_runs(self) -> None:
        self.assertEqual(_infer_run(Path("runs/chinese-hsk4/chapter-1"), self.root)[:2], ("Chinese", "HSK 4"))
        self.assertEqual(_infer_run(Path("runs/japanese-n3/chapter-1"), self.root)[:2], ("Japanese", "JLPT N 3"))

    def test_maintenance_aggregates_proposals_separately_from_verified_fixes(self) -> None:
        run = self.run_dir()
        maint = self.root / "runs/maintenance/20261003"
        write_json(maint / "incidents.json", {
            "incident_count": 12, "filtered_job_count": 8,
            "clusters": [{"id": "abc12345", "category": "review_rejection", "incident_count": 12,
                          "distinct_jobs": 2, "unresolved_count": 4, "jobs": ['first', 'second']},
                         {"id": "def67890", "category": "validator_failure", "incident_count": 1,
                          "distinct_jobs": 2, "unresolved_count": 1, "jobs": ['second', 'third']}],
        })
        write_json(maint / "triage/triage-state.json", {"clusters": {
            "abc12345": {"finding": {"cluster_id": "abc12345", "status": "proposed_fix"}},
            "def67890": {"finding": {"cluster_id": "def67890", "status": "already_addressed"}},
        }})
        write_json(maint / "github-state.json", {
            "repository": "owner/repo",
            "parent_issue": {"number": 2, "url": "https://github.com/owner/repo/issues/2"},
            "pull_request": {"number": 1, "url": "https://github.com/owner/repo/pull/1"},
            "issues": {"abc12345": 8},
        })
        write_json(maint / "verified-fixes.json", {"fixes": [
            {"cluster_id": "abc12345", "implementation_tests": {"status": "passed", "evidence_path": "tests/test_pipeline_dashboard.py"},
             "independent_review": {"status": "pending", "evidence_path": "tests/test_pipeline_dashboard.py"},
             "production_verification": "pending"},
            {"cluster_id": "def67890", "implementation_tests": {"status": "passed", "evidence_path": "tests/test_pipeline_dashboard.py"},
             "independent_review": {"status": "passed", "evidence_path": "tests/test_pipeline_dashboard.py"},
             "production_verification": "verified"},
        ]})
        evidence = self.root / "tests/test_pipeline_dashboard.py"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text("evidence", encoding="utf-8")
        result = self.dashboard(run, maint).snapshot()["maintenance"]
        self.assertEqual(result["proposed_fixes"], 1)
        self.assertEqual(result["implementation_tested_fixes"], 2)
        self.assertEqual(result["jobs_with_incidents"], 3)
        self.assertEqual(result["verified_fixes"], 1)
        self.assertEqual(result["github"]["issue2"]["number"], 2)
        self.assertEqual(result["github"]["pr1"]["url"], "https://github.com/owner/repo/pull/1")
        self.assertEqual(result["recurrent"][0]["attempts"], 12)


class DashboardHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = __import__("tempfile").TemporaryDirectory()
        self.root = Path(self.temp.name)
        run = self.root / "runs/chinese-hsk4/chapter-1"
        run.mkdir(parents=True)
        self.dashboard = Dashboard(self.root, [run], self.root / "missing-maintenance")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.dashboard))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def test_static_api_and_no_path_traversal(self) -> None:
        with urlopen(self.base + "/") as response:
            self.assertEqual(response.status, 200)
            self.assertIn('text/html', response.headers['Content-Type'])
            self.assertIn(b'<main', response.read())
        with urlopen(self.base + "/api/state") as response:
            raw = response.read()
            payload = json.loads(raw)
            chapter = next(row for row in payload["chapters"] if row["path"] == "runs/chinese-hsk4/chapter-1")
            self.assertEqual(chapter["language"], "Chinese")
            self.assertEqual(chapter["level"], "HSK 4")
            self.assertNotIn(b'"argv"', raw)
        for path in ("/../../etc/passwd", "/dashboard.html", "/api/state/../index.html"):
            with self.assertRaises(HTTPError) as error:
                urlopen(self.base + path)
            self.assertEqual(error.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
