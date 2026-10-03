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

from pipeline.dashboard import Dashboard, _infer_run, _processes, make_handler


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

    def test_process_reader_classifies_job_control_stop_and_ignores_zombies(self) -> None:
        from types import SimpleNamespace
        rows = "123 Tsl 100 codex exec -o /tmp/receipt.json -\n124 S 20 python -m pipeline.korean_agent_harness\n125 Z 9 codex exec -o /tmp/dead.json -\n"
        with patch("pipeline.dashboard.subprocess.run", return_value=SimpleNamespace(stdout=rows, returncode=0)), \
             patch("pipeline.dashboard.os.readlink", return_value=str(self.root)):
            processes = _processes()
        self.assertEqual([(p["pid"], p["process_state"]) for p in processes],
                         [(123, "suspended"), (124, "running")])
        self.assertEqual(processes[0]['elapsed_seconds'], 100)
        self.assertEqual(processes[0]['argv'], ['codex', 'exec', '-o', '/tmp/receipt.json', '-'])
        self.assertIn('pipeline.korean_agent_harness', processes[1]['argv'])

    def test_chapter_number_comes_from_explicit_run_evidence(self) -> None:
        run = self.run_dir()
        write_json(run / 'preparation.json', {'status': 'prepared', 'number': 7})
        self.assertEqual(self.chapter_row(run)['chapter_number'], 7)
        write_json(run / 'preparation.json', {'status': 'prepared', 'number': True})
        self.assertIsNone(self.chapter_row(run)['chapter_number'])

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
            "codex", "exec", "--json", "-o", str(run / "agents/annotation-0-chunk-001-current/receipt.json"), "-",
        ]}
        unrelated = {"pid": 333, "elapsed_seconds": 15, "cwd": self.root, "argv": [
            "codex", "exec", "-o", str(self.root / "elsewhere/agents/annotation-x/result.json"), "-",
        ]}
        with patch("pipeline.dashboard._processes", return_value=[coordinator, worker, unrelated]):
            row = next(row for row in dashboard.snapshot()["chapters"] if row["id"] == "runs/korean-topik3/chapter-001")
        self.assertEqual(sum(p["process_type"] == "coordinator" for p in row["active_processes"]), 1)
        self.assertEqual(sum(p["process_type"] == "worker" for p in row["active_processes"]), 1)
        running = [job for job in row["recent_jobs"] if job["state"] == "running"]
        self.assertEqual([job["job"] for job in running], ["annotation-0-chunk-001-current"])
        self.assertEqual(running[0]["stage"], "annotation generation")
        self.assertEqual(running[0]["part"], "chunk 1")
        self.assertEqual(running[0]["elapsed_seconds"], 35)
        self.assertEqual(row['display']['status'], 'Working')
        self.assertEqual(row['display']['step'], 'Adding word and grammar help')
        self.assertEqual(row['display']['workers'], 1)

    def test_nested_semantic_worker_is_deduplicated_by_output_artifact(self) -> None:
        run = self.run_dir('runs/chinese-hsk4/chapter-1')
        job_path = run / 'agents/annotations/chunk_0001/semantic_patch'
        write_json(job_path / 'meta.json', {'started_at': '2026-10-03T09:00:00+00:00', 'return_code': None})
        output = str(job_path / 'receipt.json')
        wrapper = {'pid': 501, 'elapsed_seconds': 50, 'cwd': self.root,
                   'argv': ['codex', 'exec', '--json', '-o', output, '-']}
        native_child = {'pid': 502, 'elapsed_seconds': 43, 'cwd': self.root,
                        'argv': ['codex', 'exec', '--json', '-o', output, '-']}
        with patch('pipeline.dashboard._processes', return_value=[wrapper, native_child]):
            row = self.chapter_row(run)
        workers = [process for process in row['active_processes'] if process['process_type'] == 'worker']
        self.assertEqual(len(workers), 1)
        self.assertEqual(workers[0]['job'], 'annotations/chunk_0001/semantic_patch')
        self.assertEqual(workers[0]['stage'], 'annotation semantic patch')
        self.assertEqual(workers[0]['part'], 'chunk 1')
        self.assertEqual(workers[0]['elapsed_seconds'], 50)
        active_jobs = [job for job in row['recent_jobs'] if job['submission_state'] == 'running']
        self.assertEqual(len(active_jobs), 1)
        self.assertEqual(active_jobs[0]['job'], workers[0]['job'])

    def test_runtime_receipt_paths_match_job_metadata_and_preserve_nested_names(self) -> None:
        run = self.run_dir()
        now = datetime.now(timezone.utc).isoformat()
        jobs = [
            ("annotation-0-chunk-007-current", run / "agents/annotation-0-chunk-007-current"),
            ("annotations/chunk_0002/semantic_patch", run / "agents/annotations/chunk_0002/semantic_patch"),
            ("runtime", run / "agents/runtime"),
        ]
        processes = []
        for index, (job, job_path) in enumerate(jobs, start=1):
            write_json(job_path / "meta.json", {
                "job": job, "started_at": now, "return_code": None,
            })
            processes.append({"pid": 600 + index, "elapsed_seconds": 40 + index,
                              "cwd": self.root, "argv": ["codex", "exec", "--json", "-o",
                              str(job_path / "runtime/receipt.json" if job != "runtime"
                                  else job_path / "receipt.json"), "-"]})

        with patch("pipeline.dashboard._processes", return_value=processes):
            row = self.chapter_row(run)
        active = [job for job in row["recent_jobs"] if job["state"] == "running"]
        self.assertEqual({job["job"] for job in active}, {job for job, _ in jobs})
        self.assertEqual(len([p for p in row["active_processes"] if p["process_type"] == "worker"]), 3)
        self.assertEqual(row["display"]["workers"], 3)

    def test_archived_rejected_review_is_not_counted_as_a_current_attempt(self) -> None:
        run = self.run_dir()
        archived = run / "agents/history/source-mutation-123/annotation-review-rejected"
        write_json(archived / "meta.json", {
            "job": "annotation-review-rejected", "started_at": "2026-10-02T10:00:00+00:00",
            "ended_at": "2026-10-02T10:01:00+00:00", "return_code": 0,
        })
        write_json(archived / "result.json", {"approved": False, "issues": ["archived finding"]})
        row = self.chapter_row(run)
        self.assertEqual(row["rejected_review_attempts"], 0)
        self.assertIsNone(row["latest_rejected_review"])
        self.assertFalse(any("history" in job["job"] for job in row["recent_jobs"]))

    def test_suspended_jobs_stay_separate_from_live_sibling_and_saved_rejection(self) -> None:
        run = self.run_dir()
        now = datetime.now(timezone.utc).isoformat()
        failed_review = run / "agents/annotation-review-prior"
        write_json(failed_review / "meta.json", {
            "job": "annotation-review-prior", "started_at": "2026-10-02T09:00:00+00:00",
            "ended_at": "2026-10-02T09:02:00+00:00", "return_code": 0,
        })
        write_json(failed_review / "result.json", {"approved": False, "issues": ["needs correction"]})
        paused_job = run / "agents/annotation-0-chunk-001-paused"
        write_json(paused_job / "meta.json", {"job": paused_job.name, "started_at": now, "return_code": None})
        paused_worker = {"pid": 401, "elapsed_seconds": 300, "process_state": "suspended", "cwd": self.root,
                         "argv": ["codex", "exec", "-o", str(paused_job / "receipt.json"), "-"]}
        coordinator = {"pid": 400, "elapsed_seconds": 360, "process_state": "suspended", "cwd": self.root,
                       "argv": [".venv/bin/python", "-m", "pipeline.korean_agent_harness", "--run-dir", str(run)]}
        dashboard = self.dashboard(run)
        with patch("pipeline.dashboard._processes", return_value=[coordinator, paused_worker]):
            paused = next(row for row in dashboard.snapshot()["chapters"]
                          if row["id"] == "runs/korean-topik3/chapter-001")
        self.assertTrue(paused["state"].startswith("Suspended"))
        self.assertEqual(paused["display"]["status"], "Paused")
        self.assertEqual(paused["display"]["workers"], 0)
        self.assertEqual(paused["display"]["suspended_workers"], 1)
        self.assertEqual(paused["running_process_count"], 0)
        self.assertEqual(paused["suspended_process_count"], 2)
        paused_row = next(job for job in paused["recent_jobs"] if job["job"] == paused_job.name)
        self.assertEqual(paused_row["state"], "suspended")
        self.assertEqual(paused["rejected_review_attempts"], 1)
        self.assertEqual(paused["latest_rejected_review"]["job"], "annotation-review-prior")

        live_job = run / "agents/annotation-0-chunk-002-live"
        write_json(live_job / "meta.json", {"job": live_job.name, "started_at": now, "return_code": None})
        live_worker = {"pid": 402, "elapsed_seconds": 45, "process_state": "running", "cwd": self.root,
                       "argv": ["codex", "exec", "-o", str(live_job / "receipt.json"), "-"]}
        with patch("pipeline.dashboard._processes", return_value=[coordinator, paused_worker, live_worker]):
            mixed = next(row for row in dashboard.snapshot()["chapters"]
                         if row["id"] == "runs/korean-topik3/chapter-001")
        self.assertTrue(mixed["state"].startswith("Running"))
        self.assertEqual(mixed["display"]["status"], "Working")
        self.assertEqual(mixed["display"]["workers"], 1)
        self.assertEqual(mixed["display"]["suspended_workers"], 1)
        self.assertEqual(mixed["running_process_count"], 1)
        self.assertEqual(mixed["suspended_process_count"], 2)
        self.assertEqual(mixed["rejected_review_attempts"], 1)

    def test_plain_progress_distinguishes_saved_text_publication_and_live_help(self) -> None:
        row = {'language': 'Korean', 'level': 'TOPIK 3', 'active_processes': [],
               'publication': 'Not observed as published', 'local_status': 'Preparation review only',
               'state': 'Prepared; downstream chapter status unknown'}
        prep = {'stages': {'prose': {'approved': True}}}
        waiting = Dashboard._plain_progress(row, None, prep)
        self.assertEqual(waiting['status'], 'Waiting for checks')
        self.assertEqual([stage['state'] for stage in waiting['steps']], ['done', 'waiting', 'waiting', 'waiting'])
        self.assertEqual(waiting['workers'], 0)
        live = dict(row, active_processes=[{'process_type': 'worker', 'job': 'annotation-local-review-synthetic'}])
        working = Dashboard._plain_progress(live, None, prep)
        self.assertEqual(working['status'], 'Working')
        self.assertEqual(working['activity'], [{'label': 'Checking annotations', 'count': 1}])
        ready = Dashboard._plain_progress(dict(row, publication='Published'), None, prep)
        self.assertEqual(ready['status'], 'Ready')
        self.assertTrue(all(stage['state'] == 'done' for stage in ready['steps']))

    def test_terminal_failure_checkpoint_and_resumed_worker_states_are_distinct(self) -> None:
        run = self.run_dir()
        prep = {'status': 'prepared', 'stages': {'prose': {'approved': True}}}
        write_json(run / 'preparation.json', prep)
        baseline = self.chapter_row(run)
        self.assertEqual(baseline['display']['status'], 'Waiting for checks')
        log = ('Traceback (most recent call last):\n'
               '  File "pipeline/korean_agent_harness.py", line 1, in run\n'
               'ValueError: Korean annotation chunk 1 failed independent review: SECRET-RAW-FINDING\n')
        (run / 'resume-detached.log').write_text(log, encoding='utf-8')
        failed = self.chapter_row(run)
        self.assertEqual(failed['display']['status'], 'Checks failed')
        self.assertEqual(failed['terminal_failure']['category'], 'Independent review did not approve an annotation chunk')
        self.assertNotIn('SECRET-RAW-FINDING', json.dumps(failed))
        live_process = {'pid': 51, 'elapsed_seconds': 20, 'cwd': self.root, 'argv': [
            '.venv/bin/python', '-m', 'pipeline.korean_agent_harness', '--run-dir', str(run),
        ]}
        with patch('pipeline.dashboard._processes', return_value=[live_process]):
            resumed = self.chapter_row(run)
        self.assertEqual(resumed['display']['status'], 'Working')
        published = dict(failed, publication='Published')
        ready = Dashboard._plain_progress(published, None, prep)
        self.assertEqual(ready['status'], 'Ready')

    def test_terminal_validation_failure_has_conservative_category(self) -> None:
        run = self.run_dir()
        (run / 'resume-detached.log').write_text(
            'Traceback (most recent call last):\njsonschema.exceptions.ValidationError: secret content\n'
            "Failed validating 'enum' in schema\nOn instance['lemma']:\n  secret content\n",
            encoding='utf-8')
        row = self.chapter_row(run)
        self.assertEqual(row['terminal_failure']['category'], 'Worker output failed schema validation')
        self.assertNotIn('secret content', json.dumps(row))

    def test_latest_rejected_check_survives_recent_job_truncation(self) -> None:
        run = self.run_dir()
        write_json(run / 'agents/annotation-review-old/meta.json', {
            'started_at': '2026-10-03T08:00:00+00:00', 'ended_at': '2026-10-03T08:01:00+00:00', 'return_code': 0,
        })
        write_json(run / 'agents/annotation-review-old/result.json', {'approved': False, 'issues': ['Finding to inspect']})
        for index in range(13):
            job = run / 'agents' / f'annotation-{index}'
            stamp = f'2026-10-03T09:{index:02d}:00+00:00'
            write_json(job / 'meta.json', {'started_at': stamp, 'ended_at': stamp, 'return_code': 0})
            write_json(job / 'result.json', {'submitted': True})
        row = self.chapter_row(run)
        self.assertEqual(len(row['recent_jobs']), 12)
        self.assertNotIn('annotation-review-old', [job['job'] for job in row['recent_jobs']])
        self.assertEqual(row['latest_rejected_review']['job'], 'annotation-review-old')
        self.assertEqual(row['latest_rejected_review']['review_issues'], ['Finding to inspect'])

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
        self.assertEqual(jobs["annotation-review-2"]["submission_state"], "submitted")
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

    def test_suspended_maintenance_is_not_reported_as_running(self) -> None:
        run = self.run_dir()
        maint = self.root / "runs/maintenance/20261003"
        dashboard = self.dashboard(run, maint)
        suspended = {"pid": 801, "elapsed_seconds": 120, "process_state": "suspended",
                     "cwd": self.root,
                     "argv": [".venv/bin/python", "-m", "pipeline.maintenance_loop",
                              "--output", str(maint)]}
        with patch("pipeline.dashboard._processes", return_value=[suspended]):
            state = dashboard.snapshot()["maintenance"]
        self.assertTrue(state["state"].startswith("Suspended"))
        self.assertEqual(state["running_process_count"], 0)
        self.assertEqual(state["suspended_process_count"], 1)
        self.assertEqual(state["active_processes"][0]["process_state"], "suspended")

        live = dict(suspended, pid=802, process_state="running")
        with patch("pipeline.dashboard._processes", return_value=[suspended, live]):
            state = dashboard.snapshot()["maintenance"]
        self.assertEqual(state["state"], "Running")
        self.assertEqual(state["running_process_count"], 1)
        self.assertEqual(state["suspended_process_count"], 1)


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
            html = response.read()
            self.assertIn(b'<main', html)
            self.assertIn(b'<dialog id="chapter-dialog"', html)
            self.assertIn(b"card.setAttribute('role','button')", html)
            self.assertIn(b"dialog.showModal()", html)
            self.assertIn(b"event.key==='Enter'||event.key===' '", html)
            self.assertIn(b"renderChapterDialog()", html)
            self.assertIn(b"scrollTop=shell.scrollTop", html)
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
