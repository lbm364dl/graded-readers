from pathlib import Path
from src.generator.graded_reader import GradedText
from src.output.formatter import format_reader_markdown, format_reader_plain


def write_graded_reader(reader: GradedText, output_dir: Path,
                         fmt: str = "markdown") -> Path:
    """Write a graded reader to a file."""
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_title = reader.title.replace(" ", "_")[:50]
    if fmt == "markdown":
        path = output_dir / f"hsk{reader.level}_{safe_title}.md"
        content = format_reader_markdown(reader)
    else:
        path = output_dir / f"hsk{reader.level}_{safe_title}.txt"
        content = format_reader_plain(reader)

    path.write_text(content, encoding="utf-8")
    return path
