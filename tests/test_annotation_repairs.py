import json

import pytest

from pipeline.annotation_edits import candidate_digest
from pipeline.annotation_repairs import repair_annotation, replay_annotation_repair
from pipeline.agent_harness import ChapterHarness
from pipeline.japanese_agent_harness import JapaneseChapterHarness
from argparse import Namespace
from pipeline.agent_harness import CodexRunner
from pipeline.worker_workspace import build, check, submit


def _candidate():
    return {
        "segments": [
            {"text": "她", "type": "word", "pinyin": "tā", "meaning_en": "she"},
            {"text": "走", "type": "word", "pinyin": "zǒu", "meaning_en": "walk"},
        ],
        "grammar_overlays": [],
    }


class PlannedRunner:
    def __init__(self, run_dir, plan, patch):
        self.run_dir = run_dir
        self.plan = plan
        self.patch = patch
        self.calls = []

    async def call(self, job, prompt, schema, effort, **kwargs):
        self.calls.append((job, prompt, schema, effort, kwargs))
        result = self.plan if job.endswith("_plan") else self.patch
        directory = self.run_dir / "agents" / job
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        (directory / "meta.json").write_text(json.dumps({"return_code": 0}), encoding="utf-8")
        return result


class WorkspacePlannedRunner(PlannedRunner):
    async def call(self, job, prompt, schema, effort, **kwargs):
        result = self.plan if job.endswith("_plan") else self.patch
        job_dir = self.run_dir / "agents" / job
        workspace = job_dir / "workspace"
        _, workspace_digest = build(
            workspace, prompt, json.loads(schema.read_text(encoding="utf-8")),
            context=kwargs.get("workspace_context"),
        )
        candidate_path = workspace / "candidate.json"
        candidate_path.write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")
        check(workspace, candidate_path)
        value, artifact = submit(workspace, {"candidate_path": "candidate.json"})
        job_dir.mkdir(parents=True, exist_ok=True)
        result_path = job_dir / "result.json"
        result_path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")
        (job_dir / "meta.json").write_text(json.dumps({
            "return_code": 0, "tool_profile": "workspace", "workspace_digest": workspace_digest,
            **artifact,
        }), encoding="utf-8")
        self.calls.append((job, prompt, schema, effort, kwargs))
        return value


class Harness:
    def __init__(self, run_dir, runner):
        self.run_dir = run_dir
        self.runner = runner
        self.args = type("Args", (), {"annotation_repair_effort": "low", "refresh": False})()


def _plan(*targets, boundary=False, boundary_reason=""):
    return {"issues": [{"issue_index": index, "reason": f"issue {index} semantic target",
                        "targets": list(issue_targets), "boundary_change_needed": boundary,
                        "boundary_reason": boundary_reason}
                       for index, issue_targets in enumerate(targets)]}


def _target(op, path):
    return {"op": op, "path": path}


@pytest.mark.asyncio
async def test_issue_planned_patch_is_validated_persisted_and_replayed(tmp_path):
    candidate = _candidate()
    issues = [{"segment_text": "走", "problem": "meaning", "explanation": "Use go."},
              {"segment_text": "她", "problem": "meaning", "explanation": "Use she."}]
    plan = _plan((_target("set_field", "/segments/1/meaning_en"),),
                 (_target("set_field", "/segments/0/meaning_en"),))
    patch = {"base_digest": __import__("pipeline.annotation_edits", fromlist=["candidate_digest"]).candidate_digest(candidate),
             "edits": [{"op": "set_field", "path": "/segments/1/meaning_en", "value": "go"},
                       {"op": "set_field", "path": "/segments/0/meaning_en", "value": "she (the narrator)"}]}
    runner = PlannedRunner(tmp_path, plan, patch)
    harness = Harness(tmp_path, runner)
    seen = []

    def validate_candidate(value):
        assert "".join(row["text"] for row in value["segments"]) == "她走"
        seen.append(value)

    result = await repair_annotation(
        harness, "annotations/chunk_0001/semantic", candidate, issues,
        representation="chinese-annotation", language="zh", context={"chunk_text": "她走"},
        validate_candidate=validate_candidate,
    )
    assert result["status"] == "applied"
    assert result["candidate"]["segments"][1]["meaning_en"] == "go"
    assert result["candidate"]["segments"][0]["text"] == "她"
    assert result["evidence"]["base_digest"]
    assert result["evidence"]["plan_digest"]
    assert result["evidence"]["patch_digest"]
    assert runner.calls[1][4]["workspace_context"]["annotation_patch_validation"]["representation"] == "chinese-annotation"
    replayed = replay_annotation_repair(
        tmp_path, result["evidence"]["assembly_job"], validate_candidate=validate_candidate,
    )
    assert replayed["candidate"] == result["candidate"]
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_boundary_plan_is_explicit_and_does_not_run_or_apply_patch(tmp_path):
    candidate = _candidate()
    runner = PlannedRunner(tmp_path, _plan((), boundary=True,
        boundary_reason="The learner unit must be split into two source taps."), {})
    result = await repair_annotation(
        Harness(tmp_path, runner), "repair", candidate, [{"problem": "under_grouped"}],
        representation="chinese-annotation", language="zh", validate_candidate=lambda _: None,
    )
    assert result["status"] == "boundary_change_needed"
    assert result["candidate"] == candidate
    assert [call[0] for call in runner.calls] == ["repair_plan"]
    replayed = replay_annotation_repair(tmp_path, "repair_assembly", validate_candidate=lambda _: None)
    assert replayed["status"] == "boundary_change_needed"
    assert replayed["candidate"] == candidate


@pytest.mark.asyncio
async def test_missing_issue_coverage_is_rejected_before_patch_call(tmp_path):
    candidate = _candidate()
    runner = PlannedRunner(tmp_path, _plan((_target("set_field", "/segments/0/meaning_en"),)), {})
    with pytest.raises(ValueError, match="cover supplied issue indices"):
        await repair_annotation(
            Harness(tmp_path, runner), "repair", candidate,
            [{"problem": "meaning"}, {"problem": "wrong_type"}],
            representation="chinese-annotation", language="zh", validate_candidate=lambda _: None,
        )
    assert len(runner.calls) == 1


@pytest.mark.asyncio
async def test_patch_digest_and_plan_scope_are_enforced(tmp_path):
    candidate = _candidate()
    issue = [{"problem": "meaning"}]
    target = _target("set_field", "/segments/0/meaning_en")
    plan = _plan((target,))
    stale_runner = PlannedRunner(tmp_path / "stale", plan, {
        "base_digest": "0" * 64,
        "edits": [{"op": "set_field", "path": target["path"], "value": "she"}],
    })
    stale_runner.run_dir.mkdir()
    stale = await repair_annotation(Harness(stale_runner.run_dir, stale_runner), "repair", candidate, issue,
        representation="chinese-annotation", language="zh", validate_candidate=lambda _: None)
    assert stale["status"] == "patch_rejected"
    assert stale["candidate"] == candidate

    outside_runner = PlannedRunner(tmp_path / "outside", plan, {
        "base_digest": candidate_digest(candidate),
        "edits": [{"op": "set_field", "path": "/segments/1/meaning_en", "value": "walk"}],
    })
    outside_runner.run_dir.mkdir()
    outside = await repair_annotation(Harness(outside_runner.run_dir, outside_runner), "repair", candidate, issue,
        representation="chinese-annotation", language="zh", validate_candidate=lambda _: None)
    assert outside["status"] == "patch_rejected"
    assert outside["candidate"] == candidate


@pytest.mark.asyncio
async def test_attempted_source_surface_edit_returns_boundary_outcome_and_replays(tmp_path):
    candidate = _candidate()
    target = _target("set_field", "/segments/0/text")
    runner = PlannedRunner(tmp_path, _plan((target,)), {
        "base_digest": candidate_digest(candidate),
        "edits": [{"op": "set_field", "path": target["path"], "value": "他"}],
    })
    result = await repair_annotation(
        Harness(tmp_path, runner), "repair", candidate, [{"problem": "meaning"}],
        representation="chinese-annotation", language="zh", validate_candidate=lambda _: None,
    )
    assert result["status"] == "boundary_change_needed"
    assert result["candidate"] == candidate
    replayed = replay_annotation_repair(tmp_path, "repair_assembly", validate_candidate=lambda _: None)
    assert replayed["status"] == "boundary_change_needed"
    assert replayed["candidate"] == candidate


@pytest.mark.asyncio
async def test_chinese_chunk_uses_semantic_patch_then_independently_reviews_it(tmp_path):
    initial = _candidate()
    issue = {"segment_text": "走", "problem": "meaning", "explanation": "Use go.",
             "suggested_fix": "Set its contextual meaning to go."}
    plan = _plan((_target("set_field", "/segments/1/meaning_en"),))
    patch = {"base_digest": candidate_digest(initial),
             "edits": [{"op": "set_field", "path": "/segments/1/meaning_en", "value": "go"}]}

    class ChineseIntegrationHarness(ChapterHarness):
        async def annotation_candidate(self, index, chunk, **kwargs):
            return json.loads(json.dumps(initial))

        async def review_annotation(self, index, chunk, annotation, stage, **kwargs):
            if stage == "initial":
                return {"verdict": "revise", "issues": [issue]}
            assert annotation["segments"][1]["meaning_en"] == "go"
            return {"verdict": "pass", "issues": []}

    harness = object.__new__(ChineseIntegrationHarness)
    harness.args = Namespace(
        annotation_mode="generative", max_annotation_repairs=1, refresh=False,
        no_grammar_overlays=False, annotation_repair_effort="low",
        annotation_effort="low", annotation_review_effort="low",
    )
    harness.run_dir = tmp_path
    harness.runner = PlannedRunner(tmp_path, plan, patch)
    result = await harness.annotate_chunk(0, "她走")
    assert result["segments"][1]["meaning_en"] == "go"
    assert result["attempts"][0]["semantic_repair"]["status"] == "applied"
    assert [call[0] for call in harness.runner.calls] == [
        "annotations/chunk_0000/semantic_repair_01_plan",
        "annotations/chunk_0000/semantic_repair_01_patch",
    ]


@pytest.mark.asyncio
async def test_japanese_chunk_uses_semantic_patch_then_independently_reviews_it(tmp_path):
    initial = {"segments": [{
        "surface": "猫", "type": "word", "lemma": "猫", "surface_kana": "ねこ",
        "lemma_kana": "ねこ", "part_of_speech": "noun", "conjugation_form": "non-inflecting",
        "meaning_en": "animal", "grammar_candidate_key": "", "dictionary_key": "",
        "dictionary_definition_en": "", "form_steps": [], "story_role": "none",
        "story_importance_en": "",
    }], "grammar_overlays": []}
    issue = {"segment_text": "猫", "problem": "meaning", "explanation": "Use cat.",
             "suggested_fix": "Change the segment meaning to cat."}
    plan = _plan((_target("set_field", "/segments/0/meaning_en"),))
    preparer = object.__new__(JapaneseChapterHarness)
    preparer.args = Namespace(no_grammar_overlays=False)
    preparer.story_vocabulary_plan = {"terms": []}
    prepared_base = preparer.prepare_planned_annotation("猫", initial)
    patch = {"base_digest": candidate_digest(prepared_base),
             "edits": [{"op": "set_field", "path": "/segments/0/meaning_en", "value": "cat"}]}

    class JapaneseIntegrationHarness(JapaneseChapterHarness):
        async def annotation_candidate(self, index, chunk, **kwargs):
            return json.loads(json.dumps(initial))

        async def review_annotation(self, index, chunk, annotation, stage):
            if stage == "initial":
                return {"verdict": "revise", "issues": [issue]}
            assert annotation["segments"][0]["meaning_en"] == "cat"
            return {"verdict": "pass", "issues": []}

    harness = object.__new__(JapaneseIntegrationHarness)
    harness.args = Namespace(
        level="n5", annotation_chunk=1, annotation_chunk_maximum=45,
        max_annotation_repairs=1, max_annotation_fresh_repairs=0,
        max_annotation_adjudications=0, annotation_repair_effort="low",
        annotation_final_effort="low", no_grammar_overlays=False, refresh=False,
    )
    harness.run_dir = tmp_path
    harness.runner = PlannedRunner(tmp_path, plan, patch)
    harness.story_vocabulary_plan = {"terms": []}
    result = await harness.annotate_chunk(0, "猫")
    assert result["segments"][0]["meaning_en"] == "cat"
    assert result["attempts"][0]["semantic_repair"]["status"] == "applied"
    assert [call[0] for call in harness.runner.calls] == [
        "annotations/chunk_0000/repair_01_semantic_plan",
        "annotations/chunk_0000/repair_01_semantic_patch",
    ]


@pytest.mark.asyncio
async def test_profile_check_replays_only_applied_assembly_with_completed_child_evidence(tmp_path):
    candidate = _candidate()
    issue = [{"problem": "meaning", "segment_text": "走", "explanation": "Use go."}]
    plan = _plan((_target("set_field", "/segments/1/meaning_en"),))
    patch = {"base_digest": candidate_digest(candidate),
             "edits": [{"op": "set_field", "path": "/segments/1/meaning_en", "value": "go"}]}
    runner = WorkspacePlannedRunner(tmp_path, plan, patch)
    harness = Harness(tmp_path, runner)
    result = await repair_annotation(
        harness, "repairs/chunk_0001/semantic", candidate, issue,
        representation="chinese-annotation", language="zh", context={"chunk_text": "她走"},
        validate_candidate=lambda value: None,
    )
    assembly_dir = tmp_path / "agents" / result["evidence"]["assembly_job"]
    meta = json.loads((assembly_dir / "meta.json").read_text(encoding="utf-8"))
    CodexRunner._check_tool_profile(assembly_dir, None, meta)

    # The workspace receipt and runner result are both evidence. Changing the
    # runner result must fail the recursive child profile check.
    patch_result = tmp_path / "agents" / meta["patch_job"] / "result.json"
    patch_result.write_text(json.dumps({"base_digest": "0" * 64, "edits": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="Worker submitted artifact changed"):
        CodexRunner._check_tool_profile(assembly_dir, None, meta)


@pytest.mark.asyncio
async def test_profile_check_rejects_nonapplied_assemblies_and_unsafe_child_references(tmp_path):
    candidate = _candidate()
    plan = _plan((), boundary=True, boundary_reason="A source tap must be split.")
    runner = WorkspacePlannedRunner(tmp_path, plan, {})
    result = await repair_annotation(
        Harness(tmp_path, runner), "repairs/chunk_1/change", candidate, [{"problem": "under_grouped"}],
        representation="chinese-annotation", language="zh", context={"chunk_text": "她走"},
        validate_candidate=lambda value: None,
    )
    assembly_dir = tmp_path / "agents" / result["evidence"]["assembly_job"]
    meta_path = assembly_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="Only an applied"):
        CodexRunner._check_tool_profile(assembly_dir, None, meta)

    # Synthesize a second fully applied assembly so the path check is reached.
    applied_plan = _plan((_target("set_field", "/segments/1/meaning_en"),))
    applied_patch = {"base_digest": candidate_digest(candidate),
                     "edits": [{"op": "set_field", "path": "/segments/1/meaning_en", "value": "go"}]}
    runner = WorkspacePlannedRunner(tmp_path, applied_plan, applied_patch)
    result = await repair_annotation(
        Harness(tmp_path, runner), "repairs/chunk_2/change", candidate, [{"problem": "meaning"}],
        representation="chinese-annotation", language="zh", context={"chunk_text": "她走"},
        validate_candidate=lambda value: None,
    )
    assembly_dir = tmp_path / "agents" / result["evidence"]["assembly_job"]
    meta_path = assembly_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["plan_job"] = "../outside"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid annotation patch evidence path"):
        CodexRunner._check_tool_profile(assembly_dir, None, meta)


@pytest.mark.asyncio
async def test_profile_check_rejects_symlinked_patch_child(tmp_path):
    candidate = _candidate()
    plan = _plan((_target("set_field", "/segments/1/meaning_en"),))
    patch = {"base_digest": candidate_digest(candidate),
             "edits": [{"op": "set_field", "path": "/segments/1/meaning_en", "value": "go"}]}
    runner = WorkspacePlannedRunner(tmp_path, plan, patch)
    result = await repair_annotation(
        Harness(tmp_path, runner), "repairs/symlink", candidate, [{"problem": "meaning"}],
        representation="chinese-annotation", language="zh", context={"chunk_text": "她走"},
        validate_candidate=lambda value: None,
    )
    assembly_dir = tmp_path / "agents" / result["evidence"]["assembly_job"]
    meta = json.loads((assembly_dir / "meta.json").read_text(encoding="utf-8"))
    child_dir = tmp_path / "agents" / meta["patch_job"]
    outside = tmp_path / "outside-child"
    child_dir.rename(outside)
    child_dir.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        CodexRunner._check_tool_profile(assembly_dir, None, meta)
