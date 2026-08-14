#!/usr/bin/env python3
"""Print resumable harness commands containing only blocked retelling sources."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def blocked_sources(report_path: Path) -> list[str]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return [str(item["source"]) for item in report["runs"] if item["status"] != "complete"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--level", required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    for source in blocked_sources(args.run_dir / "book-report.json"):
        print(source)


if __name__ == "__main__":
    main()
