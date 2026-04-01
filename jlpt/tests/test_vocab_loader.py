"""Tests for JLPT vocabulary loading and lookup."""
import pytest
from src.vocab.loader import load_words, load_all_levels, load_cumulative_words
from src.vocab.lookup import VocabLookup
from src.config import LEVELS, LEVEL_LABELS


class TestVocabLoader:
    def test_loads_all_levels(self):
        levels = load_all_levels()
        assert set(levels.keys()) == set(LEVELS)

    def test_n5_has_words(self):
        words = load_words(1)  # N5
        assert len(words) > 50

    def test_n5_contains_core_vocabulary(self):
        words = load_words(1)
        word_set = {w.word for w in words}
        for expected in ["私", "学校", "食べる", "大きい", "友達"]:
            assert expected in word_set, f"'{expected}' missing from N5 vocabulary"

    def test_words_have_readings(self):
        words = load_words(1)
        for w in words[:10]:
            assert w.reading, f"Word '{w.word}' is missing a reading"

    def test_levels_are_assigned_correctly(self):
        words = load_words(1)
        for w in words:
            assert w.level == 1

    def test_cumulative_words_grow_with_level(self):
        n5 = load_cumulative_words(1)
        n4 = load_cumulative_words(2)
        n3 = load_cumulative_words(3)
        assert len(n5) < len(n4) < len(n3)

    def test_n5_words_in_all_cumulative_sets(self):
        n5_words = {w.word for w in load_words(1)}
        for lvl in LEVELS:
            cum = load_cumulative_words(lvl)
            for word in list(n5_words)[:5]:
                assert word in cum, f"N5 word '{word}' missing from cumulative set at level {lvl}"


class TestVocabLookup:
    def setup_method(self):
        self.lookup = VocabLookup()

    def test_n5_word_found_at_n5(self):
        assert self.lookup.get_word_level("私") == 1

    def test_n5_word_in_level_at_n5(self):
        assert self.lookup.is_in_level("私", 1) is True

    def test_n5_word_in_level_at_n4(self):
        assert self.lookup.is_in_level("私", 2) is True

    def test_unknown_word_returns_none(self):
        assert self.lookup.get_word_level("xyzxyzxyz") is None

    def test_unknown_word_not_in_level(self):
        assert self.lookup.is_in_level("xyzxyzxyz", 5) is False

    def test_higher_level_word_not_in_lower_level(self):
        # 翁 is N1 (level 5), so it should not be in_level for N5 (level 1)
        lvl = self.lookup.get_word_level("翁")
        if lvl is not None:
            assert not self.lookup.is_in_level("翁", 1)
