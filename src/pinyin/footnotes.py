from src.pinyin.annotator import Footnote


def format_footnotes(footnotes: list[Footnote], style: str = "numbered") -> str:
    """Format footnotes as a block of text."""
    if not footnotes:
        return ""

    lines = ["\n---", "**生词 (New Words):**\n"]
    for fn in footnotes:
        level_str = f"HSK {fn.level}" if fn.level else "Non-HSK"
        english_str = f" - {fn.english}" if fn.english else ""
        if style == "numbered":
            lines.append(f"[{fn.index}] {fn.word} ({fn.pinyin}){english_str} [{level_str}]")
        else:
            lines.append(f"• {fn.word} ({fn.pinyin}){english_str} [{level_str}]")

    return "\n".join(lines)


def insert_footnote_markers(text: str, footnotes: list[Footnote]) -> str:
    """Insert superscript footnote markers after each above-level word's first occurrence."""
    result = text
    for fn in sorted(footnotes, key=lambda f: -len(f.word)):
        # Replace first occurrence only
        marker = f"{fn.word}[{fn.index}]"
        result = result.replace(fn.word, marker, 1)
    return result
