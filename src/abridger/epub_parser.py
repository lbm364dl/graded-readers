import re
from pathlib import Path
from src.abridger.parser import BookContent, Chapter


class EpubParser:
    """Extract text from EPUB files using ebooklib."""

    def parse(self, file_path: Path) -> BookContent:
        import ebooklib
        from ebooklib import epub
        from html.parser import HTMLParser

        book = epub.read_epub(str(file_path))
        title = book.get_metadata("DC", "title")
        title = title[0][0] if title else file_path.stem

        chapters = []
        idx = 0
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            html_content = item.get_content().decode("utf-8", errors="replace")
            text = self._strip_html(html_content)
            text = text.strip()
            if len(text) < 10:  # Skip near-empty pages
                continue
            ch_title = self._extract_title(text) or f"Chapter {idx + 1}"
            chapters.append(Chapter(title=ch_title, text=text, index=idx))
            idx += 1

        if not chapters:
            chapters = [Chapter(title="Full Text", text="", index=0)]

        return BookContent(title=title, chapters=chapters)

    @staticmethod
    def _strip_html(html: str) -> str:
        """Remove HTML tags, keeping text content."""
        clean = re.sub(r'<[^>]+>', '', html)
        clean = re.sub(r'&nbsp;', ' ', clean)
        clean = re.sub(r'&lt;', '<', clean)
        clean = re.sub(r'&gt;', '>', clean)
        clean = re.sub(r'&amp;', '&', clean)
        clean = re.sub(r'\n{3,}', '\n\n', clean)
        return clean.strip()

    @staticmethod
    def _extract_title(text: str) -> str | None:
        """Try to extract a chapter title from the first line."""
        first_line = text.split("\n", 1)[0].strip()
        if re.match(r'^第[一二三四五六七八九十百千万\d]+[章回节]', first_line):
            return first_line
        if len(first_line) < 30:
            return first_line
        return None
