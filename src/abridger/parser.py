from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Chapter:
    title: str
    text: str
    index: int


@dataclass
class BookContent:
    title: str
    chapters: list[Chapter]

    @property
    def full_text(self) -> str:
        return "\n\n".join(ch.text for ch in self.chapters)

    @property
    def total_characters(self) -> int:
        return sum(len(ch.text) for ch in self.chapters)


def parse_book(file_path: Path) -> BookContent:
    """Parse a book file (PDF or EPUB) into structured content."""
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        from src.abridger.pdf_parser import PdfParser
        return PdfParser().parse(file_path)
    elif suffix == ".epub":
        from src.abridger.epub_parser import EpubParser
        return EpubParser().parse(file_path)
    elif suffix == ".txt":
        return _parse_plain_text(file_path)
    else:
        raise ValueError(f"Unsupported file format: {suffix}. Use .pdf, .epub, or .txt")


def _parse_plain_text(file_path: Path) -> BookContent:
    """Parse a plain text file, splitting on blank lines or chapter markers."""
    text = file_path.read_text(encoding="utf-8")
    # Try to split on chapter markers
    import re
    parts = re.split(r'\n(?=第[一二三四五六七八九十百千\d]+[章回节])', text)
    chapters = []
    for i, part in enumerate(parts):
        part = part.strip()
        if not part:
            continue
        # Extract title from first line if it looks like a chapter heading
        lines = part.split("\n", 1)
        title = lines[0].strip() if re.match(r'^第[一二三四五六七八九十百千\d]+[章回节]', lines[0]) else f"Part {i + 1}"
        chapters.append(Chapter(title=title, text=part, index=i))

    if not chapters:
        chapters = [Chapter(title="Full Text", text=text, index=0)]

    return BookContent(title=file_path.stem, chapters=chapters)
