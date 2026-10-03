from pathlib import Path

from pipeline.annotation_review_guidance import (
    FORM_STAGE_COMPLETENESS_GUIDANCE,
    FORM_STAGE_EVIDENCE_GUIDANCE,
)
from pipeline.korean_chunk_reviews import INSTRUCTIONS as KOREAN_CHUNK_REVIEW_INSTRUCTIONS


def test_shared_review_guidance_checks_stage_completeness_and_keeps_a_valid_contrast():
    assert "A label such as “honorific” or “past”" in FORM_STAGE_COMPLETENESS_GUIDANCE
    assert "does not make a prefinal stem or bound inflection complete" in FORM_STAGE_COMPLETENESS_GUIDANCE
    assert "complete citation-form intermediate followed by an observed past connective can be valid" in FORM_STAGE_COMPLETENESS_GUIDANCE
    assert "a prefinal stem mislabeled as a complete past/honorific stage is not" in FORM_STAGE_COMPLETENESS_GUIDANCE
    assert "language-specific string heuristic" in FORM_STAGE_COMPLETENESS_GUIDANCE
    assert FORM_STAGE_COMPLETENESS_GUIDANCE in FORM_STAGE_EVIDENCE_GUIDANCE
    assert FORM_STAGE_COMPLETENESS_GUIDANCE in KOREAN_CHUNK_REVIEW_INSTRUCTIONS


def test_chinese_and_japanese_review_callers_include_the_shared_guidance():
    root = Path(__file__).resolve().parents[1]
    for relative in ("pipeline/agent_harness.py", "pipeline/japanese_agent_harness.py"):
        source = (root / relative).read_text(encoding="utf-8")
        assert "{FORM_STAGE_EVIDENCE_GUIDANCE}" in source
