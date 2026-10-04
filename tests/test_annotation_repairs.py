import json
import hashlib

import pytest

from pipeline.annotation_edits import EditCoverageError, ImmutableFieldError, apply_edits, candidate_digest
from pipeline.annotation_repairs import (
    _representation_structure_contract, digest, inspect_applied_repair_coverage,
    repair_annotation, replay_annotation_repair,
)
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


def test_representation_structure_contract_uses_korean_step_schema_without_affecting_chinese_or_japanese():
    from jsonschema import ValidationError, validate
    from pipeline import korean_contracts

    flat = _representation_structure_contract("korean-flat")
    v4 = _representation_structure_contract("korean-v4")
    assert flat["min_items"] == flat["max_items"] == 1
    assert v4 == flat
    assert _representation_structure_contract("chinese-annotation") is None
    assert _representation_structure_contract("japanese-annotation") is None

    multiform_step = {"form": "가셨다", "reading": "가시- + -었- + -다", "label": "past",
        "meaning_en": "went", "grammar_entry_ids": ["honorific", "past"]}
    with pytest.raises(ValidationError):
        validate(multiform_step, korean_contracts.STEP)
    ordered_steps = [
        {"form": "가시다", "reading": "가- + -시- + -다", "label": "subject honorific",
         "meaning_en": "go", "grammar_entry_ids": ["honorific"]},
        {"form": "가셨다", "reading": "가시- + -었- + -다", "label": "past",
         "meaning_en": "went", "grammar_entry_ids": ["past"]},
    ]
    for step in ordered_steps:
        validate(step, korean_contracts.STEP)


def test_construction_occurrence_contract_is_representation_scoped_and_keeps_endings_separate():
    from pipeline.annotation_repairs import _construction_occurrence_contract

    for representation, list_path, anchor in [
        ("chinese-annotation", "/grammar_overlays", "start Unicode offset"),
        ("japanese-annotation", "/grammar_overlays", "start Unicode offset"),
        ("korean-flat", "/grammar_links", "segment_index of first included lexical/name tap"),
        ("korean-v4", "grammar_links within the anchor segment", "source_start of first included lexical/name tap"),
    ]:
        contract = _construction_occurrence_contract(representation)
        assert contract["occurrence_list"] == list_path
        assert contract["anchor"] == anchor
        assert "retain" in contract["ending_link"]
    assert _construction_occurrence_contract("unknown") is None


@pytest.mark.asyncio
async def test_shared_repair_prompt_routes_ending_tap_construction_without_grammar_root(tmp_path):
    from pipeline.annotation_repairs import _construction_occurrence_contract

    candidate = _candidate()
    target = _target("append_row", "/grammar_overlays")
    plan = _plan((target,))
    patch = {"base_digest": candidate_digest(candidate), "edits": [
        {"op": "append_row", "path": "/grammar_overlays", "value": {
            "start": 0, "end": 2, "text": "她走", "grammar_candidate_key": "verb-aux",
            "pattern": "verb + auxiliary", "meaning_en": "she walks along"}}]}
    runner = PlannedRunner(tmp_path, plan, patch)
    result = await repair_annotation(Harness(tmp_path, runner), "construction-contract", candidate,
        [{"problem": "A supported complete construction spans the lexical tap and ending."}],
        representation="chinese-annotation", language="zh", validate_candidate=lambda _: None)
    assert result["status"] == "applied"
    context = runner.calls[0][4]["workspace_context"]
    assert context["construction_occurrence_contract"] == _construction_occurrence_contract("chinese-annotation")
    prompt = runner.calls[0][1]
    assert "do not use the ending's grammar identity as the lexical root" in prompt
    assert "noun/name + predication and lexical-verb + auxiliary" in prompt
    assert "preserve every tap and the direct ending lesson" in prompt
    assert "do not add a copula-ID exception" in prompt
    for call in runner.calls:
        assert "valency-frame gloss" in call[1]
        assert "leave correct contrasting occurrences unchanged" in call[1]
        assert "Do not copy reviewer objections" in call[1]

    # The same general instructions reach Japanese and both Korean representations;
    # the structure descriptor remains representation-specific rather than importing
    # Korean cardinality into Chinese/Japanese.
    scoped_candidates = {
        "japanese-annotation": {"segments": [{"surface": "彼", "meaning_en": "he", "form_steps": []}],
            "grammar_overlays": []},
        "korean-flat": {"segments": [{"text": "그", "meaning_en": "he", "form_steps": []}],
            "grammar_links": [], "inflected_segment_indices": [], "expression_links": []},
        "korean-v4": {"format": "source-span-links-annotation-v4", "segments": [
            {"source_start": 0, "source_end": 1, "meaning_en": "he", "form_steps": [],
             "grammar_links": [], "expression_links": []}]},
    }
    for index, representation in enumerate(("japanese-annotation", "korean-flat", "korean-v4")):
        scoped_candidate = scoped_candidates[representation]
        meaning_target = _target("set_field", "/segments/0/meaning_en")
        scoped_runner = PlannedRunner(tmp_path / str(index), _plan((meaning_target,)), {
            "base_digest": candidate_digest(scoped_candidate), "edits": [
                {"op": "set_field", "path": meaning_target["path"], "value": "updated meaning"}]})
        scoped_result = await repair_annotation(
            Harness(tmp_path / str(index), scoped_runner), f"scope-{index}", scoped_candidate,
            [{"problem": "Correct the occurrence gloss."}], representation=representation,
            language={"japanese-annotation": "ja", "korean-flat": "ko", "korean-v4": "ko"}[representation],
            validate_candidate=lambda _: None)
        assert scoped_result["status"] == "applied"
        assert scoped_runner.calls[0][4]["workspace_context"]["construction_occurrence_contract"] == \
            _construction_occurrence_contract(representation)
        assert "do not use the ending's grammar identity as the lexical root" in scoped_runner.calls[0][1]
        assert "sole support for a direct grammar link" in scoped_runner.calls[0][1]
        assert "Include that affected occurrence row in the SAME issue's plan" in scoped_runner.calls[0][1]
        for call in scoped_runner.calls:
            assert "valency-frame gloss" in call[1]
            assert "leave correct contrasting occurrences unchanged" in call[1]
            assert "Do not copy reviewer objections" in call[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("has_complete_overlay", [False, True])
async def test_removing_sole_form_step_plans_its_direct_link_dependency(tmp_path, has_complete_overlay):
    """The Korean gate rejects an orphaned direct link and accepts scoped closure."""
    from pipeline import korean_contracts, korean_dictionary
    from pipeline.korean_agent_harness import validate_annotation_chunk

    segments = [
        {"text": "홍길동", "type": "word", "meaning_en": "Hong Gildong", "lemma": "홍길동",
         "lexical_kind": "proper_name", "lexical_id": "hong-gildong", "story_importance_en": "",
         "form_steps": []},
        {"text": "은", "type": "word", "meaning_en": "topic marker", "lemma": "은",
         "lexical_kind": "grammar", "lexical_id": "topic-eun-neun", "story_importance_en": "",
         "form_steps": []},
        {"text": " ", "type": "punctuation", "meaning_en": "", "lemma": "",
         "lexical_kind": "", "lexical_id": "", "story_importance_en": "", "form_steps": []},
        {"text": "아들이었다", "type": "word", "meaning_en": "was a son", "lemma": "아들",
         "lexical_kind": "vocabulary", "lexical_id": "아들/명", "story_importance_en": "",
         "form_steps": [
             {"form": "아들이다", "reading": "아들+이다", "label": "copula",
              "meaning_en": "is a son", "grammar_entry_ids": ["copula-ida"]},
             {"form": "아들이었다", "reading": "아들+이었+다", "label": "past copula",
              "meaning_en": "was a son", "grammar_entry_ids": ["past-copula-ieot"]},
         ]},
        {"text": ".", "type": "punctuation", "meaning_en": "", "lemma": "",
         "lexical_kind": "", "lexical_id": "", "story_importance_en": "", "form_steps": []},
    ]
    direct = {"segment_index": 3, "entry_id": "copula-ida",
        "context_en": "The copula connects the noun to its identity.", "display_form": "", "display_meaning_en": "",
        "display_end_segment_index": -1}
    topic = {"segment_index": 1, "entry_id": "topic-eun-neun",
        "context_en": "Sets up Hong Gildong as the topic.", "display_form": "",
        "display_meaning_en": "", "display_end_segment_index": -1}
    past = {"segment_index": 3, "entry_id": "past-copula-ieot",
        "context_en": "Marks the copular state as past.", "display_form": "",
        "display_meaning_en": "", "display_end_segment_index": -1}
    overlay = {"segment_index": 0, "entry_id": "copula-ida",
        "context_en": "The copula links the name to the predicate.",
        "display_form": "홍길동은 아들이었다", "display_meaning_en": "Hong Gildong was a son",
        "display_end_segment_index": 3}
    candidate = {"segments": segments, "grammar_links": [topic, direct, past] +
        ([overlay] if has_complete_overlay else []),
        "inflected_segment_indices": [3], "expression_links": []}
    original_text = "".join(row["text"] for row in segments)
    gate_args = {
        "words": korean_dictionary._registry(korean_dictionary.WORDS),
        "catalog": korean_contracts.lexical_catalog(),
        "focus": {"entries": [{"id": "hong-gildong", "headword": "홍길동",
            "kind": "proper_name", "aliases": ["홍길동"], "role_en": "central character"}]},
        "title": "Fixture", "number": 1, "plan": {"beats": []}, "level": 4,
        "run_dir": tmp_path, "source_id": "korean-direct-link-dependency",
    }

    def real_gate(value):
        assert "".join(row["text"] for row in value["segments"]) == original_text
        validate_annotation_chunk(value, original_text, **gate_args)

    real_gate(candidate)

    remove_stage = _target("remove_row", "/segments/3/form_steps/0")
    remove_direct = _target("remove_row", "/grammar_links/1")
    append_overlay = _target("append_row", "/grammar_links")
    targets = [remove_stage, remove_direct] + ([] if has_complete_overlay else [append_overlay])

    # Removing the stage alone leaves its direct lesson unsupported; the real
    # Korean candidate gate must reject that candidate before any repair plan is applied.
    orphaned = apply_edits(candidate, {"base_digest": candidate_digest(candidate), "edits": [
        {"op": "remove_row", "path": remove_stage["path"]}]},
        allowed_targets=[remove_stage], representation="korean-flat")
    with pytest.raises(ValueError, match="Korean construction stage lacks complete form at segment 3"):
        real_gate(orphaned)

    edits = [{"op": "remove_row", "path": remove_stage["path"]},
             {"op": "remove_row", "path": remove_direct["path"]}]
    if not has_complete_overlay:
        edits.append({"op": "append_row", "path": append_overlay["path"], "value": overlay})
    runner = PlannedRunner(tmp_path, _plan(targets), {
        "base_digest": candidate_digest(candidate), "edits": edits})
    assert runner.plan["issues"][0]["targets"] == targets

    def check_dependency(value):
        if value["segments"][3]["form_steps"] != segments[3]["form_steps"][1:]:
            raise ValueError("unrelated complete stages were not preserved")
        if any(row["segment_index"] == 3 and row["entry_id"] == "copula-ida"
               for row in value["grammar_links"]):
            raise ValueError("unsupported direct grammar link was left behind")
        expected = [topic, past, overlay]
        if value["grammar_links"] != expected:
            raise ValueError("the full overlay dependency was lost or duplicated")

    result = await repair_annotation(Harness(tmp_path, runner), "close-form-link-dependency",
        candidate, [{"problem": "The prefinal form step must be omitted; retain the supported honorific analysis."}],
        representation="korean-flat", language="ko", validate_candidate=lambda value: (
            check_dependency(value), real_gate(value)))
    assert result["status"] == "applied"
    allowed_targets = runner.calls[1][4]["workspace_context"]["allowed_targets"]
    assert (append_overlay not in allowed_targets) is has_complete_overlay
    prompt = runner.calls[0][1]
    assert "If that step is the sole support for a direct grammar link" in prompt
    assert "remove the unsupported direct row and append a corrected source-exact complete overlay" in prompt
    assert "remove it only when another retained complete occurrence already explains" in prompt


def test_saved_korean_name_plus_predication_pattern_passes_actual_gate_but_grammar_root_fails(tmp_path):
    """Regression for K4's source-exact 길동이었다 name/predicate construction."""
    from pipeline import korean_contracts, korean_dictionary
    from pipeline.korean_agent_harness import validate_annotation_chunk

    candidate = {
        "segments": [
            {"text": "길동", "type": "word", "meaning_en": "Gildong", "lemma": "홍길동",
             "lexical_kind": "proper_name", "lexical_id": "hong-gildong",
             "story_importance_en": "", "form_steps": []},
            {"text": "이었다", "type": "word", "meaning_en": "was", "lemma": "이다",
             "lexical_kind": "grammar", "lexical_id": "copula-ida",
             "story_importance_en": "", "form_steps": []},
        ],
        "grammar_links": [
            {"segment_index": 0, "entry_id": "past-ass-eoss", "context_en": "past predicate",
             "display_form": "길동이었다", "display_meaning_en": "was Gildong",
             "display_end_segment_index": 1},
            {"segment_index": 1, "entry_id": "copula-ida", "context_en": "direct copular lesson",
             "display_form": "", "display_meaning_en": "", "display_end_segment_index": -1},
        ],
        "inflected_segment_indices": [], "expression_links": [],
    }
    gate_args = {
        "words": korean_dictionary._registry(korean_dictionary.WORDS),
        "catalog": korean_contracts.lexical_catalog(),
        "focus": {"entries": [{"id": "hong-gildong", "headword": "홍길동",
            "kind": "proper_name", "aliases": ["홍길동", "길동"], "role_en": "central child"}]},
        "title": "Fixture", "number": 1, "plan": {"beats": []}, "level": 4,
        "run_dir": tmp_path, "source_id": "k4-repair-fixture",
    }
    validate_annotation_chunk(candidate, "길동이었다", **gate_args)

    grammar_root = json.loads(json.dumps(candidate, ensure_ascii=False))
    grammar_root["segments"][1]["form_steps"] = [{
        "form": "이었다", "reading": "이-+-었-+-다", "label": "past predicate",
        "meaning_en": "was", "grammar_entry_ids": ["copula-ida"],
    }]
    grammar_root["inflected_segment_indices"] = [1]
    with pytest.raises(ValueError, match="needs an attested lexical base.*not a grammar identity"):
        validate_annotation_chunk(grammar_root, "길동이었다", **gate_args)


@pytest.mark.asyncio
async def test_korean_repair_plan_receives_exact_per_step_identity_cardinality(tmp_path):
    candidate = {"segments": [{"text": "가요", "type": "word", "meaning_en": "go",
        "lemma": "가다", "lexical_kind": "vocabulary", "lexical_id": "가다01/동",
        "story_importance_en": "", "form_steps": [{"form": "가요", "reading": "가요",
            "label": "polite", "meaning_en": "go politely", "grammar_entry_ids": ["old"]}]}],
        "grammar_links": [], "inflected_segment_indices": [0], "expression_links": []}
    path = "/segments/0/form_steps/0/grammar_entry_ids"
    plan = _plan((_target("replace_list", path),))
    patch = {"base_digest": candidate_digest(candidate), "edits": [
        {"op": "replace_list", "path": path, "value": ["polite-yo"]}]}
    runner = PlannedRunner(tmp_path, plan, patch)
    result = await repair_annotation(Harness(tmp_path, runner), "korean-step-contract", candidate,
        [{"problem": "Use one lesson identity for this form stage."}],
        representation="korean-flat", language="ko", validate_candidate=lambda _value: None)
    assert result["status"] == "applied"
    workspace_context = runner.calls[0][4]["workspace_context"]
    assert workspace_context["representation_structure_contract"] == {
        "source": "pipeline.korean_contracts.STEP",
        "path": "/segments/*/form_steps/*/grammar_entry_ids",
        "min_items": 1, "max_items": 1,
        "meaning": "The constraint applies within each form step, not across the ordered form-step chain.",
    }
    assert "never put several identities on one step" in runner.calls[0][1]
    assert "separate ordered transformations as separate complete-form steps" in runner.calls[0][1]


@pytest.mark.asyncio
async def test_source_grounded_grammar_overlay_can_be_replaced_without_changing_primary_taps(tmp_path):
    from pipeline.annotation_edits import ImmutableFieldError

    candidate = {
        "segments": [
            {"text": "걱정하지", "type": "word", "meaning_en": "worry", "lemma": "걱정하다",
             "lexical_kind": "vocabulary", "lexical_id": "걱정하다01/동", "story_importance_en": "",
             "form_steps": []},
            {"text": " ", "type": "punctuation", "meaning_en": "", "lemma": "", "lexical_kind": "",
             "lexical_id": "", "story_importance_en": "", "form_steps": []},
            {"text": "마시고", "type": "word", "meaning_en": "please do not worry, and", "lemma": "말다",
             "lexical_kind": "vocabulary", "lexical_id": "말다03/보", "story_importance_en": "",
             "form_steps": []},
        ],
        "grammar_links": [{"segment_index": 2, "entry_id": "prohibition", "context_en": "prohibition",
            "display_form": "", "display_meaning_en": "", "display_end_segment_index": -1}],
        "inflected_segment_indices": [], "expression_links": [],
    }
    original_segments = json.loads(json.dumps(candidate["segments"], ensure_ascii=False))
    old_target = _target("remove_row", "/grammar_links/0")
    new_target = _target("append_row", "/grammar_links")
    full_span = {"segment_index": 0, "entry_id": "prohibition", "context_en": "negative request",
        "display_form": "걱정하지 마시고", "display_meaning_en": "please do not worry",
        "display_end_segment_index": 2}
    runner = PlannedRunner(tmp_path, _plan((old_target, new_target)), {
        "base_digest": candidate_digest(candidate),
        "edits": [{"op": "remove_row", "path": old_target["path"]},
                  {"op": "append_row", "path": new_target["path"], "value": full_span}],
    })

    def validate_overlay(value):
        if (value["segments"] != original_segments or value["grammar_links"] != [full_span]
                or "".join(row["text"] for row in value["segments"]) != "걱정하지 마시고"):
            raise ValueError("the expected full-span construction overlay was not preserved")

    result = await repair_annotation(Harness(tmp_path, runner), "korean-overlay-span", candidate,
        [{"problem": "Replace the direct lesson link with the supported full construction occurrence."}],
        representation="korean-flat", language="ko", validate_candidate=validate_overlay)
    assert result["status"] == "applied"
    assert "semantic overlay replacement, not source resegmentation" in runner.calls[0][1]
    assert "primary source range/index" in runner.calls[0][1]
    assert "grammar-overlay start/end and text/surface/component spans" in runner.calls[0][1]
    # This fixture verifies shared edit/replay plumbing only; it does not approve
    # the illustrative English wording. Real language validation remains caller-owned.

    wrong_span = {**full_span, "segment_index": 2, "display_form": "마시고"}
    wrong_runner = PlannedRunner(tmp_path / "wrong-span", _plan((old_target, new_target)), {
        "base_digest": candidate_digest(candidate),
        "edits": [{"op": "remove_row", "path": old_target["path"]},
                  {"op": "append_row", "path": new_target["path"], "value": wrong_span}],
    })
    rejected = await repair_annotation(Harness(tmp_path / "wrong-span", wrong_runner), "korean-overlay-span",
        candidate, [{"problem": "The construction must cover the complete supported source span."}],
        representation="korean-flat", language="ko", validate_candidate=validate_overlay)
    assert rejected["status"] == "patch_rejected"
    assert rejected["candidate"] == candidate

    with pytest.raises(ImmutableFieldError):
        apply_edits(candidate, {"base_digest": candidate_digest(candidate), "edits": [
            {"op": "set_field", "path": "/grammar_links/0/display_form", "value": "걱정하지 마시고"}]},
            allowed_targets=[_target("set_field", "/grammar_links/0/display_form")],
            representation="korean-flat")


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
    assert runner.calls[0][4]["workspace_context"]["representation_structure_contract"] is None
    assert "without importing another language's cardinality" in runner.calls[0][1]
    assert "grammar-overlay start/end and text/surface/component spans" in runner.calls[0][1]
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
    assert inspect_applied_repair_coverage(tmp_path, "repair_assembly") is None
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
    coverage = inspect_applied_repair_coverage(tmp_path, "legacy_assembly")
    assert coverage is not None
    assert coverage["candidate"] == result["candidate"]
    assert coverage["unresolved_issues"][0]["issue"] == {"problem": "meaning"}
    assert coverage["unresolved_issues"][0]["uncovered_targets"] == [{
        "op": "set_field", "path": "/segments/0/form_steps",
        "diagnostic": "planned target had no effective patch change for issue 0: set_field /segments/0/form_steps"}]


@pytest.mark.asyncio
async def test_historical_assembly_inspection_routes_omitted_valid_removal_and_ignores_complete_repair(tmp_path):
    candidate = {"segments": [{"text": "가", "meaning_en": "old", "form_steps": []}],
        "grammar_links": [{"segment_index": 0, "display_end_segment_index": 0,
            "display_form": "가", "entry_id": "wrong", "context_en": "stale"}],
        "expression_links": [], "inflected_segment_indices": []}
    issue = {"problem": "Replace the inappropriate grammar occurrence."}
    plan = _plan((_target("set_field", "/segments/0/meaning_en"),
                  _target("remove_row", "/grammar_links/0")))
    targets = [*plan["issues"][0]["targets"]]

    async def create_legacy(name, *, remove):
        edits = [{"op": "set_field", "path": "/segments/0/meaning_en", "value": "revised"}]
        if remove:
            edits.append({"op": "remove_row", "path": "/grammar_links/0"})
        patch = {"base_digest": candidate_digest(candidate), "edits": edits}
        runner = PlannedRunner(tmp_path, plan, patch)
        result = await repair_annotation(Harness(tmp_path, runner), name, candidate, [issue],
            representation="korean-flat", language="ko", validate_candidate=lambda _value: None)
        assembly_dir = tmp_path / "agents" / f"{name}_assembly"
        meta_path, result_path = assembly_dir / "meta.json", assembly_dir / "result.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not remove:
            # Recreate the actual legacy artifact: it was historically marked
            # applied despite omitting the diagnosed row removal.
            applied = apply_edits(candidate, patch, allowed_targets=targets,
                                  representation="korean-flat")
            meta["status"] = "applied"
            meta["result_digest"] = candidate_digest(applied)
            meta.pop("patch_error", None)
            result_path.write_text(json.dumps(applied, ensure_ascii=False), encoding="utf-8")
        meta.pop("target_contract_version", None)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return result

    await create_legacy("omitted-link-removal", remove=False)
    incomplete = inspect_applied_repair_coverage(tmp_path, "omitted-link-removal_assembly")
    assert incomplete is not None
    assert incomplete["candidate"]["segments"][0]["meaning_en"] == "revised"
    assert incomplete["candidate"]["grammar_links"][0]["entry_id"] == "wrong"
    assert incomplete["unresolved_issues"] == [{
        "issue_index": 0, "issue": issue,
        "plan_reason": "issue 0 semantic target",
        "uncovered_targets": [{"op": "remove_row", "path": "/grammar_links/0",
            "diagnostic": "planned target had no effective patch change for issue 0: remove_row /grammar_links/0"}]}]
    meta_path = tmp_path / "agents" / "omitted-link-removal_assembly" / "meta.json"
    current_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    current_meta["target_contract_version"] = 1
    meta_path.write_text(json.dumps(current_meta), encoding="utf-8")
    with pytest.raises(EditCoverageError, match="remove_row /grammar_links/0"):
        inspect_applied_repair_coverage(tmp_path, "omitted-link-removal_assembly")
    current_meta.pop("target_contract_version")
    meta_path.write_text(json.dumps(current_meta), encoding="utf-8")

    await create_legacy("complete-link-removal", remove=True)
    assert inspect_applied_repair_coverage(tmp_path, "complete-link-removal_assembly") is None


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
    for call in runner.calls:
        assert "Apply the same complete-form and meaning-scope criteria" in call[1]
        assert "valency-frame" in call[1]
    for plan_call in (runner.calls[0], runner.calls[2]):
        assert "Inspect the supplied grammar knowledge and identity policy" in plan_call[1]
        assert "reuse an exact approved entry when it fits" in plan_call[1]
        assert "never invent a lexical ID or lesson" in plan_call[1]
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
