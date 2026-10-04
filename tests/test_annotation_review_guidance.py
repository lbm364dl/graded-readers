from pathlib import Path
import json

from pipeline.annotation_review_guidance import (
    FORM_STAGE_COMPLETENESS_GUIDANCE,
    FORM_STAGE_EVIDENCE_GUIDANCE,
    SPAN_COMPONENT_MEANING_GUIDANCE,
)
from pipeline.korean_chunk_reviews import INSTRUCTIONS as KOREAN_CHUNK_REVIEW_INSTRUCTIONS


def test_shared_review_guidance_checks_stage_completeness_and_keeps_a_valid_contrast():
    assert "A label such as “honorific” or “past”" in FORM_STAGE_COMPLETENESS_GUIDANCE
    assert "does not make a prefinal stem or bound inflection complete" in FORM_STAGE_COMPLETENESS_GUIDANCE
    assert "complete citation-form intermediate followed by an observed past connective can be valid" in FORM_STAGE_COMPLETENESS_GUIDANCE
    assert "a prefinal stem mislabeled as a complete past/honorific stage is not" in FORM_STAGE_COMPLETENESS_GUIDANCE
    assert "language-specific string heuristic" in FORM_STAGE_COMPLETENESS_GUIDANCE
    assert "a bare prefinal stem still remains incomplete even when its English gloss looks plausible" in FORM_STAGE_COMPLETENESS_GUIDANCE
    assert FORM_STAGE_COMPLETENESS_GUIDANCE in FORM_STAGE_EVIDENCE_GUIDANCE
    assert FORM_STAGE_COMPLETENESS_GUIDANCE in KOREAN_CHUNK_REVIEW_INSTRUCTIONS


def test_shared_review_guidance_respects_predicate_modifier_and_translation_scope():
    assert "complete valency-frame gloss such as “regards [X] as [Y]”" in FORM_STAGE_COMPLETENESS_GUIDANCE
    assert "do not require the whole sentence's subject, object, and complement" in FORM_STAGE_COMPLETENESS_GUIDANCE
    assert "Keep a modifier's own prospective or attributive contribution distinct" in FORM_STAGE_COMPLETENESS_GUIDANCE
    assert "do not assign the whole construction's modality to the modifier alone" in FORM_STAGE_COMPLETENESS_GUIDANCE
    assert "cite the linked lesson and source timeline" in FORM_STAGE_COMPLETENESS_GUIDANCE
    assert "another natural English rendering is also possible" in FORM_STAGE_COMPLETENESS_GUIDANCE


def test_chinese_and_japanese_review_callers_include_the_shared_guidance():
    root = Path(__file__).resolve().parents[1]
    for relative in ("pipeline/agent_harness.py", "pipeline/japanese_agent_harness.py"):
        source = (root / relative).read_text(encoding="utf-8")
        assert "{FORM_STAGE_EVIDENCE_GUIDANCE}" in source


def test_modifier_gloss_variants_need_concrete_extraneous_meaning_before_rejection():
    assert "who knows”" in FORM_STAGE_COMPLETENESS_GUIDANCE
    assert "having moved” can describe a completed modifier" in FORM_STAGE_COMPLETENESS_GUIDANCE
    assert "identify the actual extra words or semantic contribution" in FORM_STAGE_COMPLETENESS_GUIDANCE
    assert "a relative pronoun alone does not import" in FORM_STAGE_COMPLETENESS_GUIDANCE
    assert "after moving to the capital” imports" in FORM_STAGE_COMPLETENESS_GUIDANCE
    assert "do not require identical wording across these fields" in FORM_STAGE_COMPLETENESS_GUIDANCE


def test_span_overlay_and_component_form_meanings_have_distinct_scope():
    assert "Keep component and span meanings separate" in SPAN_COMPONENT_MEANING_GUIDANCE
    assert "need not read as an independent English assertion" in SPAN_COMPONENT_MEANING_GUIDANCE
    assert "Do not copy the overlay's full translation into every component" in SPAN_COMPONENT_MEANING_GUIDANCE
    assert "Conversely, do not infer a component meaning or identity solely from evidence for the whole construction" in SPAN_COMPONENT_MEANING_GUIDANCE
    assert "leave it uncertain or route it for research rather than inventing a lesson" in SPAN_COMPONENT_MEANING_GUIDANCE
    assert "bare stem that still requires an ending" in SPAN_COMPONENT_MEANING_GUIDANCE
    assert SPAN_COMPONENT_MEANING_GUIDANCE in FORM_STAGE_EVIDENCE_GUIDANCE


def test_existing_japanese_chain_and_chinese_overlay_keep_local_and_whole_meanings_separate():
    root = Path(__file__).resolve().parents[1]
    japanese = json.loads((root / "content/japanese/wagahai/n5.annotations.json").read_text(encoding="utf-8"))
    chapter = japanese["chapters"][0]
    text = chapter["text"]
    overlay = next(row for row in chapter["grammar_overlays"]
                   if row["surface"] == "住むことにしました")
    assert text[overlay["start"]:overlay["end"]] == overlay["surface"]
    positions = []
    cursor = 0
    for segment in chapter["segments"]:
        positions.append((cursor, cursor + len(segment["surface"]), segment))
        cursor += len(segment["surface"])
    components = [segment for start, end, segment in positions
                  if start < overlay["end"] and end > overlay["start"]]
    assert [(row["surface"], row["meaning_en"]) for row in components] == [
        ("住む", "live; reside"), ("ことにしました", "decided to")]
    assert overlay["meaning_en"] == "decided to live"

    chinese = json.loads((root / "content/chinese/sanguoyanyi/hsk2.annotations.json").read_text(encoding="utf-8"))
    chapter = chinese["chapters"][0]
    overlay = next(row for row in chapter["grammar_overlays"]
                   if row["text"] == "张角给百姓治病")
    assert chapter["text"][overlay["start"]:overlay["end"]] == overlay["text"]
    cursor = 0
    local_meanings = []
    for row in chapter["segments"]:
        start, end = cursor, cursor + len(row["text"])
        cursor = end
        if start < overlay["end"] and end > overlay["start"]:
            local_meanings.append(row["meaning_en"])
    assert local_meanings == ["Zhang Jiao", "for", "common people", "treat illnesses"]
    assert overlay["meaning_en"] == "Zhang Jiao treats illnesses for the common people"
