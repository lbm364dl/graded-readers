"""Tests for vocabulary lookup."""
import pytest
from src.vocab.lookup import VocabLookup


@pytest.fixture(scope="module")
def lookup():
    return VocabLookup()


class TestVocabLookup:
    def test_basic_word_lookup(self, lookup):
        assert lookup.get_word_level("学习") == 1
        assert lookup.get_word_level("经济") == 3
        assert lookup.get_word_level("nonexistent") is None

    def test_character_lookup(self, lookup):
        assert lookup.get_char_level("爱") == 1
        assert lookup.get_char_level("z") is None

    def test_is_in_level(self, lookup):
        assert lookup.is_in_level("学习", 1) is True
        assert lookup.is_in_level("学习", 3) is True  # cumulative
        assert lookup.is_in_level("经济", 1) is False
        assert lookup.is_in_level("经济", 3) is True

    def test_cumulative_words_grow(self, lookup):
        prev_size = 0
        for lvl in [1, 2, 3, 4, 5, 6, 7]:
            words = lookup.get_cumulative_words(lvl)
            assert len(words) > prev_size
            prev_size = len(words)

    def test_cumulative_includes_lower_levels(self, lookup):
        """Level N cumulative set should include all level N-1 words."""
        for lvl in [2, 3, 4, 5, 6, 7]:
            lower = lookup.get_cumulative_words(lvl - 1)
            upper = lookup.get_cumulative_words(lvl)
            assert lower.issubset(upper), f"HSK {lvl-1} should be subset of HSK {lvl}"

    def test_word_info(self, lookup):
        info = lookup.get_word_info("学习")
        assert info is not None
        assert info["word"] == "学习"
        assert info["level"] == 1
        assert info["pinyin"] != ""

    def test_all_words_count(self, lookup):
        # HSK 3.0 has ~11,000 words total
        assert len(lookup.all_words) > 10000

    def test_all_characters_count(self, lookup):
        assert len(lookup.all_characters) == 3000
