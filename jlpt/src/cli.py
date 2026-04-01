#!/usr/bin/env python3
"""CLI for JLPT Graded Readers — analyze, validate, and report on Japanese texts."""

import argparse
import sys
from pathlib import Path

from src.config import READERS_DIR, LEVEL_LABELS, LEVELS


def _read_text(filepath: str) -> str:
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
    from src.analytics.coverage import coverage_statistics
    from src.analytics.report import print_coverage_report
    from src.segmentation.classifier import LevelClassifier

    text = _read_text(args.input)
    classifier = LevelClassifier()
    stats = coverage_statistics(text, args.level, classifier)
    print(print_coverage_report(stats))


def cmd_validate(args):
    from src.generator.constraints import check_vocabulary_constraint

    text = _read_text(args.input)
    result = check_vocabulary_constraint(text, args.level)

    status = "PASS" if result.passes else "FAIL"
    print(f"[{status}] {LEVEL_LABELS[args.level]} validation")
    print(f"  Total tokens  : {result.total_tokens}")
    print(f"  In-level      : {result.in_level_tokens}")
    print(f"  Above-level   : {result.above_level_tokens} ({result.above_level_ratio*100:.1f}%)")
    print(f"  Max allowed   : {result.max_allowed_ratio*100:.0f}%")

    if result.above_level_words:
        print(f"\n  Above-level words ({len(result.above_level_words)}):")
        for word in result.above_level_words[:20]:
            print(f"    {word}")
        if len(result.above_level_words) > 20:
            print(f"    ... and {len(result.above_level_words) - 20} more")

    sys.exit(0 if result.passes else 1)


def cmd_coverage(args):
    from src.analytics.coverage import coverage_statistics
    from src.segmentation.classifier import LevelClassifier

    text = _read_text(args.input)
    classifier = LevelClassifier()

    print(f"Coverage analysis: {args.input}\n")
    print(f"{'Level':<8} {'Coverage':>10} {'Above-lvl':>10} {'Status':>8}")
    print("-" * 40)

    for level in LEVELS:
        stats = coverage_statistics(text, level, classifier)
        status = "PASS" if stats.passes else "FAIL"
        label = LEVEL_LABELS[level]
        print(f"{label:<8} {stats.coverage_percent:>9.1f}% {stats.above_level_percent:>9.1f}% {status:>8}")


def cmd_vocab(args):
    from src.vocab.loader import load_all_levels

    levels = load_all_levels()
    print(f"{'Level':<8} {'Words':>8} {'Cumulative':>12}")
    print("-" * 32)

    cum = 0
    for lvl in LEVELS:
        cnt = len(levels[lvl].words)
        cum += cnt
        label = LEVEL_LABELS[lvl]
        print(f"{label:<8} {cnt:>8} {cum:>12}")


def main():
    parser = argparse.ArgumentParser(
        prog="jlpt-reader",
        description="JLPT Graded Reader tools — analyze and validate Japanese texts",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("analyze", help="Detailed JLPT coverage analysis")
    p.add_argument("input")
    p.add_argument("-l", "--level", type=int, required=True, help="Target level 1-5 (1=N5, 5=N1)")

    p = sub.add_parser("validate", help="Validate text against 95/5 rule")
    p.add_argument("input")
    p.add_argument("-l", "--level", type=int, required=True)

    p = sub.add_parser("coverage", help="Show coverage at all JLPT levels")
    p.add_argument("input")

    sub.add_parser("vocab", help="Show JLPT vocabulary statistics")

    args = parser.parse_args()
    {"analyze": cmd_analyze, "validate": cmd_validate,
     "coverage": cmd_coverage, "vocab": cmd_vocab}[args.command](args)


if __name__ == "__main__":
    main()
