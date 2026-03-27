"""Tests for the 95/5 vocabulary constraint rule.

This is the most critical test file - it validates the core business rule
that graded readers must have at most 5% above-level vocabulary.
"""
import pytest
from pathlib import Path
import re

from src.generator.constraints import check_vocabulary_constraint, ConstraintResult
from src.config import MAX_ABOVE_LEVEL_RATIO


class TestConstraintChecker:
    def test_pure_hsk1_text_passes(self):
        """Text using only HSK 1 vocabulary should pass at level 1."""
        text = "我是学生。我在学校学习。今天天气很好。"
        result = check_vocabulary_constraint(text, 1)
        assert result.passes is True
        assert result.above_level_ratio <= MAX_ABOVE_LEVEL_RATIO

    def test_hsk1_text_has_high_coverage(self):
        text = "我是学生。我在学校学习。今天天气很好。我很高兴。"
        result = check_vocabulary_constraint(text, 1)
        assert result.above_level_ratio < 0.05

    def test_text_with_many_above_level_fails(self):
        """Text with lots of advanced vocabulary should fail for low levels."""
        text = "人工智能技术正在改变社会经济结构和教育方式。"
        result = check_vocabulary_constraint(text, 1)
        assert result.passes is False

    def test_higher_level_has_better_coverage(self):
        """Same text should have better coverage at higher levels."""
        text = "经济发展需要科学技术的支持。"
        r1 = check_vocabulary_constraint(text, 1)
        r3 = check_vocabulary_constraint(text, 3)
        r5 = check_vocabulary_constraint(text, 5)
        assert r1.above_level_ratio >= r3.above_level_ratio
        assert r3.above_level_ratio >= r5.above_level_ratio

    def test_empty_text_passes(self):
        result = check_vocabulary_constraint("", 1)
        assert result.passes is True
        assert result.total_tokens == 0

    def test_returns_above_level_words(self):
        text = "经济发展很重要"
        result = check_vocabulary_constraint(text, 1)
        assert len(result.above_level_words) > 0

    def test_max_ratio_is_configurable(self):
        text = "我是学生。经济很好。"
        # With 0% tolerance, any above-level word fails
        result = check_vocabulary_constraint(text, 1, max_ratio=0.0)
        if result.above_level_tokens > 0:
            assert result.passes is False
        # With 100% tolerance, anything passes
        result = check_vocabulary_constraint(text, 1, max_ratio=1.0)
        assert result.passes is True

    def test_punctuation_not_counted(self):
        """Chinese punctuation should not be counted as tokens."""
        text_no_punct = "我是学生"
        text_with_punct = "我是学生。"
        r1 = check_vocabulary_constraint(text_no_punct, 1)
        r2 = check_vocabulary_constraint(text_with_punct, 1)
        assert r1.total_tokens == r2.total_tokens

    def test_level7_has_full_coverage(self):
        """Level 7 (HSK 7-9) should have very high coverage for most text."""
        text = "科学技术的发展改变了人们的生活方式和思维方式。"
        result = check_vocabulary_constraint(text, 7)
        assert result.above_level_ratio < 0.1  # Should be very low


class TestGradedReaderCompliance:
    """Validate that all generated graded readers pass the 95/5 rule."""

    @pytest.fixture(scope="class")
    def reader_files(self):
        readers_dir = Path(__file__).parent.parent / "readers"
        return sorted(readers_dir.glob("hsk*_*.md"))

    def _extract_chinese(self, filepath: Path) -> str:
        text = filepath.read_text("utf-8")
        lines = text.split("\n")
        chinese = "\n".join(
            l for l in lines
            if not l.startswith("#") and not l.startswith("**") and l.strip() != "---"
        )
        return chinese

    def _extract_level(self, filepath: Path) -> int:
        match = re.match(r"hsk(\d+)", filepath.name)
        assert match, f"Cannot extract level from {filepath.name}"
        return int(match.group(1))

    def test_readers_exist(self, reader_files):
        """There should be at least one reader for each of levels 1-6."""
        levels_covered = set()
        for f in reader_files:
            levels_covered.add(self._extract_level(f))
        for lvl in [1, 2, 3, 4, 5, 6]:
            assert lvl in levels_covered, f"Missing reader for HSK {lvl}"

    def test_all_readers_pass_constraint(self, reader_files):
        """Every reader file must pass the 95/5 vocabulary rule."""
        failures = []
        for f in reader_files:
            chinese = self._extract_chinese(f)
            level = self._extract_level(f)
            result = check_vocabulary_constraint(chinese, level)
            if not result.passes:
                failures.append(
                    f"{f.name}: {result.above_level_ratio*100:.1f}% above-level "
                    f"(max {MAX_ABOVE_LEVEL_RATIO*100:.0f}%), "
                    f"words: {result.above_level_words[:5]}"
                )
        assert not failures, "Readers failing 95/5 rule:\n" + "\n".join(failures)

    def test_readers_have_minimum_length(self, reader_files):
        """Each reader should have a minimum number of tokens."""
        min_tokens = {1: 50, 2: 100, 3: 150, 4: 150, 5: 200, 6: 200}
        for f in reader_files:
            chinese = self._extract_chinese(f)
            level = self._extract_level(f)
            result = check_vocabulary_constraint(chinese, level)
            expected_min = min_tokens.get(level, 50)
            assert result.total_tokens >= expected_min, (
                f"{f.name} has only {result.total_tokens} tokens "
                f"(minimum {expected_min} for HSK {level})"
            )
