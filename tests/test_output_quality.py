"""Quality tests for output/ graded reader files.

These tests go beyond vocabulary constraints to check structural integrity,
file completeness, metadata correctness, and content consistency.
"""
import json
import re
import pytest
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent.parent / "output"
APP_CONTENT = Path(__file__).parent.parent / "app" / "assets" / "content.json"

EXPECTED_BOOKS = [
    "sanguoyanyi", "liaozhai", "tangshi", "xiyouji",
    "hongloumeng", "shuihuzhuan", "sunzibingfa",
    "chengyugushi", "minjiangushi",
    "songci", "shijing", "chuci",
    "lunyu", "shishuoxinyu", "guwenguanzhi",
]

# Books that are primarily poetry — they legitimately include 原文 at all levels
# because the poems themselves ARE the content.
POETRY_BOOKS = {"tangshi", "songci", "shijing", "chuci"}

# The three books added in the latest batch — stricter checks apply here
# because we control their content completely.
NEW_BOOKS = ["lunyu", "shishuoxinyu", "guwenguanzhi"]

HSK_LEVELS = [1, 2, 3, 4, 5, 6]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def all_output_files():
    return sorted(OUTPUT_DIR.glob("*/hsk*_*.md"))


@pytest.fixture(scope="module")
def content_json():
    return json.loads(APP_CONTENT.read_text("utf-8"))


# ---------------------------------------------------------------------------
# File existence and completeness
# ---------------------------------------------------------------------------

class TestFileCompleteness:
    """Every expected book must have all 6 HSK level files plus support files."""

    @pytest.mark.parametrize("book", EXPECTED_BOOKS)
    def test_all_hsk_levels_present(self, book):
        book_dir = OUTPUT_DIR / book
        for level in HSK_LEVELS:
            md = book_dir / f"hsk{level}_{book}.md"
            assert md.exists(), f"Missing {md}"

    @pytest.mark.parametrize("book", EXPECTED_BOOKS)
    def test_glossary_exists(self, book):
        glossary = OUTPUT_DIR / book / "glossary.txt"
        assert glossary.exists(), f"Missing glossary.txt for {book}"

    @pytest.mark.parametrize("book", EXPECTED_BOOKS)
    def test_taught_vocab_exists(self, book):
        tv = OUTPUT_DIR / book / "taught_vocab.txt"
        assert tv.exists(), f"Missing taught_vocab.txt for {book}"

    @pytest.mark.parametrize("book", EXPECTED_BOOKS)
    def test_taught_vocab_has_correct_section_headers(self, book):
        """taught_vocab.txt must use '## HSK N' headers so the test loader can parse them."""
        tv_path = OUTPUT_DIR / book / "taught_vocab.txt"
        if not tv_path.exists():
            pytest.skip("file missing")
        text = tv_path.read_text("utf-8")
        found_levels = set()
        for line in text.splitlines():
            m = re.match(r"^## HSK (\d+)$", line.strip())
            if m:
                found_levels.add(int(m.group(1)))
        # At minimum levels 3-6 for classical literature should have above-level words
        for lvl in [3, 4, 5, 6]:
            assert lvl in found_levels, (
                f"{book}/taught_vocab.txt missing '## HSK {lvl}' section"
            )

    @pytest.mark.parametrize("book", NEW_BOOKS)
    def test_no_fractional_section_numbers(self, book):
        """Section number prefixes must not be fractional (e.g., 八点五、)."""
        book_dir = OUTPUT_DIR / book
        # Pattern: Chinese ordinal digit(s) + 点 + Chinese digit(s) + 、 at start of section
        fractional_header = re.compile(
            r"^##\s+[一二三四五六七八九十百]+点[一二三四五六七八九十]+[、．]"
        )
        for level in HSK_LEVELS:
            md = book_dir / f"hsk{level}_{book}.md"
            if not md.exists():
                continue
            for line in md.read_text("utf-8").splitlines():
                assert not fractional_header.match(line), (
                    f"{md.name}: fractional section number in header: {line!r}"
                )


# ---------------------------------------------------------------------------
# Markdown structure
# ---------------------------------------------------------------------------

class TestMarkdownStructure:
    """Each file must follow the expected document structure."""

    @pytest.mark.parametrize("book", EXPECTED_BOOKS)
    @pytest.mark.parametrize("level", HSK_LEVELS)
    def test_file_starts_with_h1_title(self, book, level):
        md = OUTPUT_DIR / book / f"hsk{level}_{book}.md"
        if not md.exists():
            pytest.skip("file missing")
        lines = md.read_text("utf-8").splitlines()
        assert lines[0].startswith("# "), (
            f"{md.name}: first line should be an H1 title, got: {lines[0]!r}"
        )

    @pytest.mark.parametrize("book", EXPECTED_BOOKS)
    @pytest.mark.parametrize("level", HSK_LEVELS)
    def test_file_has_hsk_level_marker(self, book, level):
        md = OUTPUT_DIR / book / f"hsk{level}_{book}.md"
        if not md.exists():
            pytest.skip("file missing")
        text = md.read_text("utf-8")
        assert f"**HSK Level {level}**" in text, (
            f"{md.name}: missing '**HSK Level {level}**' marker"
        )

    @pytest.mark.parametrize("book", EXPECTED_BOOKS)
    @pytest.mark.parametrize("level", HSK_LEVELS)
    def test_file_has_section_headers(self, book, level):
        md = OUTPUT_DIR / book / f"hsk{level}_{book}.md"
        if not md.exists():
            pytest.skip("file missing")
        text = md.read_text("utf-8")
        h2_count = sum(1 for line in text.splitlines() if line.startswith("## "))
        assert h2_count >= 3, (
            f"{md.name}: expected at least 3 section headers, found {h2_count}"
        )

    @pytest.mark.parametrize("book", EXPECTED_BOOKS)
    @pytest.mark.parametrize("level", HSK_LEVELS)
    def test_no_empty_sections(self, book, level):
        """No ## section should have zero body content."""
        md = OUTPUT_DIR / book / f"hsk{level}_{book}.md"
        if not md.exists():
            pytest.skip("file missing")
        lines = md.read_text("utf-8").splitlines()
        for i, line in enumerate(lines):
            if not line.startswith("## "):
                continue
            content_lines = []
            for j in range(i + 1, len(lines)):
                if lines[j].startswith("## "):
                    break
                if lines[j].strip() and lines[j].strip() != "---":
                    content_lines.append(lines[j])
            assert content_lines, (
                f"{md.name}: section has no body content: {line!r}"
            )


# ---------------------------------------------------------------------------
# Content quality (new books only — we fully control their content)
# ---------------------------------------------------------------------------

class TestNewBookContentQuality:
    """Quality checks scoped to the three new books we authored."""

    @pytest.mark.parametrize("book", NEW_BOOKS)
    def test_levels_strictly_increase_in_length(self, book):
        """Each HSK level must have more Chinese content than the level before it."""
        lengths = {}
        for level in HSK_LEVELS:
            md = OUTPUT_DIR / book / f"hsk{level}_{book}.md"
            if not md.exists():
                continue
            text = md.read_text("utf-8")
            lengths[level] = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")

        pairs = sorted(lengths.items())
        for i in range(len(pairs) - 1):
            lvl_a, len_a = pairs[i]
            lvl_b, len_b = pairs[i + 1]
            assert len_a < len_b, (
                f"{book}: HSK {lvl_a} ({len_a} chars) not shorter "
                f"than HSK {lvl_b} ({len_b} chars)"
            )

    @pytest.mark.parametrize("book", NEW_BOOKS)
    def test_no_duplicate_section_titles(self, book):
        """Within a single file, all ## section titles should be unique."""
        for level in HSK_LEVELS:
            md = OUTPUT_DIR / book / f"hsk{level}_{book}.md"
            if not md.exists():
                continue
            titles = [line.strip() for line in md.read_text("utf-8").splitlines()
                      if line.startswith("## ")]
            assert len(titles) == len(set(titles)), (
                f"{md.name}: duplicate section titles found"
            )

    @pytest.mark.parametrize("book", NEW_BOOKS)
    def test_no_placeholder_text(self, book):
        """Files must not contain common placeholder strings."""
        placeholders = ["TODO", "FIXME", "PLACEHOLDER", "lorem ipsum", "待填写"]
        for level in HSK_LEVELS:
            md = OUTPUT_DIR / book / f"hsk{level}_{book}.md"
            if not md.exists():
                continue
            text = md.read_text("utf-8")
            for p in placeholders:
                assert p not in text, (
                    f"{md.name}: contains placeholder text: {p!r}"
                )

    @pytest.mark.parametrize("book", set(NEW_BOOKS) - POETRY_BOOKS)
    def test_no_original_text_in_hsk1_hsk2(self, book):
        """Prose books: 原文 (classical text) sections should not appear at HSK 1-2."""
        for level in [1, 2]:
            md = OUTPUT_DIR / book / f"hsk{level}_{book}.md"
            if not md.exists():
                continue
            text = md.read_text("utf-8")
            has_original = bool(re.search(r"\*\*原文", text))
            assert not has_original, (
                f"{md.name}: HSK {level} prose file should not contain 原文 sections"
            )

    @pytest.mark.parametrize("book", NEW_BOOKS)
    def test_glossary_entries_are_chinese(self, book):
        """Glossary entries should mostly contain Chinese characters."""
        glossary_path = OUTPUT_DIR / book / "glossary.txt"
        if not glossary_path.exists():
            pytest.skip("file missing")
        entries = []
        for line in glossary_path.read_text("utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                entries.append(line)
        assert len(entries) >= 5, (
            f"{book}/glossary.txt has fewer than 5 entries ({len(entries)})"
        )
        chinese_entries = [e for e in entries if any("\u4e00" <= ch <= "\u9fff" for ch in e)]
        assert len(chinese_entries) >= len(entries) * 0.8, (
            f"{book}/glossary.txt: fewer than 80% of entries contain Chinese characters"
        )

    @pytest.mark.parametrize("book", NEW_BOOKS)
    def test_hsk1_uses_simple_language(self, book):
        """HSK1 files should have short sections — a proxy for simple vocabulary."""
        md = OUTPUT_DIR / book / f"hsk1_{book}.md"
        if not md.exists():
            pytest.skip("file missing")
        text = md.read_text("utf-8")
        # Average section length should be under 150 Chinese characters
        sections = re.split(r"\n## ", text)
        cjk_lengths = [
            sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
            for s in sections[1:]  # skip preamble
        ]
        if cjk_lengths:
            avg = sum(cjk_lengths) / len(cjk_lengths)
            assert avg < 200, (
                f"{md.name}: HSK1 average section length {avg:.0f} chars "
                f"seems too long for a beginner text"
            )


# ---------------------------------------------------------------------------
# content.json integrity
# ---------------------------------------------------------------------------

class TestContentJson:
    """content.json must accurately reflect all output/ files."""

    def test_content_json_exists(self):
        assert APP_CONTENT.exists(), f"Missing {APP_CONTENT}"

    def test_content_json_is_valid(self, content_json):
        assert isinstance(content_json, list)
        assert len(content_json) > 0

    @pytest.mark.parametrize("book", EXPECTED_BOOKS)
    def test_all_levels_in_content_json(self, book, content_json):
        ids_in_json = {entry["id"] for entry in content_json}
        for level in HSK_LEVELS:
            expected_id = f"{book}_hsk{level}"
            assert expected_id in ids_in_json, (
                f"content.json missing entry for {expected_id}"
            )

    def test_content_json_entries_have_chapters(self, content_json):
        for entry in content_json:
            assert "chapters" in entry, f"Entry {entry.get('id')} missing 'chapters'"
            assert len(entry["chapters"]) >= 1, (
                f"Entry {entry.get('id')} has no chapters"
            )
            for ch in entry["chapters"]:
                assert "title" in ch and "content" in ch, (
                    f"Entry {entry.get('id')}: chapter missing title or content"
                )
                assert ch["content"].strip(), (
                    f"Entry {entry.get('id')}: chapter {ch['title']!r} has empty content"
                )

    def test_content_json_required_fields(self, content_json):
        required = {"id", "book", "bookTitle", "bookTitleEn", "level", "chapters"}
        for entry in content_json:
            missing = required - entry.keys()
            assert not missing, (
                f"Entry {entry.get('id')} missing fields: {missing}"
            )

    def test_content_json_level_is_integer(self, content_json):
        for entry in content_json:
            assert isinstance(entry["level"], int), (
                f"Entry {entry.get('id')}: 'level' should be int, got {type(entry['level'])}"
            )
            assert 1 <= entry["level"] <= 6, (
                f"Entry {entry.get('id')}: 'level' {entry['level']} out of range 1-6"
            )

    def test_content_json_no_duplicate_ids(self, content_json):
        ids = [entry["id"] for entry in content_json]
        assert len(ids) == len(set(ids)), (
            f"Duplicate IDs in content.json: {[i for i in ids if ids.count(i) > 1]}"
        )

    @pytest.mark.parametrize("book", EXPECTED_BOOKS)
    def test_content_json_book_titles_consistent(self, book, content_json):
        """All entries for a book must have the same bookTitle and bookTitleEn."""
        entries = [e for e in content_json if e["book"] == book]
        if not entries:
            pytest.fail(f"No content.json entries for {book}")
        titles = {e["bookTitle"] for e in entries}
        en_titles = {e["bookTitleEn"] for e in entries}
        assert len(titles) == 1, f"{book}: inconsistent bookTitle values: {titles}"
        assert len(en_titles) == 1, f"{book}: inconsistent bookTitleEn values: {en_titles}"
