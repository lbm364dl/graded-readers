#!/usr/bin/env python3
"""Source-grounded neighboring-chapter audit and autonomous repair.

This is deliberately a post-generation pass.  It reads accepted ``chapter.txt``
files, compares every adjacent pair with the exact corresponding source files,
and regenerates only chapters named by concrete findings.  All calls and review
artifacts are durable through :class:`CodexRunner`.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any

from pipeline.agent_harness import CodexRunner, ROOT, SCHEMAS, cjk_count, utc_now
from pipeline.adaptation_policy import policy_for


CHAPTER_BACKUP = "chapter.pre-continuity.txt"
REPORT_BACKUP = "report.pre-continuity.json"


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    temporary.replace(path)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def promote_continuity_candidate(run_dir: Path, candidate: str) -> dict[str, Any]:
    """Safely promote a reviewed candidate and invalidate stale annotations."""
    chapter_path = run_dir / "chapter.txt"
    report_path = run_dir / "report.json"
    manifest_path = run_dir / "manifest.json"
    original = chapter_path.read_bytes()
    candidate_bytes = candidate.encode("utf-8")
    if not candidate_bytes.strip():
        raise ValueError(f"refusing to promote empty continuity candidate: {run_dir}")
    # Validate all authoritative metadata before changing any artifact.
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError(f"expected report object: {report_path}")
    manifest_before = manifest_path.read_bytes()

    chapter_backup = run_dir / CHAPTER_BACKUP
    report_backup = run_dir / REPORT_BACKUP
    if not chapter_backup.exists():
        _atomic_bytes(chapter_backup, original)
    if not report_backup.exists():
        _atomic_bytes(report_backup, report_path.read_bytes())

    invalidated: list[str] = []
    reader = run_dir / "reader.json"
    reader_backup = run_dir / "reader.pre-continuity.json"
    if reader.exists():
        if not reader_backup.exists():
            reader.replace(reader_backup)
        else:
            reader.unlink()
        invalidated.append("reader.json")
    annotations = run_dir / "agents" / "annotations"
    annotations_backup = run_dir / "agents" / "annotations.pre-continuity"
    if annotations.exists():
        if not annotations_backup.exists():
            annotations.replace(annotations_backup)
        else:
            shutil.rmtree(annotations)
        invalidated.append("agents/annotations")

    _atomic_bytes(chapter_path, candidate_bytes)
    report["chapter_cjk"] = cjk_count(candidate)
    report["continuity_promotion"] = {
        "promoted_at": utc_now(),
        "candidate_sha256": _sha256(candidate_bytes),
        "original_backup": str(chapter_backup.resolve()),
        "original_sha256": _sha256(chapter_backup.read_bytes()),
        "annotations_invalidated": invalidated,
        "annotation_rerun_required": True,
    }
    _atomic_bytes(
        report_path,
        (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    if manifest_path.read_bytes() != manifest_before:
        raise RuntimeError(f"continuity promotion changed manifest provenance: {run_dir}")
    return report["continuity_promotion"]


@dataclass(frozen=True)
class Chapter:
    number: int
    source_path: Path
    run_dir: Path

    @property
    def source(self) -> str:
        return self.source_path.read_text(encoding="utf-8")

    @property
    def text(self) -> str:
        return (self.run_dir / "chapter.txt").read_text(encoding="utf-8")


def discover_chapters(sources: list[str], book_run_dir: Path, level: str) -> list[Chapter]:
    paths = sorted(Path(item).resolve() for item in sources)
    chapters = [
        Chapter(i, path, book_run_dir / f"{path.stem}-{level}")
        for i, path in enumerate(paths, 1)
    ]
    missing = [str(ch.run_dir / "chapter.txt") for ch in chapters if not (ch.run_dir / "chapter.txt").is_file()]
    if missing:
        raise FileNotFoundError("accepted chapter missing: " + ", ".join(missing[:5]))
    return chapters


def implicated_chapters(audits: dict[int, dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Map pair-local findings to zero-based absolute chapter indexes."""
    result: dict[int, list[dict[str, Any]]] = {}
    for left, audit in audits.items():
        if audit.get("verdict") == "pass":
            continue
        for issue in audit.get("issues", []):
            index = left + issue["chapter_offset"]
            result.setdefault(index, []).append({"pair_start": left + 1, **issue})
    return result


class ContinuityHarness:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.book_run_dir = Path(args.book_run_dir).resolve()
        self.run_dir = self.book_run_dir / "continuity" / args.level
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.chapters = discover_chapters(args.source, self.book_run_dir, args.level)
        self.runner = CodexRunner(
            self.run_dir, args.model, asyncio.Semaphore(args.concurrency), args.timeout
        )

    async def audit_pair(self, left: int, texts: list[str], stage: str) -> dict[str, Any]:
        a, b = self.chapters[left], self.chapters[left + 1]
        policy = policy_for(self.args.level)
        source_context = ""
        if policy.audit_source_omissions:
            source_context = f"""
SOURCE CHAPTER {a.number}:
{a.source}

SOURCE CHAPTER {b.number}:
{b.source}
"""
        prompt = f"""Return only JSON matching the schema. Independently audit the
handoff between two adjacent chapters of an abridged {self.args.level.upper()}
reader. {policy.continuity} {policy.fidelity}
Compression, merging, and intentional omission are expected. Report only material
cross-chapter problems: contradictions, accidental repetition, broken identity
or timeline, lost causal/motivational handoff, or a transition so abrupt that
the second chapter is confusing. Never demand an event merely because it exists
in the original. chapter_offset=0 identifies the first adapted chapter;
chapter_offset=1 identifies the second. verdict=pass iff no material issue exists.

ACCEPTED CHAPTER {a.number}:
{texts[left]}

ACCEPTED CHAPTER {b.number}:
{texts[left + 1]}
{source_context}"""
        return await self.runner.call(
            f"{stage}/pair_{a.number:03d}_{b.number:03d}", prompt,
            SCHEMAS / "continuity-review.schema.json", self.args.review_effort,
            refresh=self.args.refresh,
        )

    async def audit(self, texts: list[str], stage: str, pairs: set[int] | None = None) -> dict[int, dict[str, Any]]:
        indexes = sorted(pairs if pairs is not None else range(len(self.chapters) - 1))
        values = await asyncio.gather(*(self.audit_pair(i, texts, stage) for i in indexes))
        return dict(zip(indexes, values))

    async def repair(self, index: int, text: str, texts: list[str], issues: list[dict[str, Any]], round_no: int) -> str:
        chapter = self.chapters[index]
        before = texts[index - 1] if index else "(none: this is the first chapter)"
        after = texts[index + 1] if index + 1 < len(texts) else "(none: this is the last chapter)"
        prompt = f"""Return only JSON matching the adaptation schema, with the
complete revised chapter in `text`. Repair only the concrete continuity findings.
Ground every fact in VERBATIM SOURCE. Preserve the accepted chapter's good prose,
{self.args.level.upper()} readability, approximate length, and all unaffected
events. Neighbor text is context, never authority for changing source facts.
Use simplified Chinese. Do not add headings or commentary.

VERBATIM SOURCE CHAPTER {chapter.number}:
{chapter.source}

PREVIOUS ACCEPTED CHAPTER:
{before}

CURRENT ACCEPTED CHAPTER:
{text}

NEXT ACCEPTED CHAPTER:
{after}

FINDINGS:
{json.dumps(issues, ensure_ascii=False, indent=2)}"""
        result = await self.runner.call(
            f"round_{round_no:02d}/chapter_{chapter.number:03d}/repair", prompt,
            SCHEMAS / "adaptation.schema.json", self.args.repair_effort,
            refresh=self.args.refresh,
        )
        return result["text"].strip() + "\n"

    async def source_review(self, index: int, candidate: str, round_no: int) -> dict[str, Any]:
        chapter = self.chapters[index]
        policy = policy_for(self.args.level)
        prompt = f"""Return only JSON matching the schema. Compare CANDIDATE
directly against VERBATIM ORIGINAL. It is a compact modern-Chinese adaptation,
Editorial scope: {policy.scope}
Fidelity rule: {policy.fidelity}
Intentional level-appropriate omissions are expected. Identify invented facts,
distortions, broken causal links within the selected retelling, awkward Chinese, or
traditional-only characters. Set verdict=pass only when source_fidelity,
naturalness, and readability are all at least 8 and there is no material issue.
Score fidelity by whether retained claims are true, not by source coverage.

ORIGINAL:
{chapter.source}

CANDIDATE:
{candidate}"""
        return await self.runner.call(
            f"round_{round_no:02d}/chapter_{chapter.number:03d}/source_review", prompt,
            SCHEMAS / "source-review.schema.json", self.args.review_effort,
            refresh=self.args.refresh,
        )

    def write_report(self, payload: dict[str, Any]) -> None:
        payload = {**payload, "updated_at": utc_now()}
        (self.run_dir / "continuity-report.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    async def run(self) -> dict[str, Any]:
        texts = [chapter.text for chapter in self.chapters]
        initial = await self.audit(texts, "initial")
        audits = initial
        rounds: list[dict[str, Any]] = []
        for round_no in range(1, self.args.max_repair_rounds + 1):
            implicated = implicated_chapters(audits)
            if not implicated:
                break
            indexes = sorted(implicated)
            candidates = await asyncio.gather(*(
                self.repair(i, texts[i], texts, implicated[i], round_no) for i in indexes
            ))
            source_reviews = await asyncio.gather(*(
                self.source_review(i, candidate, round_no)
                for i, candidate in zip(indexes, candidates)
            ))
            trial = texts.copy()
            source_pass = {}
            for i, candidate, review in zip(indexes, candidates, source_reviews):
                source_pass[i] = review["verdict"] == "pass"
                if source_pass[i]:
                    trial[i] = candidate
            affected_pairs = {
                pair for i in indexes for pair in (i - 1, i)
                if 0 <= pair < len(self.chapters) - 1
            }
            post = await self.audit(trial, f"round_{round_no:02d}/post", affected_pairs)
            still_implicated = implicated_chapters(post)
            promoted = []
            promotion_records: dict[str, dict[str, Any]] = {}
            for i in indexes:
                touching = {pair for pair in (i - 1, i) if pair in affected_pairs}
                if source_pass[i] and all(post[p]["verdict"] == "pass" for p in touching):
                    promotion_records[str(i + 1)] = promote_continuity_candidate(
                        self.chapters[i].run_dir, trial[i]
                    )
                    texts[i] = trial[i]
                    promoted.append(i)
            rounds.append({
                "round": round_no, "implicated": [i + 1 for i in indexes],
                "source_pass": {str(i + 1): value for i, value in source_pass.items()},
                "promoted": [i + 1 for i in promoted],
                "promotion_records": promotion_records,
                "remaining_in_affected_pairs": [i + 1 for i in sorted(still_implicated)],
            })
            # Re-audit the entire book after promotion. This is cheap enough at
            # 119 pair calls, parallel, and prevents local fixes hiding a new edge.
            audits = await self.audit(texts, f"round_{round_no:02d}/full")
            self.write_report({"status": "running", "rounds": rounds})

        remaining = implicated_chapters(audits)
        result = {
            "status": "complete" if not remaining else "blocked",
            "level": self.args.level, "chapters": len(self.chapters),
            "pairs": max(0, len(self.chapters) - 1),
            "initial_failed_pairs": [i + 1 for i, value in initial.items() if value["verdict"] != "pass"],
            "remaining_chapters": [i + 1 for i in sorted(remaining)],
            "rounds": rounds,
            "chapter_cjk": [cjk_count(text) for text in texts],
        }
        self.write_report(result)
        return result


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", action="append", required=True)
    p.add_argument("--book-run-dir", required=True)
    p.add_argument("--level", required=True)
    p.add_argument("--model", default="gpt-5.6-luna")
    p.add_argument("--review-effort", default="medium")
    p.add_argument("--repair-effort", default="xhigh")
    p.add_argument("--concurrency", type=int, default=16)
    p.add_argument("--max-repair-rounds", type=int, default=2)
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--refresh", action="store_true")
    return p


def main() -> int:
    result = asyncio.run(ContinuityHarness(parser().parse_args()).run())
    return 0 if result["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
