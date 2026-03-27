"""Tests for HSK level classification."""
import pytest
from src.segmentation.classifier import LevelClassifier, TextClassification


@pytest.fixture(scope="module")
def classifier():
    return LevelClassifier()


class TestLevelClassifier:
    def test_classify_simple_text(self, classifier):
        result = classifier.classify_text("我是学生", 1)
        assert isinstance(result, TextClassification)
        assert result.total_tokens > 0
        assert result.target_level == 1

    def test_hsk1_text_coverage(self, classifier):
        result = classifier.classify_text("我是学生。我很好。", 1)
        assert result.coverage_ratio > 0.9

    def test_above_level_detection(self, classifier):
        result = classifier.classify_text("经济发展很快", 1)
        assert result.above_level_count > 0
        assert "经济" in result.above_level_words

    def test_level_distribution(self, classifier):
        result = classifier.classify_text("我学习经济", 3)
        dist = result.level_distribution
        assert isinstance(dist, dict)
        assert sum(dist.values()) == result.total_tokens

    def test_passes_threshold_property(self, classifier):
        # Pure HSK 1 text should pass
        result = classifier.classify_text("我是学生", 1)
        assert result.passes_threshold is True

    def test_empty_text(self, classifier):
        result = classifier.classify_text("", 1)
        assert result.total_tokens == 0
        assert result.passes_threshold is True
