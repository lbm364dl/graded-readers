"""Tests for vocabulary data loading."""
import pytest
from src.vocab.loader import load_words, load_characters, load_all_levels, load_cumulative_words
from src.vocab.models import Word, Character, HskLevel
from src.config import LEVELS


class TestLoadWords:
    def test_load_hsk1_words(self):
        words = load_words(1)
        assert len(words) > 400  # HSK 1 has ~500 words
        assert all(isinstance(w, Word) for w in words)
        assert all(w.level == 1 for w in words)

    def test_load_hsk1_contains_basic_words(self):
        words = load_words(1)
        word_set = {w.word for w in words}
        # These must be in HSK 1
        for w in ["我", "你", "他", "是", "的", "学习", "中文", "学校"]:
            assert w in word_set, f"'{w}' should be in HSK 1"

    def test_load_all_levels(self):
        levels = load_all_levels()
        assert len(levels) == len(LEVELS)
        for lvl in LEVELS:
            assert lvl in levels
            assert isinstance(levels[lvl], HskLevel)
            assert len(levels[lvl].words) > 0
            assert len(levels[lvl].characters) > 0

    def test_load_characters(self):
        chars = load_characters(1)
        assert len(chars) == 300  # HSK 1 has exactly 300 characters
        assert all(isinstance(c, Character) for c in chars)

    def test_cumulative_words_increase(self):
        """Cumulative word sets should grow with each level."""
        prev_size = 0
        for lvl in LEVELS:
            cum = load_cumulative_words(lvl)
            assert len(cum) > prev_size
            prev_size = len(cum)


class TestLoadCharacters:
    def test_each_level_has_characters(self):
        for lvl in LEVELS:
            chars = load_characters(lvl)
            assert len(chars) > 0

    def test_total_characters(self):
        total = sum(len(load_characters(lvl)) for lvl in LEVELS)
        assert total == 3000  # HSK 3.0 has exactly 3000 characters
