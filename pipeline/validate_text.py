#!/usr/bin/env python3
"""Validate generated graded reader texts against character-level constraints.

This is the core quality gate of the pipeline. It checks what percentage
of characters in a text fall within the allowed character set for a level.

Two modes:
  - character-level: checks each CJK character against the cumulative charset
  - word-level: uses the existing segmentation-based constraint checker

The character-level check is preferred for the generation pipeline because:
  1. No segmentation ambiguity — a character is or isn't in the set
  2. Easier for the AI to follow — just a flat list of allowed chars
  3. More robust for both Chinese and Japanese
"""

import json
import sys
from pathlib import Path


def is_cjk(char: str) -> bool:
    cp = ord(char)
    return (
        0x4E00 <= cp <= 0x9FFF
        or 0x3400 <= cp <= 0x4DBF
        or 0x20000 <= cp <= 0x2A6DF
        or 0xF900 <= cp <= 0xFAFF
    )


def load_charset(charset_path: Path) -> set[str]:
    """Load a character set from a flat text file (chars concatenated)."""
    text = charset_path.read_text(encoding="utf-8").strip()
    return set(text)


def validate_characters(
    text: str,
    allowed_chars: set[str],
    allowed_extra: set[str] | None = None,
    max_above_ratio: float = 0.05,
) -> dict:
    """Validate text against an allowed character set.

    Returns a report dict with:
      - passes: bool
      - total_chars: number of CJK characters in text
      - in_level_chars: number within allowed set
      - above_level_chars: number outside allowed set
      - above_level_ratio: ratio of above-level chars
      - above_level_list: sorted list of unique above-level characters
      - max_allowed_ratio: the threshold used
    """
    allowed = allowed_chars | (allowed_extra or set())
    total = 0
    in_level = 0
    above_set: set[str] = set()

    for ch in text:
        if is_cjk(ch):
            total += 1
            if ch in allowed:
                in_level += 1
            else:
                above_set.add(ch)

    above_count = total - in_level
    ratio = above_count / total if total > 0 else 0.0

    return {
        "passes": ratio <= max_above_ratio,
        "total_chars": total,
        "in_level_chars": in_level,
        "above_level_chars": above_count,
        "above_level_ratio": ratio,
        "above_level_percent": round(ratio * 100, 2),
        "in_level_percent": round((1 - ratio) * 100, 2),
        "above_level_list": sorted(above_set),
        "max_allowed_ratio": max_above_ratio,
    }


def strip_markdown_headers(text: str) -> str:
    """Remove markdown headers and metadata lines from reader text."""
    lines = text.split("\n")
    return "\n".join(
        line for line in lines
        if not line.startswith("#")
        and not line.startswith("**")
        and line.strip() != "---"
    )


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Validate text against character constraints")
    parser.add_argument("input", help="Text file or reader markdown to validate")
    parser.add_argument(
        "-c", "--charset",
        help="Path to charset file (flat text of allowed chars). "
             "If not given, auto-detect from level.",
    )
    parser.add_argument(
        "-l", "--level",
        help="Level key (e.g. hsk3, n4). Used for auto-detecting charset file.",
    )
    parser.add_argument(
        "--language", choices=["chinese", "japanese"],
        help="Language (auto-detected from level key if not given)",
    )
    parser.add_argument(
        "--threshold", type=float, default=0.05,
        help="Maximum allowed ratio of above-level characters (default: 0.05)",
    )
    parser.add_argument(
        "--extra-chars", default="",
        help="Additional allowed characters (e.g. proper nouns)",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    # Load text
    text_path = Path(args.input)
    text = text_path.read_text(encoding="utf-8")
    if text_path.suffix == ".md":
        text = strip_markdown_headers(text)

    # Resolve charset
    if args.charset:
        charset_path = Path(args.charset)
    elif args.level:
        pipeline_dir = Path(__file__).resolve().parent
        if args.level.startswith("hsk"):
            charset_path = pipeline_dir / "charsets" / "hsk" / f"{args.level}_chars.txt"
        elif args.level.startswith("n"):
            charset_path = pipeline_dir / "charsets" / "jlpt" / f"{args.level}_chars.txt"
        else:
            print(f"Error: cannot auto-detect charset for level '{args.level}'", file=sys.stderr)
            sys.exit(1)
    else:
        print("Error: must provide --charset or --level", file=sys.stderr)
        sys.exit(1)

    if not charset_path.exists():
        print(f"Error: charset file not found: {charset_path}", file=sys.stderr)
        print("Run `python -m pipeline.extract_characters` first to generate charsets.", file=sys.stderr)
        sys.exit(1)

    allowed_chars = load_charset(charset_path)
    extra = set(args.extra_chars) if args.extra_chars else None

    result = validate_characters(text, allowed_chars, extra, args.threshold)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        status = "PASS" if result["passes"] else "FAIL"
        print(f"[{status}] Character validation for {text_path.name}")
        print(f"  Total CJK chars : {result['total_chars']}")
        print(f"  In-level        : {result['in_level_chars']} ({result['in_level_percent']}%)")
        print(f"  Above-level     : {result['above_level_chars']} ({result['above_level_percent']}%)")
        print(f"  Threshold       : {result['max_allowed_ratio'] * 100:.0f}%")

        if result["above_level_list"]:
            chars = result["above_level_list"]
            display = chars[:50]
            print(f"\n  Above-level characters ({len(chars)}):")
            print(f"    {''.join(display)}")
            if len(chars) > 50:
                print(f"    ... and {len(chars) - 50} more")

    sys.exit(0 if result["passes"] else 1)


if __name__ == "__main__":
    main()
