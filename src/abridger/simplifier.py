"""Text simplification to target HSK level.

This module provides the core simplification logic for abridging books.
The approach:
1. Segment and classify text by HSK level
2. Identify above-level vocabulary
3. Generate footnotes for above-level words (up to 5% threshold)
4. Flag text that exceeds the threshold for manual review or LLM rewriting

Full automatic simplification (synonym substitution, sentence rewriting)
requires an LLM and will be handled by the abridger orchestrator.
"""

from dataclasses import dataclass
from src.segmentation.segmenter import ChineseSegmenter
from src.segmentation.classifier import LevelClassifier, TextClassification
from src.vocab.lookup import VocabLookup
from src.pinyin.annotator import PinyinAnnotator, Footnote
from src.config import MAX_ABOVE_LEVEL_RATIO


@dataclass
class SimplificationResult:
    original_text: str
    target_level: int
    classification: TextClassification
    footnotes: list[Footnote]
    passes_threshold: bool
    above_level_ratio: float
    suggestions: list[str]  # human-readable suggestions for manual fixes


class TextSimplifier:
    """Analyze text difficulty and prepare it for a target HSK level."""

    def __init__(self, target_level: int,
                 vocab_lookup: VocabLookup | None = None,
                 segmenter: ChineseSegmenter | None = None):
        self.target_level = target_level
        self.vocab = vocab_lookup or VocabLookup()
        self.segmenter = segmenter or ChineseSegmenter()
        self.classifier = LevelClassifier(self.vocab, self.segmenter)
        self.annotator = PinyinAnnotator(self.vocab)

    def analyze(self, text: str) -> SimplificationResult:
        """Analyze text and produce simplification guidance."""
        classification = self.classifier.classify_text(text, self.target_level)
        footnotes = self.annotator.annotate_above_level(classification)

        suggestions = []
        if not classification.passes_threshold:
            ratio_pct = classification.above_level_ratio * 100
            suggestions.append(
                f"Text has {ratio_pct:.1f}% above-level vocabulary "
                f"(max allowed: {MAX_ABOVE_LEVEL_RATIO * 100:.0f}%). "
                f"Needs simplification."
            )
            # Group above-level words by frequency
            word_counts: dict[str, int] = {}
            for seg in classification.segments:
                if seg.is_above_target:
                    word_counts[seg.word] = word_counts.get(seg.word, 0) + 1

            frequent_above = sorted(word_counts.items(), key=lambda x: -x[1])[:10]
            if frequent_above:
                suggestions.append("Most frequent above-level words to replace:")
                for word, count in frequent_above:
                    lvl = self.vocab.get_word_level(word)
                    lvl_str = f"HSK {lvl}" if lvl else "Non-HSK"
                    suggestions.append(f"  {word} [{lvl_str}] x{count}")

        return SimplificationResult(
            original_text=text,
            target_level=self.target_level,
            classification=classification,
            footnotes=footnotes,
            passes_threshold=classification.passes_threshold,
            above_level_ratio=classification.above_level_ratio,
            suggestions=suggestions,
        )
