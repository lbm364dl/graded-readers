import re
from pathlib import Path
from src.abridger.parser import BookContent, Chapter


class PdfParser:
    """Extract text from PDF files using pdfplumber."""

    def parse(self, file_path: Path) -> BookContent:
        import pdfplumber

        pages_text = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages_text.append(text)

        full_text = "\n".join(pages_text)
        chapters = self._split_chapters(full_text)

        title = file_path.stem
        return BookContent(title=title, chapters=chapters)

    def _split_chapters(self, text: str) -> list[Chapter]:
        """Split text into chapters based on common Chinese chapter markers."""
        pattern = r'(?=第[一二三四五六七八九十百千万\d]+[章回节])'
        parts = re.split(pattern, text)

        chapters = []
        for i, part in enumerate(parts):
            part = part.strip()
            if not part:
                continue
            lines = part.split("\n", 1)
            match = re.match(r'^(第[一二三四五六七八九十百千万\d]+[章回节].*)', lines[0])
            title = match.group(1) if match else f"Section {i + 1}"
            chapters.append(Chapter(title=title, text=part, index=i))

        if not chapters:
            chapters = [Chapter(title="Full Text", text=text, index=0)]

        return chapters
