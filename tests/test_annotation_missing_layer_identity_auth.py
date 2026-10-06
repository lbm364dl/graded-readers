"""Portable synthetic regressions for authenticated missing-layer identity authority."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import validate

def _project_root():
    for parent in Path(__file__).resolve().parents:
        if (parent / "pipeline" / "schemas").is_dir():
            return parent
    raise RuntimeError("test is not under a repository with pipeline schemas")

ROOT = _project_root()

from pipeline.annotation_edits import candidate_digest
from pipeline.annotation_issue_targets import issue_target_paths, missing_layer_review_schema, validate_issue_targets
from pipeline.annotation_missing_layer import identity_bindings
from pipeline.annotation_repairs import repair_annotation, replay_annotation_repair
from tests.test_annotation_run_lessons import overlay_fixture


def _row(op, path):
    return {"op": op, "path": path}


class _Args:
    annotation_repair_effort = "low"
    refresh = False


class _Harness:
    def __init__(self, run_dir, runner):
        self.run_dir = run_dir
        self.runner = runner
        self.args = _Args()


class _Runner:
    def __init__(self, run_dir, plan, patch):
        self.run_dir, self.plan, self.patch = run_dir, plan, patch
        self.calls = []

    async def call(self, job, prompt, schema, effort, **kwargs):
        self.calls.append(job)
        from pipeline.worker_workspace import build, check
        workspace = self.run_dir / "agents" / job / "workspace"
        build(workspace, prompt, json.loads(Path(schema).read_text()),
              context=kwargs.get("workspace_context"))
        result = self.plan if job.endswith("_plan") else self.patch
        candidate = workspace / "candidate.json"
        candidate.write_text(json.dumps(result, ensure_ascii=False))
        check(workspace, candidate)
        job_dir = self.run_dir / "agents" / job
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False))
        (job_dir / "meta.json").write_text(json.dumps({"return_code": 0, "tool_profile": "workspace"}))
        return result


def _fixtures():
    approved_id = "fixture-intention"
    zh = {
        "representation": "chinese-annotation", "language": "zh", "source": "想走",
        "candidate": {"segments": [
            {"text": "想", "type": "word", "pinyin": "xiǎng", "meaning_en": "want"},
            {"text": "走", "type": "word", "pinyin": "zǒu", "meaning_en": "go"}],
            "grammar_overlays": [{"start": 1, "end": 2, "text": "走",
                "grammar_candidate_key": "intent-key", "pattern": "fixture",
                "meaning_en": "existing fixture row"}]},
        "collection": "/grammar_overlays", "identity": "intent-key",
        "knowledge": {"catalog_status": "provisional_keys_only", "catalog_source": None,
            "approved_entries": [], "candidate_keys_in_base": ["intent-key"],
            "identity_policy": "Provisional fixture key."},
        "row": {"start": 0, "end": 2, "text": "想走", "grammar_candidate_key": "intent-key",
            "pattern": "fixture construction", "meaning_en": "intend to go"},
        "schema": "annotation.schema.json", "anchor": "/segments/0/meaning_en",
        "interval": {"start": 0, "end": 2, "surface": "想走"},
    }
    ja = {
        "representation": "japanese-annotation", "language": "ja", "source": "歩く",
        "candidate": {"segments": [{"surface": "歩く", "type": "word", "lemma": "歩く",
            "surface_kana": "あるく", "lemma_kana": "あるく", "part_of_speech": "verb",
            "conjugation_form": "dictionary", "meaning_en": "walk", "grammar_candidate_key": "",
            "dictionary_key": "歩く", "dictionary_definition_en": "to walk", "form_steps": [],
            "story_role": "none", "story_importance_en": ""}], "grammar_overlays": []},
        "collection": "/grammar_overlays", "identity": approved_id,
        "knowledge": {"catalog_status": "reviewed", "catalog_source": "fixture",
            "approved_entries": [{"id": approved_id, "pattern": "fixture"}],
            "identity_policy": "Reuse supplied approved entry."},
        "row": {"start": 0, "end": 2, "surface": "歩く", "grammar_candidate_key": approved_id,
            "pattern": "fixture", "meaning_en": "walk intending to act",
            "head_lemma": "歩く", "head_lemma_kana": "あるく", "form_label": "fixture",
            "explanation_en": "fixture construction", "components": [
                {"start": 0, "end": 1, "surface": "歩", "lemma": "歩く", "lemma_kana": "あるく",
                 "function_en": "verb stem", "lookup_kind": "none", "dictionary_key": "",
                 "dictionary_definition_en": ""},
                {"start": 1, "end": 2, "surface": "く", "lemma": "歩く", "lemma_kana": "あるく",
                 "function_en": "ending", "lookup_kind": "none", "dictionary_key": "",
                 "dictionary_definition_en": ""}]},
        "schema": "japanese-annotation.schema.json", "anchor": "/segments/0/meaning_en",
        "interval": {"start": 0, "end": 2, "surface": "歩く"},
    }
    ko = {
        "representation": "korean-flat", "language": "ko", "source": "가며",
        "candidate": {"segments": [
            {"text": "가", "type": "word", "meaning_en": "go", "lemma": "가다",
             "lexical_kind": "vocabulary", "lexical_id": "가다/동", "story_importance_en": "", "form_steps": []},
            {"text": "며", "type": "word", "meaning_en": "and", "lemma": "며",
             "lexical_kind": "grammar", "lexical_id": "fixture-me", "story_importance_en": "", "form_steps": []}],
            "grammar_links": [], "expression_links": [], "inflected_segment_indices": []},
        "collection": "/grammar_links", "identity": approved_id,
        "knowledge": {"catalog_status": "reviewed", "catalog_source": "fixture",
            "approved_entries": [{"id": approved_id, "pattern": "fixture"}],
            "identity_policy": "Reuse supplied approved entry."},
        "row": {"segment_index": 0, "entry_id": approved_id, "context_en": "fixture role",
            "display_form": "가며", "display_meaning_en": "while going", "display_end_segment_index": 1},
        "schema": "korean-annotation.schema.json", "anchor": "/segments/0/meaning_en",
        "interval": {"start": 0, "end": 2, "surface": "가며"},
    }
    kv4 = {
        "representation": "korean-v4", "language": "ko", "source": "가며",
        "candidate": {"format": "source-span-links-annotation-v4", "segments": [
            {"type": "word", "meaning_en": "go", "lemma": "가다", "lexical_kind": "vocabulary",
             "lexical_id": "가다/동", "story_importance_en": "", "form_steps": [], "is_inflected": False,
             "source_start": 0, "source_end": 1, "grammar_links": [], "expression_links": []},
            {"type": "word", "meaning_en": "and", "lemma": "며", "lexical_kind": "grammar",
             "lexical_id": "fixture-me", "story_importance_en": "", "form_steps": [], "is_inflected": False,
             "source_start": 1, "source_end": 2, "grammar_links": [], "expression_links": []}]},
        "collection": "/segments/0/grammar_links", "identity": approved_id,
        "knowledge": {"catalog_status": "reviewed", "catalog_source": "fixture",
            "approved_entries": [{"id": approved_id, "pattern": "fixture"}],
            "identity_policy": "Reuse supplied approved entry."},
        "row": {"entry_id": approved_id, "context_en": "fixture role", "display_meaning_en": "while going",
            "source_start": 0, "source_end": 2},
        "schema": "korean-chunk-annotation-v4.schema.json", "anchor": "/segments/0/meaning_en",
        "interval": {"start": 0, "end": 2, "surface": "가며"},
    }
    return [zh, ja, ko, kv4]


def _issue(fixture):
    binding = identity_bindings(fixture["knowledge"])[fixture["identity"]]
    return {"problem": "grammar",
        "start": fixture["interval"]["start"], "end": fixture["interval"]["end"],
        "segment_text": fixture["interval"]["surface"],
        "explanation": "A reviewed grammar occurrence layer is missing.", "suggested_fix": "Append the bound row.",
        "candidate_paths": [fixture["anchor"]], "supporting_paths": [],
        "missing_layer": {"version": 1, "anchor_paths": [fixture["anchor"]],
            "collection_path": fixture["collection"], "identity": fixture["identity"],
            "identity_status": binding["status"], "identity_digest": binding["digest"],
            "source_interval": dict(fixture["interval"])} }


def _schema(fixture):
    return json.loads((ROOT / "pipeline" / "schemas" / fixture["schema"]).read_text())


def _review_schema_and_value(fixture, issue):
    if fixture['representation'] == 'chinese-annotation':
        schema_path = ROOT / 'pipeline/schemas/annotation-review-targets.schema.json'
        schema = json.loads(schema_path.read_text())
        return missing_layer_review_schema(schema, policy_version=1), {'verdict': 'revise', 'issues': [issue]}
    if fixture['representation'] == 'japanese-annotation':
        schema_path = ROOT / 'pipeline/schemas/japanese-annotation-review-targets.schema.json'
        schema = json.loads(schema_path.read_text())
        japanese_issue = {key: value for key, value in issue.items() if key not in {'start', 'end'}}
        return missing_layer_review_schema(schema, policy_version=1), {'verdict': 'revise', 'issues': [japanese_issue]}
    from pipeline.korean_contracts import CHUNK_REVIEW_TARGETED
    schema = missing_layer_review_schema(CHUNK_REVIEW_TARGETED, policy_version=1)
    typed_issue = {key: issue[key] for key in ('candidate_paths', 'supporting_paths', 'missing_layer')}
    typed_issue['explanation'] = issue['explanation']
    return schema, {'approved': False, 'issues': [typed_issue], 'prose_revision_reason_en': ''}


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture", _fixtures(), ids=lambda f: f["representation"])
async def test_typed_review_to_append_patch_fresh_review_and_replay(tmp_path, fixture):
    before = copy.deepcopy(fixture["candidate"])
    schema = _schema(fixture)
    validate(before, schema)
    issue = _issue(fixture)
    review_schema, review_value = _review_schema_and_value(fixture, issue)
    validate(review_value, review_schema)
    validated_review_issue = review_value['issues'][0]
    validate_issue_targets(review_value, before,
        source_text=fixture["source"], representation=fixture["representation"], require_typed=True,
        missing_layer_policy_version=1, grammar_knowledge=fixture["knowledge"])

    plan = {"issues": [{"issue_index": 0, "reason": "Append exact supported occurrence row.",
        "targets": [_row("append_row", fixture["collection"])],
        "boundary_change_needed": False, "boundary_reason": ""}]}
    patch = {"base_digest": candidate_digest(before), "edits": [
        {"op": "append_row", "path": fixture["collection"], "value": fixture["row"]}]}
    runner = _Runner(tmp_path, plan, patch)
    # This is a mock-worker integration test: retain real workspace request and
    # authority checks, while stubbing the language-specific full candidate
    # gate. The host callback below still validates the actual schema.
    import pipeline.worker_workspace as workspace_module
    from pipeline.annotation_edits import apply_edits
    from pipeline.annotation_repair_authority import validate_derived_effects
    def local_gate_stub(workspace, submitted_patch, patch_context):
        derived = apply_edits(patch_context['base_candidate'], submitted_patch,
            allowed_targets=patch_context['allowed_targets'], representation=patch_context['representation'])
        validate_derived_effects(patch_context['base_candidate'], derived, patch_context['repair_plan'],
            patch_context['repair_target_authority'], submitted_patch['edits'])
        validate(derived, schema)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(workspace_module, '_check_annotation_patch', local_gate_stub)
    def full_gate(value):
        validate(value, schema)
        if fixture["representation"] == "korean-v4":
            assert len(value["segments"]) == len(before["segments"])
            assert [(r['source_start'], r['source_end']) for r in value['segments']] == [
                (r['source_start'], r['source_end']) for r in before['segments']]
        else:
            assert value["segments"] == before["segments"]
    context = {"chunk_text": fixture["source"], "grammar_knowledge": fixture["knowledge"],
        "missing_layer_authority_policy_version": 1,
        "candidate_gate": {"focus": {}, "title": "Fixture", "number": 1,
            "plan": {}, "level": 1, "source_id": "fixture"}}
    try:
        result = await repair_annotation(_Harness(tmp_path, runner), "missing-layer-fixture",
            before, [validated_review_issue], representation=fixture["representation"], language=fixture["language"],
            context=context, validate_candidate=full_gate)
    finally:
        monkeypatch.undo()
    assert result["status"] == "applied"
    assert runner.calls == ["missing-layer-fixture_plan", "missing-layer-fixture_patch"]
    if fixture["representation"] == "korean-v4":
        assert [(r['source_start'], r['source_end']) for r in result['candidate']['segments']] == [
            (r['source_start'], r['source_end']) for r in before['segments']]
    else:
        assert result["candidate"]["segments"] == before["segments"]

    # A fresh independent reviewer mock accepts the derived candidate; no old
    # review is reused. The empty review follows the ordinary typed host gate.
    fresh = ({"approved": True, "issues": [], "prose_revision_reason_en": ""}
             if fixture['representation'].startswith('korean-')
             else {"verdict": "pass", "issues": []})
    validate(fresh, review_schema)
    validate_issue_targets(fresh, result["candidate"], source_text=fixture["source"],
        representation=fixture["representation"], require_typed=True)
    replay = replay_annotation_repair(tmp_path, "missing-layer-fixture_assembly", validate_candidate=full_gate)
    assert replay["candidate"] == result["candidate"]


def test_missing_layer_policy_rejects_wrong_identity_range_duplicate_and_collateral_edits():
    fixture = _fixtures()[2]
    before = copy.deepcopy(fixture["candidate"])
    issue = _issue(fixture)
    from pipeline.annotation_repair_authority import build_authority_packet, validate_derived_effects, validate_plan_authority
    from pipeline.annotation_edits import apply_edits
    from pipeline.annotation_repair_dependencies import repair_dependency_constraints
    from pipeline.annotation_repairs import _validate_plan
    packet = build_authority_packet([issue], before, fixture["representation"], candidate_digest(before),
        missing_layer_policy_version=1, source_text=fixture["source"], grammar_knowledge=fixture["knowledge"])
    plan = {"issues": [{"issue_index": 0, "reason": "append", "targets": [_row("append_row", "/grammar_links")],
        "boundary_change_needed": False, "boundary_reason": ""}]}
    _validate_plan(plan, 1, before, fixture["representation"], target_contract_version=3,
        dependency_constraints=repair_dependency_constraints(before, fixture["representation"]), target_authority=packet)
    for wrong in (
        {**fixture["row"], "entry_id": "foreign-identity"},
        {**fixture["row"], "display_end_segment_index": 0, "display_form": "가"},
    ):
        edits = [{"op": "append_row", "path": "/grammar_links", "value": wrong}]
        after = apply_edits(before, {"base_digest": candidate_digest(before), "edits": edits},
            allowed_targets=[_row("append_row", "/grammar_links")], representation=fixture["representation"])
        with pytest.raises(ValueError):
            validate_derived_effects(before, after, plan, packet, edits)

    duplicate_before = copy.deepcopy(before)
    duplicate_before["grammar_links"].append(copy.deepcopy(fixture["row"]))
    with pytest.raises(ValueError, match="already exists|duplicate"):
        issue_target_paths(issue, duplicate_before, source_text=fixture["source"],
            representation=fixture["representation"], missing_layer_policy_version=1,
            grammar_knowledge=fixture["knowledge"])

    # Rewriting/removing a sibling or altering a primary tap cannot be hidden
    # alongside the appended target: the patch schema/authority allows one edit.
    bad_plan = copy.deepcopy(plan)
    bad_plan["issues"][0]["targets"].append(_row("remove_row", "/grammar_links/0"))
    with pytest.raises(ValueError, match="exceeds authenticated target authority"):
        validate_plan_authority(bad_plan, packet)

    wrong_identity = copy.deepcopy(issue)
    wrong_identity['missing_layer']['identity'] = 'unbound-grammar-id'
    with pytest.raises(ValueError, match='not bound to host grammar knowledge'):
        issue_target_paths(wrong_identity, before, source_text=fixture['source'],
            representation=fixture['representation'], missing_layer_policy_version=1,
            grammar_knowledge=fixture['knowledge'])
    wrong_interval = copy.deepcopy(issue)
    wrong_interval['missing_layer']['source_interval'] = {'start': 1, 'end': 2, 'surface': '며'}
    with pytest.raises(ValueError, match='detached from its exact diagnostic anchors'):
        issue_target_paths(wrong_interval, before, source_text=fixture['source'],
            representation=fixture['representation'], missing_layer_policy_version=1,
            grammar_knowledge=fixture['knowledge'])

    # Exact edit replay prevents changing an existing sibling or source tap
    # while presenting an append-only patch.
    valid_edits = [{'op': 'append_row', 'path': '/grammar_links', 'value': fixture['row']}]
    valid_after = apply_edits(before, {'base_digest': candidate_digest(before), 'edits': valid_edits},
        allowed_targets=[_row('append_row', '/grammar_links')], representation=fixture['representation'])
    collateral = copy.deepcopy(valid_after)
    collateral['segments'][0]['text'] = '다'
    with pytest.raises(ValueError, match='primary source taps|exact base-bound authorized edits'):
        validate_derived_effects(before, collateral, plan, packet, valid_edits)


def test_missing_layer_append_can_coexist_with_a_separately_authorized_semantic_field_edit():
    fixture = _fixtures()[2]
    before = copy.deepcopy(fixture['candidate'])
    append_issue = _issue(fixture)
    field_issue = {'issue_id': 'correct-meaning', 'paths': ['/segments/0/meaning_en'],
        'target_dispositions': [{'path': '/segments/0/meaning_en', 'disposition': 'actionable'}]}
    from pipeline.annotation_repair_authority import build_authority_packet, validate_derived_effects
    from pipeline.annotation_edits import apply_edits
    from pipeline.annotation_repair_dependencies import repair_dependency_constraints
    from pipeline.annotation_repairs import _validate_plan
    packet = build_authority_packet([append_issue, field_issue], before, fixture['representation'],
        candidate_digest(before), missing_layer_policy_version=1, source_text=fixture['source'],
        grammar_knowledge=fixture['knowledge'])
    plan = {'issues': [
        {'issue_index': 0, 'reason': 'Add the exact missing occurrence.',
         'targets': [_row('append_row', '/grammar_links')], 'boundary_change_needed': False, 'boundary_reason': ''},
        {'issue_index': 1, 'reason': 'Correct a separate supported meaning field.',
         'targets': [_row('set_field', '/segments/0/meaning_en')], 'boundary_change_needed': False, 'boundary_reason': ''},
    ]}
    _validate_plan(plan, 2, before, fixture['representation'], target_contract_version=3,
        dependency_constraints=repair_dependency_constraints(before, fixture['representation']),
        target_authority=packet)
    edits = [
        {'op': 'append_row', 'path': '/grammar_links', 'value': fixture['row']},
        {'op': 'set_field', 'path': '/segments/0/meaning_en', 'value': 'revised independently'},
    ]
    after = apply_edits(before, {'base_digest': candidate_digest(before), 'edits': edits},
        allowed_targets=[_row('append_row', '/grammar_links'), _row('set_field', '/segments/0/meaning_en')],
        representation=fixture['representation'])
    validate_derived_effects(before, after, plan, packet, edits)
    assert after['segments'][0]['meaning_en'] == 'revised independently'
    assert after['grammar_links'] == [fixture['row']]


def test_historical_packet_without_missing_layer_policy_stays_historical():
    from pipeline.annotation_repair_authority import build_authority_packet
    candidate = {"segments": [{"text": "她", "type": "word", "pinyin": "tā", "meaning_en": "she"}],
        "grammar_overlays": []}
    issue = {"candidate_paths": ["/segments/0/meaning_en"], "supporting_paths": []}
    packet = build_authority_packet([issue], candidate, "chinese-annotation", candidate_digest(candidate))
    assert "missing_layer_authority_policy_version" not in packet
    assert "missing_layer_authority" not in packet["issues"][0]


def test_korean_v4_local_link_anchor_uses_recorded_multi_tap_geometry():
    fixture = _fixtures()[3]
    fixture["candidate"]["segments"][0]["grammar_links"].append({
        "entry_id": "existing-different-layer", "context_en": "existing range",
        "display_meaning_en": "go and", "source_start": 0, "source_end": 2})
    issue = _issue(fixture)
    issue["candidate_paths"] = ["/segments/0/grammar_links/0/display_meaning_en"]
    issue["supporting_paths"] = []
    issue["missing_layer"]["anchor_paths"] = list(issue["candidate_paths"])
    from pipeline.annotation_missing_layer import validate_declaration
    validated = validate_declaration(issue, fixture["candidate"], "korean-v4",
        fixture["source"], fixture["knowledge"])
    assert validated["anchor_owners"] == [0]
    assert validated["source_interval"] == {"start": 0, "end": 2, "surface": "가며"}

    # The same owner tap cannot lend authority to another recorded link range.
    wrong = copy.deepcopy(issue)
    wrong["missing_layer"]["source_interval"] = {"start": 1, "end": 2, "surface": "며"}
    with pytest.raises(ValueError, match="detached from its exact diagnostic anchors"):
        validate_declaration(wrong, fixture["candidate"], "korean-v4",
            fixture["source"], fixture["knowledge"])


def test_two_missing_layers_append_exact_same_surface_at_distinct_positions():
    fixture = _fixtures()[2]
    before = copy.deepcopy(fixture["candidate"])
    before["segments"] = [copy.deepcopy(before["segments"][0]),
                          copy.deepcopy(before["segments"][1]),
                          copy.deepcopy(before["segments"][0]),
                          copy.deepcopy(before["segments"][1])]
    source = "가며가며"
    issues, rows = [], []
    binding = identity_bindings(fixture["knowledge"])[fixture["identity"]]
    for start, first in ((0, 0), (2, 2)):
        issue = {"problem": "grammar", "start": start, "end": start + 2,
            "segment_text": "가며", "explanation": "An occurrence layer is missing.",
            "suggested_fix": "Append the exact bound row.",
            "candidate_paths": [f"/segments/{first}/meaning_en"], "supporting_paths": [],
            "missing_layer": {"version": 1, "anchor_paths": [f"/segments/{first}/meaning_en"],
                "collection_path": "/grammar_links", "identity": fixture["identity"],
                "identity_status": binding["status"], "identity_digest": binding["digest"],
                "source_interval": {"start": start, "end": start + 2, "surface": "가며"}}}
        issues.append(issue)
        rows.append({"segment_index": first, "display_end_segment_index": first + 1,
            "entry_id": fixture["identity"], "context_en": f"occurrence at {start}",
            "display_form": "가며", "display_meaning_en": "while going"})

    from pipeline.annotation_repair_authority import build_authority_packet, validate_derived_effects
    from pipeline.annotation_edits import apply_edits
    from pipeline.annotation_repair_dependencies import repair_dependency_constraints
    from pipeline.annotation_repairs import _validate_plan
    from pipeline.annotation_edits import candidate_digest
    packet = build_authority_packet(issues, before, "korean-flat", candidate_digest(before),
        missing_layer_policy_version=1, source_text=source, grammar_knowledge=fixture["knowledge"])
    plan = {"issues": [{"issue_index": index, "reason": "Append the bound missing layer.",
        "targets": [_row("append_row", "/grammar_links")], "boundary_change_needed": False,
        "boundary_reason": ""} for index in range(2)]}
    _validate_plan(plan, 2, before, "korean-flat", target_contract_version=3,
        dependency_constraints=repair_dependency_constraints(before, "korean-flat"), target_authority=packet)
    edits = [{"op": "append_row", "path": "/grammar_links", "value": row} for row in rows]
    after = apply_edits(before, {"base_digest": candidate_digest(before), "edits": edits},
        allowed_targets=[_row("append_row", "/grammar_links")], representation="korean-flat")
    validate_derived_effects(before, after, plan, packet, edits)
    assert after["grammar_links"] == rows

    # Same identity and same surface do not let the second declaration borrow
    # the first occurrence's range.
    transplanted = copy.deepcopy(issues[1])
    transplanted["missing_layer"]["source_interval"] = {
        "start": 0, "end": 2, "surface": "가며"}
    with pytest.raises(ValueError, match="detached from its exact diagnostic anchors"):
        issue_target_paths(transplanted, before, source_text=source, representation="korean-flat",
            missing_layer_policy_version=1, grammar_knowledge=fixture["knowledge"])


def test_new_run_identity_requires_authenticated_writer_critic_catalog(overlay_fixture):
    from pipeline.annotation_missing_layer import identity_bindings
    source_run, run_dir, candidate, source, actual_language, representation, context, envelope = overlay_fixture
    language = actual_language
    knowledge = {"catalog_status": "provisional_keys_only", "candidate_keys_in_base": [],
        "approved_entries": [], "reviewed_run_lesson_sources": envelope["lessons"],
        "identity_policy": "Run-reviewed lessons remain provisional pending publication review."}
    bindings = identity_bindings(knowledge, run_dir=run_dir, language=language)
    assert bindings["new-pattern"]["status"] == "reviewed_for_run"
    binding = bindings["new-pattern"]
    issue = {"candidate_paths": ["/segments/0/meaning_en"], "supporting_paths": [],
        "missing_layer": {"version": 1, "anchor_paths": ["/segments/0/meaning_en"],
            "collection_path": "/grammar_overlays", "identity": "new-pattern",
            "identity_status": binding["status"], "identity_digest": binding["digest"],
            "source_interval": {"start": 0, "end": len(source), "surface": source}}}
    validate_issue_targets({"issues": [issue]}, candidate, source_text=source,
        representation=representation, require_typed=True, missing_layer_policy_version=1,
        grammar_knowledge=knowledge, run_dir=run_dir, language=language)
    from pipeline.annotation_edits import candidate_digest
    from pipeline.annotation_repair_authority import build_authority_packet, validate_authority_packet
    packet = build_authority_packet([issue], candidate, representation, candidate_digest(candidate),
        missing_layer_policy_version=1, source_text=source,
        grammar_knowledge=knowledge, run_dir=run_dir)
    validate_authority_packet(packet, [issue], candidate, representation, candidate_digest(candidate),
        source_text=source, grammar_knowledge=knowledge, run_dir=run_dir)
    assert packet["issues"][0]["missing_layer_authority"]["declaration"]["identity_status"] == "reviewed_for_run"

    forged = copy.deepcopy(knowledge)
    forged.pop("reviewed_run_lesson_sources")
    forged["reviewed_function_identities"] = [{"identity": "new-pattern", "evidence_digest": "e" * 64}]
    with pytest.raises(ValueError, match="not an authentication source"):
        identity_bindings(forged, run_dir=run_dir, language=language)


def test_unknown_provisional_identity_is_not_created_from_a_digest():
    from pipeline.annotation_missing_layer import identity_bindings
    knowledge = {"catalog_status": "provisional_keys_only", "candidate_keys_in_base": [],
        "approved_entries": [], "reviewed_function_identities": [
            {"identity": "invented", "evidence_digest": "f" * 64}]}
    with pytest.raises(ValueError, match="not an authentication source"):
        identity_bindings(knowledge, language="zh")
