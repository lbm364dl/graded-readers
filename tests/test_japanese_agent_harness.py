from argparse import Namespace
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from pipeline.japanese_agent_harness import (
    JLPT_LEVELS,
    JAPANESE_ANNOTATION_CHUNK_MAXIMUMS, JAPANESE_ANNOTATION_CHUNK_POLICY,
    JAPANESE_ANNOTATION_CHUNK_TARGETS,
    JapaneseBookHarness, JapaneseChapterHarness,
    apply_reader_useful_annotation_review_policy, clear_unavailable_dictionary_links,
    japanese_semantic_repair_grammar_knowledge,
    japanese_char_count,
    japanese_beginner_prose_issues,
    japanese_learner_segmentation_issues,
    japanese_form_step_issues,
    japanese_narrative_register_issues,
    japanese_paragraph_structure_issues,
    japanese_overlay_policy_issue,
    japanese_required_overlay_issues,
    jlpt_orthography_guidance,
    material_review_findings,
    minimum_vocabulary_replacements,
    normalize_redundant_japanese_form_steps,
    overlay_crosses_clause_boundary, parser,
    overlong_japanese_sentences, resolve_source_boundary,
    split_japanese_annotation_chunks, strip_duplicate_source_header,
    strip_inline_japanese_readings, verify_japanese_chunk_review,
)
from pipeline.annotation_review_guidance import FORM_STAGE_EVIDENCE_GUIDANCE
from pipeline.japanese_readability import (
    level_diagnostics, matched_level, preflight_level_diagnostics,
)
from pipeline.japanese_agent_harness import japanese_segment_issue
from pipeline.agent_harness import digest


def test_japanese_semantic_repair_sees_alternate_reviewed_lesson_and_keeps_draft_provisional():
    from pipeline.usage_dictionary import decision_digest

    data = {"entries": [
        {"id": "ja-grammar-used", "title": "Used", "explanation": "Used lesson."},
        {"id": "ja-grammar-alternate", "title": "Alternate", "explanation": "Independent alternate lesson."},
    ], "assignments": []}
    registry = {"reviewed": True, "review_digest": decision_digest(data), "data": data}
    knowledge = japanese_semantic_repair_grammar_knowledge(registry)
    assert knowledge["catalog_status"] == "reviewed"
    assert {entry["id"] for entry in knowledge["approved_entries"]} == {
        "ja-grammar-used", "ja-grammar-alternate",
    }
    assert "provisional" in knowledge["identity_policy"]
    assert "do not present it as approved" in knowledge["identity_policy"]


def test_japanese_semantic_repair_does_not_expose_unreviewed_grammar_as_approved():
    knowledge = japanese_semantic_repair_grammar_knowledge({
        "reviewed": False, "review_digest": "stale", "data": {"entries": [{"id": "draft"}]},
    })
    assert knowledge["catalog_status"] == "unavailable_or_unreviewed"
    assert knowledge["approved_entries"] == []


@pytest.mark.asyncio
async def test_japanese_semantic_repair_caller_passes_alternate_reviewed_entry(tmp_path, monkeypatch):
    from pipeline import annotation_repairs, japanese_grammar_dictionary

    candidate = {"segments": [{
        "surface": "猫", "type": "word", "lemma": "猫",
        "surface_kana": "ねこ", "lemma_kana": "ねこ",
        "part_of_speech": "noun", "conjugation_form": "non-inflecting",
        "meaning_en": "cat", "grammar_candidate_key": "",
        "dictionary_key": "", "dictionary_definition_en": "",
        "form_steps": [], "story_role": "none", "story_importance_en": "",
    }], "grammar_overlays": []}
    captured = {}

    class Harness(JapaneseChapterHarness):
        async def annotation_candidate(self, index, chunk, **kwargs):
            return candidate

        async def review_annotation(self, index, chunk, annotation, stage):
            return ({"verdict": "revise", "issues": [{"problem": "grammar", "explanation": "Check available lessons."}]}
                    if stage == "initial" else {"verdict": "pass", "issues": []})

        def annotation_reconstructs(self, chunk, annotation):
            return True

        def annotation_contract_issues(self, chunk, annotation):
            return []

        def prepare_planned_annotation(self, chunk, annotation):
            return annotation

    async def fake_repair(harness, job, base, issues, **kwargs):
        captured.update(kwargs["context"])
        return {"status": "applied", "candidate": base, "evidence": {"assembly_job": job}}

    monkeypatch.setattr(annotation_repairs, "repair_annotation", fake_repair)
    harness = object.__new__(Harness)
    harness.args = Namespace(level="n5", annotation_chunk=None, annotation_chunk_maximum=None,
        max_annotation_repairs=1, annotation_repair_effort="low", annotation_final_effort="low",
        refresh=False, annotation_chunk_indices=None)
    harness.run_dir = tmp_path
    harness.story_vocabulary_plan = {"terms": []}
    result = await harness.annotate_chunk(0, "猫")
    assert result["resolved"] is True
    knowledge = captured["grammar_knowledge"]
    registry = japanese_grammar_dictionary.words.read(japanese_grammar_dictionary.REGISTRY)
    expected = registry["data"]["entries"]
    assert knowledge["catalog_status"] == "reviewed"
    assert knowledge["approved_entries"] == expected
    assert any(entry["id"] not in {s["grammar_candidate_key"] for s in candidate["segments"]}
               for entry in knowledge["approved_entries"])


@pytest.mark.parametrize("route_mode", ['clear', 'derived', 'unchanged', 'exhausted', 'tail', 'tail_pass'])
@pytest.mark.asyncio
async def test_japanese_applied_semantic_review_clear_is_saved_as_distinct_adjudication(tmp_path, monkeypatch, route_mode):
    from pipeline import annotation_repairs, annotation_adjudication

    candidate = {"segments": [{"surface": "猫", "type": "word", "lemma": "猫",
        "surface_kana": "ねこ", "lemma_kana": "ねこ", "part_of_speech": "noun",
        "conjugation_form": "non-inflecting", "meaning_en": "cat",
        "grammar_candidate_key": "", "dictionary_key": "",
        "dictionary_definition_en": "", "form_steps": [], "story_role": "none",
        "story_importance_en": ""}], "grammar_overlays": []}
    issue = {"segment_text": "猫", "problem": "meaning",
             "explanation": "Check the contextual gloss.", "suggested_fix": "Review it."}

    repaired_candidates = {}
    route_candidates = []

    class Harness(JapaneseChapterHarness):
        async def annotation_candidate(self, index, chunk, **kwargs):
            return copy.deepcopy(next(reversed(repaired_candidates.values()))) if repaired_candidates else copy.deepcopy(candidate)

        async def review_annotation(self, index, chunk, annotation, stage):
            receipts = getattr(self, "_annotation_review_receipts", None)
            if receipts is None:
                receipts = self._annotation_review_receipts = {}
            receipts[(index, stage)] = [
                {"job": "fixture/general", "input_digest": "input-1", "result_digest": "result-1"},
                {"job": "fixture/boundary", "input_digest": "input-2", "result_digest": "result-2"},
            ]
            if route_mode == "tail_pass" and stage == "fresh_repair_01":
                return {"verdict": "pass", "issues": []}
            return {"verdict": "revise", "issues": [issue]}

        def annotation_surfaces_reconstruct(self, chunk, annotation):
            return True

        def annotation_reconstructs(self, chunk, annotation):
            return True

        def annotation_contract_issues(self, chunk, annotation):
            return []

        def prepare_planned_annotation(self, chunk, annotation):
            return annotation

    async def fake_repair(harness, job, base, issues, **kwargs):
        root = tmp_path / "agents" / job
        root.mkdir(parents=True, exist_ok=True)
        (root / "meta.json").write_text(json.dumps({"kind": "annotation_patch_assembly",
            "status": "applied", "return_code": 0}))
        derived = copy.deepcopy(base)
        if route_mode != "unchanged":
            derived["segments"][0]["meaning_en"] += " (repair)"
        repaired_candidates[job] = derived
        return {"status": "applied", "candidate": derived,
                "evidence": {"assembly_job": job}}

    def fake_replay(run_dir, job, *, validate_candidate):
        derived = repaired_candidates[job]
        validate_candidate(derived)
        return {"status": "applied", "candidate": derived}

    async def fake_adjudicate(*args, **kwargs):
        assert kwargs["current_review"]["issues"] == [issue]
        assert len(kwargs["normal_review_receipt"]["components"]) == 2
        route_candidates.append(copy.deepcopy(kwargs["candidate"]))
        if route_mode in ("unchanged", "exhausted") or (route_mode in ("derived", "tail", "tail_pass") and len(route_candidates) == 1):
            return {"status": "actionable", "approved": False,
                    "repair_diagnoses": [{"issue_id": "genuine", "diagnosis": "Correct the gloss.",
                        "paths": ["/segments/0/meaning_en"]}]}
        return {"status": "cleared", "approved": True, "job": "fixture-adjudication"}

    monkeypatch.setattr(annotation_repairs, "repair_annotation", fake_repair)
    monkeypatch.setattr(annotation_repairs, "replay_annotation_repair", fake_replay)
    monkeypatch.setattr(annotation_adjudication, "adjudicate_annotation_review", fake_adjudicate)
    harness = object.__new__(Harness)
    harness.args = Namespace(level="n5", annotation_chunk=None, annotation_chunk_maximum=None,
        max_annotation_repairs=(1 if route_mode in ("clear", "tail", "tail_pass") else 5), max_annotation_fresh_repairs=(1 if route_mode in ("tail", "tail_pass") else 0),
        max_annotation_adjudications=0, annotation_repair_effort="low",
        annotation_final_effort="low", annotation_review_effort="low",
        refresh=False, annotation_chunk_indices=None, no_grammar_overlays=False)
    harness.run_dir = tmp_path
    harness.runner = object()
    harness.story_vocabulary_plan = {"terms": []}
    if route_mode in ("unchanged", "exhausted"):
        with pytest.raises(ValueError):
            await harness.annotate_chunk(0, "猫")
        assert len(route_candidates) == (1 if route_mode == "unchanged" else 3)
        assert len({json.dumps(row, sort_keys=True) for row in route_candidates}) == len(route_candidates)
        return
    result = await harness.annotate_chunk(0, "猫")
    if route_mode == "tail_pass":
        assert len(route_candidates) == 1  # The earlier actionable route consumed a slot.
        assert result["resolved"] is True
        final = result["attempts"][-1]
        assert final["stage"] == "fresh_repair_01"
        assert final["review"] == {"verdict": "pass", "issues": []}
        assert final["annotation"] != route_candidates[0]
        assert final["semantic_repair"]["status"] == "applied"
        assert "adjudication" not in final
        assert any(row.get("adjudication", {}).get("status") == "actionable" for row in result["attempts"])
        stored = json.loads((tmp_path / "accepted-annotations" / "chunk_0000.json").read_text())
        assert stored["attempts"][-1]["review"]["verdict"] == "pass"
        return
    assert len(route_candidates) == (1 if route_mode == "clear" else 2)
    if route_mode != "clear":
        assert route_candidates[0] != route_candidates[1]
        assert result["attempts"][-1]["review"]["verdict"] == "revise"
        assert any(row.get("adjudication", {}).get("status") == "actionable" for row in result["attempts"])
    assert result["effective_review"]["kind"] == "adjudicated"
    assert result["attempts"][-1]["review"]["verdict"] == "revise"
    stored = json.loads((tmp_path / "accepted-annotations" / "chunk_0000.json").read_text())
    replay = stored["attempts"][-1]["adjudication_replay"]
    assert replay["prior_history"] == stored["attempts"][:-1]


def _adjudicated_japanese_cache_fixture(tmp_path, monkeypatch):
    from pipeline.annotation_adjudication import digest as evidence_digest
    from pipeline.annotation_publication import bind_review_job
    from pipeline.agent_harness import CodexRunner
    from pipeline import annotation_adjudication

    source = "猫"
    candidate = {"segments": [{"surface": source}], "grammar_overlays": []}
    review = {"verdict": "revise", "issues": [{"problem": "meaning"}]}
    story_plan = {"terms": []}
    (tmp_path / "story-vocabulary-plan.json").write_text(json.dumps(story_plan))
    components = []
    for suffix in ("review", "boundary_review"):
        job = f"annotations/chunk_0000/adjudicated_final_01_{suffix}"
        job_dir = tmp_path / "agents" / job
        job_dir.mkdir(parents=True, exist_ok=True)
        raw_review = {"verdict": "revise", "issues": [{"problem": suffix}]}
        (job_dir / "meta.json").write_text(json.dumps({
            "fingerprint": f"input-{suffix}", "return_code": 0,
            "model": "gpt-6-luna", "effort": "low", "tool_profile": "offline"}))
        (job_dir / "result.json").write_text(json.dumps(raw_review))
        child = bind_review_job(tmp_path, job, candidate=candidate, source_text=source)
        child.update(candidate_digest=evidence_digest(candidate),
                     source_digest=evidence_digest(source))
        components.append(child)

    grammar = japanese_semantic_repair_grammar_knowledge()
    context = {"chunk_text": source, "grammar_knowledge": grammar,
               "story_plan": story_plan}
    references = {
        "approved-grammar": {"kind": "approved_lesson",
                             "content": grammar.get("approved_entries", [])},
        "review-policy": {"kind": "explicit_review_policy",
            "content": {"form_stage_guidance": FORM_STAGE_EVIDENCE_GUIDANCE,
                "review_policy": "", "dictionary_policy": JAPANESE_ANNOTATION_CHUNK_POLICY}},
    }
    receipt = {"kind": "composite", "review_digest": evidence_digest(review),
               "components": components}
    gate = {"passed": True, "issues": [],
            "candidate_digest": evidence_digest(candidate),
            "source_text_digest": evidence_digest(source)}
    replay = {"prior_history": [], "context": context,
              "known_reference_input": references,
              "deterministic_gate_evidence": gate,
              "normal_review_receipt": receipt}
    row = {"stage": "adjudicated_final_01", "annotation": candidate,
           "review": review, "adjudication": {"status": "cleared", "approved": True},
           "effective_review": {"kind": "adjudicated"},
           "adjudication_replay": replay}
    item = {"segments": candidate["segments"], "grammar_overlays": [],
            "attempts": [row], "resolved": True,
            "effective_review": {"kind": "adjudicated"}}
    monkeypatch.setattr(CodexRunner, "_check_tool_profile", staticmethod(lambda *_args: None))
    monkeypatch.setattr(annotation_adjudication, "verify_adjudication_evidence",
        lambda *_args, **_kwargs: {"status": "cleared", "approved": True})
    return source, item


def test_japanese_adjudicated_replay_rejects_tampered_history(tmp_path, monkeypatch):
    source, item = _adjudicated_japanese_cache_fixture(tmp_path, monkeypatch)
    assert verify_japanese_chunk_review(item, source, tmp_path) is True
    tampered = copy.deepcopy(item)
    tampered["attempts"][-1]["adjudication_replay"]["prior_history"] = [{"fake": True}]
    assert verify_japanese_chunk_review(tampered, source, tmp_path) is False


def test_japanese_adjudication_must_be_the_final_attempt(tmp_path, monkeypatch):
    source, item = _adjudicated_japanese_cache_fixture(tmp_path, monkeypatch)
    item["attempts"].append({"stage": "later", "review": {"verdict": "pass"}})
    assert verify_japanese_chunk_review(item, source, tmp_path) is False


@pytest.mark.asyncio
async def test_japanese_adjudicated_cache_path_uses_shared_lineage_verifier(tmp_path, monkeypatch):
    source, item = _adjudicated_japanese_cache_fixture(tmp_path, monkeypatch)

    class CacheHarness(JapaneseChapterHarness):
        @property
        def annotation_chunk_cache_tag(self):
            return "fixture-policy"
        def refresh_annotation_chunk(self, _index):
            return False
        def reuse_unselected_annotation_cache(self, _index):
            return False
        def annotation_reconstructs(self, _chunk, _candidate):
            return True
        def annotation_contract_issues(self, _chunk, _candidate):
            return []
        async def annotation_candidate(self, *_args, **_kwargs):
            raise AssertionError("invalid adjudication cache must miss")

    monkeypatch.setattr("pipeline.japanese_agent_harness.clear_unavailable_dictionary_links",
                        lambda value: value)
    monkeypatch.setattr("pipeline.japanese_agent_harness.normalize_redundant_japanese_form_steps",
                        lambda value: value)
    harness = object.__new__(CacheHarness)
    harness.args = SimpleNamespace(refresh=False, annotation_chunk_indices=None)
    harness.run_dir = tmp_path
    harness.story_vocabulary_plan = {"terms": []}
    cache_key = digest(json.dumps({"policy": "fixture-policy", "chunk": source,
                                  "story_plan": {"terms": []}},
                                 ensure_ascii=False, sort_keys=True))
    accepted = tmp_path / "accepted-annotations"
    accepted.mkdir()
    path = accepted / "chunk_0000.json"
    path.write_text(json.dumps({**item, "cache_key": cache_key}))
    reused = await harness.annotate_chunk(0, source)
    assert reused["effective_review"]["kind"] == "adjudicated"

    from pipeline.annotation_reference_carry_callers import register_chunk_positions
    register_chunk_positions(harness, [source], parent_text=source)
    positioned = copy.deepcopy(item)
    positioned['attempts'][-1]['adjudication_replay']['context']['annotation_source_position'] = (
        harness._annotation_source_positions[0])
    path.write_text(json.dumps({**positioned, 'cache_key': cache_key}))
    assert (await harness.annotate_chunk(0, source))['resolved'] is True
    # Same chunk and index, changed parent: miss before publication rather
    # than returning a self-consistent proof for the old occurrence.
    register_chunk_positions(harness, [source, '。'], parent_text=source + '。')
    with pytest.raises(AssertionError, match='invalid adjudication cache must miss'):
        await harness.annotate_chunk(0, source)
    # Historical absent-position contracts retain their prior cache behavior.
    path.write_text(json.dumps({**item, 'cache_key': cache_key}))
    assert (await harness.annotate_chunk(0, source))['resolved'] is True

    changed = copy.deepcopy(item)
    changed["attempts"][-1]["adjudication_replay"]["prior_history"] = [{"fake": True}]
    path.write_text(json.dumps({**changed, "cache_key": cache_key}))
    with pytest.raises(AssertionError, match="invalid adjudication cache must miss"):
        await harness.annotate_chunk(0, source)


def test_beginner_orthography_modernizes_lexical_nai_but_advanced_can_preserve_it():
    assert "lexical 無い" in jlpt_orthography_guidance("n4")
    assert "Write" in jlpt_orthography_guidance("n4")
    assert "may remain" in jlpt_orthography_guidance("n1")
    assert "auxiliary negative ない" in jlpt_orthography_guidance("n1")


def test_inline_reading_cleanup_keeps_real_parenthetical_prose():
    text = "吾輩（わがはい）は猫（ねこ）である。説明（しかし、これは本文だ）も残す。"
    assert strip_inline_japanese_readings(text) == (
        "吾輩は猫である。説明（しかし、これは本文だ）も残す。"
    )


def test_form_steps_preserve_meaningful_negative_and_past_stages():
    segment = {
        "surface": "怖くなかった", "type": "word", "lemma": "怖い",
        "surface_kana": "こわくなかった", "lemma_kana": "こわい",
        "conjugation_form": "plain negative past",
        "form_steps": [
            {
                "form": "怖くない", "reading": "こわくない",
                "label": "plain negative", "meaning_en": "not scary",
            },
            {
                "form": "怖くなかった", "reading": "こわくなかった",
                "label": "plain negative past", "meaning_en": "was not scary",
            },
        ],
    }
    assert japanese_form_step_issues(segment) == []
    segment["form_steps"] = segment["form_steps"][:1]
    assert "final form step" in japanese_form_step_issues(segment)[0]


def test_inflected_word_requires_a_surface_ending_form_chain():
    assert japanese_form_step_issues({
        "surface": "ありました", "type": "word", "lemma": "ある",
        "surface_kana": "ありました", "lemma_kana": "ある",
        "conjugation_form": "polite past", "form_steps": [],
    }) == ["an inflected learner target needs a derivation ending at its surface"]


def test_inflected_grammar_primary_also_requires_form_chain():
    assert japanese_form_step_issues({
        "surface": "速くなりました", "type": "grammar", "lemma": "速くなる",
        "surface_kana": "はやくなりました", "lemma_kana": "はやくなる",
        "conjugation_form": "polite past", "form_steps": [],
    }) == ["an inflected learner target needs a derivation ending at its surface"]


def test_form_chain_requires_polite_and_connective_intermediates():
    polite = {
        "surface": "出ません", "type": "word", "lemma": "出る",
        "surface_kana": "でません", "lemma_kana": "でる",
        "conjugation_form": "polite negative",
        "form_steps": [{
            "form": "出ません", "reading": "でません",
            "label": "polite negative", "meaning_en": "does not come out",
        }],
    }
    assert any("でます" in issue for issue in japanese_form_step_issues(polite))
    polite["form_steps"].insert(0, {
        "form": "出ます", "reading": "でます",
        "label": "polite", "meaning_en": "comes out",
    })
    assert japanese_form_step_issues(polite) == []

    concessive = {
        "surface": "出されても", "type": "word", "lemma": "出す",
        "surface_kana": "だされても", "lemma_kana": "だす",
        "conjugation_form": "passive ても-form",
        "form_steps": [
            {"form": "出される", "reading": "だされる", "label": "passive", "meaning_en": "be put out"},
            {"form": "出されても", "reading": "だされても", "label": "passive ても-form", "meaning_en": "even if put out"},
        ],
    }
    assert any("だされて" in issue for issue in japanese_form_step_issues(concessive))
    concessive["form_steps"].insert(1, {
        "form": "出されて", "reading": "だされて",
        "label": "passive て-form", "meaning_en": "being put out",
    })
    assert japanese_form_step_issues(concessive) == []


def test_desire_change_chain_keeps_desiderative_and_plain_construction():
    segment = {
        "surface": "したくなり", "type": "grammar", "lemma": "したくなる",
        "surface_kana": "したくなり", "lemma_kana": "したくなる",
        "conjugation_form": "conjunctive ren'yōkei",
        "form_steps": [
            {
                "form": "したくなる", "reading": "したくなる",
                "label": "plain desire-change construction",
                "meaning_en": "come to want or need to do",
            },
            {
                "form": "したくなり", "reading": "したくなり",
                "label": "conjunctive ren'yōkei",
                "meaning_en": "coming to want or need to do",
            },
        ],
    }
    assert any(
        "desiderative intermediate form したい" in issue
        for issue in japanese_form_step_issues(segment)
    )
    segment["form_steps"].insert(0, {
        "form": "したい", "reading": "したい", "label": "desiderative",
        "meaning_en": "want to do",
    })
    assert japanese_form_step_issues(segment) == []


def test_abstract_desire_change_lemma_checks_concrete_intermediate_forms():
    segment = {
        "surface": "試してみたくなった", "type": "grammar",
        "lemma": "たくなる", "surface_kana": "ためしてみたくなった",
        "lemma_kana": "たくなる", "conjugation_form": "plain past",
        "form_steps": [{
            "form": "試してみたい", "reading": "ためしてみたい",
            "label": "desiderative", "meaning_en": "want to try",
        }, {
            "form": "試してみたくなる", "reading": "ためしてみたくなる",
            "label": "plain desire-change", "meaning_en": "come to want to try",
        }, {
            "form": "試してみたくなった", "reading": "ためしてみたくなった",
            "label": "plain past", "meaning_en": "came to want to try",
        }],
    }
    assert japanese_form_step_issues(segment) == []


def test_polite_negative_past_uses_verb_chain_not_copular_desu():
    segment = {
        "surface": "いませんでした", "type": "word", "lemma": "いる",
        "surface_kana": "いませんでした", "lemma_kana": "いる",
        "conjugation_form": "polite negative past",
        "form_steps": [
            {"form": "います", "reading": "います", "label": "polite nonpast", "meaning_en": "is present"},
            {"form": "いません", "reading": "いません", "label": "polite negative", "meaning_en": "is not present"},
            {"form": "いませんでした", "reading": "いませんでした", "label": "polite negative past", "meaning_en": "was not present"},
        ],
    }
    assert japanese_form_step_issues(segment) == []
    segment["form_steps"].insert(2, {
        "form": "です", "reading": "です", "label": "copula",
        "meaning_en": "is",
    })
    # Extra spurious steps are a semantic review concern; the mechanical gate
    # must at least never demand です for this verb form.
    assert not any(
        "copula" in issue for issue in japanese_form_step_issues(segment)
    )


def test_noninflecting_words_ending_in_temo_are_not_misread_as_te_mo_forms():
    assert japanese_form_step_issues({
        "surface": "とても", "type": "word", "lemma": "とても",
        "surface_kana": "とても", "lemma_kana": "とても",
        "conjugation_form": "non-inflecting", "form_steps": [],
    }) == []


def test_certain_aru_uses_verified_local_determiner_head_not_existential_verb():
    good = {
        "surface": "ある", "type": "word", "lemma": "或",
        "surface_kana": "ある", "lemma_kana": "ある",
        "part_of_speech": "determiner", "conjugation_form": "non-inflecting",
        "meaning_en": "a certain", "dictionary_key": "或",
        "dictionary_definition_en": "a certain; some", "form_steps": [],
    }
    assert japanese_learner_segmentation_issues([good]) == []
    bad = {**good, "lemma": "或る", "dictionary_key": ""}
    assert any(
        issue["surface"] == "ある" and "或" in issue["message"]
        for issue in japanese_learner_segmentation_issues([bad])
    )


def test_n5_chapter_gate_rejects_cross_paragraph_register_switch():
    mixed = (
        "吾輩は家に入りました。主人を見ました。ここに住みました。\n\n"
        "主人は教師だった。吾輩はそばにいた。子供と寝た。"
    )
    assert japanese_narrative_register_issues(mixed, "n5")
    assert japanese_narrative_register_issues(
        mixed.replace("だった", "でした").replace("いた", "いました").replace("寝た", "寝ました"),
        "n5",
    ) == []


def test_n5_beginner_prose_gate_rejects_literary_source_leaks():
    issues = japanese_beginner_prose_issues(
        "誰も来ず、吾輩は住家と極めました。笹原へ棄てられました。", "n5",
    )
    assert len(issues) == 3
    assert japanese_beginner_prose_issues(
        "誰も来ません。吾輩はこの家に住むことにしました。笹原へ捨てられました。",
        "n5",
    ) == []
    assert japanese_beginner_prose_issues("兄弟も母も見えず、ひとりでした。", "n5")
    assert japanese_beginner_prose_issues(
        "吾輩は池の左をゆっくり行きました。", "n5",
    )
    assert japanese_beginner_prose_issues(
        "吾輩は池を左に見ながら、ゆっくり歩きました。", "n5",
    ) == []


def test_n4_beginner_prose_gate_requires_modern_optional_spellings():
    issues = japanese_beginner_prose_issues(
        "何も考えず、猫を棄てる事もある。", "n4",
    )
    assert len(issues) == 3
    assert japanese_beginner_prose_issues(
        "何も考えないで、猫を捨てることもある。", "n4",
    ) == []


def test_preflight_level_gate_uses_lemmas_without_publishing_tokenizer_segments():
    easy = preflight_level_diagnostics(
        "猫は家に入りました。何度も見ました。", "n5",
    )
    assert easy["passes"] is True
    assert easy["tokenizer"].startswith("fugashi-unidic-lite")
    hard = preflight_level_diagnostics(
        "胸が痛く、腹が減り、台所へ戻りました。", "n5",
    )
    assert hard["passes"] is False
    assert {"胸", "腹", "減る", "戻る"}.intersection(hard["sample"])


def test_preflight_excludes_reviewed_recurring_story_terms():
    hard = preflight_level_diagnostics("書生は書生です。", "n5")
    easy = preflight_level_diagnostics(
        "書生は書生です。", "n5", [{"surface": "書生", "lemma": "書生"}],
    )
    assert hard["passes"] is False
    assert easy["passes"] is True


def test_invalid_optional_story_terms_are_dropped_deterministically():
    harness = object.__new__(JapaneseChapterHarness)
    harness.args = SimpleNamespace(level="n5")
    plan = {"terms": [{
        "surface": "人間", "lemma": "人間", "lemma_kana": "にんげん",
        "meaning_en": "human", "importance_en": "claimed recurring concept",
    }]}
    assert harness.sanitize_story_vocabulary_plan(
        plan, "初めて人間を見ました。",
    ) == {"terms": []}


def test_preflight_counts_agent_grouped_sokode_as_one_lexical_unit():
    diagnostics = preflight_level_diagnostics("そこで猫を見ました。", "n5")
    assert "そこで" in diagnostics["sample"]


def test_basic_grouped_n5_connectors_do_not_become_special_vocabulary():
    diagnostics = preflight_level_diagnostics(
        "その時、猫を見ました。そして家に入りました。", "n5",
    )
    assert "その時" not in diagnostics["sample"]
    assert "そして" not in diagnostics["sample"]


def test_chapter_paragraph_gate_preserves_selected_scene_blocks():
    assert japanese_paragraph_structure_issues("一つ。\n\n二つ。\n", 2) == []
    assert japanese_paragraph_structure_issues("一つ。二つ。\n", 2)


def test_final_vocabulary_rescue_requests_only_minimum_needed_changes():
    assert minimum_vocabulary_replacements({
        "tokens_considered": 48,
        "above_level_tokens": 5,
        "maximum_above_level_ratio": 0.10,
    }) == 1
    assert minimum_vocabulary_replacements({
        "tokens_considered": 40,
        "above_level_tokens": 4,
        "maximum_above_level_ratio": 0.10,
    }) == 0


def test_functional_grammar_unit_may_use_an_empty_form_chain():
    assert japanese_form_step_issues({
        "surface": "ですが", "type": "grammar", "lemma": "です",
        "surface_kana": "ですが", "lemma_kana": "です",
        "conjugation_form": "polite contrastive connector", "form_steps": [],
    }) == []


def test_standalone_nai_cannot_masquerade_as_an_auxiliary_word():
    assert "standalone ない" in japanese_segment_issue("ない", "auxiliary")
    assert japanese_segment_issue("ない", "word") is None


def test_unchanged_plain_form_does_not_repeat_itself_as_an_arrow_step():
    annotation = {"segments": [{
        "surface": "とる", "surface_kana": "とる",
        "lemma": "とる", "lemma_kana": "とる", "type": "word",
        "form_steps": [{
            "form": "とる", "reading": "とる", "label": "plain",
            "meaning_en": "take",
        }],
    }]}
    normalize_redundant_japanese_form_steps(annotation)
    assert annotation["segments"][0]["form_steps"] == []


def test_kana_surface_to_kanji_headword_is_not_a_conjugation_step():
    annotation = {"segments": [{
        "surface": "とる", "surface_kana": "とる",
        "lemma": "捕る", "lemma_kana": "とる", "type": "word",
        "form_steps": [{
            "form": "とる", "reading": "とる", "label": "plain nonpast",
            "meaning_en": "catch",
        }],
    }]}
    normalize_redundant_japanese_form_steps(annotation)
    assert annotation["segments"][0]["form_steps"] == []
    assert japanese_form_step_issues(annotation["segments"][0]) == []


def test_inflected_construction_grammar_keeps_its_form_chain():
    annotation = {"segments": [{
        "surface": "速くした", "surface_kana": "はやくした",
        "lemma": "速くする", "lemma_kana": "はやくする", "type": "grammar",
        "form_steps": [{
            "form": "速くした", "reading": "はやくした",
            "label": "plain past", "meaning_en": "made faster",
        }],
    }]}
    normalize_redundant_japanese_form_steps(annotation)
    assert [
        step["form"] for step in annotation["segments"][0]["form_steps"]
    ] == ["速くした"]


def test_form_chain_removes_displayed_lemma_before_later_inflection():
    annotation = {"segments": [{
        "surface": "入れなくなった", "surface_kana": "いれなくなった",
        "lemma": "入れなくなる", "lemma_kana": "いれなくなる",
        "type": "grammar", "form_steps": [{
            "form": "入れなくなる", "reading": "いれなくなる",
        }, {
            "form": "入れなくなった", "reading": "いれなくなった",
        }],
    }]}
    normalize_redundant_japanese_form_steps(annotation)
    assert [
        step["form"] for step in annotation["segments"][0]["form_steps"]
    ] == ["入れなくなった"]


def test_adjective_root_stays_in_overlay_not_after_construction_lemma():
    annotation = {"segments": [{
        "surface": "悪くなり", "surface_kana": "わるくなり",
        "lemma": "悪くなる", "lemma_kana": "わるくなる", "type": "grammar",
        "form_steps": [{
            "form": "悪く", "reading": "わるく",
            "label": "く-form", "meaning_en": "unwell",
        }, {
            "form": "悪くなり", "reading": "わるくなり",
            "label": "conjunctive", "meaning_en": "becoming unwell, and",
        }],
    }]}
    normalize_redundant_japanese_form_steps(annotation)
    assert [
        step["form"] for step in annotation["segments"][0]["form_steps"]
    ] == ["悪くなり"]


def test_impossible_agent_dictionary_links_are_cleared_deterministically():
    annotation = {
        "segments": [
            {
                "surface": "教師", "type": "word", "lemma": "教師",
                "lemma_kana": "きょうし", "dictionary_key": "教師",
                "dictionary_definition_en": "teacher",
            },
            {
                "surface": "と", "type": "particle", "lemma": "と",
                "lemma_kana": "と", "dictionary_key": "と",
                "dictionary_definition_en": "and",
            },
            {
                "surface": "捨てる", "type": "word", "lemma": "捨てる",
                "lemma_kana": "すてる", "dictionary_key": "捨てる",
                "dictionary_definition_en": "abandon",
            },
            {
                "surface": "つかまえて", "type": "word", "lemma": "捕まえる",
                "lemma_kana": "つかまえる", "meaning_en": "catching",
                "dictionary_key": "", "dictionary_definition_en": "",
            },
        ],
        "grammar_overlays": [{
            "components": [{
                "surface": "すいて", "lookup_kind": "lexical",
                "lemma": "すく", "lemma_kana": "すく",
                "dictionary_key": "空く",
                "dictionary_definition_en": "become empty",
            }],
        }],
    }
    clear_unavailable_dictionary_links(annotation)
    assert annotation["segments"][0]["dictionary_key"] == ""
    assert annotation["segments"][1]["dictionary_key"] == ""
    assert annotation["segments"][2]["dictionary_key"] == "捨てる"
    assert annotation["segments"][3]["dictionary_key"] == "捕まえる"
    assert annotation["segments"][3]["dictionary_definition_en"] == "catching"
    assert annotation["grammar_overlays"][0]["components"][0]["dictionary_key"] == ""
    assert annotation["grammar_overlays"][0]["components"][0]["lookup_kind"] == "none"


def test_suru_compound_component_links_to_available_nominal_dictionary_head():
    annotation = {"segments": [], "grammar_overlays": [{"components": [{
        "surface": "写生し", "lookup_kind": "lexical",
        "lemma": "写生する", "lemma_kana": "しゃせいする",
        "function_en": "the sketching action in conjunctive form",
        "dictionary_key": "写生する",
        "dictionary_definition_en": "sketch",
    }]}]}
    clear_unavailable_dictionary_links(annotation)
    component = annotation["grammar_overlays"][0]["components"][0]
    assert component["lemma"] == "写生"
    assert component["lemma_kana"] == "しゃせい"
    assert component["dictionary_key"] == "写生"
    assert component["lookup_kind"] == "lexical"


def test_proper_name_does_not_link_to_an_unrelated_common_noun_homograph():
    annotation = {"segments": [{
        "surface": "黒", "type": "name", "lemma": "黒",
        "lemma_kana": "くろ", "meaning_en": "Kuro (a cat's name)",
        "dictionary_key": "黒", "dictionary_definition_en": "black",
    }]}
    clear_unavailable_dictionary_links(annotation)
    assert annotation["segments"][0]["dictionary_key"] == ""
    assert annotation["segments"][0]["dictionary_definition_en"] == ""


def test_review_does_not_invent_a_proper_name_enum_outside_the_schema():
    review = {"verdict": "revise", "issues": [{
        "segment_text": "黒", "problem": "wrong_type",
        "explanation": "This named animal should use the proper-name category.",
        "suggested_fix": "Change type from name to proper_name.",
    }]}
    annotation = {"segments": [{"surface": "黒", "type": "name"}]}
    assert apply_reader_useful_annotation_review_policy(
        review, annotation
    ) == {"verdict": "pass", "issues": []}


def test_review_does_not_duplicate_adjective_root_overlay_in_form_chain():
    review = {"verdict": "revise", "issues": [{
        "segment_text": "悪くなった", "problem": "conjugation",
        "explanation": (
            "The derivation hides the meaningful adjective connective stage 悪く."
        ),
        "suggested_fix": (
            "Use form_steps [悪く (adjective connective form), "
            "悪くなった (plain past)]."
        ),
    }]}
    annotation = {"segments": [{
        "surface": "悪くなった", "type": "grammar",
        "lemma": "悪くなる", "lemma_kana": "わるくなる",
    }]}
    assert apply_reader_useful_annotation_review_policy(
        review, annotation,
    ) == {"verdict": "pass", "issues": []}


def test_review_keeps_lexical_head_primary_when_whole_grammar_overlay_exists():
    review = {"verdict": "revise", "issues": [{
        "segment_text": "する / ようになった", "problem": "under_grouped",
        "explanation": "Use one grammar primary and retain する only in its overlay.",
        "suggested_fix": "Merge into one primary.",
    }]}
    annotation = {
        "segments": [
            {"surface": "する", "type": "word"},
            {"surface": "ようになった", "type": "grammar"},
        ],
        "grammar_overlays": [{"surface": "するようになった"}],
    }
    assert apply_reader_useful_annotation_review_policy(
        review, annotation
    ) == {"verdict": "pass", "issues": []}


def test_review_cannot_demand_a_dictionary_entry_that_does_not_exist():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "書生",
            "problem": "lemma_reading",
            "explanation": "The lexical verb is missing its dictionary link.",
            "suggested_fix": "Set dictionary_key to canonical 書生.",
        }],
    }
    annotation = {"segments": [{
        "surface": "書生", "type": "word", "lemma": "書生",
        "lemma_kana": "しょせい", "dictionary_key": "",
        "dictionary_definition_en": "",
    }]}
    assert apply_reader_useful_annotation_review_policy(
        review, annotation
    ) == {"verdict": "pass", "issues": []}


def test_review_cannot_reject_a_link_that_passes_canonical_validation():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "何も",
            "problem": "lemma_reading",
            "explanation": "The dictionary key なにも is only a reading alias.",
            "suggested_fix": "Use a different canonical dictionary_key.",
        }],
    }
    annotation = {"segments": [{
        "surface": "何も", "type": "word", "lemma": "何も",
        "lemma_kana": "なにも", "dictionary_key": "なにも",
        "dictionary_definition_en": "nothing",
    }]}
    assert apply_reader_useful_annotation_review_policy(
        review, annotation
    ) == {"verdict": "pass", "issues": []}


@pytest.mark.parametrize(
    ("surface", "lemma", "lemma_kana", "part_of_speech"),
    [
        ("捨てられていました", "捨てる", "すてる", "verb"),
        ("痛かったです", "痛い", "いたい", "i-adjective"),
    ],
)
def test_inflected_lexical_heads_must_not_be_typed_as_grammar(
    surface, lemma, lemma_kana, part_of_speech,
):
    segment = {
        "surface": surface,
        "type": "grammar",
        "lemma": lemma,
        "lemma_kana": lemma_kana,
        "part_of_speech": part_of_speech,
        "conjugation_form": "inflected",
    }
    issues = japanese_learner_segmentation_issues([segment])
    assert any("word tap target" in issue["message"] for issue in issues)


def test_productive_compound_verb_requires_roots_overlay():
    segment = {
        "surface": "動き出し", "type": "word", "lemma": "動き出す",
        "part_of_speech": "compound verb",
        "conjugation_form": "conjunctive ren'yōkei",
    }
    issues = japanese_required_overlay_issues([segment], [])
    assert any(issue["surface"] == "動き出し" for issue in issues)
    assert japanese_required_overlay_issues([segment], [{
        "surface": "動き出し",
    }]) == []


@pytest.mark.parametrize(
    "overlay",
    [
        {
            "surface": "と思ったとき",
            "components": [
                {"surface": "と"}, {"surface": "思った"}, {"surface": "とき"},
            ],
        },
        {
            "surface": "寝ている猫",
            "components": [{"surface": "寝ている"}, {"surface": "猫"}],
        },
        {
            "surface": "と言った",
            "components": [{"surface": "と"}, {"surface": "言った"}],
        },
        {
            "surface": "棄てられていた",
            "components": [
                {"surface": "棄てられ", "lemma": "棄てる"},
                {"surface": "ていた", "lemma": "いる"},
            ],
        },
        {
            "surface": "来るかと思い",
            "grammar_candidate_key": "ka_to_omou",
            "components": [
                {"surface": "来るか"}, {"surface": "と思い"},
            ],
        },
        {
            "surface": "来るかと思い",
            "grammar_candidate_key": "ka_to_omou",
            "components": [
                {"surface": "来る"}, {"surface": "か"},
                {"surface": "と"}, {"surface": "思い"},
            ],
        },
        {
            "surface": "していたり",
            "components": [
                {"surface": "していた"}, {"surface": "り"},
            ],
        },
        {
            "surface": "動かずにいた",
            "components": [
                {"surface": "動かず"}, {"surface": "にいた"},
            ],
        },
        {
            "surface": "だということを",
            "components": [
                {"surface": "だ"}, {"surface": "という"},
                {"surface": "ことを"},
            ],
        },
        {
            "surface": "あとで",
            "components": [{"surface": "あと"}, {"surface": "で"}],
        },
        {
            "surface": "のだろう",
            "components": [{"surface": "の"}, {"surface": "だろう"}],
        },
        {
            "surface": "捕まえて煮て食うという",
            "components": [
                {"surface": "捕まえて"}, {"surface": "煮て"},
                {"surface": "食う"}, {"surface": "という"},
            ],
        },
        {
            "surface": "見たのは",
            "components": [
                {"surface": "見た"}, {"surface": "の"}, {"surface": "は"},
            ],
        },
    ],
)
def test_overlay_policy_rejects_mechanically_redundant_clutter(overlay):
    assert japanese_overlay_policy_issue(overlay)


def test_overlay_policy_keeps_reusable_multi_part_construction():
    overlay = {
        "surface": "歩くことにしました",
        "components": [
            {"surface": "歩く"}, {"surface": "ことにしました"},
        ],
    }
    assert japanese_overlay_policy_issue(overlay) is None
    assert japanese_overlay_policy_issue({
        "surface": "来るかと思い",
        "grammar_candidate_key": "ka_to_omou",
        "components": [
            {"surface": "来る"}, {"surface": "かと思い"},
        ],
    }) is None


def test_overlay_policy_requires_linkable_roots_for_productive_compounds():
    bad = {
        "surface": "動き出した",
        "head_lemma": "動き出す",
        "grammar_candidate_key": "compound-ugoki-dasu",
        "components": [
            {"surface": "動き", "lemma": "動く", "lookup_kind": "lexical"},
            {"surface": "出した", "lemma": "出す", "lookup_kind": "grammar"},
        ],
    }
    assert japanese_overlay_policy_issue(bad)
    bad["components"][1]["lookup_kind"] = "lexical"
    assert japanese_overlay_policy_issue(bad) is None


def test_overlay_policy_allows_unavailable_compound_root_as_nonclickable():
    # The bundled dictionary has no exact 回る/まわる headword. The component
    # still explains the semantic root, but it must not invent a click target.
    overlay = {
        "surface": "回り始めた",
        "head_lemma": "回り始める",
        "grammar_candidate_key": "v-hajimeru",
        "components": [
            {
                "surface": "回り", "lemma": "回る", "lemma_kana": "まわる",
                "lookup_kind": "none",
            },
            {
                "surface": "始めた", "lemma": "始める",
                "lemma_kana": "はじめる", "lookup_kind": "lexical",
            },
        ],
    }
    assert japanese_overlay_policy_issue(overlay) is None


def test_overlay_policy_requires_linkable_roots_for_adjective_change():
    bad = {
        "surface": "速くなりました",
        "head_lemma": "速くなる",
        "grammar_candidate_key": "adjective-ku-naru",
        "components": [{
            "surface": "速くなりました", "lemma": "速くなる",
            "lookup_kind": "grammar",
        }],
    }
    assert japanese_overlay_policy_issue(bad)
    good = {
        **bad,
        "components": [
            {"surface": "速く", "lemma": "速い", "lookup_kind": "lexical"},
            {"surface": "なりました", "lemma": "なる", "lookup_kind": "lexical"},
        ],
    }
    assert japanese_overlay_policy_issue(good) is None
    assert japanese_overlay_policy_issue({
        "surface": "動かずにいた",
        "components": [
            {"surface": "動かずに", "lemma": "動く"},
            {"surface": "いた", "lemma": "いる"},
        ],
    }) is None
    assert japanese_overlay_policy_issue({
        "surface": "三、四十匹",
        "components": [
            {"surface": "三", "lemma": "三"},
            {"surface": "四十匹", "lemma": "四十匹"},
        ],
    }) is None


@pytest.mark.parametrize("components", [
    [{"surface": "三"}, {"surface": "、"}, {"surface": "四十匹"}],
    [{"surface": "三"}, {"surface": "四十"}, {"surface": "匹"}],
])
def test_overlay_policy_rejects_tokenizer_sized_numeric_range_parts(components):
    assert japanese_overlay_policy_issue({
        "surface": "三、四十匹",
        "components": components,
    })


def test_overlay_policy_rejects_routine_noun_plus_copula_and_atomic_no_you_da():
    assert japanese_overlay_policy_issue({
        "surface": "見始めだった",
        "components": [
            {"surface": "見始め", "lemma": "見始め"},
            {"surface": "だった", "lemma": "だ"},
        ],
    })
    assert japanese_overlay_policy_issue({
        "surface": "のようだった",
        "components": [
            {"surface": "のよう", "lemma": "のようだ"},
            {"surface": "だった", "lemma": "だ"},
        ],
    })
    assert japanese_overlay_policy_issue({
        "surface": "やかんのようだった",
        "components": [
            {"surface": "やかん", "lemma": "やかん"},
            {"surface": "のようだった", "lemma": "のようだ"},
        ],
    }) is None
    assert japanese_overlay_policy_issue({
        "surface": "猫なのか",
        "components": [
            {"surface": "猫", "lemma": "猫"},
            {"surface": "なのか", "lemma": "だ"},
        ],
    }) is None
    assert japanese_overlay_policy_issue({
        "surface": "こりごりだ",
        "components": [
            {"surface": "こりごり", "lemma": "こりごり"},
            {"surface": "だ", "lemma": "だ"},
        ],
    }) is None
    assert japanese_overlay_policy_issue({
        "surface": "だめだ",
        "grammar_candidate_key": "dame-da",
        "form_label": "predicative with plain copula",
        "components": [
            {"surface": "だめ", "lemma": "だめ"},
            {"surface": "だ", "lemma": "だ"},
        ],
    }) is None
    assert japanese_overlay_policy_issue({
        "surface": "のである",
        "components": [
            {"surface": "の", "lemma": "の"},
            {"surface": "である", "lemma": "である"},
        ],
    }) is None


@pytest.mark.parametrize("surface", [
    "人間というものの見始めだったのだろう",
    "の見始めだったのだろう",
    "毛があるはずの",
])
def test_overlay_policy_rejects_clause_like_chapter_overlays(surface):
    assert japanese_overlay_policy_issue({
        "surface": surface,
        "components": [{"surface": surface[:-1]}, {"surface": surface[-1:]}],
    })


def test_primary_segmentation_keeps_quotative_to_outside_thought_verb():
    segments = [{
        "surface": "と思った", "type": "word", "lemma": "思う",
        "conjugation_form": "plain past",
    }]
    issues = japanese_learner_segmentation_issues(segments)
    assert [issue["surface"] for issue in issues] == ["と思った"]
    assert japanese_learner_segmentation_issues([{
        "surface": "かと思った", "type": "grammar", "lemma": "かと思う",
        "conjugation_form": "plain past",
    }]) == []


def test_primary_segmentation_exposes_head_before_hazuno():
    issues = japanese_learner_segmentation_issues([{
        "surface": "あるはずの", "type": "grammar", "lemma": "あるはずだ",
        "conjugation_form": "attributive",
    }])
    assert [issue["surface"] for issue in issues] == ["あるはずの"]


def test_primary_segmentation_rejects_copular_surface_with_verb_lemma():
    issues = japanese_learner_segmentation_issues([{
        "surface": "見始めだった", "type": "grammar", "lemma": "見始める",
        "conjugation_form": "plain past",
    }])
    assert [issue["surface"] for issue in issues] == ["見始めだった"]


def test_primary_segmentation_keeps_inflected_noyouda_together():
    segments = [
        {"surface": "の", "type": "particle", "lemma": "の"},
        {"surface": "よう", "type": "word", "lemma": "よう"},
        {"surface": "だった", "type": "auxiliary", "lemma": "だ"},
    ]
    issues = japanese_learner_segmentation_issues(segments)
    assert [issue["surface"] for issue in issues] == ["のようだった"]


def test_mou_before_negative_uses_negative_polarity_sense():
    segments = [
        {
            "surface": "もう", "type": "word", "lemma": "もう",
            "conjugation_form": "non-inflecting",
            "meaning_en": "already; by then",
        },
        {
            "surface": "待てなかった", "type": "word", "lemma": "待つ",
            "conjugation_form": "plain potential negative past",
            "meaning_en": "could no longer wait",
        },
    ]
    issues = japanese_learner_segmentation_issues(segments)
    assert [issue["surface"] for issue in issues] == ["もう"]
    segments[0]["meaning_en"] = "any longer; no more"
    assert japanese_learner_segmentation_issues(segments) == []


@pytest.mark.parametrize(
    "surface", ["目の見えない", "小便がしたくなり", "胸が悪くなり"],
)
def test_contextual_whole_help_stays_overlay_not_opaque_primary(surface):
    issues = japanese_learner_segmentation_issues([{
        "surface": surface, "type": "grammar", "lemma": surface,
        "conjugation_form": "contextual construction",
    }])
    assert [issue["surface"] for issue in issues] == [surface]


def test_contextual_phrase_contract_tells_repair_agent_to_split_not_merge():
    result = {"segments": [{
        "surface": "胸が悪くなり", "type": "idiom", "lemma": "胸が悪くなる",
        "surface_kana": "むねがわるくなり", "lemma_kana": "むねがわるくなる",
        "part_of_speech": "collocation", "conjugation_form": "conjunctive",
        "meaning_en": "felt sick", "story_role": "none",
        "story_importance_en": "",
    }], "grammar_overlays": []}
    issues = JapaneseChapterHarness.annotation_contract_issues(
        "胸が悪くなり", result,
    )
    split_issue = next(
        issue for issue in issues
        if issue["segment_text"] == "胸が悪くなり"
        and issue["problem"] == "over_grouped"
    )
    assert "Split the opaque primary" in split_issue["suggested_fix"]


def test_collocation_key_is_not_mistaken_for_inner_adjective_change_key():
    overlay = {
        "surface": "胸が悪くなり",
        "grammar_candidate_key": "mune-ga-waruku-naru",
        "head_lemma": "胸が悪くなる",
        "components": [
            {"surface": "胸", "lemma": "胸", "lookup_kind": "lexical"},
            {"surface": "が", "lemma": "が", "lookup_kind": "grammar"},
            {
                "surface": "悪くなり", "lemma": "悪くなる",
                "lookup_kind": "grammar",
            },
        ],
    }
    assert japanese_overlay_policy_issue(overlay) is None


def test_canonicalizer_crops_adjective_lesson_out_of_wider_collocation():
    candidate = {
        "segments": [{"surface": "胸が悪くなり", "type": "word"}],
        "grammar_overlays": [{
            "start": 0, "end": 6, "surface": "胸が悪くなり",
            "grammar_candidate_key": "a-ku-naru",
            "head_lemma": "なる", "head_lemma_kana": "なる",
            "components": [
                {"start": 0, "end": 1, "surface": "胸", "lemma": "胸", "lemma_kana": "むね"},
                {"start": 1, "end": 2, "surface": "が", "lemma": "が", "lemma_kana": "が"},
                {"start": 2, "end": 4, "surface": "悪く", "lemma": "悪い", "lemma_kana": "わるい"},
                {"start": 4, "end": 6, "surface": "なり", "lemma": "なる", "lemma_kana": "なる"},
            ],
        }],
    }
    normalized = JapaneseChapterHarness.canonicalize_annotation_candidate(
        "胸が悪くなり", candidate,
    )
    overlay = normalized["grammar_overlays"][0]
    assert (overlay["start"], overlay["end"], overlay["surface"]) == (
        2, 6, "悪くなり",
    )
    assert [part["surface"] for part in overlay["components"]] == [
        "悪く", "なり",
    ]
    assert overlay["head_lemma"] == "悪くなる"
    assert overlay["head_lemma_kana"] == "わるくなる"


def test_directional_te_kuru_primary_keeps_content_verb_lemma():
    issues = japanese_learner_segmentation_issues([{
        "surface": "飛び出してくる", "type": "grammar",
        "lemma": "飛び出してくる", "part_of_speech": "compound verb construction",
        "conjugation_form": "て-form + come; directional nonpast",
    }])
    assert [issue["surface"] for issue in issues] == ["飛び出してくる"]
    assert japanese_learner_segmentation_issues([{
        "surface": "飛び出してくる", "type": "grammar",
        "lemma": "飛び出す", "part_of_speech": "verb construction",
        "conjugation_form": "て-form + 来る; directional nonpast",
    }]) == []


def test_benefactive_chain_is_one_primary_predicate():
    split = [
        {
            "surface": "置いて", "type": "word", "lemma": "置く",
            "part_of_speech": "verb", "conjugation_form": "て-form",
        },
        {
            "surface": "やれ", "type": "auxiliary", "lemma": "やる",
            "part_of_speech": "benefactive auxiliary verb",
            "conjugation_form": "imperative",
        },
    ]
    issues = japanese_learner_segmentation_issues(split)
    assert any(issue["surface"] == "置いてやれ" for issue in issues)
    assert japanese_learner_segmentation_issues([{
        "surface": "置いてやれ", "type": "word", "lemma": "置く",
        "part_of_speech": "verb with benefactive helper",
        "conjugation_form": "て-form + benefactive やる, imperative",
    }]) == []


def test_directional_te_iku_is_one_primary_but_true_sequence_is_not():
    split = [
        {
            "surface": "歩いて", "type": "word", "lemma": "歩く",
            "part_of_speech": "verb", "conjugation_form": "て-form",
        },
        {
            "surface": "行く", "type": "word", "lemma": "行く",
            "part_of_speech": "verb", "conjugation_form": "plain nonpast",
        },
    ]
    issues = japanese_learner_segmentation_issues(split)
    assert any(issue["surface"] == "歩いて行く" for issue in issues)
    assert japanese_learner_segmentation_issues([{
        "surface": "歩いて行く", "type": "word", "lemma": "歩く",
        "part_of_speech": "verb with directional helper",
        "conjugation_form": "て行く, directional nonpast",
    }]) == []
    assert japanese_learner_segmentation_issues([
        {
            "surface": "来て", "type": "word", "lemma": "来る",
            "part_of_speech": "verb", "conjugation_form": "て-form",
        },
        {"surface": "、", "type": "punctuation"},
        {
            "surface": "見た", "type": "word", "lemma": "見る",
            "part_of_speech": "verb", "conjugation_form": "plain past",
        },
    ]) == []


def test_mimetic_suru_form_is_one_primary_predicate():
    split = [
        {
            "surface": "ぐるぐる", "type": "word", "lemma": "ぐるぐる",
            "part_of_speech": "adverb", "conjugation_form": "non-inflecting",
        },
        {
            "surface": "して", "type": "word", "lemma": "する",
            "part_of_speech": "verb", "conjugation_form": "て-form",
        },
    ]
    issues = japanese_learner_segmentation_issues(split)
    assert any(issue["surface"] == "ぐるぐるして" for issue in issues)
    assert japanese_learner_segmentation_issues([{
        "surface": "ぐるぐるして", "type": "word", "lemma": "ぐるぐるする",
        "part_of_speech": "mimetic suru verb", "conjugation_form": "て-form",
    }]) == []
    assert any(
        issue["surface"] == "ぐるぐるして"
        for issue in japanese_required_overlay_issues([{
            "surface": "ぐるぐるして", "type": "word",
            "lemma": "ぐるぐるする",
        }], [])
    )


def test_adverbial_to_is_one_primary_target_with_parts_overlay():
    split = [
        {
            "surface": "ふわり", "type": "word", "lemma": "ふわり",
            "part_of_speech": "adverb", "conjugation_form": "non-inflecting",
        },
        {
            "surface": "と", "type": "particle", "lemma": "と",
            "part_of_speech": "adverbial particle",
            "conjugation_form": "non-inflecting",
        },
    ]
    assert [
        issue["surface"] for issue in japanese_learner_segmentation_issues(split)
    ] == ["ふわりと"]

    whole = [{
        "surface": "ふわりと", "type": "word", "lemma": "ふわり",
        "part_of_speech": "manner adverb with adverbial と",
        "conjugation_form": "mimetic-adverbial-to",
        "grammar_candidate_key": "mimetic-adverbial-to",
    }]
    assert japanese_learner_segmentation_issues(whole) == []
    assert [
        issue["surface"] for issue in japanese_required_overlay_issues(whole, [])
    ] == ["ふわりと"]


def test_connective_naku_and_past_rashii_require_scope_overlays():
    naku = [
        {"surface": "考え", "type": "word", "lemma": "考え"},
        {"surface": "が", "type": "particle", "lemma": "が"},
        {
            "surface": "なく", "type": "word", "lemma": "ない",
            "part_of_speech": "i-adjective",
            "conjugation_form": "connective く-form",
        },
    ]
    rashii = [
        {"surface": "だった", "type": "grammar", "lemma": "だ"},
        {"surface": "らしい", "type": "grammar", "lemma": "らしい"},
    ]
    assert [
        issue["surface"] for issue in japanese_required_overlay_issues(naku, [])
    ] == ["考えがなく"]
    assert [
        issue["surface"] for issue in japanese_required_overlay_issues(rashii, [])
    ] == ["だったらしい"]


def test_new_scope_overlay_component_contracts():
    fuwari = {
        "surface": "ふわりと", "head_lemma": "ふわりと",
        "components": [
            {"surface": "ふわり", "lookup_kind": "none"},
            {"surface": "と", "lookup_kind": "grammar"},
        ],
    }
    naku = {
        "surface": "考えがなく", "head_lemma": "考えがない",
        "components": [
            {"surface": "考え", "lookup_kind": "lexical"},
            {"surface": "が", "lookup_kind": "grammar"},
            {"surface": "なく", "lookup_kind": "grammar"},
        ],
    }
    rashii = {
        "surface": "だったらしい", "head_lemma": "だ + らしい",
        "components": [
            {"surface": "だった", "lookup_kind": "grammar"},
            {"surface": "らしい", "lookup_kind": "grammar"},
        ],
    }
    assert japanese_overlay_policy_issue(fuwari) is None
    assert japanese_overlay_policy_issue(naku) is None
    assert japanese_overlay_policy_issue(rashii) is None

    bad_fuwari = copy.deepcopy(fuwari)
    bad_fuwari["components"][1]["lookup_kind"] = "lexical"
    assert "grammar-only" in japanese_overlay_policy_issue(bad_fuwari)
    bad_naku = copy.deepcopy(naku)
    bad_naku["head_lemma"] = "ない"
    assert "考えがない" in japanese_overlay_policy_issue(bad_naku)
    bad_rashii = copy.deepcopy(rashii)
    bad_rashii["components"] = [{"surface": "だったらしい"}]
    assert "だった" in japanese_overlay_policy_issue(bad_rashii)


@pytest.mark.parametrize("surface", ["歩いて行く", "置いてやれ"])
def test_construction_predicates_require_internal_explanation(surface):
    segments = [{
        "surface": surface, "type": "word", "lemma": "歩く" if surface.startswith("歩") else "置く",
    }]
    assert any(
        issue["surface"] == surface
        for issue in japanese_required_overlay_issues(segments, [])
    )
    assert japanese_required_overlay_issues(
        segments, [{"surface": surface}],
    ) == []


def test_volitional_to_suru_keeps_action_separately_accessible():
    issues = japanese_learner_segmentation_issues([{
        "surface": "行こうとすると", "type": "grammar",
        "lemma": "行こうとする", "conjugation_form": "conditional",
    }])
    assert [issue["surface"] for issue in issues] == ["行こうとすると"]


def test_prenominal_aru_is_not_mislabeled_as_existential_verb():
    issues = japanese_learner_segmentation_issues([{
        "surface": "ある", "type": "word", "lemma": "ある",
        "part_of_speech": "godan verb", "conjugation_form": "plain nonpast",
        "meaning_en": "a certain; one",
    }])
    assert [issue["surface"] for issue in issues] == ["ある"]


@pytest.mark.parametrize(("first", "whole"), [
    ("何度", "何度も"),
    ("何回", "何回も"),
    ("一度", "一度も"),
])
def test_conventional_frequency_adverb_keeps_mo(first, whole):
    issues = japanese_learner_segmentation_issues([
        {"surface": first, "type": "word", "lemma": first},
        {"surface": "も", "type": "particle", "lemma": "も"},
    ])
    assert [issue["surface"] for issue in issues] == [whole]


@pytest.mark.parametrize(("number", "counter"), [
    ("三", "ページ"),
    ("四十", "匹"),
])
def test_number_and_counter_are_one_learner_facing_quantity(number, counter):
    issues = japanese_learner_segmentation_issues([
        {"surface": number, "type": "word", "lemma": number},
        {"surface": counter, "type": "word", "lemma": counter},
    ])
    assert any(issue["surface"] == number + counter for issue in issues)


def test_yonjuu_counter_reading_does_not_use_shijuu():
    bad = japanese_learner_segmentation_issues([{
        "surface": "四十匹", "type": "word", "lemma": "四十匹",
        "surface_kana": "しじっぴき", "part_of_speech": "numeral phrase",
    }])
    assert any(issue["surface"] == "四十匹" for issue in bad)
    assert japanese_learner_segmentation_issues([{
        "surface": "四十匹", "type": "word", "lemma": "四十匹",
        "surface_kana": "よんじゅっぴき", "part_of_speech": "numeral phrase",
    }]) == []


def test_copular_de_cannot_evade_predicative_grouping_as_particle():
    issues = japanese_learner_segmentation_issues([
        {
            "surface": "つるつる", "type": "word", "lemma": "つるつる",
            "part_of_speech": "adverbial mimetic",
        },
        {"surface": "で", "type": "particle", "lemma": "だ"},
    ])
    assert [issue["surface"] for issue in issues] == ["つるつるで"]


def test_required_overlay_policy_catches_contrastive_negative_and_numeric_range():
    contrastive = [
        {
            "surface": "広く", "type": "word", "lemma": "広い",
            "part_of_speech": "i-adjective",
        },
        {"surface": "は", "type": "particle", "lemma": "は"},
        {"surface": "ない", "type": "auxiliary", "lemma": "ない"},
    ]
    numeric = [
        {"surface": "三", "type": "word", "lemma": "三"},
        {"surface": "、", "type": "punctuation", "lemma": ""},
        {"surface": "四十", "type": "word", "lemma": "四十"},
        {"surface": "匹", "type": "word", "lemma": "匹"},
    ]
    assert [
        issue["surface"]
        for issue in japanese_required_overlay_issues(contrastive, [])
    ] == ["広くはない"]
    assert [
        issue["surface"]
        for issue in japanese_required_overlay_issues(numeric, [])
    ] == ["三、四十匹"]


def test_required_overlay_policy_accepts_present_explanations():
    segments = [
        {
            "surface": "広く", "type": "word", "lemma": "広い",
            "part_of_speech": "i-adjective",
        },
        {"surface": "は", "type": "particle", "lemma": "は"},
        {"surface": "ない", "type": "auxiliary", "lemma": "ない"},
    ]
    assert japanese_required_overlay_issues(
        segments, [{"surface": "広くはない"}],
    ) == []


def test_required_overlay_policy_keeps_reported_to_iu_predicate_sized():
    segments = [
        {
            "surface": "捕まえて", "type": "word", "lemma": "捕まえる",
            "part_of_speech": "verb",
        },
        {
            "surface": "煮て", "type": "word", "lemma": "煮る",
            "part_of_speech": "verb",
        },
        {
            "surface": "食う", "type": "word", "lemma": "食う",
            "part_of_speech": "verb",
        },
        {
            "surface": "という", "type": "grammar", "lemma": "という",
            "part_of_speech": "reported grammar",
        },
    ]
    assert [
        issue["surface"]
        for issue in japanese_required_overlay_issues(segments, [])
    ] == ["食うという"]
    assert japanese_required_overlay_issues(
        segments,
        [{"surface": "食うという"}],
    ) == []
    issues = japanese_required_overlay_issues(
        segments,
        [
            {"start": 6, "end": 11, "surface": "食うという"},
            {"start": 0, "end": 11, "surface": "捕まえて煮て食うという"},
        ],
    )
    assert [issue["surface"] for issue in issues] == ["捕まえて煮て食うという"]


def test_required_overlay_policy_rejects_nested_numeric_range_overlay():
    segments = [
        {"surface": "二", "type": "word", "lemma": "二"},
        {"surface": "、", "type": "punctuation", "lemma": ""},
        {"surface": "三ページ", "type": "word", "lemma": "三ページ"},
    ]
    overlays = [
        {"start": 0, "surface": "二、三"},
        {"start": 0, "surface": "二、三ページ"},
    ]
    issues = japanese_required_overlay_issues(segments, overlays)
    assert any(issue["surface"] == "二、三" for issue in issues)


def test_required_overlay_policy_requires_parts_for_primary_idiom():
    segments = [{
        "surface": "目が回り", "type": "idiom", "lemma": "目が回る",
    }]
    issues = japanese_required_overlay_issues(segments, [])
    assert [issue["surface"] for issue in issues] == ["目が回り"]
    assert japanese_required_overlay_issues(
        segments, [{"surface": "目が回り"}],
    ) == []


def test_required_overlay_policy_uses_complete_inflected_me_ga_mawaru_surface():
    segments = [
        {"surface": "目", "type": "word", "lemma": "目"},
        {"surface": "が", "type": "particle", "lemma": "が"},
        {"surface": "回りました", "type": "word", "lemma": "回る"},
    ]
    issues = japanese_required_overlay_issues(segments, [])
    assert [issue["surface"] for issue in issues] == ["目が回りました"]
    assert [
        issue["surface"] for issue in japanese_required_overlay_issues(
            segments, [{"surface": "目が回り"}],
        )
    ] == ["目が回りました"]
    assert japanese_required_overlay_issues(
        segments, [{"surface": "目が回りました"}],
    ) == []


def test_required_overlay_policy_requires_adjective_change_explanation():
    segments = [
        {
            "surface": "眠く", "type": "word", "lemma": "眠い",
            "part_of_speech": "i-adjective",
        },
        {"surface": "なる", "type": "word", "lemma": "なる"},
    ]
    issues = japanese_required_overlay_issues(segments, [])
    assert [issue["surface"] for issue in issues] == ["眠くなる"]


def test_required_overlay_policy_requires_content_word_sou_appearance():
    segments = [{
        "surface": "強そうだった", "type": "grammar", "lemma": "強い",
        "conjugation_form": "appearance auxiliary そうだ, past",
    }]
    issues = japanese_required_overlay_issues(segments, [])
    assert [issue["surface"] for issue in issues] == ["強そうだった"]
    assert japanese_required_overlay_issues([{
        "surface": "話そう", "type": "word", "lemma": "話す",
        "conjugation_form": "plain volitional",
    }], []) == []


def test_required_overlay_policy_keeps_contextual_japanese_aids():
    segments = [
        {"surface": "目", "type": "word", "lemma": "目"},
        {"surface": "の", "type": "particle", "lemma": "の"},
        {"surface": "見えない", "type": "word", "lemma": "見える"},
        {"surface": "、", "type": "punctuation", "lemma": ""},
        {"surface": "小便", "type": "word", "lemma": "小便"},
        {"surface": "が", "type": "particle", "lemma": "が"},
        {"surface": "したくなり", "type": "grammar", "lemma": "したくなる"},
    ]
    issues = japanese_required_overlay_issues(segments, [])
    assert {issue["surface"] for issue in issues} == {
        "目の見えない", "小便がしたくなり", "したくなり",
    }


def test_required_overlay_policy_does_not_depend_on_agent_idiom_type():
    segments = [{
        "surface": "気がつく", "type": "word", "lemma": "気がつく",
    }]
    issues = japanese_required_overlay_issues(segments, [])
    assert [issue["surface"] for issue in issues] == ["気がつく"]


def test_required_overlay_policy_requires_whole_adjective_change_form():
    segments = [{
        "surface": "悪くなり", "type": "word", "lemma": "悪くなる",
    }]
    issues = japanese_required_overlay_issues(segments, [])
    assert [issue["surface"] for issue in issues] == ["悪くなり"]


def test_learner_segmentation_keeps_adjective_change_as_complete_grammar_form():
    split = [
        {
            "surface": "速く", "type": "word", "lemma": "速い",
            "part_of_speech": "i-adjective",
        },
        {
            "surface": "なりました", "type": "word", "lemma": "なる",
            "part_of_speech": "verb",
        },
    ]
    assert any(
        issue["surface"] == "速くなりました"
        for issue in japanese_learner_segmentation_issues(split)
    )
    wrong_whole_type = [{
        "surface": "速くなりました", "type": "word", "lemma": "速くなる",
        "part_of_speech": "adjective-change construction",
    }]
    assert any(
        issue["surface"] == "速くなりました"
        for issue in japanese_learner_segmentation_issues(wrong_whole_type)
    )
    correct = [{
        "surface": "速くなりました", "type": "grammar", "lemma": "速くなる",
        "part_of_speech": "adjective-change construction",
    }]
    assert japanese_learner_segmentation_issues(correct) == []


def test_bare_adjective_change_is_grammar_not_idiom():
    issues = japanese_learner_segmentation_issues([{
        "surface": "悪くなり", "type": "idiom", "lemma": "悪くなる",
        "part_of_speech": "adjective change collocation",
        "conjugation_form": "conjunctive ren'yōkei",
    }])
    assert any(
        issue["surface"] == "悪くなり" and "type it as grammar" in issue["message"]
        for issue in issues
    )


def test_japanese_levels_do_not_imply_fixed_chapter_targets():
    for level in JLPT_LEVELS:
        harness = object.__new__(JapaneseChapterHarness)
        harness.args = Namespace(level=level, target_chars=None)
        assert harness.target_chars is None
        assert not harness.fixed_size_requested


def test_explicit_japanese_target_enables_fixed_size_mode():
    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(level="n3", target_chars=1200)
    assert harness.target_chars == 1200
    assert harness.fixed_size_requested


def test_japanese_count_includes_kana_and_kanji_not_punctuation_or_latin():
    assert japanese_char_count("吾輩は猫である。ABC カタカナ！") == 11
    assert japanese_char_count("時々") == 2


def test_explicitly_nonmaterial_review_notes_do_not_force_repair():
    assert material_review_findings([{
        "verdict": "pass",
        "distortions": ["位置関係がやや平板化されたが、出来事への影響はない。"],
        "unsupported_additions": [],
        "language_problems": [],
    }]) == []
    assert material_review_findings([{
        "verdict": "pass",
        "distortions": ["話者が逆転しており、出来事の因果が変わっている。"],
        "unsupported_additions": [],
        "language_problems": [],
    }]) != []
    assert material_review_findings([{
        "verdict": "pass",
        "distortions": ["原文の肴屋を魚屋としている。"],
        "unsupported_additions": [],
        "language_problems": [],
    }]) == []
    assert material_review_findings([{
        "verdict": "pass",
        "distortions": [],
        "unsupported_additions": [],
        "language_problems": [
            "Narrator self-reference is consistent; there is no unexplained switch.",
            "Concrete objects remain faithful and are appropriately simplified.",
        ],
    }]) == []


def test_n5_sentence_gate_is_separate_from_total_chapter_length():
    text = "猫は来た。" + "猫" * 31 + "。"
    assert overlong_japanese_sentences(text, "n5") == [(31, "猫" * 31 + "。")]
    assert overlong_japanese_sentences(text, "n4") == []


def test_duplicate_extracted_chapter_header_is_stripped_only_at_start():
    assert strip_duplicate_source_header("第11章　十一\n\n吾輩は猫。") == "吾輩は猫。"
    assert strip_duplicate_source_header("第一章　一\n\n吾輩は猫。") == "吾輩は猫。"
    assert strip_duplicate_source_header("吾輩は第11章を読む。") == "吾輩は第11章を読む。"


def test_source_boundary_snaps_unique_single_character_drift():
    source = "前段。主人は腹這いになって春日に甲羅を干している。後段。"
    quote = "主人は腹這になって春日に甲羅を干している。"
    assert resolve_source_boundary(source, quote) == source.index("主人は")


def test_source_boundary_rejects_ambiguous_or_material_mismatch():
    repeated = "主人は腹這いになって春日に甲羅を干す。別。主人は腹這いになって春日に甲羅を干す。"
    drifted = "主人は腹這になって春日に甲羅を干す。"
    assert resolve_source_boundary(repeated, drifted) == -1
    assert resolve_source_boundary("主人は庭で静かに本を読んでいる。", "主人は部屋で大声で歌っている。") == -1


def test_book_parser_defaults_to_all_jlpt_levels():
    args = parser().parse_args([
        "book", "--source-dir", "chapters", "--book-run-id", "wagahai",
    ])
    assert args.levels == JLPT_LEVELS
    assert args.concurrency == 12
    assert args.chapter_concurrency == 6
    assert args.source_dir == "chapters"


def test_japanese_annotation_chunks_default_to_small_learner_unit_batches():
    args = parser().parse_args([
        "run", "--source", "chapter_01.txt", "--level", "n5",
    ])
    harness = object.__new__(JapaneseChapterHarness)
    harness.args = args
    assert harness.annotation_chunk_target == 1
    assert harness.annotation_chunk_maximum == 45
    assert split_japanese_annotation_chunks(
        "猫です。犬も来ました。", target=harness.annotation_chunk_target,
        maximum=harness.annotation_chunk_maximum,
    ) == ["猫です。", "犬も来ました。"]
    assert JAPANESE_ANNOTATION_CHUNK_TARGETS["n4"] == 1
    assert JAPANESE_ANNOTATION_CHUNK_MAXIMUMS["n4"] == 60
    assert JAPANESE_ANNOTATION_CHUNK_TARGETS["n3"] == 1
    assert JAPANESE_ANNOTATION_CHUNK_TARGETS["n2"] == 1
    assert JAPANESE_ANNOTATION_CHUNK_TARGETS["n1"] == 1
    assert split_japanese_annotation_chunks(
        "吾輩は猫である。名前はまだない。",
        target=JAPANESE_ANNOTATION_CHUNK_TARGETS["n2"],
        maximum=JAPANESE_ANNOTATION_CHUNK_MAXIMUMS["n2"],
    ) == ["吾輩は猫である。", "名前はまだない。"]
    assert args.max_annotation_fresh_repairs == 6
    assert args.max_annotation_adjudications == 1
    assert "JAPANESE_ANNOTATION_CHUNK_POLICY" in harness.annotation_chunk_cache_tag


def test_japanese_manifest_records_current_learner_unit_policy(tmp_path):
    source = tmp_path / "chapter_01.txt"
    source.write_text("猫です。", encoding="utf-8")
    args = parser().parse_args([
        "run", "--source", str(source), "--level", "n5",
        "--runs-dir", str(tmp_path / "runs"),
    ])
    harness = JapaneseChapterHarness(args)
    harness.write_manifest("running")
    manifest = json.loads(
        (harness.run_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["annotation_chunk_policy"] == JAPANESE_ANNOTATION_CHUNK_POLICY


def test_japanese_annotation_chunks_never_cut_a_sentence_mid_construction():
    text = "猫は歩くことにしました。犬は家に帰りました。鳥も来ました。"
    chunks = split_japanese_annotation_chunks(text, target=20, maximum=30)
    assert "".join(chunks) == text
    assert chunks == ["猫は歩くことにしました。犬は家に帰りました。", "鳥も来ました。"]


def test_japanese_annotation_chunks_do_not_split_inside_quoted_sentence():
    text = '吾輩が「猫である。名前はまだない」と答えた。黒は笑った。'
    assert split_japanese_annotation_chunks(text, target=1, maximum=40) == [
        '吾輩が「猫である。名前はまだない」と答えた。',
        '黒は笑った。',
    ]


def test_japanese_annotation_chunks_split_complete_standalone_dialogue_sentences():
    text = '「猫である。名前はまだない。ここに住んでいる」と答えた。\n\n'
    assert split_japanese_annotation_chunks(text, target=1, maximum=40) == [
        '「猫である。',
        '名前はまだない。',
        'ここに住んでいる」と答えた。\n\n',
    ]
    assert "".join(split_japanese_annotation_chunks(
        text, target=1, maximum=40,
    )) == text


def test_n3_beginner_prose_gate_modernizes_optional_literary_spellings():
    issues = japanese_beginner_prose_issues(
        '名前は無い。小供は猫を棄てた。我儘な肴屋は体の善い泥棒だ。',
        'n3',
    )
    assert len(issues) == 6
    assert japanese_beginner_prose_issues(
        '名前はない。子供は猫を捨てた。わがままな魚屋は体のよい泥棒だ。',
        'n3',
    ) == []


def test_japanese_annotation_chunks_fail_closed_for_overlong_sentence():
    with pytest.raises(ValueError, match="do not split a sentence"):
        split_japanese_annotation_chunks("猫" * 31 + "。", target=20, maximum=30)


@pytest.mark.asyncio
async def test_progressive_annotation_worker_drains_queue_after_chunk_failure():
    class DrainHarness(JapaneseChapterHarness):
        async def annotate_chunk(self, index, chunk):
            self.visited.append(index)
            if index in {1, 4}:
                raise ValueError(f"bad sentence {index}")
            return {"segments": [], "grammar_overlays": [], "resolved": True}

    harness = object.__new__(DrainHarness)
    harness.args = Namespace(concurrency=2)
    harness.visited = []
    with pytest.raises(RuntimeError) as failure:
        await harness.annotate_chunks_progressively([
            "一。", "二。", "三。", "四。", "五。", "六。",
        ])
    assert sorted(harness.visited) == list(range(6))
    assert "chunk 1: bad sentence 1" in str(failure.value)
    assert "chunk 4: bad sentence 4" in str(failure.value)


def test_run_parser_accepts_reviewed_scene_reuse_directory():
    args = parser().parse_args([
        "run", "--source", "chapter_01.txt", "--level", "n2",
        "--reuse-scenes-from", "prior-run",
    ])
    assert args.reuse_scenes_from == "prior-run"


def test_annotation_refresh_can_target_only_selected_chunks():
    args = parser().parse_args([
        "run", "--source", "chapter_01.txt", "--level", "n4",
        "--resume-annotations", "--refresh",
        "--annotation-chunk-index", "1", "--annotation-chunk-index", "7",
    ])
    harness = object.__new__(JapaneseChapterHarness)
    harness.args = args
    assert harness.refresh_annotation_chunk(1) is True
    assert harness.refresh_annotation_chunk(7) is True
    assert harness.refresh_annotation_chunk(2) is False
    assert harness.reuse_unselected_annotation_cache(1) is False
    assert harness.reuse_unselected_annotation_cache(7) is False
    assert harness.reuse_unselected_annotation_cache(2) is True


def test_selective_annotation_refresh_reuses_review_only_for_exact_reader_text(tmp_path):
    args = parser().parse_args([
        "run", "--source", "chapter_01.txt", "--level", "n5",
        "--resume-annotations", "--refresh", "--annotation-chunk-index", "1",
    ])
    harness = object.__new__(JapaneseChapterHarness)
    harness.args = args
    harness.run_dir = tmp_path
    review = {
        "verdict": "pass", "omissions": [], "unsupported_additions": [],
        "distortions": [], "language_problems": [],
    }
    (tmp_path / "report.json").write_text(json.dumps({
        "status": "complete", "chapter_review": review,
    }))
    (tmp_path / "reader.json").write_text(json.dumps({"text": "猫です。\n"}))
    assert harness.reusable_chapter_review_for_selective_refresh(
        "猫です。\n"
    ) == review
    assert harness.reusable_chapter_review_for_selective_refresh(
        "猫でした。\n"
    ) is None


def test_reuses_only_source_identical_passing_scene_evidence(tmp_path):
    prior = tmp_path / "prior"
    current = tmp_path / "current"
    (prior / "scenes").mkdir(parents=True)
    current.mkdir()
    scene = {
        "id": "scene_01", "title": "猫", "source_start_quote": "猫",
        "required_events": ["猫が来た。"], "target_chars": 10,
        "source_start": 0, "source_end": 10,
    }
    evidence = {
        "scene": scene, "text": "猫" * 8,
        "review": {"verdict": "pass"}, "resolved": True,
        "attempts": [{"stage": "initial"}],
    }
    (prior / "scenes" / "scene_01.json").write_text(
        json.dumps(evidence, ensure_ascii=False), encoding="utf-8",
    )
    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(level="n2", reuse_scenes_from=str(prior))
    harness.run_dir = current

    assert harness.reusable_scene(scene) == evidence
    assert json.loads(
        (current / "scenes" / "scene_01.json").read_text(encoding="utf-8")
    ) == evidence
    assert harness.reusable_scene({**scene, "source_end": 11}) is None


def test_large_source_does_not_imply_an_automatic_chapter_size(tmp_path):
    source = tmp_path / "chapter_11.txt"
    source.write_text("原文", encoding="utf-8")
    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(level="n1", target_chars=None)
    harness.source_path = source
    harness.source = source.read_text(encoding="utf-8")
    assert harness.target_chars is None
    assert harness.scene_count == 1


def test_clean_vocabulary_rescue_may_use_soft_local_floor():
    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(level="n5")
    review = {
        "verdict": "revise", "omissions": [], "unsupported_additions": [],
        "distortions": [],
        "language_problems": [
            "mechanical unit length gate: 136 Japanese letters, required 140-260",
        ],
    }
    accepted = harness.accept_slightly_short_vocabulary_rescue(
        {"target_chars": 200}, "猫" * 136, review,
    )
    assert accepted["verdict"] == "pass"
    assert accepted["language_problems"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("target, expected", [(None, "pass"), (100, "revise")])
async def test_only_explicit_japanese_size_request_enables_numeric_scene_gate(target, expected):
    class Runner:
        async def call(self, *args, **kwargs):
            return {
                "source_fidelity": 9, "naturalness": 9, "readability": 9,
                "omissions": [], "unsupported_additions": [], "distortions": [],
                "language_problems": [], "verdict": "pass",
                "length_reason_en": "This complete short event is a natural stopping point for N4.",
            }

    text = "猫が来た。"
    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(
        level="n4", target_chars=target, review_effort="low", refresh=False,
    )
    harness.source, harness.runner = text, Runner()
    result = await harness.review_scene({
        "id": "scene_01", "source_start": 0, "source_end": len(text),
        "target_chars": target or 0, "required_events": [],
    }, text)
    assert result["verdict"] == expected


def test_long_source_forces_event_units_even_for_short_n5_output(tmp_path):
    source = tmp_path / "chapter_02.txt"
    source.write_text("猫" * 46322, encoding="utf-8")
    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(level="n5", target_chars=None)
    harness.source_path, harness.source = source, source.read_text()
    assert harness.target_chars is None
    assert harness.scene_count == 1


class CapturingRunner:
    def __init__(self):
        self.call_args = None

    async def call(self, *args, **kwargs):
        self.call_args = (args, kwargs)
        return {"segments": [{
            "surface": "猫", "type": "word", "lemma": "猫",
            "surface_kana": "ねこ", "lemma_kana": "ねこ",
            "part_of_speech": "noun", "conjugation_form": "non-inflecting",
            "meaning_en": "cat", "story_role": "none",
            "story_importance_en": "",
        }], "grammar_overlays": []}


@pytest.mark.asyncio
async def test_japanese_annotation_requests_reading_and_dictionary_segmentation():
    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(annotation_effort="low", refresh=False)
    harness.runner = CapturingRunner()
    result = await harness.annotation_candidate(0, "猫")
    prompt = harness.runner.call_args[0][1]
    schema = harness.runner.call_args[0][2]
    assert harness.runner.call_args[1]['workspace_context'] == {'chunk_text': '猫', 'language': 'ja'}
    assert result["segments"][0]["surface_kana"] == "ねこ"
    assert "learner-sized tap targets" in prompt
    assert "ありました is ONE word segment" in prompt
    assert "歩く (lemma 歩く) +\nことにしました" in prompt
    assert "置いてやれ is ONE `word` segment" in prompt
    assert "歩いて行く is one `word` segment with lemma 歩く" in prompt
    assert "置く -> 置いて -> 置いてやる -> 置いてやれ" in prompt
    assert "ぐるぐるして is\nONE `word` segment" in prompt
    assert "not every conjugational\nmorpheme" in prompt
    assert "do not mechanically add\nan overlay merely because" in prompt
    assert "ありました\nneeds lemma ある" in prompt
    assert "Zero overlays is\nvalid for a chunk" in prompt
    assert "conditional/temporal と is itself a precise" in prompt
    assert "formal-copula である" in prompt
    assert "single contextual cards mean `no one` and\n`nothing`" in prompt
    assert "dictionary_definition_en" in prompt
    assert "怖い -> 怖くない" in prompt
    assert "explain 動き出す as 動く + 出す" in prompt
    assert "physically healthy/well-built thief" in prompt
    assert "dictionary lemma 捕る" in prompt
    assert "来る + かと思い" in prompt
    assert schema.name == "japanese-annotation.schema.json"


@pytest.mark.asyncio
async def test_japanese_annotation_repairs_a_reviewed_fresh_restart():
    candidate = {
        "segments": [{
            "surface": "猫", "type": "word", "lemma": "猫",
            "surface_kana": "ねこ", "lemma_kana": "ねこ",
            "part_of_speech": "noun", "conjugation_form": "non-inflecting",
            "meaning_en": "cat", "story_role": "none",
            "story_importance_en": "",
        }],
        "grammar_overlays": [],
    }

    class TailHarness(JapaneseChapterHarness):
        async def annotation_candidate(self, index, chunk, **kwargs):
            return json.loads(json.dumps(candidate))

        async def review_annotation(self, index, chunk, annotation, stage):
            if stage == "fresh_repair_01":
                return {"verdict": "pass", "issues": []}
            return {
                "verdict": "revise",
                "issues": [{
                    "segment_text": "猫", "problem": "meaning",
                    "explanation": "Needs a contextual meaning.",
                    "suggested_fix": "Repair it.",
                }],
            }

    harness = object.__new__(TailHarness)
    harness.args = Namespace(
        max_annotation_repairs=0,
        max_annotation_fresh_repairs=2,
        annotation_repair_effort="low",
        annotation_final_effort="low",
        no_grammar_overlays=False,
    )
    result = await harness.annotate_chunk(0, "猫")
    assert result["resolved"] is True
    assert [attempt["stage"] for attempt in result["attempts"]] == [
        "initial", "fresh", "fresh_repair_01",
    ]


@pytest.mark.asyncio
async def test_annotation_workers_finish_chunk_lifecycles_before_taking_more():
    class ProgressiveHarness(JapaneseChapterHarness):
        async def annotate_chunk(self, index, chunk):
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            self.started.append(index)
            await asyncio.sleep(0.01 if index == 0 else 0)
            self.active -= 1
            return {"index": index, "chunk": chunk}

    harness = object.__new__(ProgressiveHarness)
    harness.args = Namespace(concurrency=2)
    harness.active = 0
    harness.maximum_active = 0
    harness.started = []
    result = await harness.annotate_chunks_progressively(["a", "b", "c", "d"])
    assert result == [
        {"index": 0, "chunk": "a"}, {"index": 1, "chunk": "b"},
        {"index": 2, "chunk": "c"}, {"index": 3, "chunk": "d"},
    ]
    assert harness.maximum_active == 2
    assert harness.started[:2] == [0, 1]


@pytest.mark.asyncio
async def test_japanese_annotation_does_not_adopt_truncated_fresh_candidate():
    complete = {
        "segments": [{
            "surface": "猫", "type": "word", "lemma": "猫",
            "surface_kana": "ねこ", "lemma_kana": "ねこ",
            "part_of_speech": "noun", "conjugation_form": "non-inflecting",
            "meaning_en": "cat", "story_role": "none",
            "story_importance_en": "",
        }],
        "grammar_overlays": [],
    }
    truncated = {
        "segments": [{
            "surface": "犬", "type": "word", "lemma": "犬",
            "surface_kana": "いぬ", "lemma_kana": "いぬ",
            "part_of_speech": "noun", "conjugation_form": "non-inflecting",
            "meaning_en": "dog", "story_role": "none",
            "story_importance_en": "",
        }],
        "grammar_overlays": [],
    }

    class TruncatedFreshHarness(JapaneseChapterHarness):
        async def annotation_candidate(self, index, chunk, **kwargs):
            if kwargs.get("stage") == "fresh":
                return json.loads(json.dumps(truncated))
            return json.loads(json.dumps(complete))

        async def review_annotation(self, index, chunk, annotation, stage):
            if stage == "fresh_repair_01":
                return {"verdict": "pass", "issues": []}
            return {
                "verdict": "revise",
                "issues": [{
                    "segment_text": "猫", "problem": "meaning",
                    "explanation": "Needs correction.",
                    "suggested_fix": "Correct it.",
                }],
            }

    harness = object.__new__(TruncatedFreshHarness)
    harness.args = Namespace(
        max_annotation_repairs=0,
        max_annotation_fresh_repairs=1,
        annotation_repair_effort="low",
        annotation_final_effort="low",
        no_grammar_overlays=False,
    )
    result = await harness.annotate_chunk(0, "猫")
    assert result["segments"][0]["surface"] == "猫"
    assert [attempt["stage"] for attempt in result["attempts"]] == [
        "initial", "fresh_contract_rejected", "fresh_repair_01",
    ]


@pytest.mark.asyncio
async def test_japanese_annotation_repairs_exact_but_contract_imperfect_candidate():
    valid = {
        "segments": [{
            "surface": "猫", "type": "word", "lemma": "猫",
            "surface_kana": "ねこ", "lemma_kana": "ねこ",
            "part_of_speech": "noun", "conjugation_form": "non-inflecting",
            "meaning_en": "cat", "story_role": "none",
            "story_importance_en": "",
        }],
        "grammar_overlays": [],
    }
    exact_but_imperfect = json.loads(json.dumps(valid))
    exact_but_imperfect["segments"][0]["meaning_en"] = ""

    class ContractRepairHarness(JapaneseChapterHarness):
        repaired_prior_meaning = None

        async def annotation_candidate(self, index, chunk, **kwargs):
            stage = kwargs.get("stage", "initial")
            if stage == "fresh":
                return json.loads(json.dumps(exact_but_imperfect))
            if stage == "fresh_repair_01":
                self.repaired_prior_meaning = kwargs["prior"]["segments"][0][
                    "meaning_en"
                ]
            return json.loads(json.dumps(valid))

        async def review_annotation(self, index, chunk, annotation, stage):
            if stage == "fresh_repair_01":
                return {"verdict": "pass", "issues": []}
            return {
                "verdict": "revise",
                "issues": [{
                    "segment_text": "猫", "problem": "meaning",
                    "explanation": "Use a more precise contextual meaning.",
                    "suggested_fix": "Repair the meaning.",
                }],
            }

    harness = object.__new__(ContractRepairHarness)
    harness.args = Namespace(
        max_annotation_repairs=0,
        max_annotation_fresh_repairs=1,
        annotation_repair_effort="low",
        annotation_final_effort="low",
        no_grammar_overlays=False,
    )
    result = await harness.annotate_chunk(0, "猫")
    assert result["resolved"] is True
    assert harness.repaired_prior_meaning == ""
    assert [attempt["stage"] for attempt in result["attempts"]] == [
        "initial", "fresh", "fresh_repair_01",
    ]


@pytest.mark.asyncio
async def test_japanese_annotation_has_one_fail_closed_adjudicated_pass():
    candidate = {
        "segments": [{
            "surface": "猫", "type": "word", "lemma": "猫",
            "surface_kana": "ねこ", "lemma_kana": "ねこ",
            "part_of_speech": "noun", "conjugation_form": "non-inflecting",
            "meaning_en": "cat", "story_role": "none",
            "story_importance_en": "",
        }],
        "grammar_overlays": [],
    }

    class AdjudicationHarness(JapaneseChapterHarness):
        async def annotation_candidate(self, index, chunk, **kwargs):
            return json.loads(json.dumps(candidate))

        async def review_annotation(self, index, chunk, annotation, stage):
            if stage == "adjudicated_final_01":
                return {"verdict": "pass", "issues": []}
            return {
                "verdict": "revise",
                "issues": [{
                    "segment_text": "猫", "problem": "meaning",
                    "explanation": "Needs correction.",
                    "suggested_fix": "Correct it.",
                }],
            }

    harness = object.__new__(AdjudicationHarness)
    harness.args = Namespace(
        max_annotation_repairs=0,
        max_annotation_fresh_repairs=0,
        max_annotation_adjudications=1,
        annotation_repair_effort="low",
        annotation_final_effort="low",
        no_grammar_overlays=False,
    )
    result = await harness.annotate_chunk(0, "猫")
    assert [attempt["stage"] for attempt in result["attempts"]] == [
        "initial", "fresh", "adjudicated_final_01",
    ]


def test_annotation_review_policy_ignores_pos_only_disputes():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "で", "problem": "wrong_type",
            "explanation": "This may be labelled a copular auxiliary, not a particle.",
            "suggested_fix": "Change only the POS label.",
        }],
    }
    assert apply_reader_useful_annotation_review_policy(review) == {
        "verdict": "pass", "issues": [],
    }


def test_annotation_review_policy_ignores_pos_category_and_explicit_minor_alternative():
    review = {
        "verdict": "revise",
        "issues": [
            {
                "segment_text": "て", "problem": "part_of_speech",
                "explanation": "This can instead be called a conjunctive particle.",
                "suggested_fix": "Change only the POS label.",
            },
            {
                "segment_text": "目", "problem": "meaning",
                "explanation": "Eyes is possible, but gaze would be more precise; this is minor.",
                "suggested_fix": "Optionally use gaze.",
            },
        ],
    }
    assert apply_reader_useful_annotation_review_policy(review) == {
        "verdict": "pass", "issues": [],
    }


def test_annotation_review_policy_ignores_accurate_but_vague_component_wording():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "寝",
            "problem": "grammar_components",
            "explanation": (
                "The component wording 'verb continuative form' is vague and "
                "does not clearly explain that it forms the て-form."
            ),
            "suggested_fix": "Describe the same continuative stem more explicitly.",
        }],
    }
    assert apply_reader_useful_annotation_review_policy(review) == {
        "verdict": "pass", "issues": [],
    }


def test_annotation_review_policy_keeps_minor_claim_when_it_says_incorrect():
    issue = {
        "segment_text": "目", "problem": "meaning",
        "explanation": "Eyes is possible elsewhere, but it is incorrect in this sentence.",
        "suggested_fix": "Use gaze.",
    }
    assert apply_reader_useful_annotation_review_policy({
        "verdict": "revise", "issues": [issue],
    }) == {"verdict": "revise", "issues": [issue]}


def test_annotation_review_policy_keeps_name_word_confusion_blocking():
    issue = {
        "segment_text": "笹原", "problem": "wrong_type",
        "explanation": "This is a common noun, not an established proper name.",
        "suggested_fix": "Annotate it as an ordinary word.",
    }
    assert apply_reader_useful_annotation_review_policy({
        "verdict": "revise", "issues": [issue],
    }) == {"verdict": "revise", "issues": [issue]}


def test_annotation_review_policy_rejects_invented_sasahara_surname():
    review = {"verdict": "revise", "issues": [{
        "segment_text": "に", "problem": "meaning",
        "explanation": (
            "笹原 is the surname Sasahara, so に marks the agent of the passive."
        ),
        "suggested_fix": "Gloss it as by Sasahara.",
    }]}
    annotation = {"segments": [{
        "surface": "笹原", "type": "word",
        "meaning_en": "bamboo-grass field",
    }, {
        "surface": "に", "type": "particle", "meaning_en": "in; at",
    }]}
    assert apply_reader_useful_annotation_review_policy(
        review, annotation,
    ) == {"verdict": "pass", "issues": []}


def test_review_does_not_advertise_unavailable_component_lookup():
    review = {"verdict": "revise", "issues": [{
        "segment_text": "ぐるぐるして", "problem": "grammar_components",
        "explanation": "The ぐるぐる component must use lookup_kind lexical.",
        "suggested_fix": "Use lexical even if its dictionary fields stay empty.",
    }]}
    annotation = {"segments": [], "grammar_overlays": [{
        "surface": "ぐるぐるして", "components": [{
            "surface": "ぐるぐる", "lemma": "ぐるぐる",
            "lemma_kana": "ぐるぐる", "lookup_kind": "none",
        }],
    }]}
    assert apply_reader_useful_annotation_review_policy(
        review, annotation,
    ) == {"verdict": "pass", "issues": []}


def test_review_does_not_repeat_displayed_lemma_as_form_step():
    review = {"verdict": "revise", "issues": [{
        "segment_text": "悪くなり", "problem": "lemma_reading",
        "explanation": "The intermediate form 悪くなる is missing.",
        "suggested_fix": "Include 悪くなる in form_steps before 悪くなり.",
    }]}
    annotation = {"segments": [{
        "surface": "悪くなり", "lemma": "悪くなる",
    }], "grammar_overlays": []}
    assert apply_reader_useful_annotation_review_policy(
        review, annotation,
    ) == {"verdict": "pass", "issues": []}


def test_annotation_review_policy_rejects_person_place_only_name_rule():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "黒", "problem": "wrong_type",
            "explanation": (
                "黒 is the cat's name, but proper-name type is restricted to "
                "people and places."
            ),
            "suggested_fix": "Use an ordinary word because it is not a person.",
        }],
    }
    assert apply_reader_useful_annotation_review_policy(review) == {
        "verdict": "pass", "issues": [],
    }


def test_annotation_review_policy_allows_stem_suffix_rows_inside_whole_form():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "思わなかった",
            "problem": "grammar_components",
            "explanation": (
                "The primary segment is whole, but 思わ and なかった are "
                "stem/suffix-level pieces."
            ),
            "suggested_fix": "Remove the nested stem/suffix breakdown.",
        }],
    }
    assert apply_reader_useful_annotation_review_policy(review) == {
        "verdict": "pass", "issues": [],
    }


def test_annotation_review_policy_allows_inflectional_stem_inside_grammar_overlay():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "たえながら",
            "problem": "grammar_components",
            "explanation": (
                "The component たえ is an inflectional stem and not a useful "
                "learned chunk by itself."
            ),
            "suggested_fix": "Remove the inflectional stem component.",
        }],
    }
    assert apply_reader_useful_annotation_review_policy(review) == {
        "verdict": "pass", "issues": [],
    }


def test_annotation_review_policy_keeps_routine_teiru_whole_with_content_lemma():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "休んでいる",
            "problem": "lemma",
            "explanation": (
                "The surface contains the て-form and the auxiliary いる, but "
                "the lemma is only 休む."
            ),
            "suggested_fix": (
                "Split into 休んで and いる, or use a construction lemma."
            ),
        }],
    }
    assert apply_reader_useful_annotation_review_policy(review) == {
        "verdict": "pass", "issues": [],
    }


def test_annotation_review_policy_keeps_tari_suru_as_learned_whole_form():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "たらしていたりする",
            "problem": "over_grouped",
            "explanation": (
                "This is a multi-part たり...たりする construction, not a "
                "single dictionary word."
            ),
            "suggested_fix": "Annotate する separately and split the form.",
        }],
    }
    assert apply_reader_useful_annotation_review_policy(review) == {
        "verdict": "pass", "issues": [],
    }


def test_annotation_review_policy_accepts_written_connective_renyokei():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "落ち着き",
            "problem": "surface_reading",
            "explanation": (
                "This is a connective masu-stem before a comma, but it is a "
                "detached stem rather than a complete form."
            ),
            "suggested_fix": (
                "Include the punctuation or rewrite it to the て-form."
            ),
        }],
    }
    assert apply_reader_useful_annotation_review_policy(review) == {
        "verdict": "pass", "issues": [],
    }


def test_annotation_review_policy_keeps_story_term_on_lexical_morpheme():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "捨て", "problem": "story_role",
            "explanation": (
                "The plan gives the whole exact surface 捨てられていました, "
                "but story_term is assigned only to the stem 捨て."
            ),
            "suggested_fix": "Use one whole-form annotation instead.",
        }],
    }
    assert apply_reader_useful_annotation_review_policy(review) == {
        "verdict": "pass", "issues": [],
    }


@pytest.mark.asyncio
async def test_annotation_review_allows_lexicalized_parts_inside_idiom_overlay():
    class ReviewRunner:
        async def call(self, *args, **kwargs):
            return {
                "verdict": "revise",
                "issues": [{
                    "candidate_paths": ["/grammar_overlays/0/surface"], "supporting_paths": [],
                    "segment_text": "目が回って",
                    "problem": "over_grouped",
                    "explanation": "The overlay includes the argument 目が.",
                    "suggested_fix": "Use only 回って unless idiom inclusion is permitted.",
                }],
            }

    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(annotation_review_effort="low", refresh=False)
    harness.runner = ReviewRunner()
    annotation = {
        "segments": [],
        "grammar_overlays": [{
            "surface": "目が回って", "form_label": "idiomatic expression",
            "pattern": "目が回る",
            "explanation_en": "A fixed expression meaning to feel dizzy.",
        }],
    }
    assert await harness.review_annotation(
        0, "目が回って", annotation, "review",
    ) == {"verdict": "pass", "issues": []}


@pytest.mark.asyncio
async def test_annotation_review_defines_canonical_overlay_head_and_editor_remits():
    class PromptRunner:
        def __init__(self):
            self.prompts = []

        async def call(self, job, prompt, *args, **kwargs):
            self.prompts.append((job, prompt))
            return {"verdict": "pass", "issues": []}

    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(annotation_review_effort="low", refresh=False)
    harness.runner = PromptRunner()
    annotation = {"segments": [], "grammar_overlays": []}

    assert await harness.review_annotation(
        0, "生まれたか", annotation, "review",
    ) == {"verdict": "pass", "issues": []}

    general = next(
        prompt for job, prompt in harness.runner.prompts
        if job.endswith("_review") and not job.endswith("_boundary_review")
    )
    boundary = next(
        prompt for job, prompt in harness.runner.prompts
        if job.endswith("_boundary_review")
    )
    assert "surface 生まれたか correctly has head 生まれるか" in general
    assert "A non-past intermediate can correctly precede an explicit past transformation" in general
    assert "a final past stage still glossed as present" in general
    assert "Do not review `grammar_overlays`" in boundary
    assert "A non-past intermediate can correctly precede an explicit past transformation" in boundary


@pytest.mark.asyncio
async def test_annotation_review_prompt_checks_present_intermediate_before_past():
    class PromptRunner:
        def __init__(self):
            self.prompts = []

        async def call(self, job, prompt, *args, **kwargs):
            self.prompts.append((job, prompt))
            return {"verdict": "pass", "issues": []}

    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(annotation_review_effort="low", refresh=False)
    harness.runner = PromptRunner()
    annotation = {
        "segments": [{
            "surface": "息子だった", "type": "word", "lemma": "息子",
            "surface_kana": "むすこだった", "lemma_kana": "むすこ",
            "conjugation_form": "past copula", "dictionary_key": "",
            "dictionary_definition_en": "", "form_steps": [
                {"form": "息子だ", "reading": "むすこだ", "label": "copula",
                 "meaning_en": "is a son"},
                {"form": "息子だった", "reading": "むすこだった",
                 "label": "past copula", "meaning_en": "was a son"},
            ],
        }],
        "grammar_overlays": [],
    }
    assert await harness.review_annotation(0, "息子だった", annotation, "review") == {
        "verdict": "pass", "issues": [],
    }
    general = next(prompt for job, prompt in harness.runner.prompts
                   if job.endswith("_review") and not job.endswith("_boundary_review"))
    assert '"form": "息子だ"' in general and '"meaning_en": "is a son"' in general
    assert '"form": "息子だった"' in general and '"meaning_en": "was a son"' in general
    assert "a past final surface whose chain stops at the non-past form" in general

    wrong = copy.deepcopy(annotation)
    wrong["segments"][0]["form_steps"][1]["meaning_en"] = "is a son"
    await harness.review_annotation(0, "息子だった", wrong, "wrong-final-meaning")
    wrong_general = next(
        prompt for job, prompt in harness.runner.prompts
        if job.endswith("_review") and not job.endswith("_boundary_review")
        and "wrong-final-meaning" in job
    )
    assert '"meaning_en": "is a son"' in wrong_general
    assert "a final past stage still glossed as present" in wrong_general


@pytest.mark.asyncio
async def test_annotation_review_preserves_required_contextual_overlay():
    class ReviewRunner:
        async def call(self, *args, **kwargs):
            return {
                "verdict": "revise",
                "issues": [{
                    "candidate_paths": ["/grammar_overlays/0/explanation_en"], "supporting_paths": [],
                    "segment_text": "じっとしていた",
                    "problem": "grammar",
                    "explanation": (
                        "This overlay is redundant because it is a basic "
                        "ている construction already explained by the primary."
                    ),
                    "suggested_fix": "Remove the routine ている overlay.",
                }],
            }

    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(annotation_review_effort="low", refresh=False)
    harness.runner = ReviewRunner()
    annotation = {
        "segments": [
            {"surface": "じっと", "type": "word"},
            {"surface": "していた", "type": "word"},
        ],
        "grammar_overlays": [{
            "surface": "じっとしていた",
            "form_label": "fixed expression in past progressive",
            "explanation_en": "じっとする means to remain still.",
            "pattern": "じっとする",
        }],
    }
    assert await harness.review_annotation(
        0, "じっとしていた", annotation, "review",
    ) == {"verdict": "pass", "issues": []}


@pytest.mark.asyncio
async def test_annotation_review_keeps_content_lemma_for_whole_te_kuru_form():
    class ReviewRunner:
        async def call(self, *args, **kwargs):
            return {
                "verdict": "revise",
                "issues": [{
                    "candidate_paths": ["/segments/0/lemma"], "supporting_paths": [],
                    "segment_text": "飛び出してくる",
                    "problem": "lemma",
                    "explanation": (
                        "The full-surface form includes directional 来る, so "
                        "lemma 飛び出す leaves Vてくる only in the overlay."
                    ),
                    "suggested_fix": (
                        "Use type `grammar` with the complete Vてくる "
                        "construction lemma."
                    ),
                }],
            }

    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(annotation_review_effort="low", refresh=False)
    harness.runner = ReviewRunner()
    annotation = {
        "segments": [{
            "surface": "飛び出してくる", "type": "word", "lemma": "飛び出す",
            "conjugation_form": "てくる, directional nonpast",
        }],
        "grammar_overlays": [{
            "surface": "飛び出してくる", "form_label": "Vてくる",
            "explanation_en": "Directional movement toward the viewpoint.",
            "pattern": "Vてくる",
        }],
    }
    assert await harness.review_annotation(
        0, "飛び出してくる", annotation, "review",
    ) == {"verdict": "pass", "issues": []}


@pytest.mark.asyncio
async def test_annotation_review_does_not_require_duplicate_overlay_for_whole_word():
    class ReviewRunner:
        async def call(self, *args, **kwargs):
            return {
                "verdict": "revise",
                "issues": [{
                    "candidate_paths": ["/segments/0/conjugation_form"], "supporting_paths": [],
                    "segment_text": "いない",
                    "problem": "grammar",
                    "explanation": (
                        "The annotation lacks a whole-form grammar overlay for "
                        "this negative existential form."
                    ),
                    "suggested_fix": "Add a whole-form overlay for いない.",
                }],
            }

    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(annotation_review_effort="low", refresh=False)
    harness.runner = ReviewRunner()
    annotation = {
        "segments": [{
            "surface": "いない", "type": "word", "lemma": "いる",
            "surface_kana": "いない", "lemma_kana": "いる",
            "part_of_speech": "verb", "conjugation_form": "plain negative",
            "meaning_en": "is not present", "story_role": "none",
            "story_importance_en": "",
        }],
        "grammar_overlays": [],
    }
    assert await harness.review_annotation(
        0, "いない", annotation, "review",
    ) == {"verdict": "pass", "issues": []}


@pytest.mark.asyncio
async def test_annotation_review_keeps_incorrect_whole_word_grammar_blocking():
    issue = {
        "candidate_paths": ["/segments/0/conjugation_form"], "supporting_paths": [],
                    "segment_text": "見られた", "problem": "grammar",
        "explanation": "The form is incorrectly described as causative, not passive.",
        "suggested_fix": "Correct the form explanation to passive past.",
    }

    class ReviewRunner:
        async def call(self, *args, **kwargs):
            return {"verdict": "revise", "issues": [issue]}

    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(annotation_review_effort="low", refresh=False)
    harness.runner = ReviewRunner()
    annotation = {
        "segments": [{"surface": "見られた", "type": "word",
                      "conjugation_form": "causative"}],
        "grammar_overlays": [],
    }
    assert await harness.review_annotation(
        0, "見られた", annotation, "review",
    ) == {"verdict": "revise", "issues": [issue]}


@pytest.mark.asyncio
async def test_annotation_review_keeps_copula_with_predicative_na_adjective():
    class ReviewRunner:
        async def call(self, *args, **kwargs):
            return {
                "verdict": "revise",
                "issues": [{
                    "candidate_paths": ["/segments/0/surface"], "supporting_paths": [],
                    "segment_text": "不人望だった", "problem": "over_grouped",
                    "explanation": (
                        "The na-adjective and past copula are separate learner "
                        "units; the copula is assigned to the predicative adjective."
                    ),
                    "suggested_fix": "Split 不人望 and だった.",
                }],
            }

    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(annotation_review_effort="low", refresh=False)
    harness.runner = ReviewRunner()
    annotation = {
        "segments": [{
            "surface": "不人望だった", "type": "word", "lemma": "不人望",
            "part_of_speech": "na-adjective",
        }],
        "grammar_overlays": [],
    }
    assert await harness.review_annotation(
        0, "不人望だった", annotation, "review",
    ) == {"verdict": "pass", "issues": []}


@pytest.mark.asyncio
async def test_annotation_review_keeps_whole_teiru_form_with_lexical_root():
    class ReviewRunner:
        async def call(self, *args, **kwargs):
            return {
                "verdict": "revise",
                "issues": [{
                    "candidate_paths": ["/segments/0/lemma"], "supporting_paths": [],
                    "segment_text": "残っている", "problem": "lemma",
                    "explanation": (
                        "The surface contains the て-form plus the auxiliary いる, "
                        "but its lemma is only 残る."
                    ),
                    "suggested_fix": "Split into 残って and auxiliary いる.",
                }],
            }

    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(annotation_review_effort="low", refresh=False)
    harness.runner = ReviewRunner()
    annotation = {
        "segments": [{
            "surface": "残っている", "type": "word", "lemma": "残る",
            "part_of_speech": "verb", "conjugation_form": "ている state; nonpast",
        }],
        "grammar_overlays": [],
    }
    assert await harness.review_annotation(
        0, "残っている", annotation, "review",
    ) == {"verdict": "pass", "issues": []}


@pytest.mark.asyncio
async def test_annotation_review_does_not_require_names_in_story_term_plan():
    class ReviewRunner:
        async def call(self, *args, **kwargs):
            return {
                "verdict": "revise",
                "issues": [{
                    "candidate_paths": ["/segments/0/story_role"], "supporting_paths": [],
                    "segment_text": "黒", "problem": "story_role",
                    "explanation": (
                        "This name is absent from the reviewed story-vocabulary "
                        "plan, so it should not receive a story role."
                    ),
                    "suggested_fix": "Set story_role to none or add 黒 to the plan.",
                }],
            }

    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(annotation_review_effort="low", refresh=False)
    harness.runner = ReviewRunner()
    annotation = {
        "segments": [{
            "surface": "黒", "type": "name", "story_role": "name",
        }],
        "grammar_overlays": [],
    }
    assert await harness.review_annotation(
        0, "黒", annotation, "review",
    ) == {"verdict": "pass", "issues": []}


def test_annotation_review_policy_ignores_model_offset_arithmetic_after_contract_gate():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "生まれたか分からず", "problem": "grammar",
            "explanation": "The overlay offsets do not match the stated surface.",
            "suggested_fix": "Set the exact Python offset to another value.",
        }],
    }
    assert apply_reader_useful_annotation_review_policy(review) == {
        "verdict": "pass", "issues": [],
    }


def test_annotation_review_policy_ignores_explicitly_equivalent_grouping_only():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "では", "problem": "under_grouped",
            "explanation": "Splitting で and は is an equally valid segmentation.",
            "suggested_fix": "Either analysis is acceptable.",
        }],
    }
    assert apply_reader_useful_annotation_review_policy(review)["verdict"] == "pass"


@pytest.mark.parametrize("problem", [
    "surface_reading", "lemma_reading", "meaning",
    "lemma", "grammar", "grammar_components",
])
def test_annotation_review_policy_keeps_reader_harming_issues_blocking(problem):
    issue = {
        "segment_text": "猫", "problem": problem,
        "explanation": "The learner-facing annotation is incorrect.",
        "suggested_fix": "Correct it.",
    }
    accepted = apply_reader_useful_annotation_review_policy({
        "verdict": "revise", "issues": [issue],
    })
    assert accepted == {"verdict": "revise", "issues": [issue]}


def test_annotation_review_policy_ignores_school_conjugation_taxonomy_only():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "栄え", "problem": "conjugation",
            "explanation": "This scheme calls the form 未然形, not plain form.",
            "suggested_fix": "Change only the form label.",
        }],
    }
    assert apply_reader_useful_annotation_review_policy(review) == {
        "verdict": "pass", "issues": [],
    }


def test_annotation_review_policy_keeps_semantic_conjugation_error_blocking():
    issue = {
        "segment_text": "させ", "problem": "conjugation",
        "explanation": "This is causative, but the annotation says passive.",
        "suggested_fix": "Correct the grammatical meaning.",
    }
    assert apply_reader_useful_annotation_review_policy({
        "verdict": "revise", "issues": [issue],
    }) == {"verdict": "revise", "issues": [issue]}


def test_annotation_review_policy_defers_position_recounting_to_contract():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "観察するほど", "problem": "grammar_components",
            "explanation": "The range is off by one and begins at position 72.",
            "suggested_fix": "Move the range.",
        }],
    }
    assert apply_reader_useful_annotation_review_policy(review) == {
        "verdict": "pass", "issues": [],
    }


def test_annotation_review_policy_defers_reconstruction_to_deterministic_gate():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "来なかった", "problem": "reconstruction",
            "explanation": "The reviewer counted the span differently.",
            "suggested_fix": "Change the indices.",
        }],
    }
    assert apply_reader_useful_annotation_review_policy(review) == {
        "verdict": "pass", "issues": [],
    }


def test_annotation_review_policy_keeps_misleading_grouping_blocking():
    issue = {
        "segment_text": "普通の長い文", "problem": "over_grouped",
        "explanation": "An ordinary clause was grouped as one word.",
        "suggested_fix": "Split it into dictionary words and particles.",
    }
    accepted = apply_reader_useful_annotation_review_policy({
        "verdict": "revise", "issues": [issue],
    })
    assert accepted["verdict"] == "revise"
    assert accepted["issues"] == [issue]


def test_annotation_review_policy_keeps_complete_passive_word_whole():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "おおわれ",
            "problem": "over_grouped",
            "explanation": (
                "The passive predicate is grouped into one segment. Split it "
                "into the lexical stem おおわ and the passive auxiliary れる."
            ),
            "suggested_fix": "Split the stem from the passive suffix.",
        }],
    }
    annotation = {"segments": [{
        "surface": "おおわれ", "type": "word", "part_of_speech": "verb",
        "lemma": "おおう", "conjugation_form": "passive continuative",
    }]}
    assert apply_reader_useful_annotation_review_policy(
        review, annotation,
    ) == {"verdict": "pass", "issues": []}


def test_annotation_review_policy_keeps_te_ite_connective_whole():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "つるつるしていて",
            "problem": "over_grouped",
            "explanation": (
                "The final て should be a separate connective particle after "
                "つるつるしている."
            ),
            "suggested_fix": "Keep the following connective て separately.",
        }],
    }
    annotation = {"segments": [{
        "surface": "つるつるしていて", "type": "word",
        "part_of_speech": "mimetic suru verb", "lemma": "つるつるする",
        "conjugation_form": "progressive connective",
    }]}
    assert apply_reader_useful_annotation_review_policy(
        review, annotation,
    ) == {"verdict": "pass", "issues": []}


@pytest.mark.parametrize("surface", ["何度も", "何回も"])
def test_annotation_review_policy_keeps_conventional_frequency_adverb_whole(surface):
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": surface,
            "problem": "over_grouped",
            "explanation": f"Split {surface} into the counter phrase and the particle も.",
            "suggested_fix": "Keep も separately as a reusable particle.",
        }],
    }
    assert apply_reader_useful_annotation_review_policy(review) == {
        "verdict": "pass", "issues": [],
    }


def test_annotation_review_policy_never_merges_nonadjacent_frequency_mo():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "何度 / も", "problem": "under_grouped",
            "explanation": "何度も should be a conventional whole adverb.",
            "suggested_fix": "Merge 何度 and も.",
        }],
    }
    annotation = {"segments": [
        {"surface": "何度"}, {"surface": "外へ"},
        {"surface": "出して"}, {"surface": "も"},
    ]}
    assert apply_reader_useful_annotation_review_policy(
        review, annotation,
    ) == {"verdict": "pass", "issues": []}


def test_annotation_review_policy_keeps_indefinite_nani_ni_demo_whole():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "何にでも", "problem": "over_grouped",
            "explanation": "Split the indefinite expression into particles.",
            "suggested_fix": "Use 何, に, and でも.",
        }],
    }
    annotation = {"segments": [{"surface": "何にでも"}]}
    assert apply_reader_useful_annotation_review_policy(
        review, annotation,
    ) == {"verdict": "pass", "issues": []}


def test_annotation_review_policy_rejects_false_past_and_dead_link_for_tagaru():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "手を出したがる", "problem": "conjugation",
            "explanation": "The chain omits the intermediate plain past form 手を出した.",
            "suggested_fix": "Add the plain past form before たがる.",
        }, {
            "segment_text": "手を出し", "problem": "grammar_components",
            "explanation": "A lexical component must use a dictionary link.",
            "suggested_fix": "Set lookup_kind lexical and link 手を出す.",
        }],
    }
    annotation = {
        "segments": [{
            "surface": "手を出したがる", "type": "grammar",
            "lemma": "手を出す", "grammar_candidate_key": "tagaru",
        }],
        "grammar_overlays": [{
            "surface": "手を出したがる", "grammar_candidate_key": "tagaru",
            "components": [{
                "surface": "手を出し", "lemma": "手を出す",
                "lemma_kana": "てをだす", "lookup_kind": "none",
            }, {
                "surface": "たがる", "lemma": "たがる",
                "lemma_kana": "たがる", "lookup_kind": "grammar",
            }],
        }],
    }
    assert apply_reader_useful_annotation_review_policy(
        review, annotation,
    ) == {"verdict": "pass", "issues": []}


def test_annotation_review_policy_keeps_number_counter_quantity_whole():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "三ページ",
            "problem": "lemma",
            "explanation": "This numeral-counter phrase is not a dictionary lemma.",
            "suggested_fix": "Split it into primary segments 三 and ページ.",
        }],
    }
    assert apply_reader_useful_annotation_review_policy(review) == {
        "verdict": "pass", "issues": [],
    }


def test_annotation_review_policy_keeps_tari_key_and_inflected_overlay_surface():
    annotation = {
        "segments": [{
            "surface": "かぶせたり", "type": "word", "lemma": "かぶせる",
            "grammar_candidate_key": "tari",
        }],
        "grammar_overlays": [{
            "surface": "投げ出したり", "head_lemma": "投げ出す",
            "components": [{
                "surface": "投げ", "lemma": "投げる",
                "lookup_kind": "lexical",
            }, {
                "surface": "出したり", "lemma": "出す",
                "lookup_kind": "lexical",
            }],
        }],
    }
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "かぶせたり", "problem": "grammar",
            "explanation": (
                "This isolated Vたり word does not need a separate grammar primary."
            ),
            "suggested_fix": "Remove the grammar candidate key.",
        }, {
            "segment_text": "投げ出したり", "problem": "over_grouped",
            "explanation": (
                "The compound overlay uses the inflected fragment 出したり; "
                "the useful component is the root 出す."
            ),
            "suggested_fix": "Replace the realized surface.",
        }],
    }
    assert apply_reader_useful_annotation_review_policy(
        review, annotation,
    ) == {"verdict": "pass", "issues": []}


def test_annotation_review_policy_keeps_canonical_collocation_head_and_compact_suruto():
    annotation = {
        "segments": [{
            "surface": "しばらく", "type": "word",
            "grammar_candidate_key": "",
        }, {
            "surface": "すると", "type": "grammar",
            "grammar_candidate_key": "suru-to",
        }],
        "grammar_overlays": [{
            "surface": "目が回り", "head_lemma": "目が回る",
        }],
    }
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "目が回り", "problem": "lemma_reading",
            "explanation": (
                "TEXT contains 目が回り; change head_lemma to the complete "
                "surface form and keep the exact overlay surface."
            ),
            "suggested_fix": "Change head_lemma to 目が回り.",
        }, {
            "segment_text": "しばらくすると", "problem": "under_grouped",
            "explanation": (
                "This temporal expression needs a predicate-sized phrase overlay."
            ),
            "suggested_fix": "Add an overlay.",
        }],
    }
    assert apply_reader_useful_annotation_review_policy(
        review, annotation,
    ) == {"verdict": "pass", "issues": []}


def test_annotation_review_policy_accepts_searchable_na_adjective_base_lemma():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "つるつるで",
            "problem": "lemma",
            "explanation": (
                "This is a na-adjective with a copula; the lemma omits the copula."
            ),
            "suggested_fix": "The lemma should be つるつるだ and include the copula.",
        }],
    }
    assert apply_reader_useful_annotation_review_policy(review) == {
        "verdict": "pass", "issues": [],
    }


def test_annotation_review_policy_defers_story_roles_to_reviewed_plan():
    review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "不人望だった",
            "problem": "story_role",
            "explanation": "The story role is missing.",
            "suggested_fix": "Mark it as a story term.",
        }],
    }
    assert apply_reader_useful_annotation_review_policy(review) == {
        "verdict": "pass", "issues": [],
    }


def test_overlay_policy_requires_searchable_aite_component_lemma():
    assert japanese_overlay_policy_issue({
        "surface": "相手にせず",
        "components": [
            {"surface": "相手に", "lemma": "相手にする"},
            {"surface": "せず", "lemma": "する"},
        ],
    })
    assert japanese_overlay_policy_issue({
        "surface": "相手にせず",
        "components": [
            {"surface": "相手に", "lemma": "相手"},
            {"surface": "せず", "lemma": "する"},
        ],
    }) is None


def test_annotation_contract_validates_segments_and_overlay_offsets():
    good = {
        "segments": [
            {"surface": "猫", "type": "word", "lemma": "猫", "surface_kana": "ねこ", "lemma_kana": "ねこ", "part_of_speech": "noun", "conjugation_form": "non-inflecting", "meaning_en": "cat", "story_role": "none", "story_importance_en": ""},
            {"surface": "なら", "type": "particle", "lemma": "なら", "surface_kana": "なら", "lemma_kana": "なら", "part_of_speech": "conditional particle", "conjugation_form": "non-inflecting", "meaning_en": "if", "story_role": "none", "story_importance_en": ""},
        ],
        "grammar_overlays": [{
            "start": 0, "end": 3, "surface": "猫なら",
            "grammar_candidate_key": "conditional.nara", "pattern": "Nなら",
            "meaning_en": "if it is N", "head_lemma": "なら",
            "head_lemma_kana": "なら", "form_label": "conditional",
            "explanation_en": "Marks a condition.",
            "components": [
                {"start": 0, "end": 1, "surface": "猫", "lemma": "猫", "lemma_kana": "ねこ", "function_en": "condition topic"},
                {"start": 1, "end": 3, "surface": "なら", "lemma": "なら", "lemma_kana": "なら", "function_en": "conditional marker"},
            ],
        }],
    }
    assert JapaneseChapterHarness.annotation_reconstructs("猫なら", good)
    bad = {**good, "grammar_overlays": [{**good["grammar_overlays"][0], "surface": "ならば"}]}
    assert not JapaneseChapterHarness.annotation_reconstructs("猫なら", bad)


def test_jlpt_diagnostic_matches_kana_reading_for_common_kanji_variants():
    segment = {
        "surface": "ある", "surface_kana": "ある", "lemma": "ある",
        "lemma_kana": "ある", "type": "word",
    }
    assert matched_level(segment) == 1
    diagnostic = level_diagnostics([segment], "n5")
    assert diagnostic["above_level_ratio"] == 0
    assert diagnostic["passes"] is True


def test_jlpt_diagnostic_treats_yoi_as_ii_spelling_variant():
    segment = {
        "surface": "よい", "surface_kana": "よい", "lemma": "良い",
        "lemma_kana": "よい", "type": "word",
    }
    assert matched_level(segment) == 1


@pytest.mark.parametrize("lemma,reading,expected", [
    ("静かだ", "しずかだ", 1),
    ("女の人", "おんなのひと", 1),
    ("男の人", "おとこのひと", 1),
    ("何回", "なんかい", 1),
    ("何回も", "なんかいも", 1),
    ("何も", "なにも", 1),
    ("誰も", "だれも", 1),
    ("何度", "なんど", 1),
    ("何度も", "なんども", 1),
    ("その後", "そのご", 1),
    ("後で", "あとで", 1),
    ("ある日", "あるひ", 2),
    ("音がする", "おとがする", 2),
    ("においがする", "においがする", 2),
    ("匂いがする", "においがする", 2),
    ("そこで", "そこで", 2),
    ("四十", "よんじゅう", 1),
    ("一円", "いちえん", 1),
    ("五十銭", "ごじっせん", 1),
    ("一円五十銭", "いちえんごじゅっせん", 1),
    ("二枚", "にまい", 1),
    ("匹", "ひき", 1),
    ("時", "とき", 1),
    ("気持ちのよい", "きもちのよい", 2),
    ("悪くなる", "わるくなる", 1),
])
def test_jlpt_diagnostic_handles_transparent_common_lemma_variants(
    lemma, reading, expected,
):
    segment = {
        "surface": lemma, "surface_kana": reading,
        "lemma": lemma, "lemma_kana": reading, "type": "word",
    }
    assert matched_level(segment) == expected


def test_story_vocabulary_plan_rejects_known_ordinary_n5_word():
    harness = object.__new__(JapaneseChapterHarness)
    harness.args = SimpleNamespace(level="n5")
    plan = {"terms": [{
        "surface": "本", "lemma": "本", "lemma_kana": "ほん",
        "meaning_en": "book", "importance_en": "It appears in the plot.",
    }]}
    issues = harness.story_vocabulary_plan_issues(plan, "本を読む。")
    assert any("already in the cumulative N5 baseline" in issue for issue in issues)


def test_beginner_story_vocabulary_must_recur():
    harness = object.__new__(JapaneseChapterHarness)
    harness.args = SimpleNamespace(level="n4")
    plan = {"terms": [{
        "surface": "体裁", "lemma": "体裁", "lemma_kana": "ていさい",
        "meaning_en": "appearances", "importance_en": "A one-off quotation.",
    }]}
    issues = harness.story_vocabulary_plan_issues(plan, "人間は体裁のいい泥棒だ。")
    assert any("occurs only once" in issue for issue in issues)


def test_inline_kana_reading_does_not_duplicate_story_vocabulary_role():
    harness = object.__new__(JapaneseChapterHarness)
    harness.args = SimpleNamespace(no_grammar_overlays=False)
    harness.story_vocabulary_plan = {"terms": [{
        "lemma": "書生", "importance_en": "A recurring historical social role.",
    }]}
    lexical = {
        "type": "word", "lemma": "書生", "surface_kana": "しょせい",
        "lemma_kana": "しょせい", "part_of_speech": "noun",
        "conjugation_form": "non-inflecting", "meaning_en": "student",
        "story_role": "none", "story_importance_en": "",
    }
    punctuation = {
        "type": "punctuation", "lemma": "", "surface_kana": "",
        "lemma_kana": "", "part_of_speech": "", "conjugation_form": "",
        "meaning_en": "", "story_role": "none", "story_importance_en": "",
    }
    candidate = {
        "segments": [
            {**lexical, "surface": "書生"},
            {**punctuation, "surface": "（"},
            {**lexical, "surface": "しょせい"},
            {**punctuation, "surface": "）"},
        ],
        "grammar_overlays": [],
    }

    normalized = harness.prepare_planned_annotation("書生（しょせい）", candidate)

    assert normalized["segments"][0]["story_role"] == "story_term"
    assert normalized["segments"][2]["story_role"] == "none"
    assert normalized["segments"][2]["story_importance_en"] == ""


def test_negative_grammar_is_n5_baseline_and_never_story_vocabulary():
    segment = {
        "surface": "ない", "surface_kana": "ない", "lemma": "ない",
        "lemma_kana": "ない", "type": "word",
    }
    assert matched_level(segment) == 1
    assert level_diagnostics([segment], "n5")["above_level_ratio"] == 0

    harness = object.__new__(JapaneseChapterHarness)
    harness.args = SimpleNamespace(level="n5")
    issues = harness.story_vocabulary_plan_issues({"terms": [{
        "surface": "ない", "lemma": "ない", "lemma_kana": "ない",
        "meaning_en": "not", "importance_en": "It marks negation.",
    }]}, "名前はない。")
    assert any("functional grammar" in issue for issue in issues)


def test_learned_grammar_chunks_are_not_counted_as_unknown_vocabulary():
    segments = [
        {
            "surface": "ことにしました", "surface_kana": "ことにしました",
            "lemma": "ことにする", "lemma_kana": "ことにする",
            "type": "grammar",
        },
        {
            "surface": "猫", "surface_kana": "ねこ", "lemma": "猫",
            "lemma_kana": "ねこ", "type": "word",
        },
    ]
    diagnostics = level_diagnostics(segments, "n5")
    assert diagnostics["tokens_considered"] == 0
    assert diagnostics["above_level_tokens"] == 0


def test_productive_compound_uses_reviewed_component_levels():
    segment = {
        "surface": "動き出し", "surface_kana": "うごきだし",
        "lemma": "動き出す", "lemma_kana": "うごきだす", "type": "word",
    }
    overlay = {
        "start": 0, "end": 4, "surface": "動き出し",
        "grammar_candidate_key": "compound-v-dasu",
        "pattern": "Vます-stem + 出す", "form_label": "conjunctive",
        "components": [
            {
                "surface": "動き", "lemma": "動く", "lemma_kana": "うごく",
                "lookup_kind": "lexical",
            },
            {
                "surface": "出し", "lemma": "出す", "lemma_kana": "だす",
                "lookup_kind": "lexical",
            },
        ],
    }
    assert matched_level(segment) is None
    diagnostics = level_diagnostics([segment], "n4", [overlay])
    assert diagnostics["above_level_tokens"] == 0
    assert diagnostics["passes"] is True


def test_fixed_idiom_is_not_promoted_only_because_some_components_are_easy():
    segment = {
        "surface": "目が回りました", "surface_kana": "めがまわりました",
        "lemma": "目が回る", "lemma_kana": "めがまわる", "type": "idiom",
    }
    overlay = {
        "start": 0, "end": 7, "surface": "目が回りました",
        "grammar_candidate_key": "me-ga-mawaru", "pattern": "目が回る",
        "form_label": "polite past", "components": [
            {
                "surface": "目", "lemma": "目", "lemma_kana": "め",
                "lookup_kind": "lexical",
            },
            {
                "surface": "回りました", "lemma": "回る",
                "lemma_kana": "まわる", "lookup_kind": "lexical",
            },
        ],
    }
    diagnostics = level_diagnostics([segment], "n5", [overlay])
    assert diagnostics["above_level_tokens"] == 1


def test_annotation_contract_rejects_story_role_on_auxiliary():
    segment = {
        "surface": "ない", "type": "auxiliary", "lemma": "ない",
        "surface_kana": "ない", "lemma_kana": "ない",
        "part_of_speech": "auxiliary adjective", "conjugation_form": "nonpast",
        "meaning_en": "not", "story_role": "story_term",
        "story_importance_en": "Negative form",
    }
    assert not JapaneseChapterHarness.annotation_reconstructs(
        "ない", {"segments": [segment], "grammar_overlays": []}
    )


def test_annotation_contract_rejects_overlay_crossing_punctuation_with_specific_feedback():
    result = {
        "segments": [
            {"surface": "嫌われ", "type": "word", "lemma": "嫌う", "surface_kana": "きらわれ", "lemma_kana": "きらう", "part_of_speech": "verb", "conjugation_form": "passive continuative", "meaning_en": "be disliked", "story_role": "none", "story_importance_en": ""},
            {"surface": "、", "type": "punctuation", "lemma": "", "surface_kana": "", "lemma_kana": "", "part_of_speech": "", "conjugation_form": "", "meaning_en": "", "story_role": "none", "story_importance_en": ""},
            {"surface": "名前", "type": "word", "lemma": "名前", "surface_kana": "なまえ", "lemma_kana": "なまえ", "part_of_speech": "noun", "conjugation_form": "non-inflecting", "meaning_en": "name", "story_role": "none", "story_importance_en": ""},
        ],
        "grammar_overlays": [{
            "start": 0, "end": 7, "surface": "嫌われ、名前",
            "grammar_candidate_key": "passive.cross_clause", "pattern": "Vられ、N",
            "meaning_en": "be disliked, and a name", "head_lemma": "嫌う",
            "head_lemma_kana": "きらう", "form_label": "passive",
            "explanation_en": "An over-wide passive overlay.",
            "components": [
                {"start": 0, "end": 3, "surface": "嫌われ", "lemma": "嫌う", "lemma_kana": "きらう", "function_en": "passive verb"},
                {"start": 3, "end": 4, "surface": "、", "lemma": "、", "lemma_kana": "、", "function_en": "punctuation"},
                {"start": 4, "end": 6, "surface": "名前", "lemma": "名前", "lemma_kana": "なまえ", "function_en": "noun"},
            ],
        }],
    }
    text = "嫌われ、名前"
    assert not JapaneseChapterHarness.annotation_reconstructs(text, result)
    issues = JapaneseChapterHarness.annotation_contract_issues(text, result)
    assert any("crosses punctuation" in item["explanation"] for item in issues)
    assert any("lemma_kana" in item["explanation"] for item in issues)


def test_numeric_approximation_can_keep_its_integral_comma_in_an_overlay():
    assert not overlay_crosses_clause_boundary("三、四十匹")
    assert not overlay_crosses_clause_boundary("四、五度も")
    assert not overlay_crosses_clause_boundary("三、四十匹捕ったこと")
    assert not overlay_crosses_clause_boundary("寝たり、読んだりする")
    assert overlay_crosses_clause_boundary("嫌われ、名前も付けられない")
    assert overlay_crosses_clause_boundary("ので、仕方なく")


def test_numeric_range_overlay_absorbs_following_emphatic_mo():
    normalized = JapaneseChapterHarness.canonicalize_annotation_candidate(
        "四、五度も",
        {
            "segments": [
                {"surface": "四", "surface_kana": "よん"},
                {"surface": "、", "type": "punctuation"},
                {"surface": "五", "surface_kana": "ご"},
                {"surface": "度", "surface_kana": "ど"},
                {"surface": "も", "surface_kana": "も"},
            ],
            "grammar_overlays": [{
                "start": 0, "end": 4, "surface": "四、五度",
                "grammar_candidate_key": "approximate-range",
                "pattern": "四、五度", "meaning_en": "about four or five times",
                "head_lemma": "四、五度", "head_lemma_kana": "よんごど",
                "form_label": "approximate range",
                "explanation_en": "A comma-separated approximate range.",
                "components": [{
                    "start": 0, "end": 1, "surface": "四",
                    "lemma": "四", "lemma_kana": "よん",
                    "function_en": "first value", "lookup_kind": "none",
                }, {
                    "start": 2, "end": 4, "surface": "五度",
                    "lemma": "五度", "lemma_kana": "ごど",
                    "function_en": "second value", "lookup_kind": "none",
                }],
            }],
        },
    )
    overlay = normalized["grammar_overlays"][0]
    assert overlay["surface"] == "四、五度も"
    assert overlay["components"][-1]["surface"] == "も"
    assert overlay["components"][-1]["lookup_kind"] == "grammar"
    assert japanese_required_overlay_issues(
        normalized["segments"], normalized["grammar_overlays"],
    ) == []


def test_tari_listing_overlay_can_keep_its_integral_comma_component():
    def segment(surface, lemma, reading, lemma_reading, form, meaning):
        return {
            "surface": surface, "type": "word", "lemma": lemma,
            "surface_kana": reading, "lemma_kana": lemma_reading,
            "part_of_speech": "verb", "conjugation_form": form,
            "meaning_en": meaning, "story_role": "none",
            "story_importance_en": "",
        }

    text = "寝たり、読んだりする"
    result = {
        "segments": [
            segment("寝たり", "寝る", "ねたり", "ねる", "たり listing", "sleep, among other things"),
            {"surface": "、", "type": "punctuation", "lemma": "", "surface_kana": "", "lemma_kana": "", "part_of_speech": "", "conjugation_form": "", "meaning_en": "", "story_role": "none", "story_importance_en": ""},
            segment("読んだりする", "読む", "よんだりする", "よむ", "たりする listing", "read, among other things"),
        ],
        "grammar_overlays": [{
            "start": 0, "end": len(text), "surface": text,
            "grammar_candidate_key": "listing.tari_suru",
            "pattern": "Vたり、Vたりする", "meaning_en": "do things such as sleep and read",
            "head_lemma": "する", "head_lemma_kana": "する",
            "form_label": "たり listing construction",
            "explanation_en": "Lists representative actions.",
            "components": [
                {"start": 0, "end": 3, "surface": "寝たり", "lemma": "寝る", "lemma_kana": "ねる", "function_en": "first example action"},
                {"start": 3, "end": 4, "surface": "、", "lemma": "、", "lemma_kana": "、", "function_en": "listing separator"},
                {"start": 4, "end": len(text), "surface": "読んだりする", "lemma": "読む", "lemma_kana": "よむ", "function_en": "second example and completing する"},
            ],
        }],
    }
    assert JapaneseChapterHarness.annotation_reconstructs(text, result)


def test_numeric_approximation_reading_punctuation_is_mechanically_normalized():
    candidate = {
        "segments": [{"surface": "三、四十匹", "type": "word", "lemma": "三、四十匹", "surface_kana": "さん、しじゅっぴき", "lemma_kana": "さん、しじゅっぴき", "part_of_speech": "quantity expression", "conjugation_form": "non-inflecting", "meaning_en": "thirty or forty mice", "story_role": "none", "story_importance_en": ""}],
        "grammar_overlays": [],
    }
    normalized = JapaneseChapterHarness.canonicalize_annotation_candidate(
        "三、四十匹", candidate
    )
    assert normalized["segments"][0]["surface_kana"] == "さんしじゅっぴき"
    assert normalized["segments"][0]["lemma_kana"] == "さんしじゅっぴき"
    assert JapaneseChapterHarness.annotation_reconstructs("三、四十匹", normalized)


def test_numeric_overlay_comma_is_not_a_fake_tappable_component():
    candidate = {
        "segments": [],
        "grammar_overlays": [{
            "start": 0, "end": 6, "surface": "二、三ページ",
            "components": [
                {"start": 0, "end": 1, "surface": "二"},
                {"start": 1, "end": 2, "surface": "、"},
                {"start": 2, "end": 6, "surface": "三ページ"},
            ],
        }],
    }
    normalized = JapaneseChapterHarness.canonicalize_annotation_candidate(
        "二、三ページ", candidate,
    )
    components = normalized["grammar_overlays"][0]["components"]
    assert [component["surface"] for component in components] == ["二", "三ページ"]
    assert [(component["start"], component["end"]) for component in components] == [
        (0, 1), (2, 6),
    ]


def test_kana_surface_keeps_kana_in_final_form_step_with_kanji_lemma():
    candidate = {"segments": [{
        "surface": "とった", "surface_kana": "とった", "lemma": "捕る",
        "lemma_kana": "とる", "form_steps": [{
            "form": "捕った", "reading": "とった", "label": "plain past",
            "meaning_en": "caught",
        }],
    }], "grammar_overlays": []}
    normalized = JapaneseChapterHarness.canonicalize_annotation_candidate(
        "とった", candidate,
    )
    assert normalized["segments"][0]["form_steps"][-1]["form"] == "とった"


def test_nagara_overlay_head_uses_content_lemma_not_realized_stem():
    candidate = {
        "segments": [{
            "surface": "見ながら", "type": "grammar", "lemma": "見る",
            "surface_kana": "みながら", "lemma_kana": "みる",
            "grammar_candidate_key": "nagara",
        }],
        "grammar_overlays": [{
            "start": 0, "end": 4, "surface": "見ながら",
            "grammar_candidate_key": "nagara",
            "head_lemma": "見ながら", "head_lemma_kana": "みながら",
            "components": [],
        }],
    }
    normalized = JapaneseChapterHarness.canonicalize_annotation_candidate(
        "見ながら", candidate,
    )
    overlay = normalized["grammar_overlays"][0]
    assert overlay["head_lemma"] == "見る"
    assert overlay["head_lemma_kana"] == "みる"


def test_nagara_primary_is_not_retyped_as_plain_lexical_word():
    normalized = JapaneseChapterHarness.canonicalize_annotation_candidate(
        "嗅ぎながら",
        {
            "segments": [{
                "surface": "嗅ぎながら", "type": "grammar", "lemma": "嗅ぐ",
                "surface_kana": "かぎながら", "lemma_kana": "かぐ",
                "part_of_speech": "verb", "grammar_candidate_key": "nagara",
            }],
            "grammar_overlays": [],
        },
    )
    assert normalized["segments"][0]["type"] == "grammar"
    assert normalized["segments"][0]["part_of_speech"] == (
        "verb grammar construction"
    )


def test_canonicalizer_restores_exact_trailing_whitespace_order():
    normalized = JapaneseChapterHarness.canonicalize_annotation_candidate(
        "猫。\n　",
        {
            "segments": [
                {"surface": "猫", "type": "word"},
                {"surface": "。", "type": "punctuation"},
                {"surface": "　", "type": "punctuation"},
                {"surface": "\n", "type": "punctuation"},
            ],
            "grammar_overlays": [],
        },
    )
    assert "".join(item["surface"] for item in normalized["segments"]) == "猫。\n　"
    assert normalized["segments"][-1]["surface"] == "\n　"


def test_overlay_pattern_placeholder_is_not_concrete_kana_head():
    normalized = JapaneseChapterHarness.canonicalize_annotation_candidate(
        "困らせてやらねばならない",
        {
            "segments": [{
                "surface": "困らせてやらねばならない", "type": "grammar",
                "surface_kana": "こまらせてやらねばならない",
            }],
            "grammar_overlays": [{
                "surface": "困らせてやらねばならない",
                "head_lemma": "Vせてやらねばならない",
                "head_lemma_kana": "Vせてやらねばならない",
                "components": [],
            }],
        },
    )
    overlay = normalized["grammar_overlays"][0]
    assert overlay["head_lemma"] == "困らせてやらねばならない"
    assert overlay["head_lemma_kana"] == "こまらせてやらねばならない"


def test_obligation_chain_uses_content_verb_as_learner_source():
    normalized = JapaneseChapterHarness.canonicalize_annotation_candidate(
        "困らせてやらねばならない",
        {
            "segments": [{
                "surface": "困らせてやらねばならない", "type": "grammar",
                "lemma": "困らせてやらねばならない",
                "surface_kana": "こまらせてやらねばならない",
                "lemma_kana": "こまらせてやらねばならない",
                "part_of_speech": "obligation construction",
                "grammar_candidate_key": "causative-yaraneba-naranai",
                "form_steps": [{"form": "困らせる"}, {"form": "困らせて"},
                    {"form": "困らせてやる"},
                    {"form": "困らせてやらねばならない"}],
            }],
            "grammar_overlays": [{
                "surface": "困らせてやらねばならない",
                "grammar_candidate_key": "causative-yaraneba-naranai",
                "head_lemma": "困らせてやらねばならない",
                "head_lemma_kana": "こまらせてやらねばならない",
                "components": [{
                    "surface": "困らせて", "lemma": "困る",
                    "lemma_kana": "こまる",
                }, {
                    "surface": "やらねばならない", "lemma": "やる",
                    "lemma_kana": "やる",
                }],
            }],
        },
    )
    primary = normalized["segments"][0]
    overlay = normalized["grammar_overlays"][0]
    assert primary["lemma"] == "困る"
    assert primary["lemma_kana"] == "こまる"
    assert overlay["head_lemma"] == "困る"
    assert overlay["head_lemma_kana"] == "こまる"
    assert [step["form"] for step in primary["form_steps"]] == [
        "困らせる", "困らせて", "困らせてやる", "困らせてやらねばならない",
    ]
    stale_review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "困らせてやらねばならない",
            "problem": "grammar_components",
            "explanation": "The construction has an empty form_steps chain.",
            "suggested_fix": "Use a canonical V... construction head.",
        }],
    }
    assert apply_reader_useful_annotation_review_policy(
        stale_review, normalized,
    ) == {"verdict": "pass", "issues": []}


def test_comparative_adjective_keeps_lexical_and_contextual_meaning():
    normalized = JapaneseChapterHarness.canonicalize_annotation_candidate(
        "より強い",
        {
            "segments": [
                {"surface": "より", "type": "particle"},
                {
                    "surface": "強い", "type": "word",
                    "meaning_en": "stronger",
                },
            ],
            "grammar_overlays": [],
        },
    )
    assert normalized["segments"][1]["meaning_en"] == (
        "strong; stronger here with より"
    )
    stale_review = {
        "verdict": "revise",
        "issues": [{
            "segment_text": "強い", "problem": "meaning",
            "explanation": "The segment itself is the adjective “strong”.",
            "suggested_fix": 'Use meaning_en such as "strong".',
        }],
    }
    assert apply_reader_useful_annotation_review_policy(
        stale_review, normalized,
    ) == {"verdict": "pass", "issues": []}


def test_sasahara_is_canonicalized_as_source_location_not_surname():
    candidate = {"segments": [{
        "surface": "笹原", "type": "name", "lemma": "笹原",
        "surface_kana": "ささはら", "lemma_kana": "ささはら",
        "part_of_speech": "proper noun", "conjugation_form": "non-inflecting",
        "meaning_en": "Sasahara", "story_role": "name",
        "story_importance_en": "a person",
    }, {
        "surface": "に", "type": "particle", "lemma": "に",
        "surface_kana": "に", "lemma_kana": "に",
        "part_of_speech": "particle", "conjugation_form": "non-inflecting",
        "meaning_en": "by", "story_role": "none", "story_importance_en": "",
    }], "grammar_overlays": []}
    normalized = JapaneseChapterHarness.canonicalize_annotation_candidate(
        "笹原に", candidate,
    )
    assert normalized["segments"][0]["type"] == "word"
    assert normalized["segments"][0]["meaning_en"] == "bamboo-grass field"
    assert normalized["segments"][1]["meaning_en"] == "in; at"


def test_context_canonicalizes_written_adjective_connector_and_no_chikaku():
    candidate = {"segments": [{
        "surface": "主人", "type": "word",
    }, {
        "surface": "の", "type": "particle",
    }, {
        "surface": "近く", "type": "word", "lemma": "近い",
        "surface_kana": "ちかく", "lemma_kana": "ちかい",
        "part_of_speech": "i-adjective", "conjugation_form": "adverbial",
        "meaning_en": "near", "form_steps": [{
            "form": "近く", "reading": "ちかく", "label": "adverbial",
            "meaning_en": "nearby",
        }],
    }, {
        "surface": "。", "type": "punctuation",
    }, {
        "surface": "明るく", "type": "word", "lemma": "明るい",
        "surface_kana": "あかるく", "lemma_kana": "あかるい",
        "part_of_speech": "i-adjective",
        "conjugation_form": "conjunctive ren'yōkei",
        "meaning_en": "brightly", "form_steps": [{
            "form": "明るく", "reading": "あかるく",
            "label": "conjunctive ren'yōkei", "meaning_en": "brightly",
        }],
    }, {
        "surface": "、", "type": "punctuation",
    }], "grammar_overlays": []}
    normalized = JapaneseChapterHarness.canonicalize_annotation_candidate(
        "主人の近く。明るく、", candidate,
    )
    chikaku, akaruku = normalized["segments"][2], normalized["segments"][4]
    assert (chikaku["lemma"], chikaku["part_of_speech"]) == ("近く", "noun")
    assert chikaku["form_steps"] == []
    assert akaruku["meaning_en"] == "bright; being bright"
    assert akaruku["form_steps"][0]["meaning_en"] == "being bright"


def test_context_canonicalizes_syntactic_koto_nominalizer_without_dictionary_homograph():
    annotation = JapaneseChapterHarness.canonicalize_annotation_candidate(
        "覚えていたことだけ。",
        {
            "segments": [
                {"surface": "覚えていた", "type": "word"},
                {
                    "surface": "こと", "type": "word", "lemma": "こと",
                    "surface_kana": "こと", "lemma_kana": "こと",
                    "part_of_speech": "noun; nominalizer",
                    "conjugation_form": "non-inflecting",
                    "meaning_en": "fact; matter", "grammar_candidate_key": "",
                    "dictionary_key": "", "dictionary_definition_en": "",
                    "form_steps": [], "story_role": "none",
                    "story_importance_en": "",
                },
                {"surface": "だけ", "type": "particle"},
                {"surface": "。", "type": "punctuation"},
            ],
            "grammar_overlays": [],
        },
    )
    koto = annotation["segments"][1]
    assert koto["type"] == "grammar"
    assert koto["grammar_candidate_key"] == "nominalizer-koto"
    assert koto["dictionary_key"] == ""
    assert "nominalizes" in koto["meaning_en"]


def test_context_canonicalizes_inflected_lexical_verb_mistyped_as_grammar():
    annotation = JapaneseChapterHarness.canonicalize_annotation_candidate(
        "残っている。",
        {
            "segments": [{
                "surface": "残っている", "type": "grammar", "lemma": "残る",
                "surface_kana": "のこっている", "lemma_kana": "のこる",
                "part_of_speech": "verb", "conjugation_form": "ている state",
                "meaning_en": "remains", "grammar_candidate_key": "te-iru",
                "dictionary_key": "", "dictionary_definition_en": "",
                "form_steps": [{"form": "残っている"}],
            }, {"surface": "。", "type": "punctuation"}],
            "grammar_overlays": [],
        },
    )
    verb = annotation["segments"][0]
    assert verb["type"] == "word"
    assert verb["dictionary_key"] == "残る"
    assert verb["dictionary_definition_en"] == "remains"
    assert verb["grammar_candidate_key"] == "te-iru"


def test_context_canonicalizes_sentence_linking_suruto_as_learned_unit():
    annotation = JapaneseChapterHarness.canonicalize_annotation_candidate(
        "すると、猫が来た。",
        {
            "segments": [{
                "surface": "すると", "type": "grammar", "lemma": "する",
                "surface_kana": "すると", "lemma_kana": "する",
                "part_of_speech": "verb plus conditional particle",
                "conjugation_form": "conditional", "meaning_en": "then",
                "grammar_candidate_key": "conditional-to",
                "dictionary_key": "", "dictionary_definition_en": "",
                "form_steps": [], "story_role": "none",
                "story_importance_en": "",
            }, {"surface": "、", "type": "punctuation"},
            {"surface": "猫が来た", "type": "word"},
            {"surface": "。", "type": "punctuation"}],
            "grammar_overlays": [],
        },
    )
    connective = annotation["segments"][0]
    assert connective["lemma"] == "すると"
    assert connective["conjugation_form"] == "non-inflecting"
    assert connective["form_steps"] == []
    assert connective["grammar_candidate_key"] == "conditional-to"


def test_context_canonicalizes_directional_predicate_to_content_verb_lemma():
    annotation = JapaneseChapterHarness.canonicalize_annotation_candidate(
        "出て来ない。",
        {
            "segments": [{
                "surface": "出て来ない", "type": "grammar",
                "lemma": "出て来る", "surface_kana": "でてこない",
                "lemma_kana": "でてくる",
                "part_of_speech": "verb grammar construction",
                "conjugation_form": "Vて来る, plain negative",
                "meaning_en": "does not come out toward here",
                "grammar_candidate_key": "v-te-kuru",
                "dictionary_key": "", "dictionary_definition_en": "",
                "form_steps": [
                    {"form": "出て", "reading": "でて"},
                    {"form": "出て来る", "reading": "でてくる"},
                    {"form": "出て来ない", "reading": "でてこない"},
                ],
            }, {"surface": "。", "type": "punctuation"}],
            "grammar_overlays": [{
                "surface": "出て来ない",
                "components": [{
                    "surface": "出て", "lemma": "出る", "lemma_kana": "でる",
                }, {
                    "surface": "来ない", "lemma": "来る", "lemma_kana": "くる",
                }],
            }],
        },
    )
    predicate = annotation["segments"][0]
    assert predicate["type"] == "word"
    assert predicate["lemma"] == "出る"
    assert predicate["dictionary_key"] == "出る"
    assert [step["form"] for step in predicate["form_steps"]] == [
        "出て", "出て来る", "出て来ない",
    ]


def test_directional_ambiguous_kana_root_uses_verified_component_link():
    annotation = JapaneseChapterHarness.canonicalize_annotation_candidate(
        "ついて来れば",
        {
            "segments": [{
                "surface": "ついて来れば", "type": "grammar",
                "lemma": "ついて来る", "surface_kana": "ついてくれば",
                "lemma_kana": "ついてくる",
                "part_of_speech": "verb grammar construction",
                "conjugation_form": "Vて来る conditional",
                "meaning_en": "if you come along",
                "grammar_candidate_key": "v-te-kuru",
                "dictionary_key": "", "dictionary_definition_en": "",
                "form_steps": [],
            }],
            "grammar_overlays": [{
                "surface": "ついて来れば",
                "components": [{
                    "surface": "ついて", "lemma": "つく",
                    "lemma_kana": "つく", "lookup_kind": "lexical",
                    "dictionary_key": "付く",
                    "dictionary_definition_en": "to follow; to accompany",
                }, {
                    "surface": "来れば", "lemma": "来る",
                    "lemma_kana": "くる", "lookup_kind": "grammar",
                    "dictionary_key": "", "dictionary_definition_en": "",
                }],
            }],
        },
    )
    predicate = annotation["segments"][0]
    assert predicate["type"] == "word"
    assert predicate["lemma"] == "つく"
    assert predicate["dictionary_key"] == "付く"


def test_multisegment_placeholder_head_gets_concrete_surface_reading():
    annotation = JapaneseChapterHarness.canonicalize_annotation_candidate(
        "「いくら猫でも",
        {
            "segments": [
                {"surface": "「", "type": "punctuation"},
                {"surface": "いくら", "type": "word", "surface_kana": "いくら"},
                {"surface": "猫", "type": "word", "surface_kana": "ねこ"},
                {"surface": "でも", "type": "grammar", "surface_kana": "でも"},
            ],
            "grammar_overlays": [{
                "start": 1, "end": 7, "surface": "いくら猫でも",
                "grammar_candidate_key": "ikura-demo",
                "pattern": "いくらXでも", "meaning_en": "no matter which cat",
                "head_lemma": "いくらXでも", "head_lemma_kana": "いくらXでも",
                "form_label": "concessive", "explanation_en": "A template.",
                "components": [],
            }],
        },
    )
    overlay = annotation["grammar_overlays"][0]
    assert overlay["head_lemma"] == "いくら猫でも"
    assert overlay["head_lemma_kana"] == "いくらねこでも"


def test_tagaru_realized_lemma_is_rebased_to_supplied_content_base():
    annotation = JapaneseChapterHarness.canonicalize_annotation_candidate(
        "手を出したがる",
        {
            "segments": [{
                "surface": "手を出したがる", "type": "grammar",
                "lemma": "手を出したがる", "surface_kana": "てをだしたがる",
                "lemma_kana": "てをだしたがる",
                "part_of_speech": "verb construction",
                "conjugation_form": "Vたがる plain nonpast",
                "meaning_en": "appears eager to get involved",
                "grammar_candidate_key": "tagaru",
                "form_steps": [{
                    "form": "手を出す", "reading": "てをだす",
                    "label": "content base", "meaning_en": "get involved",
                }, {
                    "form": "手を出したがる", "reading": "てをだしたがる",
                    "label": "Vたがる", "meaning_en": "appears eager",
                }],
            }],
            "grammar_overlays": [{
                "surface": "手を出したがる",
                "grammar_candidate_key": "tagaru",
                "components": [{
                    "surface": "手を出した", "lemma": "手を出す",
                    "lemma_kana": "てをだす", "lookup_kind": "none",
                }, {
                    "surface": "がる", "lemma": "たがる",
                    "lemma_kana": "たがる", "lookup_kind": "grammar",
                }],
            }],
        },
    )
    normalized = normalize_redundant_japanese_form_steps(annotation)
    predicate = normalized["segments"][0]
    assert predicate["lemma"] == "手を出す"
    assert predicate["lemma_kana"] == "てをだす"
    assert [step["form"] for step in predicate["form_steps"]] == [
        "手を出したい", "手を出したがる",
    ]
    assert [
        component["surface"]
        for component in normalized["grammar_overlays"][0]["components"]
    ] == ["手を出し", "たがる"]
    assert normalized["grammar_overlays"][0]["components"][0][
        "lookup_kind"
    ] == "lexical"


def test_directional_word_with_construction_lemma_is_rebased_to_content_root():
    normalized = JapaneseChapterHarness.canonicalize_annotation_candidate(
        "持って行き",
        {
            "segments": [{
                "surface": "持って行き", "type": "word", "lemma": "持って行く",
                "surface_kana": "もっていき", "lemma_kana": "もっていく",
                "part_of_speech": "verb",
                "conjugation_form": "Vて行く conjunctive",
                "meaning_en": "carried away",
                "grammar_candidate_key": "v-te-iku",
                "form_steps": [{
                    "form": "持って", "reading": "もって",
                }, {
                    "form": "持って行く", "reading": "もっていく",
                }, {
                    "form": "持って行き", "reading": "もっていき",
                }],
            }],
            "grammar_overlays": [{
                "surface": "持って行き", "components": [{
                    "surface": "持って", "lemma": "持つ", "lemma_kana": "もつ",
                }, {
                    "surface": "行き", "lemma": "行く", "lemma_kana": "いく",
                }],
            }],
        },
    )
    predicate = normalized["segments"][0]
    assert predicate["lemma"] == "持つ"
    assert predicate["dictionary_key"] == "持つ"


def test_lexical_gochisou_is_not_mistaken_for_sou_appearance():
    assert japanese_required_overlay_issues([{
        "surface": "ごちそう", "type": "word", "lemma": "ごちそう",
        "part_of_speech": "noun", "conjugation_form": "non-inflecting",
    }], []) == []


def test_numeric_approximation_skips_punctuation_component():
    segments = [
        {"surface": "三", "type": "word", "lemma": "三", "surface_kana": "さん", "lemma_kana": "さん", "part_of_speech": "numeral", "conjugation_form": "non-inflecting", "meaning_en": "three; elliptically thirty here", "story_role": "none", "story_importance_en": ""},
        {"surface": "、", "type": "punctuation", "lemma": "", "surface_kana": "", "lemma_kana": "", "part_of_speech": "", "conjugation_form": "", "meaning_en": "", "story_role": "none", "story_importance_en": ""},
        {"surface": "四十匹", "type": "word", "lemma": "四十匹", "surface_kana": "よんじゅっぴき", "lemma_kana": "よんじゅっぴき", "part_of_speech": "numeral plus counter", "conjugation_form": "non-inflecting", "meaning_en": "forty animals; mice here", "story_role": "none", "story_importance_en": ""},
    ]
    overlay = {
        "start": 0, "end": 5, "surface": "三、四十匹",
        "grammar_candidate_key": "quantity.approximate_range", "pattern": "三、四十+counter",
        "meaning_en": "roughly thirty or forty mice", "head_lemma": "三、四十匹",
        "head_lemma_kana": "さんよんじゅっぴき", "form_label": "elliptical approximate range",
        "explanation_en": "The first tens suffix is omitted in this conventional notation.",
        "components": [
            {"start": 0, "end": 1, "surface": "三", "lemma": "三", "lemma_kana": "さん", "function_en": "elliptical thirty"},
            {"start": 2, "end": 5, "surface": "四十匹", "lemma": "四十匹", "lemma_kana": "よんじゅっぴき", "function_en": "forty plus animal counter"},
        ],
    }
    assert JapaneseChapterHarness.annotation_reconstructs(
        "三、四十匹", {"segments": segments, "grammar_overlays": [overlay]}
    )


def test_japanese_canonicalizer_repairs_unique_overlay_offset_drift():
    text = "彼が騙した話を読まれていたらと心配すると言った。\n"
    candidate = {
        "segments": [{
            "surface": text.rstrip("\n"), "type": "word", "lemma": "言う",
            "surface_kana": "かれがだましたはなしをよまれていたらとしんぱいするといった",
            "lemma_kana": "いう", "part_of_speech": "verb",
            "conjugation_form": "past", "meaning_en": "placeholder segmentation",
            "story_role": "none", "story_importance_en": "",
        }],
        "grammar_overlays": [
            {"start": 2, "end": 8, "surface": "騙した話", "grammar_candidate_key": "relative.past", "pattern": "VたN", "meaning_en": "the story that someone told deceptively", "head_lemma": "騙す", "head_lemma_kana": "だます", "form_label": "past relative clause", "explanation_en": "A past verb modifies a noun.", "components": [{"start": 0, "end": 5, "surface": "騙した話", "lemma": "騙す", "lemma_kana": "だます", "function_en": "past clause plus noun"}]},
            {"start": 9, "end": 24, "surface": "読まれていたらと心配する", "grammar_candidate_key": "passive.conditional.concern", "pattern": "Vられたらと心配する", "meaning_en": "worry that it might be read", "head_lemma": "読む", "head_lemma_kana": "よむ", "form_label": "passive conditional", "explanation_en": "Expresses concern about a possible passive event.", "components": [{"start": 0, "end": 11, "surface": "読まれていたらと心配する", "lemma": "読む", "lemma_kana": "よむ", "function_en": "passive conditional concern construction"}]},
        ],
    }
    normalized = JapaneseChapterHarness.canonicalize_annotation_candidate(
        text, candidate
    )
    assert normalized["segments"][-1]["surface"] == "\n"
    for overlay in normalized["grammar_overlays"]:
        assert text[overlay["start"]:overlay["end"]] == overlay["surface"]


def test_japanese_canonicalizer_removes_only_agent_added_paragraph_whitespace():
    text = "猫だ。\n　犬だ。鳥だ。"
    candidate = {
        "segments": [
            {"surface": "猫だ。", "type": "word"},
            {"surface": "\n　", "type": "punctuation"},
            {"surface": "犬だ。", "type": "word"},
            {"surface": "\n　", "type": "punctuation"},
            {"surface": "鳥だ。", "type": "word"},
        ],
        "grammar_overlays": [{
            "start": 12, "end": 14, "surface": "鳥だ",
            "components": [{"start": 0, "end": 2, "surface": "鳥だ"}],
        }],
    }

    normalized = JapaneseChapterHarness.canonicalize_annotation_candidate(
        text, candidate
    )

    assert "".join(item["surface"] for item in normalized["segments"]) == text
    assert [item["surface"] for item in normalized["segments"]].count("\n　") == 1
    assert normalized["grammar_overlays"][0]["start"] == text.index("鳥だ")


def test_japanese_canonicalizer_normalizes_pronoun_type_and_component_offsets():
    text = "吾輩は食べました"
    candidate = {
        "segments": [
            {"surface": "吾輩", "type": "name", "lemma": "吾輩", "surface_kana": "わがはい", "lemma_kana": "わがはい", "part_of_speech": "pronoun", "conjugation_form": "non-inflecting", "meaning_en": "I", "story_role": "story_term", "story_importance_en": "the cat narrator's signature first-person expression"},
            {"surface": "は", "type": "particle", "lemma": "は", "surface_kana": "は", "lemma_kana": "は", "part_of_speech": "topic particle", "conjugation_form": "non-inflecting", "meaning_en": "as for", "story_role": "none", "story_importance_en": ""},
            {"surface": "食べました", "type": "word", "lemma": "食べる", "surface_kana": "たべました", "lemma_kana": "たべる", "part_of_speech": "verb", "conjugation_form": "polite past", "meaning_en": "ate", "story_role": "none", "story_importance_en": ""},
        ],
        "grammar_overlays": [{
            "start": 0, "end": 1, "surface": "食べました",
            "grammar_candidate_key": "verb.polite_past", "pattern": "Vます+た",
            "meaning_en": "ate", "head_lemma": "食べる", "head_lemma_kana": "たべる",
            "form_label": "polite past", "explanation_en": "Polite past form.",
            "components": [
                {"start": 9, "end": 10, "surface": "食べ", "lemma": "食べる", "lemma_kana": "たべる", "function_en": "lexical verb"},
                {"start": 9, "end": 10, "surface": "ました", "lemma": "ます", "lemma_kana": "ます", "function_en": "polite past form"},
            ],
        }],
    }
    normalized = JapaneseChapterHarness.canonicalize_annotation_candidate(text, candidate)
    assert normalized["segments"][0]["type"] == "word"
    assert normalized["segments"][0]["story_role"] == "story_term"
    overlay = normalized["grammar_overlays"][0]
    assert (overlay["start"], overlay["end"]) == (3, 8)
    assert [(item["start"], item["end"]) for item in overlay["components"]] == [
        (0, 2), (2, 5),
    ]
    assert JapaneseChapterHarness.annotation_reconstructs(text, normalized)


def test_japanese_canonicalizer_demotes_name_type_for_planned_story_term():
    candidate = {
        "segments": [{
            "surface": "書生", "type": "name", "lemma": "書生",
            "surface_kana": "しょせい", "lemma_kana": "しょせい",
            "part_of_speech": "noun", "conjugation_form": "non-inflecting",
            "meaning_en": "a live-in student or young scholar",
            "story_role": "story_term",
            "story_importance_en": "A recurring historical social role.",
        }],
        "grammar_overlays": [],
    }
    normalized = JapaneseChapterHarness.canonicalize_annotation_candidate(
        "書生", candidate
    )
    assert normalized["segments"][0]["type"] == "word"
    assert JapaneseChapterHarness.annotation_reconstructs("書生", normalized)


def test_japanese_canonicalizer_inserts_one_unambiguously_omitted_punctuation():
    text = "黒だ」と名乗った。"
    candidate = {
        "segments": [
            {"surface": "黒", "type": "name", "lemma": "黒", "surface_kana": "くろ", "lemma_kana": "くろ", "part_of_speech": "proper name", "conjugation_form": "non-inflecting", "meaning_en": "Kuro", "story_role": "name", "story_importance_en": ""},
            {"surface": "だ", "type": "auxiliary", "lemma": "だ", "surface_kana": "だ", "lemma_kana": "だ", "part_of_speech": "copula", "conjugation_form": "terminal", "meaning_en": "is", "story_role": "none", "story_importance_en": ""},
            {"surface": "と", "type": "particle", "lemma": "と", "surface_kana": "と", "lemma_kana": "と", "part_of_speech": "quotation particle", "conjugation_form": "non-inflecting", "meaning_en": "quotation marker", "story_role": "none", "story_importance_en": ""},
            {"surface": "名乗った", "type": "word", "lemma": "名乗る", "surface_kana": "なのった", "lemma_kana": "なのる", "part_of_speech": "verb", "conjugation_form": "plain past", "meaning_en": "introduced himself", "story_role": "none", "story_importance_en": ""},
            {"surface": "。", "type": "punctuation", "lemma": "", "surface_kana": "", "lemma_kana": "", "part_of_speech": "", "conjugation_form": "", "meaning_en": "", "story_role": "none", "story_importance_en": ""},
        ],
        "grammar_overlays": [],
    }
    normalized = JapaneseChapterHarness.canonicalize_annotation_candidate(
        text, candidate
    )
    assert [item["surface"] for item in normalized["segments"]][2] == "」"
    assert JapaneseChapterHarness.annotation_reconstructs(text, normalized)


def test_annotation_contract_rejects_romaji_empty_meaning_and_clause_word():
    base = {"grammar_overlays": []}
    for segment in (
        {"surface": "猫", "type": "word", "lemma": "猫", "surface_kana": "neko", "lemma_kana": "ねこ", "part_of_speech": "noun", "conjugation_form": "non-inflecting", "meaning_en": "cat", "story_role": "none", "story_importance_en": ""},
        {"surface": "猫", "type": "word", "lemma": "猫", "surface_kana": "ねこ", "lemma_kana": "ねこ", "part_of_speech": "noun", "conjugation_form": "non-inflecting", "meaning_en": "", "story_role": "none", "story_importance_en": ""},
        {"surface": "これは普通の長い文節ですから", "type": "word", "lemma": "文節", "surface_kana": "ぶんせつ", "lemma_kana": "ぶんせつ", "part_of_speech": "noun", "conjugation_form": "non-inflecting", "meaning_en": "clause", "story_role": "none", "story_importance_en": ""},
    ):
        assert not JapaneseChapterHarness.annotation_reconstructs(
            segment["surface"], {**base, "segments": [segment]}
        )


def test_learner_segmentation_rejects_tokenizer_sized_inflection_fragments():
    fragmented = [
        {"surface": "あり", "type": "word", "conjugation_form": "continuative stem"},
        {"surface": "まし", "type": "auxiliary", "conjugation_form": "continuative"},
        {"surface": "た", "type": "auxiliary", "conjugation_form": "past"},
    ]
    issues = japanese_learner_segmentation_issues(fragmented)
    assert any(item["surface"] == "まし" for item in issues)
    assert any(item["surface"] == "ました" for item in issues)


def test_learner_segmentation_keeps_ima_demo_as_one_time_adverb():
    fragmented = [
        {"surface": "今", "type": "word"},
        {"surface": "で", "type": "particle"},
        {"surface": "も", "type": "particle"},
    ]
    assert any(
        item["surface"] == "今でも"
        for item in japanese_learner_segmentation_issues(fragmented)
    )
    assert japanese_learner_segmentation_issues([
        {"surface": "今でも", "type": "word"},
    ]) == []


def test_learner_segmentation_does_not_hide_dou_sureba_yoi_question_heads():
    issues = japanese_learner_segmentation_issues([
        {"surface": "どうすればよいか", "type": "grammar"},
    ])
    assert [item["surface"] for item in issues] == ["どうすればよいか"]


def test_learner_segmentation_accepts_complete_inflected_surface_and_grammar_chunk():
    segments = [
        {"surface": "歩く", "type": "word", "conjugation_form": "plain nonpast"},
        {"surface": "ことにしました", "type": "grammar", "conjugation_form": "polite past"},
        {"surface": "。", "type": "punctuation", "conjugation_form": ""},
        {"surface": "家", "type": "word", "conjugation_form": "non-inflecting"},
        {"surface": "が", "type": "particle", "conjugation_form": "non-inflecting"},
        {"surface": "ありました", "type": "word", "conjugation_form": "polite past"},
    ]
    assert japanese_learner_segmentation_issues(segments) == []


def test_conditional_to_is_keyed_grammar_with_predicate_overlay():
    segments = [
        {
            "surface": "開ける", "type": "word", "lemma": "開ける",
            "part_of_speech": "verb", "meaning_en": "open",
        },
        {
            "surface": "と", "type": "particle", "lemma": "と",
            "part_of_speech": "conditional particle", "meaning_en": "when",
            "grammar_candidate_key": "",
        },
    ]
    issues = japanese_learner_segmentation_issues(segments)
    assert any("conditional と" in issue["message"] for issue in issues)
    segments[1].update({
        "type": "grammar", "grammar_candidate_key": "conditional-to",
    })
    assert japanese_learner_segmentation_issues(segments) == []
    required = japanese_required_overlay_issues(segments, [])
    assert any(issue["surface"] == "開けると" for issue in required)
    assert japanese_required_overlay_issues(
        segments, [{"surface": "開けると"}],
    ) == []


def test_conditional_to_accepts_a_reviewed_wider_productive_predicate_overlay():
    segments = [
        {
            "surface": "出てみる", "type": "grammar", "lemma": "出てみる",
            "grammar_candidate_key": "te-miru",
        },
        {
            "surface": "と", "type": "grammar", "lemma": "と",
            "part_of_speech": "conditional particle",
            "grammar_candidate_key": "conditional-to",
        },
    ]
    overlays = [{
        "start": 0,
        "end": 5,
        "surface": "出てみると",
        "components": [
            {"surface": "出て"},
            {"surface": "みる"},
            {"surface": "と"},
        ],
    }]
    assert japanese_required_overlay_issues(segments, overlays) == []


def test_learner_segmentation_keeps_v_nagara_as_one_grammar_primary():
    split = [
        {
            "surface": "見", "type": "word", "lemma": "見る",
            "conjugation_form": "continuative stem",
        },
        {"surface": "ながら", "type": "grammar", "lemma": "ながら"},
    ]
    issues = japanese_learner_segmentation_issues(split)
    assert any(issue["surface"] == "見ながら" for issue in issues)

    whole = [{
        "surface": "見ながら", "type": "grammar", "lemma": "見る",
        "grammar_candidate_key": "nagara",
    }]
    assert japanese_learner_segmentation_issues(whole) == []
    wrong_lemma = [dict(whole[0], lemma="ながら")]
    assert any(
        "content verb's dictionary lemma" in issue["message"]
        for issue in japanese_learner_segmentation_issues(wrong_lemma)
    )


def test_learner_segmentation_keeps_productive_te_miru_whole():
    split = [
        {
            "surface": "出て", "type": "word", "lemma": "出る",
            "conjugation_form": "て-form",
        },
        {
            "surface": "みる", "type": "grammar", "lemma": "みる",
            "part_of_speech": "auxiliary", "conjugation_form": "Vてみる",
            "grammar_candidate_key": "te-miru",
        },
    ]
    issues = japanese_learner_segmentation_issues(split)
    assert any(issue["surface"] == "出てみる" for issue in issues)


def test_productive_nagara_and_te_miru_require_roots_overlay():
    segments = [
        {
            "surface": "見ながら", "type": "grammar", "lemma": "見る",
            "grammar_candidate_key": "nagara",
        },
        {"surface": "、", "type": "punctuation"},
        {
            "surface": "出てみる", "type": "grammar", "lemma": "出てみる",
            "grammar_candidate_key": "te-miru",
        },
    ]
    issues = japanese_required_overlay_issues(segments, [])
    assert {issue["surface"] for issue in issues} == {"見ながら", "出てみる"}
    overlays = [
        {"surface": "見ながら"},
        {"surface": "出てみると"},
    ]
    assert japanese_required_overlay_issues(segments, overlays) == []


def test_quotative_to_is_not_forced_into_conditional_grammar():
    segments = [{
        "surface": "と", "type": "particle", "lemma": "と",
        "part_of_speech": "quotative particle", "meaning_en": "that",
        "grammar_candidate_key": "",
    }]
    assert japanese_learner_segmentation_issues(segments) == []


def test_learner_segmentation_keeps_conventional_desu_ga_connector_separate():
    segments = [
        {
            "surface": "痛かった", "type": "word", "lemma": "痛い",
            "part_of_speech": "i-adjective", "conjugation_form": "plain past",
        },
        {
            "surface": "ですが", "type": "grammar", "lemma": "です",
            "part_of_speech": "copula", "conjugation_form": "polite continuative contrast",
        },
    ]
    assert japanese_learner_segmentation_issues(segments) == []


@pytest.mark.parametrize("surface", [
    "歩くことにしました",
    "とったことがある",
    "とったことはない",
])
def test_learner_segmentation_leaves_predicate_before_koto_grammar(surface):
    issues = japanese_learner_segmentation_issues([{
        "surface": surface,
        "type": "grammar",
        "lemma": surface,
        "part_of_speech": "grammar construction",
    }])
    assert any(issue["surface"] == surface for issue in issues)


def test_required_overlay_policy_requires_vta_experience_whole_form():
    segments = [
        {"surface": "とった", "type": "word", "lemma": "捕る"},
        {"surface": "ことがある", "type": "grammar", "lemma": "ことがある"},
    ]
    issues = japanese_required_overlay_issues(segments, [])
    assert any(issue["surface"] == "とったことがある" for issue in issues)
    assert japanese_required_overlay_issues(
        segments, [{"surface": "とったことがある"}],
    ) == []


@pytest.mark.parametrize(("parts", "whole"), [
    (["こと", "が", "ある"], "ことがある"),
    (["こと", "は", "ない"], "ことはない"),
])
def test_experience_grammar_unit_is_not_split_into_particles(parts, whole):
    issues = japanese_learner_segmentation_issues([
        {"surface": part, "type": "word", "lemma": part}
        for part in parts
    ])
    assert any(issue["surface"] == whole for issue in issues)


def test_required_overlay_policy_requires_n_no_you_da_whole_construction():
    segments = [
        {"surface": "やかん", "type": "word", "lemma": "やかん"},
        {"surface": "のようだった", "type": "grammar", "lemma": "のようだ"},
    ]
    issues = japanese_required_overlay_issues(segments, [])
    assert any(issue["surface"] == "やかんのようだった" for issue in issues)
    assert japanese_required_overlay_issues(
        segments, [{"surface": "やかんのようだった"}],
    ) == []


def test_hunger_overlay_names_complete_collocation_as_head_form():
    overlay = {
        "surface": "お腹がすいて", "head_lemma": "すく",
        "grammar_candidate_key": "onaka-ga-suku",
        "components": [
            {"surface": "お腹", "lemma": "お腹", "lookup_kind": "lexical"},
            {"surface": "が", "lemma": "が", "lookup_kind": "none"},
            {"surface": "すいて", "lemma": "すく", "lookup_kind": "none"},
        ],
    }
    assert "complete learned collocation" in japanese_overlay_policy_issue(overlay)
    overlay["head_lemma"] = "お腹がすく"
    assert japanese_overlay_policy_issue(overlay) is None


def test_hunger_te_form_cannot_be_split_into_stem_and_connector():
    issues = japanese_learner_segmentation_issues([
        {
            "surface": "すい", "type": "word", "lemma": "すく",
            "part_of_speech": "verb", "conjugation_form": "continuative stem",
        },
        {
            "surface": "て", "type": "particle", "lemma": "て",
            "part_of_speech": "connective particle",
            "conjugation_form": "non-inflecting",
        },
    ])
    assert any(issue["surface"] == "すいて" for issue in issues)


@pytest.mark.parametrize(
    ("segments", "expected_surface"),
    [
        (
            [
                {
                    "surface": "休んで", "type": "word", "lemma": "休む",
                    "part_of_speech": "verb", "conjugation_form": "て-form",
                },
                {
                    "surface": "いる", "type": "auxiliary", "lemma": "いる",
                    "part_of_speech": "auxiliary verb",
                    "conjugation_form": "plain nonpast",
                },
            ],
            "休んでいる",
        ),
        (
            [
                {
                    "surface": "我儘", "type": "word", "lemma": "我儘",
                    "part_of_speech": "な-adjective",
                    "conjugation_form": "non-inflecting",
                },
                {
                    "surface": "な", "type": "particle", "lemma": "な",
                    "part_of_speech": "attributive particle",
                    "conjugation_form": "non-inflecting",
                },
            ],
            "我儘な",
        ),
        (
            [
                {
                    "surface": "一生懸命", "type": "word", "lemma": "一生懸命",
                    "part_of_speech": "な-adjective/adverbial noun",
                    "conjugation_form": "non-inflecting",
                },
                {
                    "surface": "だった", "type": "grammar", "lemma": "だ",
                    "part_of_speech": "copula", "conjugation_form": "plain past",
                },
            ],
            "一生懸命だった",
        ),
        (
            [
                {
                    "surface": "痛かった", "type": "word", "lemma": "痛い",
                    "part_of_speech": "i-adjective", "conjugation_form": "plain past",
                },
                {
                    "surface": "です", "type": "auxiliary", "lemma": "です",
                    "part_of_speech": "polite copula", "conjugation_form": "nonpast",
                },
            ],
            "痛かったです",
        ),
        (
            [
                {
                    "surface": "つるつる", "type": "word", "lemma": "つるつる",
                    "part_of_speech": "adverb", "conjugation_form": "non-inflecting",
                },
                {
                    "surface": "で", "type": "auxiliary", "lemma": "だ",
                    "part_of_speech": "copula", "conjugation_form": "connective",
                },
            ],
            "つるつるで",
        ),
        (
            [
                {
                    "surface": "写生し", "type": "word", "lemma": "写生する",
                    "part_of_speech": "suru verb",
                    "conjugation_form": "conjunctive ren'yōkei",
                },
                {
                    "surface": "始めた", "type": "word", "lemma": "始める",
                    "part_of_speech": "verb", "conjugation_form": "plain past",
                },
            ],
            "写生し始めた",
        ),
        (
            [
                {"surface": "そう", "type": "word", "lemma": "そう"},
                {"surface": "で", "type": "particle", "lemma": "で"},
                {"surface": "も", "type": "particle", "lemma": "も"},
                {"surface": "ない", "type": "auxiliary", "lemma": "ない"},
            ],
            "そうでもない",
        ),
    ],
)
def test_learner_segmentation_rejects_split_learner_sized_forms(
    segments, expected_surface,
):
    issues = japanese_learner_segmentation_issues(segments)
    assert expected_surface in {issue["surface"] for issue in issues}


@pytest.mark.parametrize(("surfaces", "expected"), [
    (["で", "ある"], "である"),
    (["後", "で"], "後で"),
    (["の", "だろう"], "のだろう"),
    (["の", "か"], "のか"),
    (["この", "頃"], "この頃"),
    (["その", "後"], "その後"),
    (["と", "いい"], "といい"),
    (["という", "こと"], "ということ"),
    (["だ", "ということ"], "だということ"),
    (["だ", "という", "こと"], "だということ"),
    (["はず", "の"], "はずの"),
    (["動かず", "に"], "動かずに"),
    (["垂らしていたり", "する"], "垂らしていたりする"),
])
def test_learner_segmentation_rejects_split_conventional_grammar_units(
    surfaces, expected,
):
    segments = [
        {"surface": surface, "type": "grammar", "conjugation_form": "form"}
        for surface in surfaces
    ]
    issues = japanese_learner_segmentation_issues(segments)
    assert any(item["surface"] == expected for item in issues)


def test_learner_segmentation_rejects_impossible_te_form_label():
    segments = [{
        "surface": "食べ", "type": "word", "lemma": "食べる",
        "conjugation_form": "て-form, connective",
    }]
    issues = japanese_learner_segmentation_issues(segments)
    assert any(item["surface"] == "食べ" for item in issues)


@pytest.mark.parametrize(
    ("segment", "expected_surface"),
    [
        (
            {
                "surface": "生まれたのか", "type": "grammar", "lemma": "のか",
                "part_of_speech": "grammar", "conjugation_form": "past + のか",
            },
            "生まれたのか",
        ),
        (
            {
                "surface": "人間というもの", "type": "grammar",
                "lemma": "というもの", "part_of_speech": "grammar",
                "conjugation_form": "non-inflecting",
            },
            "人間というもの",
        ),
        (
            {
                "surface": "気持ちよさそうに", "type": "grammar",
                "lemma": "そうだ", "part_of_speech": "adjectival expression",
                "conjugation_form": "そうだ, adverbial",
            },
            "気持ちよさそうに",
        ),
    ],
)
def test_learner_segmentation_preserves_lexical_head_before_grammar(
    segment, expected_surface,
):
    issues = japanese_learner_segmentation_issues([segment])
    assert expected_surface in {issue["surface"] for issue in issues}


def test_negative_nanimo_glosses_must_both_say_nothing():
    segments = [
        {
            "surface": "何", "type": "word", "lemma": "何",
            "meaning_en": "anything; what", "conjugation_form": "non-inflecting",
        },
        {
            "surface": "も", "type": "particle", "lemma": "も",
            "meaning_en": "even; any", "conjugation_form": "non-inflecting",
        },
        {
            "surface": "考え", "type": "word", "lemma": "考え",
            "meaning_en": "thought", "conjugation_form": "non-inflecting",
        },
        {
            "surface": "が", "type": "particle", "lemma": "が",
            "meaning_en": "subject marker", "conjugation_form": "non-inflecting",
        },
        {
            "surface": "なく", "type": "auxiliary", "lemma": "ない",
            "meaning_en": "not having", "conjugation_form": "negative connective",
        },
    ]
    issues = japanese_learner_segmentation_issues(segments)
    assert any(issue["surface"] == "何も" for issue in issues)


def test_learner_segmentation_keeps_ka_to_omou_form_together():
    segments = [
        {"surface": "か", "type": "particle", "lemma": "か"},
        {"surface": "と", "type": "particle", "lemma": "と"},
        {"surface": "思い", "type": "word", "lemma": "思う"},
    ]
    issues = japanese_learner_segmentation_issues(segments)
    assert any(issue["surface"] == "かと思い" for issue in issues)


def test_learner_segmentation_keeps_no_you_da_form_together():
    segments = [
        {"surface": "の", "type": "particle", "lemma": "の"},
        {"surface": "ようだった", "type": "grammar", "lemma": "ようだ"},
    ]
    issues = japanese_learner_segmentation_issues(segments)
    assert any(issue["surface"] == "のようだった" for issue in issues)


def test_learner_segmentation_keeps_body_part_wo_waruku_suru_collocation():
    segments = [
        {"surface": "足", "type": "word", "lemma": "足"},
        {"surface": "を", "type": "particle", "lemma": "を"},
        {"surface": "悪くし", "type": "word", "lemma": "悪くする"},
    ]
    issues = japanese_learner_segmentation_issues(segments)
    assert any(issue["surface"] == "足を悪くし" for issue in issues)


def test_learner_segmentation_types_lexical_compound_verbs_as_words():
    issues = japanese_learner_segmentation_issues([{
        "surface": "持って行き",
        "type": "grammar",
        "lemma": "持って行く",
        "part_of_speech": "compound verb",
        "conjugation_form": "conjunctive ren'yōkei",
    }])
    assert any(issue["surface"] == "持って行き" for issue in issues)


def test_wagahai_is_pronoun_not_name_but_real_names_and_idioms_are_allowed():
    assert japanese_segment_issue("吾輩", "name")
    assert japanese_segment_issue("苦沙弥", "name") is None
    assert japanese_segment_issue("油を売る", "idiom") is None
    assert japanese_segment_issue("想像せずにはいられなかった", "grammar") is None
    assert japanese_segment_issue("動かずにいた", "word")
    assert japanese_segment_issue("動かずにいた", "grammar") is None
    assert japanese_segment_issue("したくなり", "word")
    assert japanese_segment_issue("したくなり", "grammar") is None


def test_canonicalize_wagahai_never_retains_agent_invented_name_role():
    candidate = {
        "segments": [{
            "surface": "吾輩", "type": "word", "lemma": "吾輩",
            "surface_kana": "わがはい", "lemma_kana": "わがはい",
            "part_of_speech": "pronoun", "conjugation_form": "non-inflecting",
            "meaning_en": "I", "story_role": "name",
            "story_importance_en": "the narrator",
        }],
        "grammar_overlays": [],
    }
    result = JapaneseChapterHarness.canonicalize_annotation_candidate(
        "吾輩", candidate,
    )
    assert result["segments"][0]["type"] == "word"
    assert result["segments"][0]["story_role"] == "none"
    assert result["segments"][0]["story_importance_en"] == ""


class OutlineRunner:
    async def call(self, job, *args, **kwargs):
        if job in {"outline/review", "outline/verification"}:
            return {"verdict": "pass", "issues": []}
        return {
            "chapter_title": "一",
            "length_reason_en": "The chosen source coverage forms a coherent short scene for N5.",
            "scenes": [{
                "id": "scene_01", "title": "猫", "source_start_quote": "同じ境界",
                "required_events": ["猫がいる", "猫が家に来る"], "target_chars": 12,
            }],
        }


@pytest.mark.asyncio
async def test_outline_accepts_repeated_boundary_and_overrides_target(tmp_path):
    source = tmp_path / "chapter_01.txt"
    source.write_text("同じ境界。途中。同じ境界。", encoding="utf-8")
    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(level="n5", target_chars=None, review_effort="medium", refresh=False)
    harness.source_path, harness.source = source, source.read_text(encoding="utf-8")
    harness.run_dir, harness.runner = tmp_path / "run", OutlineRunner()
    harness.run_dir.mkdir()
    outline = await harness.outline()
    assert outline["scenes"][0]["source_start"] == 0
    assert outline["scenes"][0]["target_chars"] == 0


def test_outline_keeps_source_chunks_but_allocates_output_only_to_selected_spans(tmp_path):
    class ThreeSceneHarness(JapaneseChapterHarness):
        @property
        def scene_count(self):
            return 3

    source = tmp_path / "chapter_01.txt"
    source.write_text("第一場面。第二場面。第三場面。", encoding="utf-8")
    harness = object.__new__(ThreeSceneHarness)
    harness.args = Namespace(level="n5", target_chars=401)
    harness.source_path, harness.source = source, source.read_text(encoding="utf-8")
    outline = harness.finalize_outline({
        "chapter_title": "一",
        "scenes": [
            {"source_start_quote": "第一場面", "required_events": ["猫が生まれる"], "target_chars": 1, "title": "一"},
            {"source_start_quote": "第二場面", "required_events": [], "target_chars": 999, "title": "二"},
            {"source_start_quote": "第三場面", "required_events": ["猫が家に住む"], "target_chars": 1, "title": "三"},
        ],
    })
    assert [scene["target_chars"] for scene in outline["scenes"]] == [200, 0, 201]
    assert [scene["id"] for scene in harness.adaptation_scenes(outline)] == [
        "scene_01", "scene_03",
    ]


def test_outline_allocates_length_in_proportion_to_retained_events(tmp_path):
    class ThreeSceneHarness(JapaneseChapterHarness):
        @property
        def scene_count(self):
            return 3

    source = tmp_path / "chapter_01.txt"
    source.write_text("第一場面。第二場面。第三場面。", encoding="utf-8")
    harness = object.__new__(ThreeSceneHarness)
    harness.args = Namespace(level="n4", target_chars=700)
    harness.source_path, harness.source = source, source.read_text(encoding="utf-8")
    outline = harness.finalize_outline({
        "chapter_title": "一",
        "scenes": [
            {"source_start_quote": "第一場面", "required_events": ["一"], "target_chars": 1, "title": "一"},
            {"source_start_quote": "第二場面", "required_events": ["二", "三"], "target_chars": 1, "title": "二"},
            {"source_start_quote": "第三場面", "required_events": ["四"], "target_chars": 1, "title": "三"},
        ],
    })
    assert [scene["target_chars"] for scene in outline["scenes"]] == [175, 350, 175]


class ChapterReviewRunner:
    def __init__(self):
        self.prompt = ""

    async def call(self, job, prompt, *args, **kwargs):
        self.prompt = prompt
        assert job == "chapter/review"
        return {
            "source_fidelity": 9, "naturalness": 9, "readability": 9,
            "omissions": [], "unsupported_additions": [], "distortions": [],
            "language_problems": [], "verdict": "pass",
            "length_reason_en": "The retained events form a coherent scene at the requested level.",
        }


@pytest.mark.asyncio
async def test_whole_chapter_review_receives_required_event_ledger():
    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(level="n5", target_chars=8, review_effort="medium", refresh=False)
    harness.source = "猫の原文。"
    harness.runner = ChapterReviewRunner()
    outline = {"scenes": [{
        "id": "scene_01", "source_start": 0, "source_end": 5,
        "required_events": ["猫が家に来る"],
    }]}
    result = await harness.review_chapter(outline, "猫が家に来る。")
    assert result["verdict"] == "pass"
    assert "猫が家に来る" in harness.runner.prompt
    assert "cross-scene continuity" in harness.runner.prompt


@pytest.mark.asyncio
async def test_scene_review_pass_with_material_finding_forces_repair():
    class ContradictoryRunner:
        async def call(self, *args, **kwargs):
            return {
                "source_fidelity": 9, "naturalness": 9, "readability": 9,
                "omissions": [], "unsupported_additions": [],
                "distortions": ["右目を両目に変えている"],
                "language_problems": [], "verdict": "pass",
                "length_reason_en": "The retained source content and stopping point are suitable.",
            }

    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(level="n4", review_effort="medium", refresh=False)
    harness.source = "右目を閉じた。"
    harness.runner = ContradictoryRunner()
    scene = {"id": "scene_01", "source_start": 0,
             "source_end": len(harness.source), "target_chars": 6}
    result = await harness.review_scene(scene, "両目を閉じた。")
    assert result["verdict"] == "revise"
    assert result["harness_decision"] == "rejected_material_findings"


@pytest.mark.asyncio
async def test_post_fresh_repair_is_single_issue_scoped_final_step(tmp_path):
    class Runner:
        def __init__(self):
            self.jobs = []

        async def call(self, job, prompt, schema, effort, **kwargs):
            self.jobs.append(job)
            if schema.name == "adaptation.schema.json":
                if job.endswith("post_fresh_repair"):
                    assert "赤十字総会で上京し" in prompt
                    assert "issues only" in prompt
                    return {"text": "総会に出るため上京した。"}
                return {"text": "赤十字総会で上京した。"}
            passed = job.endswith("post_fresh_repair_review")
            return {
                "source_fidelity": 9, "naturalness": 9, "readability": 9,
                "omissions": [], "unsupported_additions": [], "distortions": [],
                "language_problems": [] if passed else [
                    "「赤十字総会で上京し」は不自然。"
                ],
                "verdict": "pass" if passed else "revise",
                "length_reason_en": "The scene retains a coherent amount of source material for N4.",
            }

    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(
        level="n4", adapt_effort="xhigh", review_effort="medium",
        repair_effort="xhigh", final_effort="max", max_repairs=0,
        fresh_rewrite=True, refresh=False,
    )
    harness.source = "赤十字総会に出るため上京した。"
    harness.runner, harness.run_dir = Runner(), tmp_path
    scene = {"id": "scene_01", "source_start": 0,
             "source_end": len(harness.source), "target_chars": 12,
             "required_events": ["赤十字総会のため上京する"]}
    result = await harness.process_scene(scene)
    assert result["resolved"] is True
    assert result["attempts"][-1]["stage"] == "post_fresh_repair"
    assert harness.runner.jobs == [
        "scene_01/adapt", "scene_01/review", "scene_01/fresh_rewrite",
        "scene_01/fresh_rewrite_review", "scene_01/post_fresh_repair",
        "scene_01/post_fresh_repair_review",
    ]


class ChapterHealRunner:
    def __init__(self, final_verdict="pass"):
        self.calls = []
        self.final_verdict = final_verdict

    async def call(self, job, prompt, schema, effort, **kwargs):
        self.calls.append((job, prompt, schema.name, effort))
        if schema.name == "adaptation.schema.json":
            return {"text": "猫が家に来て魚を食べた。"}
        return {
            "source_fidelity": 9, "naturalness": 9, "readability": 9,
            "omissions": [] if self.final_verdict == "pass" else ["犬を省略"],
            "unsupported_additions": [], "distortions": [],
            "language_problems": [], "verdict": self.final_verdict,
            "length_reason_en": "The requested fixed size was considered against source coverage.",
        }


def chapter_heal_harness(runner, max_repairs=2):
    harness = object.__new__(JapaneseChapterHarness)
    harness.args = Namespace(
        level="n5", target_chars=10, review_effort="medium",
        repair_effort="high", refresh=False, max_repairs=max_repairs,
    )
    harness.source = "原文では猫が家に来て魚を食べた。"
    harness.runner = runner
    return harness


@pytest.mark.asyncio
async def test_chapter_heal_is_issue_scoped_resumable_and_independently_reviewed():
    runner = ChapterHealRunner()
    harness = chapter_heal_harness(runner)
    outline = {"scenes": [{
        "id": "scene_01", "source_start": 0, "source_end": len(harness.source),
        "required_events": ["猫が家に来る", "魚を食べる"],
    }]}
    initial = {
        "source_fidelity": 7, "naturalness": 9, "readability": 9,
        "omissions": ["魚を食べる出来事"], "unsupported_additions": [],
        "distortions": [], "language_problems": [], "verdict": "revise",
    }
    chapter, review, attempts = await harness.heal_chapter_review(
        outline, "猫が家に来た。\n", initial
    )
    assert review["verdict"] == "pass"
    assert chapter == "猫が家に来て魚を食べた。\n"
    assert [call[0] for call in runner.calls] == [
        "chapter/repair_01", "chapter/repair_01_review",
        "chapter/repair_01_verification",
    ]
    repair_prompt = runner.calls[0][1]
    assert "魚を食べる出来事" in repair_prompt
    assert "原文では猫が家に来て魚を食べた" in repair_prompt
    assert "preserve all other wording" in repair_prompt
    assert "explicit user-requested 7-12 Japanese character range" in repair_prompt
    assert attempts[0]["repair_job"] == "chapter/repair_01"


@pytest.mark.asyncio
async def test_chapter_heal_is_bounded_and_keeps_last_reviewed_candidate():
    runner = ChapterHealRunner(final_verdict="revise")
    harness = chapter_heal_harness(runner, max_repairs=2)
    outline = {"scenes": [{
        "id": "scene_01", "source_start": 0, "source_end": len(harness.source),
        "required_events": ["猫が家に来る"],
    }]}
    initial = {
        "source_fidelity": 7, "naturalness": 9, "readability": 9,
        "omissions": ["犬を省略"], "unsupported_additions": [],
        "distortions": [], "language_problems": [], "verdict": "revise",
    }
    chapter, review, attempts = await harness.heal_chapter_review(
        outline, "猫が家に来た。\n", initial
    )
    assert review["verdict"] == "revise"
    assert chapter.endswith("\n")
    assert len(attempts) == 2
    assert [call[0] for call in runner.calls] == [
        "chapter/repair_01", "chapter/repair_01_review",
        "chapter/repair_02", "chapter/repair_02_review",
    ]


@pytest.mark.asyncio
async def test_annotation_resume_repairs_a_newly_rejected_exact_chapter(tmp_path):
    class ResumeHarness(JapaneseChapterHarness):
        async def review_chapter(self, outline, chapter, *, stage="review"):
            assert stage == "resume_current_review"
            return {
                "verdict": "revise", "source_fidelity": 9,
                "naturalness": 7, "readability": 9,
                "omissions": [], "unsupported_additions": [],
                "distortions": [],
                "language_problems": ["重複した導入"],
            }

        async def heal_chapter_review(self, outline, chapter, review):
            assert review["language_problems"] == ["重複した導入"]
            return "猫は人間を観察した。\n", {
                "verdict": "pass", "source_fidelity": 9,
                "naturalness": 9, "readability": 9,
                "omissions": [], "unsupported_additions": [],
                "distortions": [], "language_problems": [],
            }, [{"attempt": 1}]

        async def build_reader(self, outline, chapter):
            self.built_chapter = chapter
            (self.run_dir / "reader.json").write_text(json.dumps({
                "text": chapter, "segments": [], "grammar_overlays": [],
            }))

        def write_manifest(self, status, **kwargs):
            self.manifest_statuses.append(status)

    harness = object.__new__(ResumeHarness)
    harness.args = parser().parse_args([
        "run", "--source", "chapter_01.txt", "--level", "n5",
        "--resume-annotations",
    ])
    harness.run_dir = tmp_path
    harness.manifest_statuses = []
    harness.source = "猫は人間を観察した。"
    chapter = "猫は人間といる。毎日人間を見る。人間を観察する。\n"
    (tmp_path / "chapter.txt").write_text(chapter)
    (tmp_path / "outline.json").write_text(json.dumps({
        "chapter_title": "猫", "scenes": [{
            "id": "scene_01", "source_start": 0,
            "source_end": len(harness.source),
            "required_events": ["猫が人間を観察する"],
        }],
    }))
    (tmp_path / "scenes").mkdir()
    (tmp_path / "scenes" / "scene_01.json").write_text(json.dumps({
        "scene": {"id": "scene_01"},
        "review": {"verdict": "pass"}, "attempts": [],
    }))

    report = await harness.resume_annotations()

    assert harness.built_chapter == "猫は人間を観察した。\n"
    assert (tmp_path / "chapter.txt").read_text() == harness.built_chapter
    assert (tmp_path / "chapter-before-resume-review-repair.txt").read_text() == chapter
    assert report["chapter_review_repair_attempts"] == [{"attempt": 1}]
    assert report["status"] == "complete"
    assert harness.manifest_statuses == ["running", "complete"]


@pytest.mark.asyncio
async def test_level_preflight_carries_rejected_candidate_and_review_forward(tmp_path):
    class CarryHarness(JapaneseChapterHarness):
        async def ensure_story_vocabulary_plan(self, chapter, **kwargs):
            return {"terms": []}

        async def repair_level_preflight(
            self, outline, chapter, diagnostics, attempt, source_review=None,
        ):
            self.repair_inputs.append({
                "chapter": chapter,
                "diagnostics": diagnostics,
                "source_review": source_review,
            })
            if attempt == 1:
                return "猫は胸が痛い。\n"
            return "猫は家にいます。\n"

        async def review_chapter(self, outline, candidate, stage):
            if "胸" in candidate:
                return {
                    "verdict": "revise",
                    "language_problems": ["胸が痛い changes the required event"],
                    "omissions": [], "unsupported_additions": [],
                    "distortions": ["event changed"],
                }
            return {
                "verdict": "pass", "language_problems": [],
                "omissions": [], "unsupported_additions": [],
                "distortions": [],
            }

    harness = object.__new__(CarryHarness)
    harness.args = Namespace(level="n5", max_repairs=2)
    harness.run_dir = tmp_path
    harness.repair_inputs = []
    outline = {"scenes": [{
        "id": "scene_01", "required_events": ["猫が家にいる"],
    }]}

    chapter, diagnostics, attempts = await harness.heal_level_preflight(
        outline, "胸が痛い。\n",
    )

    assert chapter == "猫は家にいます。\n"
    assert diagnostics["passes"] is True
    assert harness.repair_inputs[1]["chapter"] == "猫は胸が痛い。\n"
    assert harness.repair_inputs[1]["source_review"]["verdict"] == "revise"
    assert "胸" in harness.repair_inputs[1]["diagnostics"]["sample"]
    assert attempts[0]["source_accepted"] is False
    assert attempts[1]["source_accepted"] is True


def book_harness_args(tmp_path, *, skip_annotations=True):
    return Namespace(
        runs_dir=str(tmp_path / "runs"), book_run_id="book",
        concurrency=1, chapter_concurrency=1, chapter_retries=0,
        target_chars=None, skip_annotations=skip_annotations,
    )


def write_completed_book_artifact(tmp_path, *, reader=True, source_sha=None):
    source = tmp_path / "chapter_01.txt"
    source.write_text("猫の原文。", encoding="utf-8")
    run = tmp_path / "runs" / "book" / "chapter_01-n5"
    run.mkdir(parents=True)
    chapter = "猫が来た。\n"
    (run / "chapter.txt").write_text(chapter, encoding="utf-8")
    count = japanese_char_count(chapter)
    (run / "manifest.json").write_text(json.dumps({
        "status": "complete", "source": str(source.resolve()),
        "source_sha256": source_sha or digest(source.read_text()),
        "level": "n5", "target_chars": 350,
    }), encoding="utf-8")
    (run / "report.json").write_text(json.dumps({
        "status": "complete", "japanese_chars": count, "chapter_cjk": count,
    }), encoding="utf-8")
    if reader:
        (run / "reader.json").write_text("{}", encoding="utf-8")
    return source


@pytest.mark.asyncio
async def test_book_run_skips_only_verified_completed_artifact(tmp_path, monkeypatch):
    source = write_completed_book_artifact(tmp_path, reader=False)
    harness = JapaneseBookHarness(book_harness_args(tmp_path))

    async def must_not_run(*args, **kwargs):
        raise AssertionError("verified completed chapter must not invoke chapter harness")

    monkeypatch.setattr(JapaneseChapterHarness, "run", must_not_run)
    result = await harness.run_one(str(source), "n5")
    assert result["status"] == "complete"
    assert result["cached"] is True
    assert result["attempts"] == 0


def test_book_reuses_explicit_size_only_with_explicit_manifest_provenance(tmp_path):
    source = write_completed_book_artifact(tmp_path)
    harness = JapaneseBookHarness(book_harness_args(tmp_path))
    run_id = "book/chapter_01-n5"
    assert harness.completed_run(source, "n5", run_id, 350) is None
    manifest_path = tmp_path / "runs" / run_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["fixed_size_requested"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert harness.completed_run(source, "n5", run_id, 350)["status"] == "complete"


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", ["provenance", "reader"])
async def test_book_run_reruns_stale_provenance_or_missing_reader(
    tmp_path, monkeypatch, stale
):
    source = write_completed_book_artifact(
        tmp_path, reader=stale != "reader",
        source_sha="0" * 64 if stale == "provenance" else None,
    )
    harness = JapaneseBookHarness(book_harness_args(
        tmp_path, skip_annotations=stale != "reader"
    ))
    calls = []

    class RerunHarness:
        def __init__(self, args, semaphore=None):
            calls.append((args, semaphore))

        async def run(self):
            return {"status": "complete", "chapter_cjk": 4, "japanese_chars": 4}

    monkeypatch.setattr(
        "pipeline.japanese_agent_harness.JapaneseChapterHarness", RerunHarness
    )
    result = await harness.run_one(str(source), "n5")
    assert result["status"] == "complete"
    assert len(calls) == 1
    assert "cached" not in result
