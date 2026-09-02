#!/usr/bin/env python3
"""Annotate accepted Chinese chapters without ever regenerating their prose."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any

from pipeline.agent_harness import (
    CHINESE_ANNOTATION_POLICY_VERSION, ChapterHarness, CodexRunner,
    DEFAULT_CHINESE_ANNOTATION_CHUNK_MAXIMUM as DEFAULT_ANNOTATION_CHUNK_MAXIMUM,
    DEFAULT_CHINESE_ANNOTATION_CHUNK_TARGET as DEFAULT_ANNOTATION_CHUNK_TARGET,
    split_chinese_annotation_chunks,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    """Replace a JSON artifact atomically after its complete payload exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def discover_complete_chapters(roots: list[Path]) -> list[Path]:
    """Find accepted generation runs while rejecting duplicate directories."""
    found: dict[Path, Path] = {}
    for root in roots:
        root = root.resolve()
        if not root.is_dir():
            raise ValueError(f"book run root does not exist: {root}")
        candidates = [root] if (root / "chapter.txt").is_file() else list(root.iterdir())
        for candidate in candidates:
            if not candidate.is_dir() or not (candidate / "chapter.txt").is_file():
                continue
            manifest_path, report_path = candidate / "manifest.json", candidate / "report.json"
            if not manifest_path.is_file() or not report_path.is_file():
                continue
            manifest, report = load_object(manifest_path), load_object(report_path)
            if manifest.get("status") != "complete" or report.get("status") != "complete":
                continue
            resolved = candidate.resolve()
            if resolved in found:
                continue
            found[resolved] = candidate
    return sorted(found)


def reusable_reader(
    path: Path, chapter: str, expected_level: str,
    expected_mode: str | None = None, expected_review_policy: str | None = None,
    expected_policy_version: str | None = None,
    expected_focus_vocabulary_sha256: str | None = None,
) -> dict[str, Any] | None:
    """Return a reader only when every deterministic reuse gate passes."""
    if not path.is_file():
        return None
    try:
        reader = load_object(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if reader.get("text") != chapter or reader.get("level") != expected_level:
        return None
    segments = reader.get("segments")
    audit = reader.get("annotation_audit")
    if not isinstance(segments, list) or not segments:
        return None
    if any(not isinstance(segment, dict) for segment in segments):
        return None
    if not ChapterHarness.annotation_reconstructs(chapter, reader):
        return None
    if not isinstance(audit, dict) or audit.get("all_reviewed") is not True:
        return None
    if expected_mode is not None and audit.get("mode") != expected_mode:
        return None
    if (expected_review_policy is not None
            and audit.get("review_policy") != expected_review_policy):
        return None
    if (expected_policy_version is not None
            and audit.get("policy_version") != expected_policy_version):
        return None
    if (expected_focus_vocabulary_sha256 is not None
            and audit.get("focus_vocabulary_sha256")
            != expected_focus_vocabulary_sha256):
        return None
    return reader


class ChineseAnnotationHarness(ChapterHarness):
    """Reuse the reviewed Chinese annotation ladder, never the prose stages."""

    def __init__(
        self, run_dir: Path, args: argparse.Namespace, semaphore: asyncio.Semaphore
    ) -> None:
        # Deliberately do not call ChapterHarness.__init__: it owns adaptation
        # source and run creation. Annotation-only mode owns an existing,
        # already accepted chapter directory.
        self.run_dir = run_dir.resolve()
        manifest = load_object(self.run_dir / "manifest.json")
        # Every chapter gets a private namespace: a combined six-level batch
        # must not race by mutating one shared ``args.level`` value.
        self.args = argparse.Namespace(**vars(args))
        self.args.level = str(manifest.get("level", "")).lower()
        if self.args.level not in {f"hsk{number}" for number in range(1, 7)}:
            raise ValueError(f"invalid annotation level in {self.run_dir / 'manifest.json'}")
        focus_path = self.run_dir / "focus-vocabulary-plan.json"
        self.focus_vocabulary_sha256 = (
            sha256_bytes(focus_path.read_bytes()) if focus_path.is_file() else None
        )
        self.focus_vocabulary = (
            load_object(focus_path) if focus_path.is_file()
            else {"names": [], "story_terms": []}
        )
        self.runner = CodexRunner(
            self.run_dir, self.args.model, semaphore, self.args.timeout
        )

    async def annotate_accepted_chapter(self) -> dict[str, Any]:
        chapter_path = self.run_dir / "chapter.txt"
        original_bytes = chapter_path.read_bytes()
        chapter_hash = sha256_bytes(original_bytes)
        chapter = original_bytes.decode("utf-8")
        # Salt every generation and review prompt with the exact full chapter,
        # not merely its current chunk. Any continuity edit therefore
        # invalidates every stale annotation job in this chapter.
        self.runner = ChapterFingerprintRunner(self.runner, chapter_hash)
        manifest = load_object(self.run_dir / "manifest.json")
        outline_path = self.run_dir / "outline.json"
        outline = load_object(outline_path) if outline_path.exists() else {}
        prior_reader_path = self.run_dir / "reader.json"
        prior_reader_hash = (
            sha256_bytes(prior_reader_path.read_bytes()) if prior_reader_path.exists() else None
        )
        annotation_report_path = self.run_dir / "annotation-report.json"
        expected_level = str(manifest.get("level", "")).upper()
        if not self.args.refresh:
            reusable = reusable_reader(
                prior_reader_path,
                chapter,
                expected_level,
                expected_policy_version=(
                    CHINESE_ANNOTATION_POLICY_VERSION
                    if self.args.annotation_mode == "constrained-delta" else None
                ),
                expected_focus_vocabulary_sha256=(
                    self.focus_vocabulary_sha256
                    if self.args.annotation_mode == "constrained-delta" else None
                ),
            )
            if (reusable is not None
                and self.args.annotation_mode == "constrained-delta" and (
                reusable.get("annotation_audit", {}).get("mode")
                != self.args.annotation_mode
                or reusable.get("annotation_audit", {}).get("review_policy")
                != self.args.annotation_review_policy
            )):
                reusable = None
            if reusable is not None:
                report = {
                    "status": "complete", "reused": True,
                    "chapter_sha256": chapter_hash,
                    "reader_sha256_before": prior_reader_hash,
                    "reader_sha256": prior_reader_hash,
                    "reader_replaced": False,
                    "chunks": reusable["annotation_audit"].get("chunks"),
                    "attempts": 0, "completed_at": utc_now(),
                }
                atomic_json(annotation_report_path, report)
                return {"run_dir": str(self.run_dir), **report}
        atomic_json(annotation_report_path, {
            "status": "running", "chapter_sha256": chapter_hash,
            "started_at": utc_now(), "reader_sha256_before": prior_reader_hash,
        })
        try:
            chunks = self.chinese_annotation_chunks(chapter)
            if self.args.annotation_mode == "constrained-delta":
                annotated = await self.annotate_chapter_constrained_delta(chapter, chunks)
            else:
                annotated = await asyncio.gather(*(
                    self.annotate_chunk(index, chunk)
                    for index, chunk in enumerate(chunks)
                ))
            segments = [segment for item in annotated for segment in item["segments"]]
            if "".join(segment["text"] for segment in segments) != chapter:
                raise ValueError("assembled annotations do not reconstruct chapter")
            grammar_overlays = []
            offset = 0
            for chunk, item in zip(chunks, annotated):
                for overlay in item["grammar_overlays"]:
                    grammar_overlays.append({
                        **overlay, "start": overlay["start"] + offset,
                        "end": overlay["end"] + offset,
                    })
                offset += len(chunk)
            # Guard against continuity repair or any other concurrent editor.
            if chapter_path.read_bytes() != original_bytes:
                raise RuntimeError("chapter.txt changed during annotation; refusing stale reader")
            reader = {
                "title": str(outline.get("chapter_title", self.run_dir.name)),
                "level": expected_level,
                "text": chapter, "segments": segments,
                "grammar_overlays": grammar_overlays,
                "annotation_audit": {
                    "mode": self.args.annotation_mode,
                    "review_policy": self.args.annotation_review_policy,
                    "policy_version": CHINESE_ANNOTATION_POLICY_VERSION,
                    "focus_vocabulary_sha256": self.focus_vocabulary_sha256,
                    "chapter_sha256": chapter_hash,
                    "chunks": len(annotated),
                    "attempts_per_chunk": [len(item["attempts"]) for item in annotated],
                    "all_reviewed": all(
                        item["resolved"] and item.get("reviewed", True) is True
                        and (item["attempts"][-1].get("review", {}).get("verdict") == "pass"
                             or item.get("acceptance_state") == "reviewed_and_remediated")
                        for item in annotated
                    ),
                    **({
                        "metadata_model_calls": getattr(
                            self, "annotation_metadata_model_calls", 1
                        ),
                        "semantic_review_calls": getattr(
                            self, "annotation_semantic_review_calls", 0
                        ),
                        "duplicate_override_collapses": getattr(
                            self, "annotation_duplicate_override_collapses", 0
                        ),
                        "metadata_batches": getattr(
                            self, "annotation_metadata_batches", 1
                        ),
                        "metadata_completion_calls": getattr(
                            self, "annotation_metadata_completion_calls", 0
                        ),
                        "boundary_override_drops": getattr(
                            self, "annotation_boundary_override_drops", 0
                        ),
                        "acceptance_state": getattr(
                            self, "annotation_acceptance_state", "reviewed_pass"
                        ),
                        "final_review_verdict": getattr(
                            self, "annotation_final_review_verdict", "pass"
                        ),
                        "remediation_calls": getattr(
                            self, "annotation_remediation_calls", 0
                        ),
                        "boundary_normalization": getattr(
                            self, "annotation_boundary_normalization", {
                                "discarded_count": 0, "duplicate_collapses": 0,
                                "discarded": [],
                            }
                        ),
                        "override_normalization": getattr(
                            self, "annotation_override_normalization", {
                                "discarded_count": 0, "duplicate_collapses": 0,
                                "discarded": [],
                            }
                        ),
                        "overlay_normalization": getattr(
                            self, "annotation_overlay_normalization", {
                                "discarded_count": 0, "duplicate_collapses": 0,
                                "discarded": [],
                            }
                        ),
                    } if self.args.annotation_mode == "constrained-delta" else {}),
                },
            }
            atomic_json(prior_reader_path, reader)
            if chapter_path.read_bytes() != original_bytes:
                raise RuntimeError("chapter.txt changed while publishing reader")
            report = {
                "status": "complete", "reused": False,
                "chapter_sha256": chapter_hash,
                "reader_sha256_before": prior_reader_hash,
                "reader_sha256": sha256_bytes(prior_reader_path.read_bytes()),
                "reader_replaced": prior_reader_hash is not None,
                "chunks": len(annotated),
                "attempts": sum(len(item["attempts"]) for item in annotated),
                "annotation_mode": self.args.annotation_mode,
                "review_policy": self.args.annotation_review_policy,
                **({
                    "metadata_model_calls": getattr(
                        self, "annotation_metadata_model_calls", 1
                    ),
                    "semantic_review_calls": getattr(
                        self, "annotation_semantic_review_calls", 0
                    ),
                } if self.args.annotation_mode == "constrained-delta" else {}),
                "completed_at": utc_now(),
            }
            atomic_json(annotation_report_path, report)
            return {"run_dir": str(self.run_dir), **report}
        except BaseException as exc:
            # Generation manifest/report and chapter prose remain untouched.
            atomic_json(annotation_report_path, {
                "status": "failed", "chapter_sha256": chapter_hash,
                "reader_sha256_before": prior_reader_hash,
                "error": f"{type(exc).__name__}: {exc}", "failed_at": utc_now(),
            })
            raise


class ChapterFingerprintRunner:
    """Add immutable chapter identity to CodexRunner's prompt fingerprint."""

    def __init__(self, runner: Any, chapter_sha256: str) -> None:
        self.runner = runner
        self.chapter_sha256 = chapter_sha256

    async def call(self, job: str, prompt: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        salted = f"IMMUTABLE CHAPTER SHA-256: {self.chapter_sha256}\n\n{prompt}"
        return await self.runner.call(job, salted, *args, **kwargs)


@dataclass
class AnnotationBatch:
    args: argparse.Namespace

    async def run(self) -> dict[str, Any]:
        roots = [Path(path).resolve() for path in self.args.run_dir]
        chapters = discover_complete_chapters(roots)
        if not chapters:
            raise ValueError("no complete accepted chapter runs found")
        semaphore = asyncio.Semaphore(self.args.concurrency)
        chapter_semaphore = asyncio.Semaphore(self.args.chapter_concurrency)

        async def one(path: Path) -> dict[str, Any]:
            async with chapter_semaphore:
                try:
                    return await ChineseAnnotationHarness(
                        path, self.args, semaphore
                    ).annotate_accepted_chapter()
                except Exception as exc:
                    return {
                        "run_dir": str(path), "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }

        batch_report_path = Path(self.args.batch_report).resolve() if self.args.batch_report else roots[0] / "annotation-batch-report.json"
        atomic_json(batch_report_path, {
            "status": "running", "started_at": utc_now(),
            "chapters": len(chapters), "runs": [],
        })
        tasks = [asyncio.create_task(one(path)) for path in chapters]
        results = []
        for task in asyncio.as_completed(tasks):
            results.append(await task)
            atomic_json(batch_report_path, {
                "status": "running", "updated_at": utc_now(),
                "chapters": len(chapters), "runs": results,
            })
        status = "complete" if all(x["status"] == "complete" for x in results) else "blocked"
        report = {
            "status": status, "completed_at": utc_now(),
            "chapters": len(chapters), "complete": sum(x["status"] == "complete" for x in results),
            "failed": sum(x["status"] != "complete" for x in results),
            "runs": sorted(results, key=lambda x: x["run_dir"]),
        }
        atomic_json(batch_report_path, report)
        return report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--run-dir", action="append", required=True,
                        help="book run root; repeat to combine roots")
    result.add_argument("--batch-report")
    result.add_argument("--model", default="gpt-5.6-luna")
    result.add_argument("--concurrency", type=int, default=8,
                        help="global maximum simultaneous model calls")
    result.add_argument("--chapter-concurrency", type=int, default=8)
    result.add_argument("--timeout", type=int, default=900)
    result.add_argument(
        "--annotation-chunk", type=int,
        default=DEFAULT_ANNOTATION_CHUNK_TARGET,
        help="soft validated target; chunks pack whole sentences up to --annotation-chunk-maximum",
    )
    result.add_argument(
        "--annotation-chunk-maximum", type=int,
        default=None,
        help="hard sentence-preserving ceiling (default 400, selected from correction-convergence smoke)",
    )
    result.add_argument("--annotation-effort", default="low")
    result.add_argument(
        "--annotation-mode",
        choices=("generative", "constrained", "constrained-delta"),
        default="constrained-delta",
    )
    result.add_argument(
        "--annotation-review-policy",
        choices=("chapter", "exhaustive-chunks"), default="chapter",
    )
    result.add_argument("--annotation-review-effort", default="low")
    result.add_argument("--annotation-repair-effort", default="low")
    result.add_argument("--annotation-final-effort", default="low")
    result.add_argument("--annotation-metadata-batch-targets", type=int, default=150)
    result.add_argument("--max-annotation-repairs", type=int, default=2)
    result.add_argument("--refresh", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    report = asyncio.run(AnnotationBatch(args).run())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
