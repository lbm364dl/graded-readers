"""Compliance tests for all JLPT graded reader output files.

Validates the 95/5 vocabulary rule, file integrity, minimum length,
and structural consistency across all 15 books × 5 levels.
"""

import re
import pytest
from pathlib import Path

from src.generator.constraints import check_vocabulary_constraint
from src.segmentation.segmenter import JapaneseSegmenter, _SUDACHI_AVAILABLE
from src.vocab.lookup import VocabLookup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
READERS_DIR = PROJECT_ROOT / "readers"

# All books in output/
ALL_BOOKS = sorted([d.name for d in OUTPUT_DIR.iterdir() if d.is_dir()])

# All 5 internal levels (1=N5, 2=N4, 3=N3, 4=N2, 5=N1)
ALL_LEVELS = [1, 2, 3, 4, 5]

LEVEL_LABELS = {1: "N5", 2: "N4", 3: "N3", 4: "N2", 5: "N1"}

# Minimum content word tokens per level
MIN_TOKENS = {1: 50, 2: 80, 3: 100, 4: 120, 5: 150}

# Relaxed raw ceiling (without glossary/taught vocab) — classical texts
# have many proper nouns so we allow up to 40% above-level raw
RAW_ABOVE_LEVEL_CEILING = 0.40

needs_sudachi = pytest.mark.skipif(
    not _SUDACHI_AVAILABLE,
    reason="SudachiPy not installed",
)


def _load_glossary(book_dir: Path) -> set[str]:
    """Load glossary.txt words for a book."""
    path = book_dir / "glossary.txt"
    if not path.exists():
        return set()
    words = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Take the first whitespace-delimited token
        word = line.split()[0].split("\t")[0]
        if word:
            words.add(word)
    return words


def _load_taught_vocab(book_dir: Path, level: int) -> set[str]:
    """Load taught vocabulary for a specific level from taught_vocab.txt."""
    path = book_dir / "taught_vocab.txt"
    if not path.exists():
        return set()
    words = set()
    in_section = False
    level_label = LEVEL_LABELS[level]

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("## "):
            in_section = level_label in line
            continue
        if in_section and line and not line.startswith("#") and not line.startswith("—"):
            word = line.split()[0].split("\t")[0]
            if word:
                words.add(word)
    return words


def _load_reader_text(path: Path) -> str:
    """Load a graded reader markdown file and strip headers/metadata."""
    text = path.read_text(encoding="utf-8")
    # Strip markdown headers and metadata
    lines = []
    for line in text.splitlines():
        # Skip title, level label, horizontal rules
        if line.startswith("# ") or line.startswith("## "):
            continue
        if line.startswith("**JLPT Level"):
            continue
        if line.strip() == "---":
            continue
        lines.append(line)
    return "\n".join(lines)


def _get_book_level_pairs():
    """Generate (book, level) pairs for parametrized tests."""
    pairs = []
    for book in ALL_BOOKS:
        for level in ALL_LEVELS:
            book_dir = OUTPUT_DIR / book
            filename = f"n{level}_{book}.md"
            if (book_dir / filename).exists():
                pairs.append((book, level))
    return pairs


# ---------------------------------------------------------------------------
# File integrity tests
# ---------------------------------------------------------------------------


class TestFileIntegrity:
    """Verify all expected files exist and are well-formed."""

    def test_all_books_present(self):
        expected = {
            "akutagawa", "botchan", "genji", "heike", "hyakunin",
            "kaidan", "konjaku", "kotowaza", "merosu", "miyazawa",
            "mukashibanashi", "taiheiki", "taketori", "tsurezure", "wagahai",
        }
        actual = {d.name for d in OUTPUT_DIR.iterdir() if d.is_dir()}
        assert expected <= actual, f"Missing books: {expected - actual}"

    @pytest.mark.parametrize("book", ALL_BOOKS)
    def test_all_levels_exist(self, book):
        book_dir = OUTPUT_DIR / book
        for level in ALL_LEVELS:
            path = book_dir / f"n{level}_{book}.md"
            assert path.exists(), f"Missing: {path}"

    @pytest.mark.parametrize("book", ALL_BOOKS)
    def test_glossary_exists(self, book):
        path = OUTPUT_DIR / book / "glossary.txt"
        assert path.exists(), f"Missing glossary: {path}"
        content = path.read_text(encoding="utf-8").strip()
        assert len(content) > 10, f"Glossary too short: {path}"

    @pytest.mark.parametrize("book", ALL_BOOKS)
    def test_taught_vocab_exists(self, book):
        path = OUTPUT_DIR / book / "taught_vocab.txt"
        assert path.exists(), f"Missing taught_vocab: {path}"
        content = path.read_text(encoding="utf-8").strip()
        assert len(content) > 10, f"Taught vocab too short: {path}"

    @pytest.mark.parametrize("book", ALL_BOOKS)
    def test_markdown_structure(self, book):
        """Each reader file should have a title and at least one section."""
        for level in ALL_LEVELS:
            path = OUTPUT_DIR / book / f"n{level}_{book}.md"
            text = path.read_text(encoding="utf-8")
            assert text.startswith("# "), f"Missing title in {path}"
            assert "**JLPT Level" in text, f"Missing level label in {path}"
            assert "---" in text, f"Missing section separator in {path}"

    def test_readers_directory_has_files(self):
        readers = list(READERS_DIR.glob("*.md"))
        assert len(readers) >= 20, f"Only {len(readers)} readers found"

    def test_readers_have_proper_format(self):
        for path in READERS_DIR.glob("*.md"):
            text = path.read_text(encoding="utf-8")
            assert text.startswith("# "), f"Missing title in {path.name}"
            assert "**JLPT Level" in text, f"Missing level in {path.name}"


# ---------------------------------------------------------------------------
# 95/5 vocabulary constraint tests
# ---------------------------------------------------------------------------


@needs_sudachi
class TestVocabularyConstraint:
    """Validate all graded readers against the 95/5 rule."""

    @pytest.fixture(scope="class")
    def segmenter(self):
        return JapaneseSegmenter()

    @pytest.fixture(scope="class")
    def vocab(self):
        return VocabLookup()

    @pytest.mark.parametrize("book,level", _get_book_level_pairs())
    def test_passes_95_5_with_allowed_words(self, book, level, segmenter, vocab):
        """Each reader must pass the 95/5 rule with glossary + taught vocab."""
        book_dir = OUTPUT_DIR / book
        path = book_dir / f"n{level}_{book}.md"
        text = _load_reader_text(path)

        glossary = _load_glossary(book_dir)
        taught = _load_taught_vocab(book_dir, level)
        allowed = glossary | taught

        result = check_vocabulary_constraint(
            text, level,
            vocab_lookup=vocab,
            segmenter=segmenter,
            allowed_words=allowed,
        )

        assert result.passes, (
            f"{book} level {LEVEL_LABELS[level]}: FAILED 95/5 rule. "
            f"Above-level ratio: {result.above_level_ratio:.1%} "
            f"({result.above_level_tokens}/{result.total_tokens} tokens). "
            f"Top above-level words: {result.above_level_words[:15]}"
        )

    @pytest.mark.parametrize("book,level", _get_book_level_pairs())
    def test_raw_above_level_below_ceiling(self, book, level, segmenter, vocab):
        """Even without glossary/taught vocab, above-level should stay below 40%."""
        book_dir = OUTPUT_DIR / book
        path = book_dir / f"n{level}_{book}.md"
        text = _load_reader_text(path)

        result = check_vocabulary_constraint(
            text, level,
            max_ratio=RAW_ABOVE_LEVEL_CEILING,
            vocab_lookup=vocab,
            segmenter=segmenter,
        )

        assert result.passes, (
            f"{book} level {LEVEL_LABELS[level]}: raw above-level "
            f"ratio {result.above_level_ratio:.1%} exceeds {RAW_ABOVE_LEVEL_CEILING:.0%} ceiling. "
            f"({result.above_level_tokens}/{result.total_tokens} tokens)"
        )

    @pytest.mark.parametrize("book,level", _get_book_level_pairs())
    def test_minimum_content_length(self, book, level, segmenter, vocab):
        """Each reader must have a minimum number of content tokens."""
        path = OUTPUT_DIR / book / f"n{level}_{book}.md"
        text = _load_reader_text(path)

        result = check_vocabulary_constraint(
            text, level,
            vocab_lookup=vocab,
            segmenter=segmenter,
        )

        min_tokens = MIN_TOKENS[level]
        assert result.total_tokens >= min_tokens, (
            f"{book} level {LEVEL_LABELS[level]}: only {result.total_tokens} "
            f"content tokens (minimum: {min_tokens})"
        )

    @pytest.mark.parametrize("book", ALL_BOOKS)
    def test_higher_levels_have_better_coverage(self, book, segmenter, vocab):
        """Higher JLPT levels should have equal or better vocabulary coverage."""
        book_dir = OUTPUT_DIR / book
        prev_ratio = 1.0

        for level in ALL_LEVELS:
            path = book_dir / f"n{level}_{book}.md"
            text = _load_reader_text(path)
            glossary = _load_glossary(book_dir)
            taught = _load_taught_vocab(book_dir, level)
            allowed = glossary | taught

            result = check_vocabulary_constraint(
                text, level,
                vocab_lookup=vocab,
                segmenter=segmenter,
                allowed_words=allowed,
            )

            # At each higher level, the above-level ratio should generally
            # not be worse than the previous level (with some tolerance)
            assert result.above_level_ratio <= prev_ratio + 0.10, (
                f"{book}: level {LEVEL_LABELS[level]} has worse coverage "
                f"({result.above_level_ratio:.1%}) than level "
                f"{LEVEL_LABELS.get(level-1, '?')} ({prev_ratio:.1%})"
            )
            prev_ratio = result.above_level_ratio


# ---------------------------------------------------------------------------
# Standalone reader tests
# ---------------------------------------------------------------------------


@needs_sudachi
class TestStandaloneReaders:
    """Validate standalone readers in the readers/ directory."""

    @pytest.fixture(scope="class")
    def segmenter(self):
        return JapaneseSegmenter()

    @pytest.fixture(scope="class")
    def vocab(self):
        return VocabLookup()

    def _reader_level_pairs(self):
        """Return (path, level) for each reader."""
        pairs = []
        for path in sorted(READERS_DIR.glob("*.md")):
            # Filename: n5_01_watashi_no_ichinichi.md
            match = re.match(r"n(\d+)_", path.name)
            if match:
                # n5 -> internal level 1, n1 -> internal level 5
                n_level = int(match.group(1))
                # Map: n5=1, n4=2, n3=3, n2=4, n1=5
                internal = 6 - n_level
                pairs.append((path, internal))
        return pairs

    def test_readers_pass_constraint(self, segmenter, vocab):
        """All standalone readers should pass a relaxed 90/10 rule."""
        for path, level in self._reader_level_pairs():
            text = _load_reader_text(path)
            if not text.strip():
                continue

            result = check_vocabulary_constraint(
                text, level,
                max_ratio=0.10,  # relaxed for short standalone texts
                vocab_lookup=vocab,
                segmenter=segmenter,
            )

            assert result.passes, (
                f"{path.name} (level {LEVEL_LABELS[level]}): FAILED. "
                f"Above-level: {result.above_level_ratio:.1%} "
                f"({result.above_level_tokens}/{result.total_tokens}). "
                f"Top words: {result.above_level_words[:10]}"
            )

    def test_readers_have_minimum_length(self, segmenter, vocab):
        """Each reader should have at least 20 content tokens."""
        for path, level in self._reader_level_pairs():
            text = _load_reader_text(path)
            result = check_vocabulary_constraint(
                text, level, vocab_lookup=vocab, segmenter=segmenter,
            )
            assert result.total_tokens >= 20, (
                f"{path.name}: only {result.total_tokens} tokens"
            )


# ---------------------------------------------------------------------------
# Cross-level consistency tests
# ---------------------------------------------------------------------------


class TestCrossLevelConsistency:
    """Verify structural consistency across levels within each book."""

    @pytest.mark.parametrize("book", ALL_BOOKS)
    def test_all_levels_have_chapters(self, book):
        """Each level should have at least 2 chapters (## headers)."""
        for level in ALL_LEVELS:
            path = OUTPUT_DIR / book / f"n{level}_{book}.md"
            text = path.read_text(encoding="utf-8")
            chapters = [l for l in text.splitlines() if l.startswith("## ")]
            assert len(chapters) >= 2, (
                f"{book} {LEVEL_LABELS[level]}: only {len(chapters)} chapters"
            )

    @pytest.mark.parametrize("book", ALL_BOOKS)
    def test_n1_is_longer_than_n5(self, book):
        """N1 version should generally be longer than N5."""
        n5_path = OUTPUT_DIR / book / f"n1_{book}.md"
        n1_path = OUTPUT_DIR / book / f"n5_{book}.md"
        n5_len = len(n5_path.read_text(encoding="utf-8"))
        n1_len = len(n1_path.read_text(encoding="utf-8"))
        assert n1_len >= n5_len * 0.8, (
            f"{book}: N1 ({n1_len} chars) is surprisingly shorter "
            f"than N5 ({n5_len} chars)"
        )

    @pytest.mark.parametrize("book", ALL_BOOKS)
    def test_titles_match_across_levels(self, book):
        """The book title (first line) should be the same across all levels."""
        titles = set()
        for level in ALL_LEVELS:
            path = OUTPUT_DIR / book / f"n{level}_{book}.md"
            first_line = path.read_text(encoding="utf-8").splitlines()[0]
            titles.add(first_line)
        assert len(titles) == 1, (
            f"{book}: inconsistent titles across levels: {titles}"
        )
