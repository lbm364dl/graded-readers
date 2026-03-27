from dataclasses import dataclass, field
from src.generator.constraints import check_vocabulary_constraint, ConstraintResult
from src.pinyin.annotator import PinyinAnnotator, Footnote
from src.pinyin.footnotes import format_footnotes, insert_footnote_markers
from src.segmentation.classifier import LevelClassifier
from src.vocab.lookup import VocabLookup
from src.segmentation.segmenter import ChineseSegmenter


@dataclass
class GradedText:
    title: str
    level: int
    text: str
    annotated_text: str  # text with footnote markers
    footnotes: list[Footnote]
    footnotes_text: str
    validation: ConstraintResult

    @property
    def full_text(self) -> str:
        return f"# {self.title}\n**Level: HSK {self.level}**\n\n{self.annotated_text}\n{self.footnotes_text}"


class GradedReaderGenerator:
    """Validates and annotates graded reading texts."""

    def __init__(self, vocab_lookup: VocabLookup | None = None,
                 segmenter: ChineseSegmenter | None = None):
        self.vocab = vocab_lookup or VocabLookup()
        self.segmenter = segmenter or ChineseSegmenter()
        self.classifier = LevelClassifier(self.vocab, self.segmenter)
        self.annotator = PinyinAnnotator(self.vocab)

    def validate_and_annotate(self, text: str, title: str, level: int) -> GradedText:
        """Validate a text against HSK level constraints and add pinyin footnotes."""
        validation = check_vocabulary_constraint(
            text, level, vocab_lookup=self.vocab, segmenter=self.segmenter
        )

        classification = self.classifier.classify_text(text, level)
        footnotes = self.annotator.annotate_above_level(classification)
        annotated = insert_footnote_markers(text, footnotes)
        footnotes_text = format_footnotes(footnotes)

        return GradedText(
            title=title,
            level=level,
            text=text,
            annotated_text=annotated,
            footnotes=footnotes,
            footnotes_text=footnotes_text,
            validation=validation,
        )
