from dataclasses import dataclass
from pypinyin import pinyin, Style
from src.segmentation.classifier import TextClassification
from src.vocab.lookup import VocabLookup


@dataclass
class Footnote:
    index: int
    word: str
    pinyin: str
    english: str
    level: int | None
    position: int  # character offset in text


class PinyinAnnotator:
    """Generate pinyin annotations for out-of-level vocabulary."""

    def __init__(self, vocab_lookup: VocabLookup | None = None):
        self.vocab = vocab_lookup or VocabLookup()

    def get_pinyin(self, word: str) -> str:
        """Get tone-marked pinyin for a word."""
        result = pinyin(word, style=Style.TONE)
        return "".join(syllable[0] for syllable in result)

    def annotate_above_level(self, classification: TextClassification) -> list[Footnote]:
        """Generate footnotes for all above-level words in a classified text."""
        footnotes = []
        seen: set[str] = set()
        idx = 1

        for seg in classification.segments:
            if seg.is_above_target and seg.word not in seen:
                seen.add(seg.word)
                info = self.vocab.get_word_info(seg.word)
                py = info["pinyin"] if info else self.get_pinyin(seg.word)
                english = info["english"] if info else ""

                footnotes.append(Footnote(
                    index=idx,
                    word=seg.word,
                    pinyin=py,
                    english=english,
                    level=seg.level,
                    position=0,
                ))
                idx += 1

        return footnotes
