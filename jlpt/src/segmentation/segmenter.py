"""Japanese word segmenter backed by SudachiPy.

Install requirements:
    pip install sudachipy sudachidict-small

SudachiPy returns morphemes with dictionary forms (基本形), which is what we
use for JLPT vocabulary lookup.  Particles (助詞) and auxiliary verbs (助動詞)
are filtered out before vocabulary counting — they are grammatical glue that
never appears in JLPT word lists and should not penalise the 95/5 score.
"""

from src.config import ALL_PUNCTUATION, SKIP_POS

try:
    from sudachipy import Dictionary, SplitMode
    _SUDACHI_AVAILABLE = True
except ImportError:
    _SUDACHI_AVAILABLE = False


class JapaneseSegmenter:
    """Segment Japanese text and return dictionary-form content words."""

    def __init__(self):
        if _SUDACHI_AVAILABLE:
            self._tokenizer = Dictionary(dict="small").create()
            self._split_mode = SplitMode.C  # longest-unit segmentation
        else:
            import warnings
            warnings.warn(
                "sudachipy not found. Install it with: pip install sudachipy sudachidict-small\n"
                "Falling back to naive whitespace/character segmentation (inaccurate).",
                ImportWarning,
                stacklevel=2,
            )
            self._tokenizer = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def segment(self, text: str) -> list[str]:
        """Return a list of dictionary-form content words, filtering out
        particles, auxiliaries, punctuation, and whitespace."""
        if self._tokenizer is not None:
            return self._segment_sudachi(text)
        return self._segment_fallback(text)

    def segment_with_reading(self, text: str) -> list[tuple[str, str]]:
        """Return (dictionary_form, katakana_reading) pairs for content words."""
        if self._tokenizer is None:
            return [(w, "") for w in self._segment_fallback(text)]
        results = []
        for m in self._tokenizer.tokenize(text, self._split_mode):
            pos0 = m.part_of_speech()[0]
            if pos0 in SKIP_POS:
                continue
            surface = m.surface()
            if self._is_punctuation(surface):
                continue
            dict_form = m.dictionary_form() or surface
            reading = m.reading_form()  # katakana
            if dict_form.strip():
                results.append((dict_form, reading))
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _segment_sudachi(self, text: str) -> list[str]:
        words = []
        for m in self._tokenizer.tokenize(text, self._split_mode):
            pos0 = m.part_of_speech()[0]
            if pos0 in SKIP_POS:
                continue
            surface = m.surface()
            if self._is_punctuation(surface):
                continue
            dict_form = m.dictionary_form() or surface
            if dict_form.strip():
                words.append(dict_form)
        return words

    def _segment_fallback(self, text: str) -> list[str]:
        """Naive fallback: split on whitespace, strip punctuation."""
        import re
        tokens = re.split(r"[\s\u3000]+", text)
        result = []
        for tok in tokens:
            tok = tok.strip()
            if not tok or self._is_punctuation(tok):
                continue
            result.append(tok)
        return result

    @staticmethod
    def _is_punctuation(token: str) -> bool:
        return all(c in ALL_PUNCTUATION or c.isdigit() or c.isascii()
                   for c in token)
