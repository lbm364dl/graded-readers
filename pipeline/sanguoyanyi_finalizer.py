#!/usr/bin/env python3
"""Deterministic preflight and monotonic-length recovery for 三国演义.

This helper never accepts partial coverage. Recovery invokes the existing
source-grounded harness only for missing/blocked keys and preserves its durable
cache. Length healing runs before annotation and invalidates any annotation
whose accepted prose changes.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable

from pipeline.agent_harness import cjk_count
from pipeline.extract_epub_chapters import ExtractionError, verify_manifest as verify_source_manifest


ROOT = Path(__file__).resolve().parent.parent
LEVELS = tuple(f"hsk{i}" for i in range(1, 7))
RUN_RE = re.compile(r"^chapter_(\d{3})-(hsk[1-6])$")
Runner = Callable[[list[str]], int]


class FinalizerError(ValueError):
    pass


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FinalizerError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FinalizerError(f"expected JSON object: {path}")
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def expected_keys(expected_chapters: int = 120) -> set[tuple[int, str]]:
    return {(number, level) for number in range(1, expected_chapters + 1)
            for level in LEVELS}


def discover_runs(run_roots: list[Path]) -> dict[tuple[int, str], Path]:
    found: dict[tuple[int, str], Path] = {}
    for root in run_roots:
        if not root.is_dir():
            raise FinalizerError(f"run root missing: {root}")
        for path in root.iterdir():
            match = RUN_RE.fullmatch(path.name)
            if not path.is_dir() or not match:
                continue
            key = (int(match.group(1)), match.group(2))
            if key in found and found[key].resolve() != path.resolve():
                raise FinalizerError(f"duplicate run {key}: {found[key]} and {path}")
            found[key] = path
    return found


def preflight(
    run_roots: list[Path], expected_chapters: int = 120,
) -> dict[str, Any]:
    runs = discover_runs(run_roots)
    expected = expected_keys(expected_chapters)
    missing = sorted(expected - set(runs))
    unexpected = sorted(set(runs) - expected)
    incomplete: list[dict[str, Any]] = []
    for key in sorted(expected & set(runs)):
        path = runs[key]
        absent = [name for name in ("chapter.txt", "manifest.json", "report.json")
                  if not (path / name).is_file()]
        status: dict[str, Any] = {}
        if not absent:
            try:
                manifest = _json(path / "manifest.json")
                generation_report = _json(path / "report.json")
                chapter = (path / "chapter.txt").read_text(encoding="utf-8")
                status = {"manifest": manifest.get("status"),
                          "report": generation_report.get("status")}
                acceptance_errors = []
                if manifest.get("level") != key[1]:
                    acceptance_errors.append("manifest level does not match run key")
                actual_cjk = cjk_count(chapter)
                if not chapter.strip() or actual_cjk == 0:
                    acceptance_errors.append("accepted chapter is empty")
                if generation_report.get("chapter_cjk") != actual_cjk:
                    acceptance_errors.append("report chapter_cjk does not match chapter.txt")
                verdicts = generation_report.get("scene_verdicts")
                if (not isinstance(verdicts, dict) or not verdicts
                        or any(value != "pass" for value in verdicts.values())):
                    acceptance_errors.append("not every source-grounded scene review passed")
                if acceptance_errors:
                    incomplete.append({
                        "chapter": key[0], "level": key[1],
                        "run_dir": str(path), "acceptance_errors": acceptance_errors,
                        **status,
                    })
                    continue
            except FinalizerError as exc:
                incomplete.append({"chapter": key[0], "level": key[1],
                                   "run_dir": str(path), "error": str(exc)})
                continue
        if absent or status != {"manifest": "complete", "report": "complete"}:
            incomplete.append({"chapter": key[0], "level": key[1],
                               "run_dir": str(path), "missing_artifacts": absent,
                               **status})
    complete = len(expected) - len(missing) - len(incomplete)
    return {
        "status": "complete" if complete == len(expected) and not unexpected else "blocked",
        "expected": len(expected), "complete": complete,
        "missing": [{"chapter": n, "level": level} for n, level in missing],
        "unexpected": [{"chapter": n, "level": level} for n, level in unexpected],
        "incomplete": incomplete,
    }


def parse_level_roots(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        level, separator, path = value.partition("=")
        if not separator or level not in LEVELS or level in result:
            raise FinalizerError(f"invalid or duplicate --level-root: {value}")
        result[level] = Path(path).resolve()
    if set(result) != set(LEVELS):
        raise FinalizerError("--recover/--heal requires one --level-root for every HSK level")
    return result


def active_generation_processes(
    run_roots: list[Path], proc_root: Path = Path("/proc")
) -> list[dict[str, Any]]:
    """Return live agent-harness processes targeting any supplied run root."""
    identities = {(str(root.resolve()), root.resolve().name) for root in run_roots}
    active: list[dict[str, Any]] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            tokens = [part.decode("utf-8", errors="replace")
                      for part in (entry / "cmdline").read_bytes().split(b"\0") if part]
        except OSError:
            continue
        joined = " ".join(tokens)
        if "pipeline.agent_harness" not in joined:
            continue
        option_values = {
            tokens[index + 1]
            for index, token in enumerate(tokens[:-1])
            if token in {"--run-id", "--book-run-id", "--run-dir", "--runs-dir"}
        }
        targeted = False
        for absolute, name in identities:
            # A bare root name is meaningful only as a run/path option value.
            # Searching every argv token made generic roots such as ``low``
            # collide with unrelated values like ``--annotation-effort low``.
            if (absolute in option_values or name in option_values
                    or any(value.startswith(name + "/") for value in option_values)):
                targeted = True
                break
        if targeted:
            active.append({"pid": int(entry.name), "command": joined})
    return sorted(active, key=lambda item: item["pid"])


def require_idle(run_roots: list[Path]) -> None:
    active = active_generation_processes(run_roots)
    if active:
        raise FinalizerError(
            "refusing to mutate run roots while generation is active: "
            + ", ".join(str(item["pid"]) for item in active)
        )


def _default_runner(command: list[str]) -> int:
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def recover_incomplete(
    report: dict[str, Any], level_roots: dict[str, Path], source_dir: Path,
    runner: Runner = _default_runner,
) -> list[dict[str, Any]]:
    require_idle(sorted(set(level_roots.values())))
    targets = report["missing"] + [
        {"chapter": item["chapter"], "level": item["level"]}
        for item in report["incomplete"]
    ]
    records = []
    for level in LEVELS:
        chapters = sorted({item["chapter"] for item in targets if item["level"] == level})
        if not chapters:
            continue
        root = level_roots[level]
        command = [sys.executable, "-m", "pipeline.agent_harness", "book"]
        for number in chapters:
            command += ["--source", str((source_dir / f"chapter_{number:03d}.txt").resolve())]
        command += [
            "--levels", level, "--book-run-id", root.name,
            "--runs-dir", str(root.parent), "--concurrency", "3",
            "--chapter-concurrency", "3", "--chapter-retries", "2",
            "--length-repair-rounds", "1", "--max-repairs", "3",
            "--final-effort", "xhigh", "--skip-annotations",
        ]
        code = runner(command)
        records.append({"level": level, "chapters": chapters,
                        "return_code": code, "command": command})
    return records


def length_violations(run_roots: list[Path], expected_chapters: int = 120) -> list[dict[str, Any]]:
    ready = preflight(run_roots, expected_chapters)
    if ready["status"] != "complete":
        raise FinalizerError(
            f"length audit requires all {ready['expected']} complete artifacts; "
            f"found {ready['complete']}"
        )
    runs = discover_runs(run_roots)
    counts = {key: cjk_count((path / "chapter.txt").read_text(encoding="utf-8"))
              for key, path in runs.items() if key in expected_keys(expected_chapters)}
    failures = []
    for number in range(1, expected_chapters + 1):
        for lower, upper in zip(LEVELS, LEVELS[1:]):
            if counts[(number, lower)] >= counts[(number, upper)]:
                failures.append({
                    "chapter": number, "lower": lower, "upper": upper,
                    "lower_cjk": counts[(number, lower)],
                    "upper_cjk": counts[(number, upper)],
                })
    return failures


def invalidate_annotations(run_dir: Path, reason: str, before: bytes, after: bytes) -> list[str]:
    if before == after:
        return []
    invalidated: list[str] = []
    reader = run_dir / "reader.json"
    reader_backup = run_dir / "reader.before-length-heal.json"
    if reader.exists():
        if reader_backup.exists():
            reader.unlink()
        else:
            reader.replace(reader_backup)
        invalidated.append("reader.json")
    annotation_dir = run_dir / "agents" / "annotations"
    annotation_backup = run_dir / "agents" / "annotations.before-length-heal"
    if annotation_dir.exists():
        if annotation_backup.exists():
            shutil.rmtree(annotation_dir)
        else:
            annotation_dir.replace(annotation_backup)
        invalidated.append("agents/annotations")
    annotation_report = run_dir / "annotation-report.json"
    annotation_report_backup = run_dir / "annotation-report.before-length-heal.json"
    if annotation_report.exists():
        if annotation_report_backup.exists():
            annotation_report.unlink()
        else:
            annotation_report.replace(annotation_report_backup)
        invalidated.append("annotation-report.json")
    report_path = run_dir / "report.json"
    report = _json(report_path)
    report["length_healing"] = {
        "changed_at": datetime.now(timezone.utc).isoformat(), "reason": reason,
        "before_sha256": hashlib.sha256(before).hexdigest(),
        "after_sha256": hashlib.sha256(after).hexdigest(),
        "annotations_invalidated": invalidated,
        "annotation_rerun_required": True,
    }
    _atomic_json(report_path, report)
    return invalidated


def preserve_before_length_heal(run_dir: Path, chapter: bytes) -> None:
    """Retain the first accepted prose/report before any healing rewrite."""
    chapter_backup = run_dir / "chapter.before-length-heal.txt"
    report_backup = run_dir / "report.before-length-heal.json"
    if not chapter_backup.exists():
        with tempfile.NamedTemporaryFile(dir=run_dir, delete=False) as handle:
            handle.write(chapter)
            temporary = Path(handle.name)
        temporary.replace(chapter_backup)
    if not report_backup.exists():
        report = (run_dir / "report.json").read_bytes()
        with tempfile.NamedTemporaryFile(dir=run_dir, delete=False) as handle:
            handle.write(report)
            temporary = Path(handle.name)
        temporary.replace(report_backup)


def heal_lengths(
    run_roots: list[Path], level_roots: dict[str, Path], source_dir: Path,
    expected_chapters: int = 120, max_rounds: int = 6,
    runner: Runner = _default_runner,
) -> list[dict[str, Any]]:
    require_idle(run_roots)
    records: list[dict[str, Any]] = []
    for round_number in range(1, max_rounds + 1):
        failures = length_violations(run_roots, expected_chapters)
        if not failures:
            return records
        # One deficient upper level per chapter per round prevents conflicting
        # concurrent rewrites and naturally propagates increases upward.
        targets: dict[tuple[int, str], dict[str, Any]] = {}
        for failure in failures:
            targets.setdefault((failure["chapter"], failure["upper"]), failure)
        for (number, level), failure in sorted(targets.items()):
            run_dir = level_roots[level] / f"chapter_{number:03d}-{level}"
            chapter_path = run_dir / "chapter.txt"
            before = chapter_path.read_bytes()
            preserve_before_length_heal(run_dir, before)
            margin = max(100, failure["lower_cjk"] // 10)
            # ChapterHarness accepts output down to int(0.7 * target). Pick a
            # target whose *minimum accepted output* clears the lower level;
            # otherwise a perfectly gate-compliant rewrite can remain shorter
            # forever and exhaust every healing round.
            target = max(
                math.ceil((failure["lower_cjk"] + margin) / 0.7),
                math.ceil(failure["upper_cjk"] * 1.25),
            )
            while int(target * 0.7) <= failure["lower_cjk"]:
                target += 1
            command = [
                sys.executable, "-m", "pipeline.agent_harness", "run",
                "--source", str((source_dir / f"chapter_{number:03d}.txt").resolve()),
                "--level", level, "--run-id", f"{level_roots[level].name}/{run_dir.name}",
                "--runs-dir", str(level_roots[level].parent), "--target-chars", str(target),
                "--concurrency", "3", "--max-repairs", "3", "--final-effort", "xhigh",
                "--skip-annotations", "--refresh",
            ]
            code = runner(command)
            if code != 0:
                records.append({"round": round_number, **failure,
                                "target_cjk": target, "return_code": code})
                continue
            after = chapter_path.read_bytes()
            invalidated = invalidate_annotations(
                run_dir, f"restore strict {failure['lower']}<{level} length", before, after
            )
            records.append({"round": round_number, **failure,
                            "target_cjk": target, "return_code": code,
                            "annotations_invalidated": invalidated})
    remaining = length_violations(run_roots, expected_chapters)
    if remaining:
        raise FinalizerError(f"length healing exhausted with {len(remaining)} violations")
    return records


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("mode", choices=("preflight", "length"))
    result.add_argument("--run-dir", action="append", required=True)
    result.add_argument("--level-root", action="append", default=[])
    result.add_argument("--source-dir", default=str(ROOT / "books/chinese/sanguoyanyi"))
    result.add_argument("--expected-chapters", type=int, default=120)
    result.add_argument("--recover", action="store_true")
    result.add_argument("--heal", action="store_true")
    result.add_argument("--max-rounds", type=int, default=6)
    result.add_argument("--report")
    return result


def main() -> int:
    args = parser().parse_args()
    roots = [Path(value).resolve() for value in args.run_dir]
    try:
        if args.expected_chapters == 120:
            try:
                verify_source_manifest(Path(args.source_dir).resolve() / "manifest.json")
            except (ExtractionError, OSError, KeyError, TypeError) as exc:
                raise FinalizerError(
                    f"source corpus completeness verification failed: {exc}"
                ) from exc
        payload: dict[str, Any]
        if args.mode == "preflight":
            payload = preflight(roots, args.expected_chapters)
            if payload["status"] != "complete" and args.recover:
                level_roots = parse_level_roots(args.level_root)
                payload["recovery"] = recover_incomplete(
                    payload, level_roots, Path(args.source_dir).resolve())
                payload["after_recovery"] = preflight(roots, args.expected_chapters)
                payload["status"] = payload["after_recovery"]["status"]
        else:
            if args.heal:
                payload = {"status": "complete", "healing": heal_lengths(
                    roots, parse_level_roots(args.level_root),
                    Path(args.source_dir).resolve(), args.expected_chapters,
                    args.max_rounds)}
            else:
                failures = length_violations(roots, args.expected_chapters)
                payload = {"status": "complete" if not failures else "blocked",
                           "violations": failures}
        if args.report:
            _atomic_json(Path(args.report).resolve(), payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["status"] == "complete" else 2
    except (FinalizerError, OSError) as exc:
        print(f"finalizer gate failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
