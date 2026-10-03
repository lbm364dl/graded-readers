from copy import deepcopy

import pytest

from pipeline.annotation_edits import (
    BoundaryChangeNotSupported,
    EditConflictError,
    EditCoverageError,
    EditScopeError,
    ImmutableFieldError,
    InvalidEditError,
    StaleCandidateError,
    apply_edits,
    candidate_digest,
    validate_target_contract,
    validate_issue_target_coverage,
)


def patch_for(candidate, *edits):
    return {"base_digest": candidate_digest(candidate), "edits": list(edits)}


def _japanese_segment(surface, meaning="walk"):
    return {
        "surface": surface, "type": "word", "lemma": "歩く", "surface_kana": "あるいた",
        "lemma_kana": "あるく", "part_of_speech": "verb", "conjugation_form": "past",
        "meaning_en": meaning, "grammar_candidate_key": "", "dictionary_key": "walk.v",
        "dictionary_definition_en": "walk", "form_steps": [
            {"form": surface, "reading": "あるいた", "label": "past", "meaning_en": "walked"}
        ], "story_role": "none", "story_importance_en": "",
    }


def _japanese_candidate():
    return {
        "segments": [_japanese_segment("彼", "he"), _japanese_segment("歩いた"),
                     {"surface": "。", "type": "punctuation", "lemma": "", "surface_kana": "。",
                      "lemma_kana": "", "part_of_speech": "punctuation", "conjugation_form": "",
                      "meaning_en": "", "grammar_candidate_key": "", "dictionary_key": "",
                      "dictionary_definition_en": "", "form_steps": [], "story_role": "none",
                      "story_importance_en": ""}],
        "grammar_overlays": [{
            "start": 1, "end": 2, "surface": "歩いた", "grammar_candidate_key": "past",
            "pattern": "V-past", "meaning_en": "walked", "head_lemma": "歩く",
            "head_lemma_kana": "あるく", "form_label": "past", "explanation_en": "past form",
            "components": [
                {"start": 1, "end": 1, "surface": "歩い", "lemma": "歩く", "lemma_kana": "あるく",
                 "function_en": "verb stem", "lookup_kind": "lexical", "dictionary_key": "walk.v",
                 "dictionary_definition_en": "walk"},
                {"start": 1, "end": 2, "surface": "た", "lemma": "た", "lemma_kana": "た",
                 "function_en": "past ending", "lookup_kind": "grammar", "dictionary_key": "",
                 "dictionary_definition_en": ""},
            ],
        }],
    }


def _korean_segment(text, meaning, lexical_id="단어01/명"):
    punctuation = lexical_id == ""
    return {
        "text": text, "type": "punctuation" if punctuation else "word", "meaning_en": "" if punctuation else meaning,
        "lemma": "" if punctuation else text, "lexical_kind": "" if punctuation else "vocabulary",
        "lexical_id": lexical_id, "story_importance_en": "", "form_steps": [] if punctuation else [
            {"form": text, "reading": text, "label": "base", "meaning_en": meaning,
             "grammar_entry_ids": ["base-form"]}
        ],
    }


def _korean_flat():
    return {
        "segments": [_korean_segment("열", "ten", "열03/수"), _korean_segment(" ", "", ""),
                     _korean_segment("달", "months", "달05/의"), _korean_segment(" ", "", ""),
                     _korean_segment("뒤", "later", "뒤01/명")],
        "grammar_links": [{"segment_index": 4, "entry_id": "after-eun-dwi", "context_en": "later than",
                           "display_form": "뒤", "display_meaning_en": "after", "display_end_segment_index": 4}],
        "inflected_segment_indices": [],
        "expression_links": [{"segment_index": 2, "end_segment_index": 2, "entry_id": "month-counter",
                               "form": "달", "meaning_en": "months", "context_en": "counting months"}],
    }


def _korean_v4():
    segment = {
        "source_start": 0, "source_end": 2, "type": "word", "meaning_en": "go",
        "lemma": "가다", "lexical_kind": "vocabulary", "lexical_id": "가다01/동",
        "story_importance_en": "", "form_steps": [{"form": "가요", "reading": "가요", "label": "polite",
            "meaning_en": "go", "grammar_entry_ids": ["polite"]}], "is_inflected": True,
        "grammar_links": [{"entry_id": "polite", "context_en": "polite speech", "display_meaning_en": "go politely",
                           "source_start": 0, "source_end": 2}],
        "expression_links": [],
    }
    return {"format": "source-span-links-annotation-v4", "segments": [segment]}


def _chinese():
    return {
        "segments": [
            {"index": 0, "text": "她", "type": "word", "pinyin": "tā", "meaning_en": "she"},
            {"index": 1, "text": "走", "type": "word", "pinyin": "zǒu", "meaning_en": "walk"},
            {"index": 2, "text": "了。", "type": "particle", "pinyin": "le", "meaning_en": "completed"},
        ],
        "grammar_overlays": [{"start": 1, "end": 3, "text": "走了", "grammar_candidate_key": "le.completed",
                              "pattern": "V-le", "meaning_en": "walked"}],
    }


@pytest.mark.parametrize(
    "representation,candidate,path,new",
    [
        ("chinese-fixed", _chinese(), "/segments/1/meaning_en", "go"),
        ("japanese-annotation", _japanese_candidate(), "/segments/1/meaning_en", "walked"),
        ("korean-flat", _korean_flat(), "/segments/2/meaning_en", "month counter"),
        ("korean-v4", _korean_v4(), "/segments/0/meaning_en", "move"),
    ],
)
def test_actual_annotation_shapes_allow_one_semantic_field_edit_and_preserve_other_rows(
    representation, candidate, path, new
):
    before = deepcopy(candidate)
    parts = path.split("/")
    segment_index = int(parts[2])
    field = parts[-1]
    changed = apply_edits(
        candidate, patch_for(candidate, {"op": "set_field", "path": path, "value": new}),
        allowed_targets=[{"op": "set_field", "path": path}], representation=representation,
    )
    assert candidate == before
    assert changed["segments"][segment_index][field] == new
    assert changed["segments"][:segment_index] == before["segments"][:segment_index]
    assert changed["segments"][segment_index + 1:] == before["segments"][segment_index + 1:]


def test_derived_form_steps_are_editable_while_japanese_source_surfaces_are_not():
    candidate = _japanese_candidate()
    path = "/segments/1/form_steps"
    changed = apply_edits(candidate, patch_for(candidate, {
        "op": "replace_list", "path": path,
        "value": [{"form": "歩いて", "reading": "あるいて", "label": "te-form", "meaning_en": "walk"},
                  {"form": "いる", "reading": "いる", "label": "auxiliary", "meaning_en": "be doing"}],
    }), allowed_targets=[{"op": "replace_list", "path": path}], representation="japanese-annotation")
    assert [row["form"] for row in changed["segments"][1]["form_steps"]] == ["歩いて", "いる"]
    assert changed["segments"][1]["surface"] == candidate["segments"][1]["surface"] == "歩いた"

    surface_path = "/segments/1/surface"
    with pytest.raises(ImmutableFieldError):
        apply_edits(candidate, patch_for(candidate, {
            "op": "set_field", "path": surface_path, "value": "歩いて",
        }), allowed_targets=[{"op": "set_field", "path": surface_path}],
            representation="japanese-annotation")


def test_nested_row_replacement_cannot_hide_deleted_or_changed_japanese_component_ranges():
    candidate = _japanese_candidate()
    row = deepcopy(candidate["grammar_overlays"][0])
    row["components"].pop()
    path = "/grammar_overlays/0"
    with pytest.raises(BoundaryChangeNotSupported):
        apply_edits(candidate, patch_for(candidate, {
            "op": "replace_row", "path": path, "value": row,
        }), allowed_targets=[{"op": "replace_row", "path": path}], representation="japanese-annotation")


def test_container_set_cannot_bypass_source_contract_even_when_scope_allows_it():
    candidate = _chinese()
    path = "/segments"
    edited = deepcopy(candidate["segments"])
    edited[0]["text"] = "他"
    with pytest.raises(InvalidEditError):
        apply_edits(candidate, patch_for(candidate, {
            "op": "set_field", "path": path, "value": edited,
        }), allowed_targets=[{"op": "set_field", "path": path}], representation="chinese-fixed")


def test_semantic_lesson_rows_can_be_appended_or_removed_without_moving_existing_anchors():
    candidate = _korean_flat()
    original_links = deepcopy(candidate["grammar_links"])
    appended = {"segment_index": 0, "entry_id": "ten-counter", "context_en": "counting tens",
                "display_form": "열", "display_meaning_en": "ten items", "display_end_segment_index": 0}
    add_path = "/grammar_links/1"
    changed = apply_edits(candidate, patch_for(candidate, {
        "op": "append_row", "path": add_path, "value": appended,
    }), allowed_targets=[{"op": "append_row", "path": add_path}], representation="korean-flat")
    assert changed["grammar_links"] == original_links + [appended]
    assert changed["segments"] == candidate["segments"]

    remove_path = "/grammar_links/1"
    trimmed = apply_edits(changed, patch_for(changed, {
        "op": "remove_row", "path": remove_path,
    }), allowed_targets=[{"op": "remove_row", "path": remove_path}], representation="korean-flat")
    assert trimmed["grammar_links"] == original_links
    assert trimmed["segments"] == candidate["segments"]


def test_append_row_uses_the_planned_list_path_and_keeps_legacy_indexed_targets():
    candidate = _korean_flat()
    original_links = deepcopy(candidate["grammar_links"])
    appended = {"segment_index": 0, "entry_id": "topic-eun-neun", "context_en": "topic particle",
                "display_form": "", "display_meaning_en": "", "display_end_segment_index": -1}
    changed = apply_edits(candidate, patch_for(candidate, {
        "op": "append_row", "path": "/grammar_links", "value": appended,
    }), allowed_targets=[{"op": "append_row", "path": "/grammar_links"}],
        representation="korean-flat")
    assert changed["grammar_links"] == original_links + [appended]
    assert changed["segments"] == candidate["segments"]
    assert changed["inflected_segment_indices"] == candidate["inflected_segment_indices"]

    # Old cached plans may explicitly name the numeric append index; retain it.
    legacy = {"segment_index": 0, "entry_id": "subject-i-ga", "context_en": "subject particle",
              "display_form": "", "display_meaning_en": "", "display_end_segment_index": -1}
    replay = apply_edits(changed, patch_for(changed, {
        "op": "append_row", "path": "/grammar_links/2", "value": legacy,
    }), allowed_targets=[{"op": "append_row", "path": "/grammar_links/2"}],
        representation="korean-flat")
    assert replay["grammar_links"] == original_links + [appended, legacy]


def test_multiple_planned_appends_to_one_link_list_preserve_existing_rows_and_order():
    candidate = _korean_flat()
    existing = deepcopy(candidate["grammar_links"])
    first = {"segment_index": 0, "entry_id": "topic-eun-neun", "context_en": "topic",
             "display_form": "", "display_meaning_en": "", "display_end_segment_index": -1}
    second = {"segment_index": 2, "entry_id": "object-eul-reul", "context_en": "object",
              "display_form": "", "display_meaning_en": "", "display_end_segment_index": -1}
    path = "/grammar_links"
    updated = apply_edits(candidate, patch_for(candidate,
        {"op": "append_row", "path": path, "value": first},
        {"op": "append_row", "path": path, "value": second}),
        allowed_targets=[{"op": "append_row", "path": path}], representation="korean-flat")
    assert updated["grammar_links"] == existing + [first, second]
    assert updated["segments"] == candidate["segments"]


@pytest.mark.parametrize("representation,candidate,path,row", [
    ("chinese-fixed", _chinese(), "/grammar_overlays", {
        "start": 0, "end": 1, "text": "她", "grammar_candidate_key": "topic",
        "pattern": "topic", "meaning_en": "topic marker"}),
    ("japanese-annotation", _japanese_candidate(), "/segments/1/form_steps", {
        "form": "歩いて", "reading": "あるいて", "label": "te-form", "meaning_en": "walk and"}),
])
def test_plan_list_path_append_works_for_chinese_and_japanese(representation, candidate, path, row):
    changed = apply_edits(candidate, patch_for(candidate, {
        "op": "append_row", "path": path, "value": row,
    }), allowed_targets=[{"op": "append_row", "path": path}], representation=representation)
    assert changed != candidate
    if representation == "chinese-fixed":
        assert changed["grammar_overlays"][:-1] == candidate["grammar_overlays"]
        assert changed["grammar_overlays"][-1] == row
        assert [part["text"] for part in changed["segments"]] == [part["text"] for part in candidate["segments"]]
    else:
        assert changed["segments"][1]["form_steps"][:-1] == candidate["segments"][1]["form_steps"]
        assert changed["segments"][1]["form_steps"][-1] == row
        assert changed["segments"][1]["surface"] == candidate["segments"][1]["surface"]


def test_nested_korean_grammar_id_array_requires_replace_list_and_protected_scalar_is_rejected_early():
    candidate = _korean_flat()
    identity_path = "/segments/0/form_steps/0/grammar_entry_ids"
    validate_target_contract(candidate, [{"op": "replace_list", "path": identity_path}],
                              representation="korean-flat")
    with pytest.raises(InvalidEditError, match="use replace_list"):
        validate_target_contract(candidate, [{"op": "set_field", "path": identity_path}],
                                  representation="korean-flat")
    with pytest.raises(InvalidEditError, match="append_row target must name a supported semantic list"):
        validate_target_contract(candidate, [{"op": "append_row", "path": identity_path}],
                                  representation="korean-flat")
    with pytest.raises(ImmutableFieldError, match="protected source/tap data"):
        validate_target_contract(candidate, [{"op": "set_field", "path": "/segments/0/text"}],
                                  representation="korean-flat")
    with pytest.raises(InvalidEditError, match="path does not exist"):
        validate_target_contract(candidate, [{"op": "replace_list", "path": "/segments/99/form_steps"}],
                                  representation="korean-flat")


def test_v4_nested_link_addition_preserves_source_ranges_and_form_step_form_remains_derived():
    candidate = _korean_v4()
    link = {"entry_id": "question", "context_en": "question ending", "display_meaning_en": "question",
            "source_start": 0, "source_end": 2}
    path = "/segments/0/grammar_links/1"
    changed = apply_edits(candidate, patch_for(candidate, {
        "op": "append_row", "path": path, "value": link,
    }), allowed_targets=[{"op": "append_row", "path": path}], representation="korean-v4")
    assert changed["segments"][0]["source_start"] == candidate["segments"][0]["source_start"]
    assert changed["segments"][0]["source_end"] == candidate["segments"][0]["source_end"]
    assert changed["segments"][0]["grammar_links"][0] == candidate["segments"][0]["grammar_links"][0]
    assert changed["segments"][0]["grammar_links"][1] == link


def test_korean_flat_form_chain_and_inflection_audit_are_editable_without_tap_changes():
    candidate = _korean_flat()
    candidate["segments"][2].pop("form_steps")  # Actual flat output omits empty chains.
    original_segments = deepcopy(candidate["segments"])
    changes = [
        {"op": "replace_list", "path": "/segments/2/form_steps", "value": [
            {"form": "달", "reading": "달", "label": "counter", "meaning_en": "months",
             "grammar_entry_ids": ["month-counter"]},
        ]},
        {"op": "replace_list", "path": "/inflected_segment_indices", "value": [2]},
    ]
    changed = apply_edits(candidate, patch_for(candidate, *changes), allowed_targets=[
        {"op": "replace_list", "path": "/segments/2/form_steps"},
        {"op": "replace_list", "path": "/inflected_segment_indices"},
    ], representation="korean-flat")
    assert changed["segments"][2]["form_steps"][0]["grammar_entry_ids"] == ["month-counter"]
    assert changed["inflected_segment_indices"] == [2]
    assert [{"text": row["text"], "type": row["type"]} for row in changed["segments"]] == [
        {"text": row["text"], "type": row["type"]} for row in original_segments
    ]
    assert candidate["inflected_segment_indices"] == []


def test_replacing_protected_range_or_topology_lists_is_rejected():
    candidate = _korean_v4()
    path = "/segments/0/grammar_links"
    rows = deepcopy(candidate["segments"][0]["grammar_links"])
    rows[0]["source_end"] = 1
    with pytest.raises(BoundaryChangeNotSupported):
        apply_edits(candidate, patch_for(candidate, {
            "op": "replace_list", "path": path, "value": rows,
        }), allowed_targets=[{"op": "replace_list", "path": path}], representation="korean-v4")
    segments_path = "/segments"
    with pytest.raises(InvalidEditError):
        apply_edits(candidate, patch_for(candidate, {
            "op": "replace_list", "path": segments_path, "value": [],
        }), allowed_targets=[{"op": "replace_list", "path": segments_path}], representation="korean-v4")


def test_repeated_source_text_at_distinct_positions_remains_separate():
    candidate = _korean_flat()
    candidate["segments"][4]["text"] = candidate["segments"][2]["text"]
    candidate["segments"][4]["meaning_en"] = "different occurrence"
    path = "/segments/4/meaning_en"
    changed = apply_edits(candidate, patch_for(candidate, {
        "op": "set_field", "path": path, "value": "later occurrence",
    }), allowed_targets=[{"op": "set_field", "path": path}], representation="korean-flat")
    assert changed["segments"][2]["text"] == changed["segments"][4]["text"] == "달"
    assert changed["segments"][2]["meaning_en"] == "months"
    assert changed["segments"][4]["meaning_en"] == "later occurrence"


def test_stale_candidate_off_scope_and_conflicts_are_rejected():
    candidate = _japanese_candidate()
    edit = {"op": "set_field", "path": "/segments/0/meaning_en", "value": "person"}
    patch = patch_for(candidate, edit)
    changed_base = deepcopy(candidate)
    changed_base["segments"][0]["meaning_en"] = "someone"
    with pytest.raises(StaleCandidateError):
        apply_edits(changed_base, patch, allowed_targets=[{"op": "set_field", "path": edit["path"]}],
                    representation="japanese-annotation")
    with pytest.raises(EditScopeError):
        apply_edits(candidate, patch, allowed_targets=[{"op": "set_field", "path": "/segments/0/surface"}],
                    representation="japanese-annotation")
    with pytest.raises(EditConflictError):
        apply_edits(candidate, patch_for(candidate, edit, edit),
                    allowed_targets=[{"op": "set_field", "path": edit["path"]}],
                    representation="japanese-annotation")


def test_bad_paths_operations_and_representation_are_rejected():
    candidate = _chinese()
    with pytest.raises(InvalidEditError):
        apply_edits(candidate, patch_for(candidate, {"op": "delete_row", "path": "/segments/0", "value": None}),
                    allowed_targets=[], representation="chinese-fixed")
    with pytest.raises(InvalidEditError):
        apply_edits(candidate, patch_for(candidate, {"op": "set_field", "path": "/segments/~2/text", "value": "x"}),
                    allowed_targets=[{"op": "set_field", "path": "/segments/~2/text"}],
                    representation="chinese-fixed")
    with pytest.raises(InvalidEditError):
        apply_edits(candidate, patch_for(candidate, {"op": "set_field", "path": "/segments/0/text", "value": "x"}),
                    allowed_targets=[{"op": "set_field", "path": "/segments/0/text"}], representation="unknown")


def test_declared_issue_targets_must_change_and_enclosing_row_replacement_can_cover():
    candidate = {"segments": [
        {"text": "她", "type": "word", "pinyin": "tā", "meaning_en": "old"},
        {"text": "走", "type": "word", "pinyin": "zǒu", "meaning_en": "walk"}],
        "grammar_overlays": []}
    plan = {"issues": [
        {"issue_index": 0, "targets": [{"op": "set_field", "path": "/segments/0/meaning_en"}],
         "boundary_change_needed": False},
        {"issue_index": 1, "targets": [{"op": "set_field", "path": "/segments/0/pinyin"}],
         "boundary_change_needed": False}]}
    empty = patch_for(candidate)
    unchanged = apply_edits(candidate, empty, allowed_targets=[], representation="chinese-annotation")
    with pytest.raises(EditCoverageError, match="meaning_en"):
        validate_issue_target_coverage(candidate, unchanged, empty["edits"], plan,
                                       representation="chinese-annotation")

    only_meaning = patch_for(candidate, {
        "op": "set_field", "path": "/segments/0/meaning_en", "value": "she"})
    partially_changed = apply_edits(candidate, only_meaning,
        allowed_targets=plan["issues"][0]["targets"], representation="chinese-annotation")
    with pytest.raises(EditCoverageError, match="pinyin"):
        validate_issue_target_coverage(candidate, partially_changed, only_meaning["edits"], plan,
                                       representation="chinese-annotation")

    replacement = patch_for(candidate, {"op": "replace_row", "path": "/segments/0", "value": {
        **candidate["segments"][0], "meaning_en": "she", "pinyin": "tā (she)"}})
    replaced = apply_edits(candidate, replacement,
        allowed_targets=[{"op": "replace_row", "path": "/segments/0"}],
        representation="chinese-annotation")
    validate_issue_target_coverage(candidate, replaced, replacement["edits"], plan,
                                   representation="chinese-annotation")
    assert replaced["segments"][1] == candidate["segments"][1]
    assert replaced["segments"][0]["text"] == candidate["segments"][0]["text"]


def test_duplicate_append_issue_targets_require_distinct_rows_and_keep_existing_order():
    candidate = {"segments": [{"text": "她"}], "grammar_overlays": [],
        "grammar_links": [{"segment_index": 0, "display_end_segment_index": 0,
            "display_form": "她", "pattern": "old", "start": 0}],
        "inflected_segment_indices": []}
    plan = {"issues": [
        {"issue_index": 0, "targets": [{"op": "append_row", "path": "/grammar_links"}],
         "boundary_change_needed": False},
        {"issue_index": 1, "targets": [{"op": "append_row", "path": "/grammar_links"}],
         "boundary_change_needed": False}]}
    one = patch_for(candidate, {"op": "append_row", "path": "/grammar_links",
        "value": {"segment_index": 0, "display_end_segment_index": 0,
                  "display_form": "她", "pattern": "new-a"}})
    one_result = apply_edits(candidate, one, allowed_targets=plan["issues"][0]["targets"],
                             representation="korean-flat")
    with pytest.raises(EditCoverageError, match="issue 1"):
        validate_issue_target_coverage(candidate, one_result, one["edits"], plan,
                                       representation="korean-flat")

    two = patch_for(candidate,
        {"op": "append_row", "path": "/grammar_links", "value": {
            "segment_index": 0, "display_end_segment_index": 0, "display_form": "她", "pattern": "new-a"}},
        {"op": "append_row", "path": "/grammar_links", "value": {
            "segment_index": 0, "display_end_segment_index": 0, "display_form": "她", "pattern": "new-b"}})
    two_result = apply_edits(candidate, two, allowed_targets=plan["issues"][0]["targets"],
                             representation="korean-flat")
    validate_issue_target_coverage(candidate, two_result, two["edits"], plan,
                                   representation="korean-flat")
    assert two_result["grammar_links"][:1] == candidate["grammar_links"]
    assert [row["pattern"] for row in two_result["grammar_links"][1:]] == ["new-a", "new-b"]


def test_multiple_base_indexed_row_removals_preserve_untargeted_rows_and_reject_overlap():
    candidate = _korean_flat()
    first = {"segment_index": 0, "entry_id": "first", "context_en": "first",
        "display_form": "열", "display_meaning_en": "first", "display_end_segment_index": 0}
    middle = {"segment_index": 2, "entry_id": "keep", "context_en": "keep",
        "display_form": "달", "display_meaning_en": "months", "display_end_segment_index": 2}
    last = {"segment_index": 4, "entry_id": "last", "context_en": "last",
        "display_form": "뒤", "display_meaning_en": "later", "display_end_segment_index": 4}
    candidate["grammar_links"] = [first, middle, last]
    targets = [{"op": "remove_row", "path": "/grammar_links/0"},
               {"op": "remove_row", "path": "/grammar_links/2"}]
    plan = {"issues": [
        {"issue_index": 0, "targets": [targets[0]], "boundary_change_needed": False},
        {"issue_index": 1, "targets": [targets[1]], "boundary_change_needed": False}]}
    patch = patch_for(candidate,
        {"op": "remove_row", "path": "/grammar_links/0"},
        {"op": "remove_row", "path": "/grammar_links/2"})
    changed = apply_edits(candidate, patch, allowed_targets=targets, representation="korean-flat")
    validate_issue_target_coverage(candidate, changed, patch["edits"], plan,
                                   representation="korean-flat")
    assert changed["grammar_links"] == [middle]
    assert changed["grammar_links"][0]["segment_index"] == 2
    assert changed["segments"] == candidate["segments"]

    conflicting = patch_for(candidate,
        {"op": "remove_row", "path": "/grammar_links/0"},
        {"op": "set_field", "path": "/grammar_links/0/context_en", "value": "overlap"})
    with pytest.raises(EditConflictError):
        apply_edits(candidate, conflicting, allowed_targets=[
            targets[0], {"op": "set_field", "path": "/grammar_links/0/context_en"}],
            representation="korean-flat")
