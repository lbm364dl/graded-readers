#!/usr/bin/env python3
"""Generate AI prompts for creating graded reader texts.

This builds the prompt that should be fed to the AI (Claude, etc.) to produce
a simplified version of a source text at a given proficiency level.

The key insight: instead of asking the AI to match a word list (which requires
segmentation and is error-prone to verify), we give it a flat character set.
Characters are unambiguous — either a character appears in the set or it doesn't.
"""

import json
from pathlib import Path


def load_charset(charset_path: Path) -> str:
    """Load charset as a string of characters."""
    return charset_path.read_text(encoding="utf-8").strip()


def load_level_metadata(language: str) -> dict:
    """Load level metadata (sentence length, text length targets, etc.)."""
    project_root = Path(__file__).resolve().parent.parent
    if language == "chinese":
        meta_path = project_root / "data" / "hsk_levels.json"
    else:
        meta_path = project_root / "jlpt" / "data" / "jlpt_levels.json"

    with open(meta_path, encoding="utf-8") as f:
        return json.load(f)


def get_level_info(metadata: dict, level_num: int) -> dict:
    """Get info for a specific level from the metadata."""
    for lvl in metadata["levels"]:
        if lvl["level"] == level_num:
            return lvl
    raise ValueError(f"Level {level_num} not found in metadata")


def build_prompt(
    source_text: str,
    charset: str,
    level_key: str,
    level_num: int,
    language: str,
    title: str = "",
    iteration: int = 1,
    previous_attempt: str = "",
    previous_validation: dict | None = None,
    glossary_chars: str = "",
) -> str:
    """Build the AI prompt for generating a graded reader text.

    Args:
        source_text: the original text to simplify
        charset: string of allowed characters
        level_key: e.g. "hsk3", "n4"
        level_num: numeric level (1-7 for HSK, 1-5 for JLPT)
        language: "chinese" or "japanese"
        title: title for the reader
        iteration: which attempt this is (1 = first, 2+ = retry)
        previous_attempt: the text from the previous iteration (for retries)
        previous_validation: validation result from previous attempt
        glossary_chars: extra characters allowed (proper nouns, etc.)
    """
    metadata = load_level_metadata(language)
    level_info = get_level_info(metadata, level_num)

    lang_name = "Chinese" if language == "chinese" else "Japanese"
    char_name = "hanzi" if language == "chinese" else "kanji"
    level_label = level_key.upper().replace("TO", "-")

    # Build the base prompt
    parts = []

    parts.append(f"# Task: Create a {level_label} Graded Reader")
    parts.append("")
    parts.append(f"You are creating a graded reader text in {lang_name} at the "
                 f"{level_label} level ({level_info.get('band', '')}).")
    parts.append(f"Level description: {level_info['description']}")
    parts.append("")

    # Source text section
    parts.append("## Source Text")
    parts.append("")
    parts.append("Simplify and adapt the following text. Preserve the core story/meaning "
                 "but rewrite it so that a learner at this level can read it:")
    parts.append("")
    parts.append("```")
    parts.append(source_text[:8000])  # Truncate very long source texts
    if len(source_text) > 8000:
        parts.append(f"\n[... truncated, {len(source_text)} total characters ...]")
    parts.append("```")
    parts.append("")

    # Character constraint section — THE KEY PART
    parts.append("## Character Constraint (CRITICAL)")
    parts.append("")
    parts.append(f"You MUST write using ONLY the following {char_name}. "
                 f"At least 95% of all {char_name} in your output must come from this list. "
                 f"At most 5% may be outside this list (for unavoidable proper nouns or "
                 f"story-specific terms).")
    parts.append("")
    parts.append(f"### Allowed {char_name} ({len(charset)} characters):")
    parts.append("")
    # Present chars in rows of 50 for readability
    for i in range(0, len(charset), 50):
        parts.append(charset[i:i+50])
    parts.append("")

    if glossary_chars:
        parts.append(f"### Additional allowed characters (glossary/proper nouns):")
        parts.append(glossary_chars)
        parts.append("")

    # Length and style guidelines
    text_len = level_info.get("target_text_length", {})
    sent_len = level_info.get("target_sentence_length", {})

    parts.append("## Writing Guidelines")
    parts.append("")
    if text_len:
        parts.append(f"- Target length: {text_len.get('medium', 300)}-"
                     f"{text_len.get('long', 800)} characters")
    if sent_len:
        parts.append(f"- Sentence length: {sent_len.get('min', 5)}-"
                     f"{sent_len.get('max', 15)} characters per sentence")

    if language == "chinese":
        parts.append("- Use simple sentence structures appropriate for this level")
        parts.append("- Avoid classical Chinese (文言文) unless the level is HSK 6+")
        parts.append("- Use common, everyday vocabulary")
    else:
        parts.append("- Use appropriate grammar patterns for this JLPT level")
        parts.append("- Use furigana-friendly kanji (common readings)")
        parts.append("- Prefer kun'yomi for lower levels, allow more on'yomi at higher levels")

    parts.append("- Break into short paragraphs")
    parts.append("- Keep the narrative engaging despite simplification")
    parts.append("")

    # Strategy tips
    parts.append("## Strategy")
    parts.append("")
    parts.append(f"Before writing, mentally check each {char_name} you're about to use. "
                 "If a character is not in the allowed list:")
    parts.append(f"  1. Find a synonym that uses only allowed {char_name}")
    parts.append("  2. Rephrase the sentence to avoid that character")
    parts.append("  3. Only if absolutely necessary (proper nouns, key concepts), "
                 "use the out-of-level character — but keep this under 5%")
    parts.append("")

    # Retry section
    if iteration > 1 and previous_attempt and previous_validation:
        parts.append("## Previous Attempt (NEEDS IMPROVEMENT)")
        parts.append("")
        pv = previous_validation
        parts.append(f"Your previous attempt had {pv['above_level_percent']}% "
                     f"above-level characters (target: ≤{pv['max_allowed_ratio']*100:.0f}%).")
        parts.append("")
        if pv.get("above_level_list"):
            above = "".join(pv["above_level_list"][:100])
            parts.append(f"Characters that were above-level: {above}")
            parts.append("")
        parts.append("Please revise this text, replacing the above-level characters "
                     "with in-level alternatives:")
        parts.append("")
        parts.append("```")
        parts.append(previous_attempt)
        parts.append("```")
        parts.append("")

    # Output format
    parts.append("## Output Format")
    parts.append("")
    parts.append("Return ONLY the simplified text, with no metadata, headers, or explanations. "
                 "Just the story text, paragraph by paragraph.")

    return "\n".join(parts)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Generate AI prompt for graded reader creation")
    parser.add_argument("source", help="Source text file")
    parser.add_argument("-l", "--level", required=True, help="Level key (e.g. hsk3, n4)")
    parser.add_argument("--language", choices=["chinese", "japanese"], required=True)
    parser.add_argument("--title", default="", help="Title for the reader")
    parser.add_argument("--glossary-chars", default="", help="Extra allowed characters")
    parser.add_argument("-o", "--output", help="Output file for the prompt (default: stdout)")
    parser.add_argument(
        "--previous-attempt", help="Previous attempt text file (for iteration)",
    )
    parser.add_argument(
        "--previous-validation", help="Previous validation JSON file (for iteration)",
    )

    args = parser.parse_args()

    # Parse level number from key
    level_key = args.level
    if level_key.startswith("hsk"):
        level_num = int(level_key.replace("hsk", "").replace("7to9", "7"))
    elif level_key.startswith("n"):
        level_num = int(level_key.replace("n", ""))
    else:
        raise ValueError(f"Unknown level key: {level_key}")

    # Load source text
    source_text = Path(args.source).read_text(encoding="utf-8")

    # Load charset
    pipeline_dir = Path(__file__).resolve().parent
    if args.language == "chinese":
        charset_path = pipeline_dir / "charsets" / "hsk" / f"{level_key}_chars.txt"
    else:
        charset_path = pipeline_dir / "charsets" / "jlpt" / f"{level_key}_chars.txt"

    if not charset_path.exists():
        print(f"Error: charset not found at {charset_path}")
        print("Run `python -m pipeline.extract_characters` first.")
        return

    charset = load_charset(charset_path)

    # Handle iteration
    iteration = 1
    previous_attempt = ""
    previous_validation = None
    if args.previous_attempt:
        iteration = 2
        previous_attempt = Path(args.previous_attempt).read_text(encoding="utf-8")
    if args.previous_validation:
        with open(args.previous_validation, encoding="utf-8") as f:
            previous_validation = json.load(f)

    prompt = build_prompt(
        source_text=source_text,
        charset=charset,
        level_key=level_key,
        level_num=level_num,
        language=args.language,
        title=args.title,
        iteration=iteration,
        previous_attempt=previous_attempt,
        previous_validation=previous_validation,
        glossary_chars=args.glossary_chars,
    )

    if args.output:
        Path(args.output).write_text(prompt, encoding="utf-8")
        print(f"Prompt written to {args.output} ({len(prompt)} chars)")
    else:
        print(prompt)


if __name__ == "__main__":
    main()
