#!/usr/bin/env python3
"""CLI for HSK Graded Readers - generate, analyze, validate, and abridge Chinese texts."""

import argparse
import sys
from pathlib import Path

from src.config import READERS_DIR, LEVEL_LABELS, LEVELS


def _read_text(filepath: str) -> str:
    """Read a text file, stripping markdown headers if it's a .md file."""
    path = Path(filepath)
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".md":
        lines = text.split("\n")
        text = "\n".join(
            l for l in lines
            if not l.startswith("#") and not l.startswith("**") and l.strip() != "---"
        )
    return text


def cmd_analyze(args):
    """Analyze a text file's HSK coverage."""
    from src.analytics.coverage import coverage_statistics
    from src.analytics.report import print_coverage_report
    from src.segmentation.classifier import LevelClassifier

    text = _read_text(args.input)
    classifier = LevelClassifier()
    stats = coverage_statistics(text, args.level, classifier)
    report = print_coverage_report(stats)
    print(report)


def cmd_validate(args):
    """Validate a text against the 95/5 vocabulary rule."""
    from src.generator.constraints import check_vocabulary_constraint

    text = _read_text(args.input)
    result = check_vocabulary_constraint(text, args.level)

    status = "PASS" if result.passes else "FAIL"
    print(f"[{status}] HSK {args.level} validation")
    print(f"  Total tokens:    {result.total_tokens}")
    print(f"  In-level:        {result.in_level_tokens}")
    print(f"  Above-level:     {result.above_level_tokens} ({result.above_level_ratio*100:.1f}%)")
    print(f"  Max allowed:     {result.max_allowed_ratio*100:.0f}%")

    if result.above_level_words:
        print(f"\n  Above-level words ({len(result.above_level_words)}):")
        for word in result.above_level_words[:20]:
            print(f"    {word}")
        if len(result.above_level_words) > 20:
            print(f"    ... and {len(result.above_level_words) - 20} more")

    sys.exit(0 if result.passes else 1)


def cmd_abridge(args):
    """Analyze a book for abridging at a target HSK level."""
    from src.abridger.abridger import BookAbridger

    abridger = BookAbridger()
    report = abridger.analyze_book(Path(args.input), args.level)
    print(report)

    if args.output:
        result = abridger.abridge(Path(args.input), args.level)
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(result.full_text, encoding="utf-8")
        print(f"\nAnnotated output written to: {out_path}")


def cmd_coverage(args):
    """Show vocabulary coverage for a text at multiple HSK levels."""
    from src.analytics.coverage import coverage_statistics
    from src.segmentation.classifier import LevelClassifier

    text = _read_text(args.input)
    classifier = LevelClassifier()

    print(f"Coverage analysis for: {args.input}\n")
    print(f"{'Level':<10} {'Coverage':>10} {'Above-lvl':>10} {'Status':>8}")
    print("-" * 42)

    for level in LEVELS:
        stats = coverage_statistics(text, level, classifier)
        status = "PASS" if stats.passes else "FAIL"
        label = LEVEL_LABELS[level]
        print(f"{label:<10} {stats.coverage_percent:>9.1f}% {stats.above_level_percent:>9.1f}% {status:>8}")


def cmd_vocab(args):
    """Show vocabulary statistics."""
    from src.vocab.loader import load_all_levels

    levels = load_all_levels()
    print(f"{'Level':<10} {'Words':>8} {'Chars':>8} {'Cum.Words':>10} {'Cum.Chars':>10}")
    print("-" * 50)

    cum_words = 0
    cum_chars = 0
    for lvl in LEVELS:
        hsk = levels[lvl]
        cum_words += len(hsk.words)
        cum_chars += len(hsk.characters)
        label = LEVEL_LABELS[lvl]
        print(f"{label:<10} {len(hsk.words):>8} {len(hsk.characters):>8} {cum_words:>10} {cum_chars:>10}")


def main():
    parser = argparse.ArgumentParser(
        prog="hsk-reader",
        description="HSK Graded Reader tools - generate, analyze, and validate Chinese texts",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # analyze
    p_analyze = subparsers.add_parser("analyze", help="Detailed HSK coverage analysis")
    p_analyze.add_argument("input", help="Input text file")
    p_analyze.add_argument("-l", "--level", type=int, required=True, help="Target HSK level (1-7)")

    # validate
    p_validate = subparsers.add_parser("validate", help="Validate text against 95/5 rule")
    p_validate.add_argument("input", help="Input text file")
    p_validate.add_argument("-l", "--level", type=int, required=True, help="Target HSK level (1-7)")

    # abridge
    p_abridge = subparsers.add_parser("abridge", help="Analyze/abridge a book for target HSK level")
    p_abridge.add_argument("input", help="Input book file (PDF, EPUB, or TXT)")
    p_abridge.add_argument("-l", "--level", type=int, required=True, help="Target HSK level (1-7)")
    p_abridge.add_argument("-o", "--output", help="Output file path")

    # coverage
    p_coverage = subparsers.add_parser("coverage", help="Show coverage at all HSK levels")
    p_coverage.add_argument("input", help="Input text file")

    # vocab
    subparsers.add_parser("vocab", help="Show HSK vocabulary statistics")

    args = parser.parse_args()

    commands = {
        "analyze": cmd_analyze,
        "validate": cmd_validate,
        "abridge": cmd_abridge,
        "coverage": cmd_coverage,
        "vocab": cmd_vocab,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
