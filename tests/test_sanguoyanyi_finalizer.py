import json
from pathlib import Path
import sys

from pipeline.sanguoyanyi_finalizer import (
    LEVELS, active_generation_processes, heal_lengths,
    invalidate_annotations, length_diagnostics, length_violations,
    main, preflight, recover_incomplete,
)


def _json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _prose(count: int) -> str:
    return "。".join(
        "人" * min(20, count - offset)
        for offset in range(0, count, 20)
    ) + "。\n"


def _matrix(tmp_path: Path, counts=None):
    low, high = tmp_path / "low", tmp_path / "high"
    sources = tmp_path / "sources"
    low.mkdir(); high.mkdir(); sources.mkdir()
    counts = counts or {level: index + 1 for index, level in enumerate(LEVELS)}
    roots = {level: low if level in LEVELS[:3] else high for level in LEVELS}
    for chapter in range(1, 3):
        (sources / f"chapter_{chapter:03d}.txt").write_text("原文", encoding="utf-8")
        for level in LEVELS:
            run = roots[level] / f"chapter_{chapter:03d}-{level}"
            run.mkdir()
            text = _prose(counts[level])
            (run / "chapter.txt").write_text(text, encoding="utf-8")
            _json(run / "manifest.json", {"status": "complete", "level": level})
            _json(run / "report.json", {
                "status": "complete", "chapter_cjk": counts[level],
                "scene_verdicts": {"scene_01": "pass"},
            })
    return low, high, sources, roots


def test_preflight_requires_exact_complete_matrix(tmp_path):
    low, high, _, _ = _matrix(tmp_path)
    result = preflight([low, high], expected_chapters=2)
    assert result == {"status": "complete", "expected": 12, "complete": 12,
                      "missing": [], "unexpected": [], "incomplete": []}

    (low / "chapter_002-hsk2" / "report.json").unlink()
    result = preflight([low, high], expected_chapters=2)
    assert result["status"] == "blocked"
    assert result["complete"] == 11
    assert result["incomplete"][0]["missing_artifacts"] == ["report.json"]


def test_preflight_rejects_unexpected_matching_run(tmp_path):
    low, high, _, _ = _matrix(tmp_path)
    extra = low / "chapter_003-hsk1"
    extra.mkdir()
    result = preflight([low, high], expected_chapters=2)
    assert result["status"] == "blocked"
    assert result["unexpected"] == [{"chapter": 3, "level": "hsk1"}]


def test_preflight_rejects_false_complete_manifest_report_or_text(tmp_path):
    low, high, _, _ = _matrix(tmp_path)
    run = low / "chapter_001-hsk2"
    manifest = _json_read(run / "manifest.json")
    manifest["level"] = "hsk3"
    _json(run / "manifest.json", manifest)
    report = _json_read(run / "report.json")
    report["chapter_cjk"] = 999
    report["scene_verdicts"] = {"scene_01": "revise"}
    _json(run / "report.json", report)

    result = preflight([low, high], expected_chapters=2)
    assert result["status"] == "blocked"
    errors = result["incomplete"][0]["acceptance_errors"]
    assert "manifest level does not match run key" in errors
    assert "report chapter_cjk does not match chapter.txt" in errors
    assert "not every source-grounded scene review passed" in errors


def test_preflight_rejects_stale_chapter_and_reader_from_blocked_rerun(tmp_path):
    low, high, _, _ = _matrix(tmp_path)
    run = low / "chapter_001-hsk2"
    # Simulate the historical resumability bug: an older accepted chapter and
    # reader remain even though the newest generation attempt is blocked.
    _json(run / "reader.json", {
        "text": (run / "chapter.txt").read_text(encoding="utf-8"),
        "segments": [], "grammar_overlays": [],
        "annotation_audit": {"all_reviewed": True},
    })
    manifest = _json_read(run / "manifest.json")
    manifest["status"] = "blocked"
    _json(run / "manifest.json", manifest)
    report = _json_read(run / "report.json")
    report["status"] = "blocked"
    report["scene_verdicts"] = {"scene_01": "revise"}
    _json(run / "report.json", report)

    result = preflight([low, high], expected_chapters=2)

    assert result["status"] == "blocked"
    assert result["complete"] == 11
    rejected = result["incomplete"][0]
    assert rejected["manifest"] == "blocked"
    assert rejected["report"] == "blocked"
    assert "not every source-grounded scene review passed" in rejected["acceptance_errors"]


def test_recovery_targets_only_missing_or_blocked_keys(tmp_path):
    low, high, sources, roots = _matrix(tmp_path)
    blocked = low / "chapter_001-hsk2" / "manifest.json"
    value = json.loads(blocked.read_text()); value["status"] = "blocked"; _json(blocked, value)
    missing = high / "chapter_002-hsk5"
    for child in missing.iterdir(): child.unlink()
    missing.rmdir()
    report = preflight([low, high], expected_chapters=2)
    commands = []
    records = recover_incomplete(report, roots, sources, lambda command: commands.append(command) or 0)
    assert [(item["level"], item["chapters"]) for item in records] == [
        ("hsk2", [1]), ("hsk5", [2])]
    joined = [" ".join(command) for command in commands]
    assert "chapter_001.txt --levels hsk2" in joined[0]
    assert "chapter_002.txt --levels hsk5" in joined[1]
    assert all("--skip-annotations" in command for command in commands)


def test_shorter_higher_level_remains_valid_when_preflight_accepts_each_level(tmp_path):
    counts = {"hsk1": 1, "hsk2": 2, "hsk3": 5,
              "hsk4": 4, "hsk5": 6, "hsk6": 7}
    low, high, _, _ = _matrix(tmp_path, counts)
    assert preflight([low, high], expected_chapters=2)["status"] == "complete"
    assert length_violations([low, high], expected_chapters=2) == []
    report = length_diagnostics([low, high], expected_chapters=2)
    assert report["status"] == "complete"
    assert report["length_order_observation"]["strictly_increasing_per_chapter_and_total"] is False
    assert report["length_order_observation"]["irregularities"]


def test_length_diagnostics_fail_closed_when_run_artifacts_are_missing(tmp_path):
    low, high = tmp_path / "low", tmp_path / "high"
    low.mkdir(); high.mkdir()
    report = length_diagnostics([low, high], expected_chapters=1)
    assert report["status"] == "blocked"
    assert report["preflight"]["missing"]


def test_length_cli_returns_failure_for_missing_artifacts(tmp_path, monkeypatch):
    low, high = tmp_path / "low", tmp_path / "high"
    low.mkdir(); high.mkdir()
    monkeypatch.setattr(sys, "argv", [
        "sanguoyanyi_finalizer", "length", "--run-dir", str(low),
        "--run-dir", str(high), "--expected-chapters", "1",
    ])
    assert main() == 2


def test_equal_or_shorter_adjacent_levels_are_not_length_failures(tmp_path):
    counts = {"hsk1": 1, "hsk2": 2, "hsk3": 3,
              "hsk4": 4, "hsk5": 6, "hsk6": 6}
    low, high, _, _ = _matrix(tmp_path, counts)
    assert length_violations([low, high], expected_chapters=2) == []


def test_changed_prose_invalidates_and_preserves_annotation_backups(tmp_path):
    run = tmp_path / "run"; run.mkdir()
    _json(run / "report.json", {"status": "complete", "chapter_cjk": 3})
    _json(run / "reader.json", {"text": "旧文"})
    _json(run / "annotation-report.json", {"status": "complete"})
    annotation = run / "agents" / "annotations"; annotation.mkdir(parents=True)
    (annotation / "cache").write_text("durable")
    changed = invalidate_annotations(run, "length", b"old", b"new")
    assert changed == ["reader.json", "agents/annotations", "annotation-report.json"]
    assert not (run / "reader.json").exists()
    assert (run / "reader.before-length-heal.json").exists()
    assert (run / "agents" / "annotations.before-length-heal" / "cache").exists()
    assert _json_read(run / "report.json")["length_healing"]["annotation_rerun_required"]


def _json_read(path: Path):
    return json.loads(path.read_text())


def test_legacy_heal_option_does_not_rewrite_reviewed_output(tmp_path):
    counts = {"hsk1": 1, "hsk2": 2, "hsk3": 5,
              "hsk4": 4, "hsk5": 200, "hsk6": 300}
    low, high, sources, roots = _matrix(tmp_path, counts)
    target_run = high / "chapter_001-hsk4"
    _json(target_run / "reader.json", {"text": "stale"})

    before = target_run.joinpath("chapter.txt").read_bytes()
    def runner(command):
        raise AssertionError(f"automatic size healing must not run: {command}")

    records = heal_lengths([low, high], roots, sources, expected_chapters=2,
                           max_rounds=2, runner=runner)
    assert records == []
    assert target_run.joinpath("chapter.txt").read_bytes() == before
    assert (target_run / "reader.json").is_file()


def test_healing_does_not_invent_user_fixed_target_from_level_order(tmp_path):
    counts = {"hsk1": 1, "hsk2": 2, "hsk3": 100,
              "hsk4": 99, "hsk5": 300, "hsk6": 400}
    low, high, sources, roots = _matrix(tmp_path, counts)
    def runner(command):
        raise AssertionError(f"automatic size healing must not run: {command}")

    records = heal_lengths([low, high], roots, sources, expected_chapters=2,
                           max_rounds=2, runner=runner)
    assert records == []


def test_detects_live_harness_targeting_run_root(tmp_path):
    proc = tmp_path / "proc"
    (proc / "123").mkdir(parents=True)
    (proc / "123" / "cmdline").write_bytes(
        b"python3\0-m\0pipeline.agent_harness\0book\0"
        b"--book-run-id\0sanguoyanyi-full-hsk123\0"
    )
    (proc / "456").mkdir()
    (proc / "456" / "cmdline").write_bytes(
        b"python3\0-m\0pipeline.japanese_agent_harness\0book\0"
    )
    active = active_generation_processes(
        [tmp_path / "sanguoyanyi-full-hsk123"], proc_root=proc
    )
    assert [item["pid"] for item in active] == [123]


def test_live_harness_detection_ignores_unrelated_option_value(tmp_path):
    proc = tmp_path / "proc"
    (proc / "123").mkdir(parents=True)
    (proc / "123" / "cmdline").write_bytes(
        b"python3\0-m\0pipeline.agent_harness\0annotate\0"
        b"--run-dir\0runs/experiments/smoke\0"
        b"--annotation-effort\0low\0"
    )
    assert active_generation_processes([tmp_path / "low"], proc_root=proc) == []


def test_shell_propagates_publication_failure():
    script = (Path(__file__).parents[1] / "pipeline/finalize_sanguoyanyi.sh").read_text()
    publication = script[script.index("if ! python3 -m pipeline.publish_sanguoyanyi"):]
    assert "exit 2" in publication.split("fi", 1)[0]
    assert publication.index("exit 2") < publication.index("published successfully")


def test_shell_writes_dedicated_success_marker_only_after_publication():
    script = (Path(__file__).parents[1] / "pipeline/finalize_sanguoyanyi.sh").read_text()
    publisher = script.index("if ! python3 -m pipeline.publish_sanguoyanyi")
    marker = script.index('touch "$success_marker"')
    assert 'rm -f "$success_marker"' in script
    assert marker > publisher
    assert marker > script.index("published successfully", publisher)


def test_shell_rechecks_continuity_after_last_omission_promotion():
    script = (Path(__file__).parents[1] / "pipeline/finalize_sanguoyanyi.sh").read_text()
    post_omission = script.index("post-generation omission audit")
    final_continuity = script.index("post-omission continuity verification")
    annotations = script.index("refreshing only missing or stale annotations")
    assert post_omission < final_continuity < annotations
    assert "--heal" not in script
    verification = script[final_continuity:annotations]
    assert verification.count(" 0 & continuity_pids") == 6
    assert "exit 2" in verification


def test_shell_audits_omissions_and_does_not_rewrite_for_length_order():
    script = (Path(__file__).parents[1] / "pipeline/finalize_sanguoyanyi.sh").read_text()
    post_omission = script.index("post-generation omission audit")
    final_continuity = script.index("post-omission continuity verification", post_omission)
    annotations = script.index("refreshing only missing or stale annotations")
    assert post_omission < final_continuity < annotations
    omission_block = script[post_omission:final_continuity]
    assert "--promote-passed --refresh" in omission_block
    assert "strict six-level" not in script


def test_japanese_handoff_requires_marker_and_fails_closed():
    script = (Path(__file__).parents[1] / "pipeline/resume_wagahai_after_chinese.sh").read_text()
    assert 'if [[ ! -f "$chinese_success" ]]' in script
    generation = script.index("if ! python3 -m pipeline.japanese_agent_harness book")
    publication = script.index("if ! python3 -m pipeline.publish_wagahai")
    success = script.index("Japanese readers published successfully")
    assert "exit 2" in script[generation:publication]
    assert "exit 2" in script[publication:success]
