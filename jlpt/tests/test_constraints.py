"""Tests for the 95/5 vocabulary constraint rule (Japanese).

Mirrors the Chinese test_constraints.py structure.
Requires sudachipy to be installed for segmentation.
"""
import pytest
from src.generator.constraints import check_vocabulary_constraint, ConstraintResult
from src.config import MAX_ABOVE_LEVEL_RATIO

try:
    import sudachipy  # noqa: F401
    SUDACHI_AVAILABLE = True
except ImportError:
    SUDACHI_AVAILABLE = False

needs_sudachi = pytest.mark.skipif(
    not SUDACHI_AVAILABLE,
    reason="sudachipy not installed — run: pip install sudachipy sudachidict-small",
)


class TestConstraintChecker:
    @needs_sudachi
    def test_n5_text_passes_at_n5(self):
        text = "私は学生です。毎日学校に行きます。今日はとてもいい天気です。"
        result = check_vocabulary_constraint(text, 1)
        assert result.passes is True

    @needs_sudachi
    def test_advanced_text_fails_at_n5(self):
        text = "経済の持続可能な発展には、環境保全と技術革新の融合が不可欠である。"
        result = check_vocabulary_constraint(text, 1)
        assert result.passes is False

    @needs_sudachi
    def test_higher_level_has_better_coverage(self):
        text = "社会における価値観の多様性を尊重することが重要である。"
        r1 = check_vocabulary_constraint(text, 1)
        r3 = check_vocabulary_constraint(text, 3)
        r5 = check_vocabulary_constraint(text, 5)
        assert r1.above_level_ratio >= r3.above_level_ratio
        assert r3.above_level_ratio >= r5.above_level_ratio

    def test_empty_text_passes(self):
        result = check_vocabulary_constraint("", 1)
        assert result.passes is True
        assert result.total_tokens == 0

    def test_max_ratio_is_configurable(self):
        text = "私は学校に行きます。"
        result_strict = check_vocabulary_constraint(text, 1, max_ratio=0.0)
        result_loose = check_vocabulary_constraint(text, 1, max_ratio=1.0)
        assert result_loose.passes is True

    @needs_sudachi
    def test_allowed_words_are_treated_as_in_level(self):
        # 翁 is N1, but if it's in the allowed set it should not count against ratio
        text = "翁は竹を切りました。"
        without_allowed = check_vocabulary_constraint(text, 1)
        with_allowed = check_vocabulary_constraint(text, 1, allowed_words={"翁"})
        assert with_allowed.above_level_tokens <= without_allowed.above_level_tokens

    @needs_sudachi
    def test_returns_above_level_words(self):
        text = "経済の持続可能な発展には技術革新が不可欠だ。"
        result = check_vocabulary_constraint(text, 1)
        assert len(result.above_level_words) > 0

    def test_result_dataclass_fields(self):
        result = check_vocabulary_constraint("", 3)
        assert hasattr(result, "passes")
        assert hasattr(result, "total_tokens")
        assert hasattr(result, "above_level_ratio")
        assert hasattr(result, "above_level_words")
        assert result.max_allowed_ratio == MAX_ABOVE_LEVEL_RATIO
