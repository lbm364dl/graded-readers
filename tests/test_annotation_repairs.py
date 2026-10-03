import json
import hashlib

import pytest

from pipeline.annotation_edits import ImmutableFieldError, apply_edits, candidate_digest
from pipeline.annotation_repairs import digest, repair_annotation, replay_annotation_repair
from pipeline.agent_harness import ChapterHarness
from pipeline.japanese_agent_harness import JapaneseChapterHarness
from argparse import Namespace
from pipeline.agent_harness import CodexRunner
from pipeline.worker_workspace import build, check, submit
from pipeline.worker_workspace import CandidateSubmissionError


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
    assert runner.calls[0][4]['workspace_context']['annotation_plan_validation'] == {
        'issue_count': 2, 'target_contract_version': 1}
    assert 'annotation_plan_validation' not in runner.calls[1][4]['workspace_context']
    assert "a wrong occurrence gloss on an already-valid chain should target only that meaning field" in runner.calls[0][1]
    assert "a changed lemma/identity for an inflected surface may also require corrected ordered stages" in runner.calls[0][1]
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
    with pytest.raises(ImmutableFieldError, match="protected source/tap data"):
        await repair_annotation(
            Harness(tmp_path, runner), "repair", candidate, [{"problem": "meaning"}],
            representation="chinese-annotation", language="zh", validate_candidate=lambda _: None,
        )
    assert [call[0] for call in runner.calls] == ["repair_plan"]


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
async def test_legacy_applied_plan_with_unused_invalid_target_still_replays_actual_patch(tmp_path):
    candidate = _candidate()
    plan = _plan((_target("set_field", "/segments/1/meaning_en"),))
    patch = {"base_digest": candidate_digest(candidate), "edits": [
        {"op": "set_field", "path": "/segments/1/meaning_en", "value": "go"}]}
    runner = PlannedRunner(tmp_path, plan, patch)
    result = await repair_annotation(Harness(tmp_path, runner), "legacy", candidate,
        [{"problem": "meaning"}], representation="chinese-annotation", language="zh",
        validate_candidate=lambda _value: None)
    # Simulate a previously accepted plan from before target feasibility
    # checks. The extra stale array target is unused by the stored patch.
    plan_path = tmp_path / "agents" / "legacy_plan" / "result.json"
    historical_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    historical_plan["issues"][0]["targets"].append(
        _target("set_field", "/segments/0/form_steps"))
    plan_path.write_text(json.dumps(historical_plan), encoding="utf-8")
    assembly_path = tmp_path / "agents" / "legacy_assembly" / "meta.json"
    meta = json.loads(assembly_path.read_text(encoding="utf-8"))
    meta["plan_digest"] = digest(historical_plan)
    meta["target_contract_version"] = 0
    assembly_path.write_text(json.dumps(meta), encoding="utf-8")

    replayed = replay_annotation_repair(tmp_path, "legacy_assembly", validate_candidate=lambda _: None)
    assert replayed["candidate"] == result["candidate"]
    assert replayed["candidate"]["segments"][1]["meaning_en"] == "go"


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


class ReplanningRunner:
    """Runner fixture with one exhausted, derived-gate submission correction."""
    def __init__(self, run_dir, candidate, *, terminal=False, failure_category="derived_annotation_rejection"):
        self.run_dir = run_dir
        self.candidate = candidate
        self.terminal = terminal
        self.failure_category = failure_category
        self.calls = []

    async def call(self, job, prompt, schema, effort, **kwargs):
        self.calls.append((job, prompt, kwargs))
        if "_replan_plan" in job:
            n = len(kwargs["workspace_context"]["issues"])
            value = {"issues": [{"issue_index": index, "reason": "Repair the diagnosed gate dependency.",
                "targets": [_target("set_field", "/segments/0/meaning_en")],
                "boundary_change_needed": False, "boundary_reason": ""}
                for index in range(n)]}
        elif job.endswith("_plan"):
            value = _plan((_target("set_field", "/segments/0/meaning_en"),))
        elif job.endswith("_patch") and "_replan_" not in job:
            value = {"base_digest": candidate_digest(self.candidate),
                     "edits": [{"op": "set_field", "path": "/segments/0/meaning_en",
                                "value": "bad-first-patch"}]}
            raw = (json.dumps(value, ensure_ascii=False) + "\n").encode()
            correction = "repairs/chunk/submission-correction"
            correction_dir = self.run_dir / "agents" / correction
            (correction_dir / "workspace").mkdir(parents=True, exist_ok=True)
            (correction_dir / "workspace" / "candidate.json").write_bytes(raw)
            (correction_dir / "meta.json").write_text(json.dumps({"return_code": 1,
                "tool_profile": "workspace", "workspace_digest": "fixture"}), encoding="utf-8")
            recovery = {"status": "rejected", "job": correction,
                "initial_rejection": {"category": self.failure_category,
                    "artifact_sha256": hashlib.sha256(raw).hexdigest(),
                    "diagnostic": "first derived gate failure"},
                "repair_rejection": {"category": self.failure_category,
                    "artifact_path": "candidate.json",
                    "artifact_sha256": hashlib.sha256(raw).hexdigest(),
                    "diagnostic": "Submitted annotation patch failed derived-candidate validation: " +
                        ("dependent semantic row still invalid" if self.terminal else
                         "complete candidate gate: required dependent form analysis missing")}}
            original_dir = self.run_dir / "agents" / job
            original_dir.mkdir(parents=True, exist_ok=True)
            (original_dir / "submission-recovery.json").write_text(
                json.dumps(recovery), encoding="utf-8")
            (original_dir / "workspace").mkdir(exist_ok=True)
            (original_dir / "workspace" / "submission-rejection.json").write_text(
                json.dumps(recovery["initial_rejection"]), encoding="utf-8")
            (original_dir / "workspace" / "rejected-candidate-attempt-01.json").write_bytes(raw)
            (original_dir / "workspace" / "candidate.json").write_bytes(raw)
            raise CandidateSubmissionError("bounded correction rejected",
                artifact_path="candidate.json", artifact_bytes=raw,
                category="bounded_repair_rejected")
        else:
            value = {"base_digest": candidate_digest(self.candidate),
                "edits": [{"op": "set_field", "path": "/segments/0/meaning_en",
                           "value": "bad-second-patch" if self.terminal else "corrected"}]}
        # Minimal child artifact evidence for deterministic assembly replay.
        directory = self.run_dir / "agents" / job
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "result.json").write_text(json.dumps(value), encoding="utf-8")
        (directory / "meta.json").write_text(json.dumps({"return_code": 0}), encoding="utf-8")
        return value


@pytest.mark.asyncio
async def test_one_derived_gate_replan_uses_original_base_and_replays_lineage(tmp_path):
    candidate = _candidate()
    runner = ReplanningRunner(tmp_path, candidate)
    def gate(value):
        if value["segments"][0]["meaning_en"].startswith("bad-"):
            raise ValueError("complete candidate gate: required dependent form analysis missing")

    result = await repair_annotation(Harness(tmp_path, runner), "repair", candidate,
        [{"problem": "meaning", "explanation": "Correct the meaning."}],
        representation="chinese-annotation", language="zh", validate_candidate=gate)
    assert result["status"] == "applied"
    assert result["candidate"]["segments"][0]["meaning_en"] == "corrected"
    assert candidate["segments"][0]["meaning_en"] == "she"
    assert len(runner.calls) == 4
    assert "exact ORIGINAL base candidate" in runner.calls[3][1]
    assert runner.calls[3][2]["workspace_context"]["base_digest"] == candidate_digest(candidate)
    replayed = replay_annotation_repair(tmp_path, "repair_assembly", validate_candidate=gate)
    assert replayed["candidate"] == result["candidate"]
    meta = json.loads((tmp_path / "agents" / "repair_assembly" / "meta.json").read_text())
    assert meta["replan"]["failed_patch"]["edits"][0]["value"] == "bad-first-patch"
    assert meta["replan"]["reproduced_gate_diagnostic"] == \
        "complete candidate gate: required dependent form analysis missing"
    assert meta["replan"]["replan_patch_job"].endswith("_replan_patch")


@pytest.mark.asyncio
async def test_replan_exhaustion_is_terminal_and_never_starts_a_third_attempt(tmp_path):
    candidate = _candidate()
    runner = ReplanningRunner(tmp_path, candidate, terminal=True)
    def gate(value):
        if value["segments"][0]["meaning_en"].startswith("bad-"):
            raise ValueError("dependent semantic row still invalid")

    result = await repair_annotation(Harness(tmp_path, runner), "repair", candidate,
        [{"problem": "meaning"}], representation="chinese-annotation", language="zh",
        validate_candidate=gate)
    assert result["status"] == "patch_rejected"
    assert result["candidate"] == candidate
    assert len(runner.calls) == 4
    replayed = replay_annotation_repair(tmp_path, "repair_assembly", validate_candidate=gate)
    assert replayed["status"] == "patch_rejected"
    assert replayed["candidate"] == candidate


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_category", ["schema_rejection", "capacity_timeout"])
async def test_non_derived_submission_failure_does_not_replan(tmp_path, failure_category):
    candidate = _candidate()
    runner = ReplanningRunner(tmp_path, candidate, failure_category=failure_category)
    with pytest.raises(CandidateSubmissionError, match="bounded correction rejected"):
        await repair_annotation(Harness(tmp_path, runner), "repair", candidate,
            [{"problem": "meaning"}], representation="chinese-annotation", language="zh",
            validate_candidate=lambda _: None)
    assert len(runner.calls) == 2


@pytest.mark.asyncio
async def test_source_and_target_contract_failure_does_not_replan(tmp_path):
    candidate = _candidate()
    runner = ReplanningRunner(tmp_path, candidate,
        failure_category="annotation_patch_contract_rejection")
    with pytest.raises(CandidateSubmissionError, match="bounded correction rejected"):
        await repair_annotation(Harness(tmp_path, runner), "repair", candidate,
            [{"problem": "meaning"}], representation="chinese-annotation", language="zh",
            validate_candidate=lambda _: None)
    assert len(runner.calls) == 2


def test_lemma_identity_repair_keeps_dependent_form_chain_while_gloss_patch_stays_narrow():
    base = {"segments": [{"text": "좋아해", "lemma": "좋다", "lexical_id": "ko:좋다",
        "meaning_en": "likes", "form_steps": [{"form": "좋아해", "meaning_en": "likes",
            "grammar_id": "ko-descriptive-aeo-hada"}]}],
        "grammar_links": [{"segment_index": 0, "display_end_segment_index": 0,
            "display_form": "좋아해", "grammar_id": "ko-aeo"}],
        "expression_links": [], "inflected_segment_indices": [0]}
    gate = lambda value: (value["segments"][0]["lemma"] == "좋아하다" and
        bool(value["segments"][0]["form_steps"]) and
        value["segments"][0]["form_steps"][-1]["form"] == value["segments"][0]["text"] and
        value["segments"][0]["form_steps"][-1]["grammar_id"] == "ko-aeo")
    identity_patch = {"base_digest": candidate_digest(base), "edits": [
        {"op": "set_field", "path": "/segments/0/lemma", "value": "좋아하다"},
        {"op": "replace_list", "path": "/segments/0/form_steps", "value": [
            {"form": "좋아해", "meaning_en": "likes", "grammar_id": "ko-aeo"}]}]}
    identity_allowed = [{"op": "set_field", "path": "/segments/0/lemma"},
        {"op": "replace_list", "path": "/segments/0/form_steps"}]
    repaired = apply_edits(base, identity_patch, allowed_targets=identity_allowed,
                           representation="korean-flat")
    assert gate(repaired)
    assert repaired["segments"][0]["text"] == "좋아해"
    assert repaired["grammar_links"] == base["grammar_links"]
    assert repaired["inflected_segment_indices"] == [0]

    missing_dependency = {"base_digest": candidate_digest(base), "edits": [
        {"op": "set_field", "path": "/segments/0/lemma", "value": "좋아하다"}]}
    incomplete = apply_edits(base, missing_dependency,
        allowed_targets=[{"op": "set_field", "path": "/segments/0/lemma"}],
        representation="korean-flat")
    assert not gate(incomplete)
    valid_base = json.loads(json.dumps(base, ensure_ascii=False))
    valid_base["segments"][0]["lemma"] = "좋아하다"
    valid_base["segments"][0]["form_steps"][0]["grammar_id"] = "ko-aeo"
    gloss_only = {"base_digest": candidate_digest(valid_base), "edits": [
        {"op": "set_field", "path": "/segments/0/meaning_en", "value": "likes this"}]}
    gloss_allowed = [{"op": "set_field", "path": "/segments/0/meaning_en"}]
    gloss = apply_edits(valid_base, gloss_only, allowed_targets=gloss_allowed,
                        representation="korean-flat")
    assert gate(gloss)
    assert gloss["segments"][0]["form_steps"] == valid_base["segments"][0]["form_steps"]


@pytest.mark.asyncio
async def test_applied_replan_profile_replays_real_workspace_lineage(tmp_path, monkeypatch):
    from pipeline.agent_harness import ChapterHarness

    candidate = _candidate()
    gate_message = "semantic gate: dependent form analysis is missing"
    monkeypatch.setattr(ChapterHarness, "annotation_contract_issues", staticmethod(
        lambda _text, value: [gate_message] if value["segments"][0]["meaning_en"] == "bad-first-patch" else []))

    class WorkspaceReplanningRunner:
        def __init__(self):
            self.run_dir = tmp_path
            self.calls = []

        async def _workspace_result(self, job, prompt, schema, context, value):
            job_dir = tmp_path / "agents" / job
            workspace = job_dir / "workspace"
            _, workspace_digest = build(workspace, prompt,
                json.loads(schema.read_text(encoding="utf-8")), context=context)
            (workspace / "candidate.json").write_text(json.dumps(value, ensure_ascii=False) + "\n",
                                                        encoding="utf-8")
            return job_dir, workspace, workspace_digest

        async def call(self, job, prompt, schema, effort, **kwargs):
            self.calls.append(job)
            context = kwargs["workspace_context"]
            if "_replan_plan" in job:
                values = _plan(*[(_target("set_field", "/segments/0/meaning_en"),)
                                 for _ in context["issues"]])
            elif job.endswith("_plan"):
                values = _plan((_target("set_field", "/segments/0/meaning_en"),))
            elif "_replan_patch" in job:
                values = {"base_digest": candidate_digest(candidate), "edits": [
                    {"op": "set_field", "path": "/segments/0/meaning_en", "value": "corrected"}]}
            else:
                values = {"base_digest": candidate_digest(candidate), "edits": [
                    {"op": "set_field", "path": "/segments/0/meaning_en", "value": "bad-first-patch"}]}
            job_dir, workspace, workspace_digest = await self._workspace_result(
                job, prompt, schema, context, values)
            if job.endswith("_patch") and "_replan_patch" not in job:
                try:
                    submit(workspace, {"candidate_path": "candidate.json"})
                except CandidateSubmissionError as first:
                    # Model the real runner's one workspace correction attempt.
                    correction_job = f"{job}-submission-correction-fixture"
                    repair_dir, repair_workspace, repair_digest = await self._workspace_result(
                        correction_job, prompt, schema, context, values)
                    try:
                        submit(repair_workspace, {"candidate_path": "candidate.json"})
                    except CandidateSubmissionError as final:
                        first_record, final_record = first.record(), final.record()
                    else:
                        raise AssertionError("fixture correction unexpectedly passed")
                    first_bytes = (workspace / "candidate.json").read_bytes()
                    (workspace / "submission-rejection.json").write_text(
                        json.dumps(first_record, ensure_ascii=False), encoding="utf-8")
                    (workspace / "rejected-candidate-attempt-01.json").write_bytes(first_bytes)
                    (job_dir / "submission-recovery.json").write_text(json.dumps({
                        "status": "rejected", "job": correction_job,
                        "initial_rejection": first_record, "repair_rejection": final_record,
                    }, ensure_ascii=False), encoding="utf-8")
                    (job_dir / "result.json").write_text("{}", encoding="utf-8")
                    (job_dir / "meta.json").write_text(json.dumps({"return_code": 1,
                        "tool_profile": "workspace", "workspace_digest": workspace_digest}), encoding="utf-8")
                    (repair_dir / "result.json").write_text("{}", encoding="utf-8")
                    (repair_dir / "meta.json").write_text(json.dumps({"return_code": 1,
                        "tool_profile": "workspace", "workspace_digest": repair_digest}), encoding="utf-8")
                    raise CandidateSubmissionError("bounded correction rejected",
                        artifact_path="candidate.json", artifact_bytes=first_bytes,
                        category="bounded_repair_rejected")
            else:
                returned, artifact = submit(workspace, {"candidate_path": "candidate.json"})
                (job_dir / "result.json").write_text(json.dumps(returned, ensure_ascii=False) + "\n",
                                                      encoding="utf-8")
                (job_dir / "meta.json").write_text(json.dumps({"return_code": 0,
                    "tool_profile": "workspace", "workspace_digest": workspace_digest,
                    **artifact}), encoding="utf-8")
                return returned

    runner = WorkspaceReplanningRunner()
    def gate(value):
        if value["segments"][0]["meaning_en"] == "bad-first-patch":
            raise ValueError(json.dumps([gate_message], ensure_ascii=False))
    result = await repair_annotation(Harness(tmp_path, runner), "repair", candidate,
        [{"problem": "meaning"}], representation="chinese-annotation", language="zh",
        context={"chunk_text": "她走"}, validate_candidate=gate)
    assert result["status"] == "applied"
    assembly_dir = tmp_path / "agents" / "repair_assembly"
    meta = json.loads((assembly_dir / "meta.json").read_text(encoding="utf-8"))
    CodexRunner._check_tool_profile(assembly_dir, None, meta)
    # Cache replay does not re-run the historical failure through a possibly
    # changed catalog, but it still applies the current gate to the final edit.
    assert replay_annotation_repair(tmp_path, "repair_assembly",
        validate_candidate=lambda _value: None)["status"] == "applied"
    with pytest.raises(ValueError, match="new final gate failure"):
        replay_annotation_repair(tmp_path, "repair_assembly", validate_candidate=lambda value:
            (_ for _ in ()).throw(ValueError("new final gate failure"))
            if value["segments"][0]["meaning_en"] == "corrected" else None)

    # The raw final rejected bytes are hash-bound to the decoded failed patch.
    recovery_path = tmp_path / "agents" / meta["replan"]["first_patch_job"] / "submission-recovery.json"
    recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
    artifact = tmp_path / "agents" / recovery["job"] / "workspace" / "candidate.json"
    artifact.write_text(json.dumps({"base_digest": "f" * 64, "edits": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="Final rejected annotation patch artifact changed"):
        CodexRunner._check_tool_profile(assembly_dir, None, meta)
