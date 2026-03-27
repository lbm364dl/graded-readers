"""Tests for pinyin annotation."""
import pytest
from src.pinyin.annotator import PinyinAnnotator, Footnote
from src.pinyin.footnotes import format_footnotes, insert_footnote_markers
from src.segmentation.classifier import LevelClassifier


@pytest.fixture(scope="module")
def annotator():
    return PinyinAnnotator()


@pytest.fixture(scope="module")
def classifier():
    return LevelClassifier()


class TestPinyinAnnotator:
    def test_get_pinyin(self, annotator):
        py = annotator.get_pinyin("学习")
        assert "xué" in py or "xue" in py

    def test_annotate_above_level(self, annotator, classifier):
        result = classifier.classify_text("我学习经济", 1)
        footnotes = annotator.annotate_above_level(result)
        # "经济" is above HSK 1, should have a footnote
        above_words = [fn.word for fn in footnotes]
        assert "经济" in above_words

    def test_no_footnotes_for_in_level(self, annotator, classifier):
        result = classifier.classify_text("我是学生", 1)
        footnotes = annotator.annotate_above_level(result)
        # All words are HSK 1, should have no footnotes
        assert len(footnotes) == 0

    def test_footnote_has_pinyin(self, annotator, classifier):
        result = classifier.classify_text("经济发展", 1)
        footnotes = annotator.annotate_above_level(result)
        for fn in footnotes:
            assert fn.pinyin != ""


class TestFootnotes:
    def test_format_empty(self):
        assert format_footnotes([]) == ""

    def test_format_numbered(self):
        fn = Footnote(index=1, word="经济", pinyin="jīngjì",
                      english="economy", level=3, position=0)
        result = format_footnotes([fn], style="numbered")
        assert "[1]" in result
        assert "经济" in result
        assert "jīngjì" in result

    def test_insert_markers(self):
        text = "我学习经济"
        fn = Footnote(index=1, word="经济", pinyin="jīngjì",
                      english="economy", level=3, position=0)
        result = insert_footnote_markers(text, [fn])
        assert "经济[1]" in result
