"""Tests for Chinese word segmentation."""
import pytest
from src.segmentation.segmenter import ChineseSegmenter


@pytest.fixture(scope="module")
def seg():
    return ChineseSegmenter()


class TestChineseSegmenter:
    def test_basic_segmentation(self, seg):
        result = seg.segment("我是学生")
        assert "我" in result
        assert "是" in result
        assert "学生" in result

    def test_filters_punctuation(self, seg):
        result = seg.segment("你好！我是学生。")
        assert "！" not in result
        assert "。" not in result
        assert "你好" not in result or "你" in result  # 你好 may or may not segment

    def test_hsk_words_preserved(self, seg):
        """Multi-character HSK words should be kept as units."""
        result = seg.segment("我在学校学习中文")
        assert "学校" in result
        assert "学习" in result
        assert "中文" in result

    def test_ambiguous_boundary_split(self, seg):
        """Words like 今天天气 should split into 今天 + 天气."""
        result = seg.segment("今天天气很好")
        assert "今天" in result
        assert "天气" in result

    def test_post_processing_splits_non_hsk(self, seg):
        """Non-HSK compound tokens should be split into HSK sub-words."""
        result = seg.segment("太好了")
        # 太, 好, 了 are all HSK words
        assert "太" in result
        assert "好" in result

    def test_numbers_handled(self, seg):
        result = seg.segment("我有三个苹果")
        # Numbers should be handled gracefully
        assert len(result) > 0

    def test_empty_string(self, seg):
        assert seg.segment("") == []

    def test_punctuation_only(self, seg):
        assert seg.segment("，。！？") == []
