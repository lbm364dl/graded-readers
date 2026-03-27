from src.config import LEVELS
from src.vocab.loader import load_all_levels
from src.vocab.models import HskLevel


class VocabLookup:
    """Fast word/character to HSK level lookup with cumulative level sets."""

    def __init__(self, levels: dict[int, HskLevel] | None = None):
        self._levels = levels or load_all_levels()
        self._word_to_level: dict[str, int] = {}
        self._char_to_level: dict[str, int] = {}
        self._cumulative_words: dict[int, set[str]] = {}
        self._cumulative_chars: dict[int, set[str]] = {}
        self._build_index()

    def _build_index(self):
        # Map each word/char to its lowest level
        for lvl in LEVELS:
            hsk = self._levels[lvl]
            for w in hsk.words:
                if w.word not in self._word_to_level:
                    self._word_to_level[w.word] = lvl
            for c in hsk.characters:
                if c.character not in self._char_to_level:
                    self._char_to_level[c.character] = lvl

        # Build cumulative sets
        cum_words: set[str] = set()
        cum_chars: set[str] = set()
        for lvl in LEVELS:
            hsk = self._levels[lvl]
            cum_words = cum_words | hsk.word_set
            cum_chars = cum_chars | hsk.char_set
            self._cumulative_words[lvl] = set(cum_words)
            self._cumulative_chars[lvl] = set(cum_chars)

    def get_word_level(self, word: str) -> int | None:
        return self._word_to_level.get(word)

    def get_char_level(self, char: str) -> int | None:
        return self._char_to_level.get(char)

    def is_in_level(self, word: str, level: int) -> bool:
        return word in self._cumulative_words.get(level, set())

    def get_cumulative_words(self, level: int) -> set[str]:
        return self._cumulative_words.get(level, set())

    def get_cumulative_chars(self, level: int) -> set[str]:
        return self._cumulative_chars.get(level, set())

    def get_word_info(self, word: str) -> dict | None:
        lvl = self._word_to_level.get(word)
        if lvl is None:
            return None
        for w in self._levels[lvl].words:
            if w.word == word:
                return {
                    "word": w.word,
                    "pinyin": w.pinyin,
                    "pos": w.pos,
                    "english": w.english,
                    "level": w.level,
                }
        return None

    @property
    def all_words(self) -> set[str]:
        return set(self._word_to_level.keys())

    @property
    def all_characters(self) -> set[str]:
        return set(self._char_to_level.keys())
