from src.generator.graded_reader import GradedText
from src.analytics.report import print_coverage_report
from src.analytics.coverage import CoverageStats


def format_reader_markdown(reader: GradedText) -> str:
    """Format a graded reader as markdown."""
    lines = [
        f"# {reader.title}",
        f"",
        f"**HSK Level {reader.level}** | "
        f"Coverage: {(1 - reader.validation.above_level_ratio) * 100:.1f}% | "
        f"Tokens: {reader.validation.total_tokens}",
        f"",
        reader.annotated_text,
    ]
    if reader.footnotes_text:
        lines.append(reader.footnotes_text)
    return "\n".join(lines)


def format_reader_plain(reader: GradedText) -> str:
    """Format a graded reader as plain text."""
    lines = [
        reader.title,
        f"HSK Level {reader.level}",
        "=" * 40,
        "",
        reader.text,
    ]
    if reader.footnotes:
        lines.append("")
        lines.append("--- New Words ---")
        for fn in reader.footnotes:
            english = f" - {fn.english}" if fn.english else ""
            lines.append(f"{fn.word} ({fn.pinyin}){english}")
    return "\n".join(lines)
