from src.config import LEVELS
from src.vocab.loader import load_all_levels


class VocabLookup:
    """Fast JLPT vocabulary lookup with level assignment."""

    def __init__(self):
        self._all = load_all_levels()
        # word → lowest level at which it appears
        self._word_level: dict[str, int] = {}
        for lvl in LEVELS:
            for word in self._all[lvl].word_set:
                if word not in self._word_level:
                    self._word_level[word] = lvl

    def get_word_level(self, word: str) -> int | None:
        """Return the JLPT level of *word* (1=N5 … 5=N1), or None if unknown."""
        return self._word_level.get(word)

    def is_in_level(self, word: str, target_level: int) -> bool:
        """Return True if *word* is known at or below *target_level*."""
        lvl = self.get_word_level(word)
        return lvl is not None and lvl <= target_level

    def get_cumulative_words(self, target_level: int) -> set[str]:
        result: set[str] = set()
        for lvl in LEVELS:
            if lvl <= target_level:
                result |= self._all[lvl].word_set
        return result
