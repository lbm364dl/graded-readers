"""Tests for analytics module."""
import pytest
from src.analytics.frequency import word_frequency, level_frequency
from src.analytics.coverage import coverage_statistics
from src.analytics.report import print_coverage_report


class TestWordFrequency:
    def test_basic_frequency(self):
        freq = word_frequency("我是学生我是老师")
        assert freq["我"] == 2
        assert freq["是"] == 2

    def test_empty_text(self):
        freq = word_frequency("")
        assert freq == {}


class TestLevelFrequency:
    def test_level_counts(self):
        freq = level_frequency("我学习经济")
        assert 1 in freq  # 我, 学习 are HSK 1
        assert isinstance(freq, dict)


class TestCoverageStatistics:
    def test_basic_coverage(self):
        stats = coverage_statistics("我是学生", 1)
        assert stats.total_tokens > 0
        assert stats.coverage_percent > 90.0
        assert stats.target_level == 1

    def test_passes_property(self):
        stats = coverage_statistics("我是学生", 1)
        assert stats.passes is True

    def test_above_level_words_listed(self):
        stats = coverage_statistics("经济发展很重要", 1)
        words = [w for w, _ in stats.above_level_words]
        assert len(words) > 0


class TestReport:
    def test_report_generation(self):
        stats = coverage_statistics("我是学生", 1)
        report = print_coverage_report(stats)
        assert "Coverage Report" in report
        assert "HSK 1" in report
