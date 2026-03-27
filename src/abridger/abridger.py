"""Book abridger orchestrator.

Pipeline: parse book -> analyze each chapter -> generate simplification report
-> (future: LLM-based rewriting) -> validate -> output.

Currently provides analysis and footnote annotation. Full automatic
simplification (rewriting sentences with simpler vocabulary) is designed
to be done with an LLM - the infrastructure here handles everything
around it: parsing, analysis, validation, and output formatting.
"""

from dataclasses import dataclass
from pathlib import Path
from src.abridger.parser import parse_book, BookContent, Chapter
from src.abridger.simplifier import TextSimplifier, SimplificationResult
from src.generator.constraints import check_vocabulary_constraint
from src.pinyin.footnotes import format_footnotes, insert_footnote_markers
from src.vocab.lookup import VocabLookup
from src.segmentation.segmenter import ChineseSegmenter


@dataclass
class AbridgedChapter:
    original: Chapter
    analysis: SimplificationResult
    annotated_text: str
    footnotes_text: str


@dataclass
class AbridgedBook:
    title: str
    target_level: int
    chapters: list[AbridgedChapter]
    overall_passes: bool

    @property
    def full_text(self) -> str:
        parts = [f"# {self.title}", f"**Target Level: HSK {self.target_level}**\n"]
        for ch in self.chapters:
            parts.append(f"\n## {ch.original.title}\n")
            parts.append(ch.annotated_text)
            parts.append(ch.footnotes_text)
        return "\n".join(parts)


class BookAbridger:
    """Orchestrates the book abridging pipeline."""

    def __init__(self, vocab_lookup: VocabLookup | None = None,
                 segmenter: ChineseSegmenter | None = None):
        self.vocab = vocab_lookup or VocabLookup()
        self.segmenter = segmenter or ChineseSegmenter()

    def abridge(self, file_path: Path, target_level: int) -> AbridgedBook:
        """Parse a book and analyze/annotate it for a target HSK level."""
        book = parse_book(file_path)
        simplifier = TextSimplifier(
            target_level, self.vocab, self.segmenter
        )

        chapters = []
        all_pass = True

        for chapter in book.chapters:
            analysis = simplifier.analyze(chapter.text)
            annotated = insert_footnote_markers(chapter.text, analysis.footnotes)
            fn_text = format_footnotes(analysis.footnotes)

            if not analysis.passes_threshold:
                all_pass = False

            chapters.append(AbridgedChapter(
                original=chapter,
                analysis=analysis,
                annotated_text=annotated,
                footnotes_text=fn_text,
            ))

        return AbridgedBook(
            title=book.title,
            target_level=target_level,
            chapters=chapters,
            overall_passes=all_pass,
        )

    def analyze_book(self, file_path: Path, target_level: int) -> str:
        """Generate a human-readable analysis report for a book."""
        result = self.abridge(file_path, target_level)
        lines = [
            f"=== Book Analysis: {result.title} ===",
            f"Target Level: HSK {target_level}",
            f"Chapters: {len(result.chapters)}",
            f"Overall passes 95/5 rule: {'YES' if result.overall_passes else 'NO'}",
            "",
        ]
        for ch in result.chapters:
            a = ch.analysis
            status = "PASS" if a.passes_threshold else "FAIL"
            lines.append(
                f"  {ch.original.title}: {a.above_level_ratio*100:.1f}% above-level [{status}]"
            )
            for suggestion in a.suggestions:
                lines.append(f"    {suggestion}")

        return "\n".join(lines)
