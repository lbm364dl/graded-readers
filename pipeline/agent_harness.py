#!/usr/bin/env python3
"""Resumable, source-grounded agent harness for graded-reader books."""

from __future__ import annotations

import argparse
import asyncio
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
from typing import Any

from jsonschema import Draft202012Validator
from opencc import OpenCC


ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = Path(__file__).resolve().parent / "schemas"
DEFAULT_RUNS = ROOT / "runs" / "graded-readers"
DEFAULT_LEVEL_TARGETS = {
    "hsk1": 140, "hsk2": 300, "hsk3": 500,
    "hsk4": 750, "hsk5": 1050, "hsk6": 1450,
}
_T2S = OpenCC("t2s")


def simplified(text: str) -> str:
    """Deterministically normalize model prose before any review or storage."""
    return _T2S.convert(text)


def apply_compact_review_policy(review: dict[str, Any]) -> dict[str, Any]:
    """Do not heal away intentional compression of nonessential details.

    A reviewer may emit ``revise`` while giving every quality dimension >= 8
    and listing only omissions it describes as minor/background. With no
    additions, distortions, or language problems, that is a passing compact
    adaptation under the editorial policy.
    """
    if review.get("verdict") == "pass":
        # Passed reviews may contain hedged/minor notes. Their materiality is
        # audited source-groundedly by audit_compact_omissions instead of
        # forcing broad regeneration here.
        return review
    scores = (
        review.get("source_fidelity", 0), review.get("naturalness", 0),
        review.get("readability", 0),
    )
    if (
        min(scores) >= 8
        and not review.get("omissions")
        and not review.get("unsupported_additions")
        and not review.get("distortions")
        and not review.get("language_problems")
    ):
        result = dict(review)
        result["reviewer_verdict"] = review["verdict"]
        result["verdict"] = "pass"
        result["harness_decision"] = "accepted_omission_only_compact_adaptation"
        return result
    return review


def discard_incorrect_length_findings(review: dict[str, Any]) -> dict[str, Any]:
    """Remove qualitative length complaints after the mechanical gate passes.

    Models are poor character counters and occasionally claim that an in-range
    adaptation is two or three times its target.  Only findings whose text is
    explicitly about length are removed; factual and language findings remain.
    """
    result = dict(review)
    markers = (
        "篇幅", "字数", "字符", "长度", "目标字", "target", "length",
        "文字数", "分量", "長さ",
    )
    for key in (
        "omissions", "unsupported_additions", "distortions", "language_problems"
    ):
        result[key] = [
            finding for finding in result.get(key, [])
            if not any(marker.lower() in str(finding).lower() for marker in markers)
        ]
    return result


def length_violations(runs: list[dict[str, Any]], levels: list[str]) -> list[dict[str, Any]]:
    """Return adjacent-level violations for every source with complete results."""
    order = {level: index for index, level in enumerate(levels)}
    by_source: dict[str, list[dict[str, Any]]] = {}
    for item in runs:
        if item.get("status") == "complete" and "chapter_cjk" in item:
            by_source.setdefault(item["source"], []).append(item)
    problems = []
    for source, items in by_source.items():
        items.sort(key=lambda item: order[item["level"]])
        for lower, upper in zip(items, items[1:]):
            if lower["chapter_cjk"] >= upper["chapter_cjk"]:
                problems.append({
                    "source": source, "lower_level": lower["level"],
                    "lower_cjk": lower["chapter_cjk"],
                    "upper_level": upper["level"], "upper_cjk": upper["chapter_cjk"],
                })
    return problems


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def gather_all_or_raise(*awaitables):
    """Drain every sibling operation before propagating the first failure.

    Cancelling a coroutine that is awaiting a model subprocess does not
    reliably terminate the subprocess group.  Plain ``asyncio.gather`` also
    returns immediately on the first exception while siblings keep consuming
    the shared semaphore.  Draining preserves useful resumable results and
    prevents a retry from overlapping its predecessor.
    """
    results = await asyncio.gather(*awaitables, return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return results


def digest(*values: str) -> str:
    h = hashlib.sha256()
    for value in values:
        h.update(value.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def cjk_count(text: str) -> int:
    return sum("\u4e00" <= char <= "\u9fff" for char in text)


def run_status_for_verdicts(verdicts: dict[str, str]) -> str:
    return (
        "complete" if verdicts and all(value == "pass" for value in verdicts.values())
        else "blocked"
    )


def publish_chapter_candidate(
    run_dir: Path, chapter: str, final_status: str
) -> Path:
    """Publish exactly one authoritative prose artifact, failing closed.

    A run directory is resumable and may already contain output from an older
    attempt.  Leaving that accepted ``chapter.txt`` in place after a newer
    attempt is blocked lets downstream tools consume prose which no longer
    matches ``report.json``.  Likewise, a reader is invalid as soon as its
    underlying chapter is replaced.  Keep the accepted and rejected states
    mutually exclusive and force annotations to be regenerated from the new
    prose.
    """
    accepted = run_dir / "chapter.txt"
    rejected = run_dir / "chapter-candidate.txt"
    reader = run_dir / "reader.json"
    if final_status == "complete":
        rejected.unlink(missing_ok=True)
        destination = accepted
    else:
        accepted.unlink(missing_ok=True)
        destination = rejected
    reader.unlink(missing_ok=True)
    destination.write_text(chapter, encoding="utf-8")
    return destination


def split_annotation_chunks(text: str, target: int = 200, maximum: int = 260) -> list[str]:
    """Split at sentence/paragraph boundaries without changing the text."""
    pieces = re.findall(r".*?(?:[。！？]\s*|\n+|$)", text, flags=re.S)
    pieces = [piece for piece in pieces if piece]
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) > maximum:
            chunks.append(current)
            current = ""
        if len(piece) > maximum:
            if current:
                chunks.append(current)
                current = ""
            for start in range(0, len(piece), target):
                chunks.append(piece[start : start + target])
        else:
            current += piece
            if len(current) >= target:
                chunks.append(current)
                current = ""
    if current:
        chunks.append(current)
    assert "".join(chunks) == text
    return chunks


CHINESE_ANNOTATION_CHUNK_POLICY = "sentence-safe-v2-400-cap"
DEFAULT_CHINESE_ANNOTATION_CHUNK_TARGET = 350
DEFAULT_CHINESE_ANNOTATION_CHUNK_MAXIMUM = 400


def split_chinese_annotation_chunks(
    text: str,
    target: int = DEFAULT_CHINESE_ANNOTATION_CHUNK_TARGET,
    maximum: int = DEFAULT_CHINESE_ANNOTATION_CHUNK_MAXIMUM,
) -> list[str]:
    """Minimize Chinese annotation calls while preserving whole sentences.

    The maximum reflects the correction-convergence smoke: a roughly
    200-character unit passed within two repairs, whereas a 650-character unit
    still had seven findings after two repairs. An anomalously long sentence
    fails closed rather than being cut through an unknown lexical item or
    grammar construction.
    """
    if target < 1 or maximum < target:
        raise ValueError("annotation chunk maximum must be at least target")
    if not text:
        return []
    # Keep closing quotation/bracket marks with the terminator. Otherwise a
    # boundary can fall between ``。`` and ``”``, which is not actually a
    # whole-sentence split and can bisect a deterministic punctuation segment.
    sentences = [
        item for item in re.findall(
            r".*?(?:[。！？][’”〉》」』）】〕〗〙〛]*\s*|\n+|$)",
            text,
            flags=re.S,
        )
        if item
    ]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > maximum:
            raise ValueError(
                f"Chinese annotation sentence has {len(sentence)} characters, "
                f"exceeding the safe {maximum}-character schema budget"
            )
        if current and len(current) + len(sentence) > maximum:
            chunks.append(current)
            current = ""
        current += sentence
    if current:
        chunks.append(current)
    if "".join(chunks) != text:
        raise AssertionError("Chinese annotation chunks changed source text")
    return chunks


@dataclass
class CodexRunner:
    run_dir: Path
    model: str
    semaphore: asyncio.Semaphore
    timeout: int = 900
    max_throttle_retries: int = 2
    throttle_backoff_seconds: float = 2.0
    max_launch_retries: int = 1
    max_process_timeout_retries: int = 1
    launch_timeout_seconds: float = 60.0
    launch_backoff_seconds: float = 0.25

    @staticmethod
    def _completed_timeout_result(
        result_path: Path, events_path: Path, schema_path: Path
    ) -> dict[str, Any] | None:
        """Recover a completed schema-valid result from a hung CLI wrapper.

        ``codex -o`` can durably write its final result and agent-message event
        before a descendant keeps the wrapper alive.  The launch permit still
        needs its timeout, but deleting that proven output forces an identical
        expensive retry.  A fresh result file plus an equal schema-valid final
        agent message is sufficient evidence that model work completed.
        """
        if not result_path.is_file() or not events_path.is_file():
            return None
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            events = [
                json.loads(line) for line in events_path.read_text(
                    encoding="utf-8", errors="strict"
                ).splitlines()
            ]
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(result, dict) or not Draft202012Validator(schema).is_valid(result):
            return None
        messages: list[dict[str, Any]] = []
        for event in events:
            item = event.get("item") if event.get("type") == "item.completed" else None
            if not isinstance(item, dict) or item.get("type") != "agent_message":
                continue
            try:
                value = json.loads(item.get("text", ""))
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict) and Draft202012Validator(schema).is_valid(value):
                messages.append(value)
        return result if messages and messages[-1] == result else None

    @staticmethod
    def _is_explicit_throttle(*logs: Path) -> bool:
        """Recognize only unambiguous transient capacity failures.

        Broad matches such as ``rate`` or ``temporarily unavailable`` risk
        retrying model/schema failures.  Codex currently reports capacity
        exhaustion as HTTP 429, a ThrottlerException, or the CLI's exact
        "Selected model is at capacity" message.
        """
        evidence = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in logs if path.exists()
        )
        return bool(re.search(
            r"(?:\bThrottlerException\b|\bHTTP(?:/\d(?:\.\d)?)?"
            r"(?:\s+status(?:\s+code)?\s*[:=]?)?\s*429\b|"
            r"\bstatus(?:\s+code)?\s*[:=]\s*429\b|\b429\s+Too\s+Many\s+Requests\b|"
            r"\bSelected\s+model\s+is\s+at\s+capacity\b)",
            evidence,
            flags=re.IGNORECASE,
        ))

    async def call(
        self,
        job: str,
        prompt: str,
        schema: Path,
        effort: str,
        *,
        refresh: bool = False,
    ) -> dict[str, Any]:
        job_dir = self.run_dir / "agents" / job
        job_dir.mkdir(parents=True, exist_ok=True)
        result_path = job_dir / "result.json"
        meta_path = job_dir / "meta.json"
        fingerprint = digest(prompt, schema.read_text(), self.model, effort)
        if not refresh and result_path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text())
            if meta.get("fingerprint") == fingerprint and meta.get("return_code") == 0:
                return json.loads(result_path.read_text())

        command = [
            "codex", "exec", "--json", "--ephemeral", "-s", "read-only",
            "-m", self.model, "-c", f'model_reasoning_effort="{effort}"',
            "-C", str(ROOT), "--output-schema", str(schema),
            "-o", str(result_path), "-",
        ]
        started = utc_now()
        attempts: list[dict[str, Any]] = []
        process_timeout_retries = 0
        # Write output directly to files. Some MCP descendants inherit the CLI's
        # stdout/stderr descriptors; PIPE + communicate() then waits forever for
        # descendant-held pipe EOF even after the direct codex process exits.
        proc = None
        for attempt_index in range(self.max_throttle_retries + 1):
            launch_retry = 0
            while True:
                attempt_number = len(attempts) + 1
                events_path = job_dir / f"events.attempt-{attempt_number:02d}.jsonl"
                stderr_path = job_dir / f"stderr.attempt-{attempt_number:02d}.log"
                attempt_started = utc_now()
                launch_timed_out = False
                async with self.semaphore:
                    # Keep mismatched historical evidence intact while queued.
                    # Delete it only after this job owns a launch permit; if a
                    # sibling aborts the parent, never-started jobs remain
                    # recoverable instead of being mass-unlinked.
                    result_path.unlink(missing_ok=True)
                    with events_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                        try:
                            async with asyncio.timeout(
                                min(self.launch_timeout_seconds, float(self.timeout))
                            ):
                                proc = await asyncio.create_subprocess_exec(
                                    *command,
                                    stdin=asyncio.subprocess.PIPE,
                                    stdout=stdout,
                                    stderr=stderr,
                                    start_new_session=True,
                                )
                        except TimeoutError:
                            proc = None
                            launch_timed_out = True
                        if proc is not None:
                            assert proc.stdin is not None
                            try:
                                # A wedged CLI may accept the subprocess launch but
                                # stop reading stdin.  Bound that phase as well as
                                # proc.wait(); otherwise the nominal process timeout
                                # does not start until after an indefinitely blocked
                                # drain, and the retry path is never reached.
                                proc.stdin.write(prompt.encode("utf-8"))
                                await asyncio.wait_for(proc.stdin.drain(), self.timeout)
                                proc.stdin.close()
                                await asyncio.wait_for(proc.wait(), self.timeout)
                            except TimeoutError:
                                try:
                                    os.killpg(proc.pid, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass
                                await proc.wait()
                                attempt = {
                                    "attempt": attempt_number,
                                    "started_at": attempt_started,
                                    "ended_at": utc_now(), "return_code": None,
                                    "throttled": False, "process_timeout": True,
                                    "error": "codex process timed out",
                                    "events": events_path.name,
                                    "stderr": stderr_path.name,
                                }
                                attempts.append(attempt)
                                # The child writes through these descriptors; flush
                                # the parent-side handles before reading durable
                                # timeout evidence (also matters for test doubles).
                                stdout.flush()
                                stderr.flush()
                                recovered = self._completed_timeout_result(
                                    result_path, events_path, schema
                                )
                                if recovered is not None:
                                    attempt["recovered_completed_result"] = True
                                    (job_dir / "attempts.json").write_text(
                                        json.dumps(attempts, ensure_ascii=False, indent=2) + "\n"
                                    )
                                    meta = {
                                        "job": job, "model": self.model, "effort": effort,
                                        "fingerprint": fingerprint, "started_at": started,
                                        "ended_at": utc_now(), "return_code": 0,
                                        "recovered_after_process_timeout": True,
                                        "attempt_count": len(attempts), "attempts": attempts,
                                    }
                                    meta_path.write_text(
                                        json.dumps(meta, ensure_ascii=False, indent=2) + "\n"
                                    )
                                    return recovered
                                (job_dir / "attempts.json").write_text(
                                    json.dumps(attempts, ensure_ascii=False, indent=2) + "\n"
                                )
                                result_path.unlink(missing_ok=True)
                                if process_timeout_retries >= self.max_process_timeout_retries:
                                    meta = {
                                        "job": job, "model": self.model, "effort": effort,
                                        "fingerprint": fingerprint, "started_at": started,
                                        "ended_at": utc_now(), "return_code": None,
                                        "error": "process_timeout",
                                        "attempt_count": len(attempts), "attempts": attempts,
                                    }
                                    meta_path.write_text(
                                        json.dumps(meta, ensure_ascii=False, indent=2) + "\n"
                                    )
                                    raise RuntimeError(f"agent timed out: {job}")
                                process_timeout_retries += 1
                                proc = None
                                continue
                if not launch_timed_out:
                    break
                attempt = {
                    "attempt": attempt_number, "started_at": attempt_started,
                    "ended_at": utc_now(), "return_code": None,
                    "throttled": False, "launch_timeout": True,
                    "error": "create_subprocess_exec timed out",
                    "events": events_path.name, "stderr": stderr_path.name,
                }
                attempts.append(attempt)
                (job_dir / "attempts.json").write_text(
                    json.dumps(attempts, ensure_ascii=False, indent=2) + "\n"
                )
                result_path.unlink(missing_ok=True)
                if launch_retry >= self.max_launch_retries:
                    meta = {
                        "job": job, "model": self.model, "effort": effort,
                        "fingerprint": fingerprint, "started_at": started,
                        "ended_at": utc_now(), "return_code": None,
                        "error": "launch_timeout", "attempt_count": len(attempts),
                        "attempts": attempts,
                    }
                    meta_path.write_text(
                        json.dumps(meta, ensure_ascii=False, indent=2) + "\n"
                    )
                    raise RuntimeError(f"agent launch timed out: {job}")
                launch_retry += 1
                await asyncio.sleep(self.launch_backoff_seconds)
            assert proc is not None
            throttled = proc.returncode != 0 and self._is_explicit_throttle(
                events_path, stderr_path
            )
            attempt = {
                "attempt": attempt_number,
                "started_at": attempt_started,
                "ended_at": utc_now(),
                "return_code": proc.returncode,
                "throttled": throttled,
                "events": events_path.name,
                "stderr": stderr_path.name,
            }
            attempts.append(attempt)
            (job_dir / "attempts.json").write_text(
                json.dumps(attempts, ensure_ascii=False, indent=2) + "\n"
            )
            if proc.returncode == 0:
                break
            if not throttled or attempt_index >= self.max_throttle_retries:
                break
            # Do not occupy scarce model concurrency while waiting for capacity.
            delay = self.throttle_backoff_seconds * (2 ** attempt_index)
            attempt["retry_delay_seconds"] = delay
            (job_dir / "attempts.json").write_text(
                json.dumps(attempts, ensure_ascii=False, indent=2) + "\n"
            )
            await asyncio.sleep(delay)
        assert proc is not None
        meta = {
            "job": job, "model": self.model, "effort": effort,
            "fingerprint": fingerprint, "started_at": started,
            "ended_at": utc_now(), "return_code": proc.returncode,
            "attempt_count": len(attempts), "attempts": attempts,
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
        if proc.returncode != 0 or not result_path.exists():
            raise RuntimeError(f"agent failed: {job} (exit {proc.returncode})")
        return json.loads(result_path.read_text())


class ChapterHarness:
    def __init__(
        self, args: argparse.Namespace, semaphore: asyncio.Semaphore | None = None
    ):
        self.args = args
        self.source_path = Path(args.source).resolve()
        self.source = self.source_path.read_text(encoding="utf-8")
        run_id = args.run_id or f"{self.source_path.stem}-{args.level}"
        self.run_dir = Path(args.runs_dir).resolve() / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.runner = CodexRunner(
            self.run_dir, args.model,
            semaphore or asyncio.Semaphore(args.concurrency), args.timeout,
        )

    @property
    def target_chars(self) -> int:
        return self.args.target_chars or DEFAULT_LEVEL_TARGETS.get(self.args.level, 5000)

    @property
    def scene_count(self) -> int:
        """Keep compact original chapters coherent; split only very long outputs."""
        return max(1, min(6, round(self.target_chars / 1500)))

    @property
    def annotation_chunk_target(self) -> int:
        return getattr(
            self.args, "annotation_chunk", DEFAULT_CHINESE_ANNOTATION_CHUNK_TARGET
        )

    @property
    def annotation_chunk_maximum(self) -> int:
        supplied = getattr(self.args, "annotation_chunk_maximum", None)
        if supplied is not None:
            return supplied
        # Preserve --annotation-chunk as a meaningful standalone sizing
        # override.  Scale its ceiling by the same 350:400 soft/hard ratio;
        # callers can still set an exact hard ceiling explicitly.
        return max(
            self.annotation_chunk_target,
            round(
                self.annotation_chunk_target
                * DEFAULT_CHINESE_ANNOTATION_CHUNK_MAXIMUM
                / DEFAULT_CHINESE_ANNOTATION_CHUNK_TARGET
            ),
        )

    def chinese_annotation_chunks(self, chapter: str) -> list[str]:
        return split_chinese_annotation_chunks(
            chapter, self.annotation_chunk_target, self.annotation_chunk_maximum
        )

    @property
    def annotation_chunk_cache_tag(self) -> str:
        """Salt every annotation call when policy or CLI sizing changes."""
        return (
            f"CHINESE_ANNOTATION_CHUNK_POLICY={CHINESE_ANNOTATION_CHUNK_POLICY};"
            f"target={self.annotation_chunk_target};"
            f"maximum={self.annotation_chunk_maximum}"
        )

    def write_manifest(self, status: str) -> None:
        manifest = {
            "run_id": self.run_dir.name,
            "status": status,
            "source": str(self.source_path),
            "source_sha256": digest(self.source),
            "level": self.args.level,
            "model": self.args.model,
            "adapt_effort": self.args.adapt_effort,
            "review_effort": self.args.review_effort,
            "repair_effort": self.args.repair_effort,
            "final_effort": self.args.final_effort,
            "max_repairs": self.args.max_repairs,
            "fresh_rewrite": self.args.fresh_rewrite,
            "annotation_review_effort": self.args.annotation_review_effort,
            "annotation_repair_effort": self.args.annotation_repair_effort,
            "annotation_final_effort": self.args.annotation_final_effort,
            "max_annotation_repairs": self.args.max_annotation_repairs,
            "annotation_mode": getattr(self.args, "annotation_mode", "generative"),
            "annotation_review_policy": getattr(
                self.args, "annotation_review_policy", "chapter"
            ),
            "annotation_chunk_policy": CHINESE_ANNOTATION_CHUNK_POLICY,
            "annotation_chunk_target": self.annotation_chunk_target,
            "annotation_chunk_maximum": self.annotation_chunk_maximum,
            "target_chars": self.target_chars,
            "updated_at": utc_now(),
        }
        (self.run_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        )

    async def outline(self) -> dict[str, Any]:
        prompt = f"""Return only JSON matching the supplied schema.
Read the complete original Chinese chapter and divide it into exactly
{self.scene_count} consecutive adaptation scene(s).
Every source_start_quote must be an exact, unique substring
of ORIGINAL. Scenes must cover the source in order. Capture every important
event and causal link that is essential to a coherent compressed retelling.
Target lengths should sum to approximately
{self.target_chars} Chinese characters.

ORIGINAL:\n{self.source}"""
        outline = await self.runner.call(
            "outline", prompt, SCHEMAS / "scene-outline.schema.json",
            self.args.review_effort, refresh=self.args.refresh,
        )
        scenes = outline["scenes"]
        if self.scene_count == 1 and len(scenes) != 1:
            scenes = [{
                "id": "scene_01",
                "title": outline["chapter_title"],
                "source_start_quote": scenes[0]["source_start_quote"],
                "required_events": [
                    event for item in scenes for event in item["required_events"]
                ],
                "target_chars": self.target_chars,
            }]
            outline["scenes"] = scenes
        if len(scenes) != self.scene_count:
            raise ValueError(
                f"outline returned {len(scenes)} scenes; expected {self.scene_count}"
            )
        # Numeric targets are orchestration policy, never model authority. This
        # also prevents plausible schema-valid slips such as 105 instead of 1050.
        base, remainder = divmod(self.target_chars, len(scenes))
        for index, scene in enumerate(scenes):
            scene["target_chars"] = base + (1 if index < remainder else 0)
        starts: list[int] = []
        search_from = 0
        for index, scene in enumerate(scenes, 1):
            # IDs are orchestration metadata, not editorial output. Normalize
            # them rather than wasting agent retries on harmless numbering such
            # as scene_11/scene_22.
            scene["id"] = f"scene_{index:02d}"
            quote = scene["source_start_quote"]
            # The first scene unconditionally owns the chapter from byte zero.
            # Do not make a harmless model correction, orthographic variant, or
            # source typo in its anchor quote fatal (for example 闞澤 vs 闕澤).
            # Subsequent scene boundaries still require exact ordered anchors.
            position = 0 if index == 1 else self.source.find(quote, search_from)
            if position < 0:
                raise ValueError(
                    f"scene boundary not found after prior boundary: {quote!r}"
                )
            starts.append(position)
            search_from = position + max(1, len(quote))
        if starts != sorted(starts):
            raise ValueError("scene boundaries are not in source order")
        if starts:
            # The first generated quote anchors the first scene, but the first
            # scene itself owns every byte from the start of the source.
            starts[0] = 0
        for index, scene in enumerate(scenes):
            start = starts[index]
            end = starts[index + 1] if index + 1 < len(starts) else len(self.source)
            scene["source_start"] = start
            scene["source_end"] = end
        (self.run_dir / "outline.json").write_text(
            json.dumps(outline, ensure_ascii=False, indent=2) + "\n"
        )
        return outline

    async def adapt_scene(self, scene: dict[str, Any]) -> dict[str, Any]:
        original = self.source[scene["source_start"] : scene["source_end"]]
        events = "\n".join(f"- {event}" for event in scene["required_events"])
        prompt = f"""Return only JSON matching the supplied schema, with the
adapted scene in `text`. Adapt the verbatim ORIGINAL into natural, engaging
modern Chinese for an annotated {self.args.level.upper()} literary reader.
Write only simplified Chinese, even though ORIGINAL uses traditional Chinese.
Preserve the required essential events, causal links, motivations, names,
numbers, and order. Intentional compression is expected at this level.
Target about {scene['target_chars']} Chinese characters. Core grammar should be
comfortable at {self.args.level.upper()}, but natural historical and story
vocabulary is allowed because every word will be annotated. Do not invent facts.

REQUIRED EVENTS:\n{events}\n\nORIGINAL:\n{original}"""
        result = await self.runner.call(
            f"{scene['id']}/adapt", prompt, SCHEMAS / "adaptation.schema.json",
            self.args.adapt_effort, refresh=self.args.refresh,
        )
        result["text"] = simplified(result["text"])
        return result

    async def review_scene(
        self, scene: dict[str, Any], adaptation: str, suffix: str = "review"
    ) -> dict[str, Any]:
        original = self.source[scene["source_start"] : scene["source_end"]]
        prompt = f"""Return only JSON matching the supplied schema. Compare
ADAPTATION directly against VERBATIM ORIGINAL and the requested compact target.
The target for this scene is {scene['target_chars']} Chinese characters; keep
the result within roughly 70%-130% of that target.
Natural condensation and paraphrase are expected. Identify omissions of
essential events needed for a coherent retelling, unsupported
additions, factual/causal distortions, awkward Chinese, and readability issues.
The adaptation must use simplified Chinese consistently; any traditional-only
characters or mixed simplified/traditional prose requires verdict=revise.
Set verdict=pass only when source_fidelity, naturalness, and readability are all
at least 8 and there are no material distortions.

ORIGINAL:\n{original}\n\nADAPTATION:\n{adaptation}"""
        result = await self.runner.call(
            f"{scene['id']}/{suffix}", prompt, SCHEMAS / "source-review.schema.json",
            self.args.review_effort, refresh=self.args.refresh,
        )
        length = cjk_count(adaptation)
        low = int(scene["target_chars"] * 0.7)
        high = int(scene["target_chars"] * 1.3)
        in_range = low <= length <= high
        if in_range:
            result = discard_incorrect_length_findings(result)
        result = apply_compact_review_policy(result)
        if not in_range:
            result = dict(result)
            result["verdict"] = "revise"
            result.setdefault("language_problems", []).append(
                f"mechanical length gate: {length} CJK, required {low}-{high}"
            )
            result["harness_decision"] = "rejected_by_mechanical_length_gate"
        return result

    async def repair_scene(
        self,
        scene: dict[str, Any],
        adaptation: str,
        review: dict[str, Any],
        attempt: int,
    ) -> dict[str, Any]:
        original = self.source[scene["source_start"] : scene["source_end"]]
        findings = json.dumps(review, ensure_ascii=False, indent=2)
        prompt = f"""Return only JSON matching the supplied schema, with the
revised scene in `text`. Repair ADAPTATION using the source-grounded REVIEW.
Change only what the findings require. Preserve good prose, length, event order,
and {self.args.level.upper()}-readable core language. Remove unsupported additions, restore material
omissions, correct distortions, fix listed language problems, and use only
simplified Chinese. Keep the revised scene within roughly 70%-130% of the
original target of {scene['target_chars']} Chinese characters.

ORIGINAL:\n{original}\n\nADAPTATION:\n{adaptation}\n\nREVIEW:\n{findings}"""
        result = await self.runner.call(
            f"{scene['id']}/repair_{attempt:02d}", prompt,
            SCHEMAS / "adaptation.schema.json",
            self.args.repair_effort, refresh=self.args.refresh,
        )
        result["text"] = simplified(result["text"])
        return result

    async def rewrite_scene(
        self,
        scene: dict[str, Any],
        attempts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Fresh final rewrite after local repair attempts fail."""
        original = self.source[scene["source_start"] : scene["source_end"]]
        history = json.dumps(
            [
                {"text": item["text"], "review": item["review"]}
                for item in attempts
            ],
            ensure_ascii=False,
            indent=2,
        )
        prompt = f"""Return only JSON matching the supplied schema, with a
fresh scene in `text`. Earlier adaptation and repair attempts all failed strict
source review. Start again from VERBATIM ORIGINAL. Use the failure history only
as a list of traps to avoid; do not patch or imitate its prose. Preserve all
facts, causal links, motivations, names, numbers, and event order. Write natural
modern Chinese for an annotated {self.args.level.upper()} literary reader.
Use only simplified Chinese. Natural story vocabulary is allowed. Do not invent
facts. Target about {scene['target_chars']} Chinese characters and stay within
roughly 70%-130% of that compact target.

ORIGINAL:\n{original}\n\nFAILED ATTEMPTS AND REVIEWS:\n{history}"""
        result = await self.runner.call(
            f"{scene['id']}/fresh_rewrite", prompt,
            SCHEMAS / "adaptation.schema.json", self.args.final_effort,
            refresh=self.args.refresh,
        )
        result["text"] = simplified(result["text"])
        return result

    async def process_scene(self, scene: dict[str, Any]) -> dict[str, Any]:
        adapted = await self.adapt_scene(scene)
        review = await self.review_scene(scene, adapted["text"])
        attempts = [{"stage": "adapt", "text": adapted["text"], "review": review}]
        for attempt in range(1, self.args.max_repairs + 1):
            if review["verdict"] == "pass":
                break
            adapted = await self.repair_scene(
                scene, adapted["text"], review, attempt
            )
            review = await self.review_scene(
                scene, adapted["text"], suffix=f"repair_{attempt:02d}_review"
            )
            attempts.append(
                {"stage": f"repair_{attempt:02d}", "text": adapted["text"], "review": review}
            )
        if review["verdict"] != "pass" and self.args.fresh_rewrite:
            adapted = await self.rewrite_scene(scene, attempts)
            review = await self.review_scene(
                scene, adapted["text"], suffix="fresh_rewrite_review"
            )
            attempts.append(
                {"stage": "fresh_rewrite", "text": adapted["text"], "review": review}
            )
        result = {
            "scene": scene,
            "text": adapted["text"],
            "review": review,
            "attempts": attempts,
            "resolved": review["verdict"] == "pass",
        }
        scene_path = self.run_dir / "scenes" / f"{scene['id']}.json"
        scene_path.parent.mkdir(parents=True, exist_ok=True)
        scene_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        return result

    async def annotation_candidate(
        self,
        index: int,
        chunk: str,
        *,
        stage: str = "initial",
        prior: dict[str, Any] | None = None,
        findings: dict[str, Any] | None = None,
        effort: str | None = None,
    ) -> dict[str, Any]:
        repair_context = ""
        if prior is not None:
            repair_context = f"""

PRIOR ANNOTATION:
{json.dumps(prior, ensure_ascii=False, indent=2)}

INDEPENDENT REVIEW FINDINGS:
{json.dumps(findings, ensure_ascii=False, indent=2)}

Re-annotate the complete TEXT so reconstruction remains exact, but change only
the segmentation or explanations implicated by the findings. Do not preserve a
bad grouped span merely because it appeared in the prior annotation."""
        prompt = f"""{self.annotation_chunk_cache_tag}
Return only JSON matching the supplied schema. Annotate
TEXT without changing, omitting, or reordering any character. Segment into
individual natural dictionary words and grammatical particles. Keep names
together. Use idiom only for genuine lexicalized idioms. Never group ordinary
phrases or clauses. Include whitespace and punctuation as punctuation segments.
For every non-punctuation segment, give pinyin and a concise contextual English
meaning for that segment alone. Separately add grammar_overlays for grammatical
constructions spanning one or more segments. Overlay start/end are zero-based
Python character offsets into TEXT (end exclusive), and overlay text must equal
TEXT[start:end]. A grammar overlay explains the construction; it must not
replace word segmentation or paraphrase an ordinary sentence. Concatenating
segment text must exactly reproduce TEXT.

TEXT:\n{chunk}{repair_context}"""
        return await self.runner.call(
            f"annotations/chunk_{index:04d}/{stage}", prompt,
            SCHEMAS / "annotation.schema.json", effort or self.args.annotation_effort,
            refresh=self.args.refresh,
        )

    async def review_annotation(
        self, index: int, chunk: str, annotation: dict[str, Any], stage: str,
        *, effort: str | None = None,
    ) -> dict[str, Any]:
        segment_rows: list[list[Any]] = []
        cursor = 0
        for segment in annotation.get("segments", []):
            end = cursor + len(segment["text"])
            segment_rows.append([
                cursor, end, segment["text"], segment["type"],
                segment["pinyin"], segment["meaning_en"],
            ])
            cursor = end
        compact = lambda value: json.dumps(
            value, ensure_ascii=False, separators=(",", ":")
        )
        prompt = f"""{self.annotation_chunk_cache_tag}
Return only JSON matching the supplied schema. Independently
audit this annotation for a Chinese learner's tap-to-explain reader. The segment
texts and grammar-overlay offsets have already passed deterministic exactness
checks. Do not re-count offsets, dispute reconstruction, request adding/removing
whitespace, or report offset/span-boundary errors; deterministic code is the sole
authority for those properties. Focus only on semantic quality. Ordinary
sentences and compositional phrases must be split into natural dictionary words
and grammatical particles; they are not expressions. Keep personal/place names
together and accept an idiom segment only when it is a genuine lexicalized idiom.
Meanings must explain the individual segment in its local context, not paraphrase
a whole clause. Grammar overlays should explain genuine, useful, localized
grammar patterns separately from lexical segments; reject generic sentence or
clause summaries. This is a pragmatic learner reader, not a lexicography paper:
report only problems that materially mislead a learner, hide a useful word or
particle inside a larger span, split a real word/name/idiom, or turn an ordinary
clause into a fake expression. Do not fail an otherwise useful annotation for a
minor wording nuance, an optional grammar overlay, or another defensible gloss.
Set verdict=revise only when at least one such material problem remains;
otherwise return verdict=pass with no issues.
For every issue, start/end are REQUIRED zero-based Python character offsets into
the exact TEXT, end exclusive. Copy segment_text exactly from TEXT[start:end].
Identify the specific occurrence: repeated surface text must use the offsets of
the occurrence that is actually wrong. Self-check TEXT[start:end] == segment_text.
Do not use tools, a shell, Python, search, or external sources. Copy issue offsets
directly from the supplied segment/overlay rows and verify them against TEXT.

TEXT:
{chunk}

SEGMENT ROWS:
COLUMNS=[CHAR_START,CHAR_END,TEXT,TYPE,PINYIN,MEANING_EN]
{compact(segment_rows)}

GRAMMAR OVERLAYS (existing exact offset objects):
{compact(annotation.get("grammar_overlays", []))}"""
        review = await self.runner.call(
            f"annotations/chunk_{index:04d}/{stage}_review", prompt,
            SCHEMAS / "annotation-review.schema.json",
            effort or self.args.annotation_review_effort, refresh=self.args.refresh,
        )
        # Retain invalid/legacy findings as audit evidence, but mutation code
        # independently validates offsets and will not grant them scope.
        review = copy.deepcopy(review)
        issues = review.get("issues", [])
        valid = legacy = invalid = 0
        for issue in issues if isinstance(issues, list) else []:
            if not isinstance(issue, dict):
                invalid += 1
                continue
            if "start" not in issue and "end" not in issue:
                legacy += 1
                continue
            start, end = issue.get("start"), issue.get("end")
            if (isinstance(start, int) and not isinstance(start, bool)
                    and isinstance(end, int) and not isinstance(end, bool)
                    and 0 <= start < end <= len(chunk)
                    and issue.get("segment_text") == chunk[start:end]):
                valid += 1
            else:
                invalid += 1
        review["offset_audit"] = {
            "valid": valid, "legacy_missing": legacy, "invalid": invalid,
        }
        return review

    @staticmethod
    def annotation_contract_issues(
        chunk: str, result: dict[str, Any]
    ) -> list[dict[str, str]]:
        """Return concrete deterministic findings for annotation self-heal."""
        issues: list[dict[str, str]] = []
        segments = result.get("segments")
        overlays = result.get("grammar_overlays")
        if not isinstance(segments, list) or not isinstance(overlays, list):
            return [{
                "segment_text": "", "problem": "reconstruction",
                "explanation": "segments and grammar_overlays must both be arrays.",
                "suggested_fix": "Return both required arrays using the supplied schema.",
            }]
        if "".join(str(segment.get("text", "")) for segment in segments) != chunk:
            issues.append({
                "segment_text": "", "problem": "reconstruction",
                "explanation": "Concatenated segment texts do not exactly reproduce TEXT, including whitespace and punctuation.",
                "suggested_fix": "Restore every omitted or changed character and segment whitespace/punctuation explicitly.",
            })
        for segment in segments:
            text = segment.get("text", "")
            kind = segment.get("type")
            pinyin = segment.get("pinyin", "")
            meaning = segment.get("meaning_en", "")
            if kind == "punctuation":
                if pinyin or meaning:
                    issues.append({"segment_text": text, "problem": "meaning", "explanation": "Punctuation must have empty pinyin and meaning.", "suggested_fix": "Set both fields to empty strings."})
                continue
            if not pinyin.strip() or not meaning.strip() or len(meaning) > 160:
                issues.append({"segment_text": text, "problem": "meaning", "explanation": "A lexical segment needs pinyin and a concise contextual meaning.", "suggested_fix": "Supply nonempty pinyin and a segment-level meaning under 160 characters."})
            if re.search(r"[\u3400-\u9fff]", pinyin):
                issues.append({"segment_text": text, "problem": "pinyin", "explanation": "Pinyin contains Han characters.", "suggested_fix": "Use tone-marked Latin pinyin only."})
            # Four Han characters cover ordinary words, conventional idioms,
            # and Chinese personal/place names. Longer spans are almost always
            # precisely the phrase/clause grouping this tap-reader forbids.
            if kind in {"word", "particle", "name", "idiom"} and cjk_count(text) > 4:
                issues.append({"segment_text": text, "problem": "over_grouped", "explanation": "A semantic segment is longer than a normal lexical unit or Chinese name.", "suggested_fix": "Split it into dictionary words, particles, or independently meaningful name components."})
            if any(mark in text for mark in "。！？；，、\n"):
                issues.append({"segment_text": text, "problem": "under_grouped", "explanation": "A semantic segment contains punctuation or a newline.", "suggested_fix": "Put punctuation and whitespace in separate punctuation segments."})
        for overlay in overlays:
            start, end = overlay.get("start"), overlay.get("end")
            if not isinstance(start, int) or not isinstance(end, int):
                issues.append({"segment_text": str(overlay.get("text", "")), "problem": "grammar", "explanation": "Grammar offsets are not integers.", "suggested_fix": "Use zero-based Python character offsets into the exact TEXT."})
                continue
            if not 0 <= start < end <= len(chunk):
                issues.append({"segment_text": str(overlay.get("text", "")), "problem": "grammar", "explanation": "Grammar offsets fall outside TEXT.", "suggested_fix": "Recalculate zero-based start/end offsets, with end exclusive."})
                continue
            if overlay.get("text") != chunk[start:end]:
                issues.append({"segment_text": str(overlay.get("text", "")), "problem": "grammar", "explanation": f"Grammar surface does not equal TEXT[{start}:{end}].", "suggested_fix": "Recalculate offsets by Python characters so the slice exactly equals overlay text."})
            if not str(overlay.get("pattern", "")).strip():
                issues.append({"segment_text": str(overlay.get("text", "")), "problem": "grammar", "explanation": "Grammar pattern is empty.", "suggested_fix": "Name the grammatical construction concisely."})
            meaning = str(overlay.get("meaning_en", ""))
            if not meaning.strip() or len(meaning) > 200:
                issues.append({"segment_text": str(overlay.get("text", "")), "problem": "grammar", "explanation": "Grammar explanation is empty or not concise.", "suggested_fix": "Explain the construction in under 200 characters."})
        return issues

    @staticmethod
    def canonicalize_annotation_candidate(
        chunk: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        """Mechanically repair only unambiguous surfaces and trailing spacing."""
        value = copy.deepcopy(result)
        segments = value.get("segments")
        if isinstance(segments, list):
            reconstructed = "".join(str(item.get("text", "")) for item in segments)
            remainder = chunk[len(reconstructed):] if chunk.startswith(reconstructed) else ""
            if remainder and remainder.isspace():
                segments.append({
                    "text": remainder, "type": "punctuation",
                    "pinyin": "", "meaning_en": "",
                })
            elif reconstructed and chunk.endswith(reconstructed):
                prefix = chunk[:len(chunk) - len(reconstructed)]
                if prefix and prefix.isspace():
                    segments.insert(0, {
                        "text": prefix, "type": "punctuation",
                        "pinyin": "", "meaning_en": "",
                    })
        overlays = value.get("grammar_overlays")
        if isinstance(overlays, list):
            for overlay in overlays:
                surface = overlay.get("text")
                if not isinstance(surface, str) or not surface:
                    continue
                occurrences = [
                    match.start() for match in re.finditer(re.escape(surface), chunk)
                ]
                selected = occurrences[0] if len(occurrences) == 1 else None
                supplied = overlay.get("start")
                if len(occurrences) > 1 and isinstance(supplied, int):
                    distances = sorted((abs(item - supplied), item) for item in occurrences)
                    if distances[0][0] < distances[1][0]:
                        selected = distances[0][1]
                if selected is not None:
                    overlay["start"] = selected
                    overlay["end"] = selected + len(surface)
        return value

    @staticmethod
    def annotation_reconstructs(chunk: str, result: dict[str, Any]) -> bool:
        return not ChapterHarness.annotation_contract_issues(chunk, result)

    async def annotate_chunk(self, index: int, chunk: str) -> dict[str, Any]:
        mode = getattr(self.args, "annotation_mode", "generative")
        if mode == "constrained":
            return await self.annotate_chunk_constrained(index, chunk)
        if mode == "constrained-delta":
            raise ValueError("constrained-delta annotations must be seeded once at chapter scope")
        result = self.canonicalize_annotation_candidate(
            chunk, await self.annotation_candidate(index, chunk)
        )
        attempts: list[dict[str, Any]] = []
        for attempt in range(self.args.max_annotation_repairs + 1):
            if self.annotation_reconstructs(chunk, result):
                review = await self.review_annotation(
                    index, chunk, result, "initial" if attempt == 0 else f"repair_{attempt:02d}"
                )
            else:
                review = {
                    "verdict": "revise",
                    "issues": self.annotation_contract_issues(chunk, result),
                }
            attempts.append({"stage": "initial" if attempt == 0 else f"repair_{attempt:02d}",
                             "annotation": result, "review": review})
            if review["verdict"] == "pass":
                return {"segments": result["segments"],
                        "grammar_overlays": result["grammar_overlays"],
                        "attempts": attempts, "resolved": True}
            if attempt < self.args.max_annotation_repairs:
                result = self.canonicalize_annotation_candidate(
                    chunk, await self.annotation_candidate(
                        index, chunk, stage=f"repair_{attempt + 1:02d}", prior=result,
                        findings=review, effort=self.args.annotation_repair_effort,
                    )
                )

        # A fresh, higher-effort attempt avoids repeatedly patching a bad segmentation.
        result = self.canonicalize_annotation_candidate(
            chunk, await self.annotation_candidate(
                index, chunk, stage="fresh", effort=self.args.annotation_final_effort
            )
        )
        if self.annotation_reconstructs(chunk, result):
            review = await self.review_annotation(index, chunk, result, "fresh")
        else:
            review = {"verdict": "revise",
                      "issues": self.annotation_contract_issues(chunk, result)}
        attempts.append({"stage": "fresh", "annotation": result, "review": review})
        if review["verdict"] != "pass":
            raise ValueError(
                f"annotation quality gate failed for chunk {index} after "
                f"{len(attempts)} attempts"
            )
        return {"segments": result["segments"],
                "grammar_overlays": result["grammar_overlays"],
                "attempts": attempts, "resolved": True}

    async def constrained_annotation_candidate(
        self, index: int, chunk: str, *, stage: str, effort: str
    ) -> dict[str, Any]:
        """Enrich deterministic surfaces without permitting boundary changes."""
        # Lazy import avoids the intentional module cycle: the experimental
        # helper imports this harness's deterministic publisher contract.
        from pipeline.fixed_boundary_annotation import (
            SCHEMA, prompt_for, refined_fixed_segments, validate_result,
        )

        surfaces = refined_fixed_segments(chunk)
        raw = await self.runner.call(
            f"annotations/chunk_{index:04d}/constrained_{stage}",
            f"{self.annotation_chunk_cache_tag}\n{prompt_for(chunk, surfaces, self.args.level)}",
            SCHEMA, effort,
            refresh=self.args.refresh,
        )
        return validate_result(chunk, surfaces, raw)

    async def constrained_annotation_correction(
        self, index: int, chunk: str, annotation: dict[str, Any],
        findings: dict[str, Any], attempt: int, *,
        effort: str | None = None, stage: str | None = None,
    ) -> dict[str, Any]:
        """Apply only lossless, index-addressed patches proposed by a model."""
        from pipeline.fixed_boundary_annotation import (
            CORRECTION_SCHEMA, apply_correction_patches, correction_prompt,
            review_scoped_correction,
        )

        correction = await self.runner.call(
            f"annotations/chunk_{index:04d}/constrained_"
            f"{stage or f'correction_{attempt:02d}'}",
            f"{self.annotation_chunk_cache_tag}\n"
            f"{correction_prompt(chunk, annotation, findings)}", CORRECTION_SCHEMA,
            effort or self.args.annotation_repair_effort, refresh=self.args.refresh,
        )
        correction, scope_evidence = review_scoped_correction(
            chunk, annotation, correction, findings
        )
        # A correction response can contain many independent patches. Preserve
        # every strictly lossless patch even if one sibling accidentally adds,
        # drops, or normalizes a source character. The strict primitive remains
        # fail-closed; this layer merely selects the maximal valid prefix-set.
        accepted: list[dict[str, Any]] = []
        for patch in correction.get("patches", []):
            try:
                apply_correction_patches(chunk, annotation, {
                    "patches": [*accepted, patch],
                    "grammar_overlays": annotation.get("grammar_overlays", []),
                })
            except ValueError as exc:
                scope_evidence["discarded_patches"].append({
                    "range": [patch.get("start_index"), patch.get("end_index")]
                    if isinstance(patch, dict) else None,
                    "reason": f"invalid_or_lossy: {exc}",
                })
                continue
            accepted.append(patch)
        scope_evidence["applied_patch_count"] = len(accepted)
        scope_evidence["applied_patches"] = copy.deepcopy(accepted)
        candidate = {
            "patches": accepted,
            "grammar_overlays": correction.get("grammar_overlays", []),
        }
        try:
            result = apply_correction_patches(chunk, annotation, candidate)
        except ValueError:
            # Bad overlay offsets must not throw away otherwise valid lexical
            # corrections. Retain the previously reviewed overlays instead.
            candidate["grammar_overlays"] = annotation.get("grammar_overlays", [])
            result = apply_correction_patches(chunk, annotation, candidate)
            scope_evidence["discarded_overlay_count"] = max(
                1, scope_evidence["discarded_overlay_count"]
            )
        result["correction_evidence"] = scope_evidence
        return result

    async def issue_scoped_chapter_correction(
        self, chapter: str, annotation: dict[str, Any], review: dict[str, Any], *,
        stage: str, effort: str, maximum_findings: int = 6,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Correct every valid finding via small exact, independent patch calls."""
        from pipeline.fixed_boundary_annotation import (
            _review_scopes, apply_correction_patches,
        )

        issues = review.get("issues", []) if isinstance(review, dict) else []
        scoped: dict[tuple[int, int], list[dict[str, Any]]] = {}
        discarded: list[dict[str, Any]] = []
        offsets, cursor = [], 0
        for segment in annotation["segments"]:
            end = cursor + len(segment["text"])
            offsets.append((cursor, end, segment["text"]))
            cursor = end

        def narrow_under_grouped(issue: dict[str, Any]) -> dict[str, Any]:
            """Use an explicit suggested token when the reviewer included context."""
            if issue.get("problem") != "under_grouped":
                return issue
            start, end = issue.get("start"), issue.get("end")
            suggestion = issue.get("suggested_fix", "")
            if not isinstance(start, int) or not isinstance(end, int) or not isinstance(suggestion, str):
                return issue
            owners = [index for index, (left, right, _text) in enumerate(offsets)
                      if left < end and start < right]
            choices: list[tuple[int, int, str]] = []
            for left_pos, left_index in enumerate(owners):
                for right_index in owners[left_pos:]:
                    surface = "".join(item[2] for item in offsets[left_index:right_index + 1])
                    char_start, char_end = offsets[left_index][0], offsets[right_index][1]
                    if (char_start, char_end) != (start, end) and len(surface) >= 2 \
                            and surface in suggestion:
                        choices.append((char_start, char_end, surface))
            if not choices:
                return issue
            char_start, char_end, surface = max(choices, key=lambda item: len(item[2]))
            return {**issue, "start": char_start, "end": char_end,
                    "segment_text": surface}

        for raw_issue in issues if isinstance(issues, list) else []:
            issue = narrow_under_grouped(raw_issue) if isinstance(raw_issue, dict) else raw_issue
            finding = {"issues": [issue]}
            ranges, _chars, evidence = _review_scopes(
                chapter, annotation["segments"], finding, grammar=False,
            )
            if evidence or len(ranges) != 1:
                discarded.extend(evidence or [{"kind": "issue", "reason": "not_lexical"}])
                continue
            token_range = next(iter(ranges))
            scoped.setdefault(token_range, []).append(issue)
        ordered = sorted(scoped)
        for left, first in enumerate(ordered):
            for second in ordered[left + 1:]:
                if first[0] < second[1] and second[0] < first[1]:
                    raise ValueError("review findings resolve to conflicting token scopes")
        groups = [ordered[pos:pos + maximum_findings]
                  for pos in range(0, len(ordered), maximum_findings)]

        async def correct(number: int, ranges: list[tuple[int, int]]):
            batch_review = {
                "verdict": "revise",
                "issues": [issue for token_range in ranges for issue in scoped[token_range]],
            }
            result = await self.constrained_annotation_correction(
                number, chapter, annotation, batch_review, number + 1,
                effort=effort, stage=f"{stage}_batch_{number:04d}",
            )
            patches = result["correction_evidence"].get("applied_patches", [])
            def covered(token_range: tuple[int, int], candidates: list[dict[str, Any]]) -> bool:
                start, end = token_range
                return any(
                    isinstance(patch, dict)
                    and patch.get("start_index", end) <= start
                    and end <= patch.get("end_index", start)
                    for patch in candidates
                )
            missing = [token_range for token_range in ranges
                       if not covered(token_range, patches)]
            completion_calls = 0
            if missing:
                missing_review = {
                    "verdict": "revise",
                    "issues": [issue for token_range in missing
                               for issue in scoped[token_range]],
                }
                completion = await self.constrained_annotation_correction(
                    number, chapter, annotation, missing_review, number + 1,
                    effort=effort, stage=f"{stage}_batch_{number:04d}_missing",
                )
                extra = completion["correction_evidence"].get("applied_patches", [])
                patches = [*patches, *extra]
                completion_calls = 1
                missing = [token_range for token_range in ranges
                           if not covered(token_range, patches)]
            if missing:
                raise ValueError("issue-scoped correction did not cover every finding span")
            return patches, completion_calls

        patch_groups = await gather_all_or_raise(*(
            correct(number, ranges) for number, ranges in enumerate(groups)
        )) if groups else []
        patches = sorted(
            [patch for group, _calls in patch_groups for patch in group],
            key=lambda patch: (patch["start_index"], patch["end_index"]),
        )
        for previous, current in zip(patches, patches[1:]):
            if previous["end_index"] > current["start_index"]:
                raise ValueError("issue-scoped correction patches overlap")
        result = apply_correction_patches(chapter, annotation, {
            "patches": patches,
            "grammar_overlays": annotation.get("grammar_overlays", []),
        })
        evidence = {
            "calls": len(groups) + sum(calls for _group, calls in patch_groups),
            "valid_finding_spans": len(ordered),
            "applied_patch_count": len(patches), "exact_coverage": True,
            "discarded_findings": discarded,
        }
        return result, evidence

    async def annotate_chunk_constrained(
        self, index: int, chunk: str
    ) -> dict[str, Any]:
        """Deterministic proposal -> enrichment -> review -> strict patch loop.

        A successful return always includes a real independent semantic pass.
        Deterministic validation alone is deliberately insufficient.
        """
        result = await self.constrained_annotation_candidate(
            index, chunk, stage="initial", effort=self.args.annotation_effort
        )
        attempts: list[dict[str, Any]] = []
        for attempt in range(self.args.max_annotation_repairs + 1):
            stage = "initial" if attempt == 0 else f"correction_{attempt:02d}"
            review = await self.review_annotation(index, chunk, result, stage)
            attempts.append({"stage": stage, "annotation": result, "review": review})
            if review["verdict"] == "pass":
                return {
                    "segments": result["segments"],
                    "grammar_overlays": result["grammar_overlays"],
                    "attempts": attempts, "resolved": True,
                    "reviewed": True, "mode": "constrained",
                }
            if attempt < self.args.max_annotation_repairs:
                result = await self.constrained_annotation_correction(
                    index, chunk, result, review, attempt + 1
                )

        # Start from a clean deterministic proposal if accumulated patches did
        # not pass. This is a genuinely new, higher-effort enrichment, not an
        # unreviewed acceptance or an unconstrained rewrite.
        result = await self.constrained_annotation_candidate(
            index, chunk, stage="fresh", effort=self.args.annotation_final_effort
        )
        review = await self.review_annotation(index, chunk, result, "fresh")
        attempts.append({"stage": "fresh", "annotation": result, "review": review})
        if review["verdict"] != "pass":
            raise ValueError(
                f"constrained annotation quality gate failed for chunk {index} "
                f"after {len(attempts)} reviewed attempts"
            )
        return {
            "segments": result["segments"],
            "grammar_overlays": result["grammar_overlays"],
            "attempts": attempts, "resolved": True,
            "reviewed": True, "mode": "constrained",
        }

    @staticmethod
    def split_chapter_annotation(
        chapter: str, chunks: list[str], annotation: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Partition an exact chapter annotation without permitting span drift."""
        boundaries, cursor = [], 0
        for chunk in chunks:
            boundaries.append((cursor, cursor + len(chunk)))
            cursor += len(chunk)
        if cursor != len(chapter):
            raise ValueError("annotation chunks do not reconstruct chapter")
        output = [{"segments": [], "grammar_overlays": []} for _ in chunks]
        segment_start = 0
        chunk_index = 0
        for segment in annotation["segments"]:
            segment_end = segment_start + len(segment["text"])
            while chunk_index < len(boundaries) and segment_start >= boundaries[chunk_index][1]:
                chunk_index += 1
            if chunk_index >= len(boundaries) or segment_end > boundaries[chunk_index][1]:
                raise ValueError("deterministic segment crosses an annotation chunk boundary")
            output[chunk_index]["segments"].append(segment)
            segment_start = segment_end
        if segment_start != len(chapter):
            raise ValueError("chapter annotation does not reconstruct chapter")
        for overlay in annotation["grammar_overlays"]:
            owners = [i for i, (start, end) in enumerate(boundaries)
                      if start <= overlay["start"] < overlay["end"] <= end]
            if len(owners) != 1:
                raise ValueError("grammar overlay crosses an annotation chunk boundary")
            owner = owners[0]
            offset = boundaries[owner][0]
            output[owner]["grammar_overlays"].append({
                **overlay, "start": overlay["start"] - offset,
                "end": overlay["end"] - offset,
            })
        for chunk, item in zip(chunks, output):
            issues = ChapterHarness.annotation_contract_issues(chunk, item)
            if issues:
                raise ValueError(f"partitioned delta annotation failed contract: {issues}")
        return output

    async def review_constrained_delta_chunk(
        self, index: int, chunk: str, initial: dict[str, Any]
    ) -> dict[str, Any]:
        """Review a chapter-level delta seed and apply only chunk-local patches."""
        result, attempts = initial, []
        for attempt in range(self.args.max_annotation_repairs + 1):
            stage = "delta_initial" if attempt == 0 else f"delta_correction_{attempt:02d}"
            review = await self.review_annotation(index, chunk, result, stage)
            attempts.append({"stage": stage, "annotation": result, "review": review})
            if review["verdict"] == "pass":
                return {"segments": result["segments"],
                        "grammar_overlays": result["grammar_overlays"],
                        "attempts": attempts, "resolved": True, "reviewed": True,
                        "mode": "constrained-delta"}
            if attempt < self.args.max_annotation_repairs:
                result = await self.constrained_annotation_correction(
                    index, chunk, result, review, attempt + 1
                )
        # The chapter seed is already fixed and lossless, so the bounded final
        # self-heal remains local to this failing chunk. Use a fresh cache key
        # and high-effort correction, followed by a genuinely independent
        # high-effort review. Never accept the correction deterministically.
        result = await self.constrained_annotation_correction(
            index, chunk, result, review,
            self.args.max_annotation_repairs + 1,
            effort=self.args.annotation_final_effort,
            stage="delta_final_correction",
        )
        review = await self.review_annotation(
            index, chunk, result, "delta_final",
            effort=self.args.annotation_final_effort,
        )
        attempts.append({
            "stage": "delta_final_correction",
            "annotation": result,
            "review": review,
            "correction_effort": self.args.annotation_final_effort,
            "review_effort": self.args.annotation_final_effort,
        })
        if review["verdict"] == "pass":
            return {
                "segments": result["segments"],
                "grammar_overlays": result["grammar_overlays"],
                "attempts": attempts, "resolved": True, "reviewed": True,
                "mode": "constrained-delta",
            }
        raise ValueError(
            f"constrained-delta annotation quality gate failed for chunk {index} "
            f"after {len(attempts)} reviewed attempts"
        )

    async def annotate_chapter_constrained_delta(
        self, chapter: str, chunks: list[str]
    ) -> list[dict[str, Any]]:
        """Bounded contextual metadata batches followed by the semantic gate."""
        from pipeline.delta_boundary_annotation import (
            SCHEMA as DELTA_SCHEMA, apply_delta, contextual_delta_batches,
            contextual_delta_targets, delta_batch_prompt, deterministic_baseline,
            boundary_covered_indices, collapse_identical_overrides, missing_required_prompt,
            normalize_batch_overrides, normalize_optional_boundary_patches,
            normalize_optional_overlays,
        )

        baseline, unknown = deterministic_baseline(chapter)
        target_set = contextual_delta_targets(baseline, unknown)
        batches = contextual_delta_batches(
            chapter, baseline, unknown, chunks,
            getattr(self.args, "annotation_metadata_batch_targets", 150),
        )
        seen_chunk_regions: set[tuple[int, int]] = set()

        async def one_batch(number: int, batch: dict[str, Any]) -> dict[str, Any]:
            region = (batch["start"], batch["end"])
            include_boundary = region not in seen_chunk_regions
            seen_chunk_regions.add(region)
            indices = set(batch["indices"])
            value = await self.runner.call(
                f"annotations/chapter_delta/batch_{number:04d}/initial",
                f"{self.annotation_chunk_cache_tag}\n"
                f"{delta_batch_prompt(chapter, baseline, indices, self.args.level, *region, include_boundary)}",
                DELTA_SCHEMA, self.args.annotation_effort, refresh=self.args.refresh,
            )
            value, override_evidence = normalize_batch_overrides(value, indices)
            collapsed = override_evidence["duplicate_collapses"]
            value, boundary_evidence = normalize_optional_boundary_patches(
                chapter, baseline, value,
            )
            returned = [item.get("index") for item in value.get("overrides", [])]
            returned_set = {item for item in returned if isinstance(item, int)}
            local_covered = boundary_covered_indices(chapter, baseline, value)
            if (len(returned) != len(returned_set)
                    or not returned_set <= indices):
                raise ValueError("metadata batch returned duplicate or non-batch indices")
            missing = indices - returned_set - local_covered
            calls = 1
            completion_collapsed = 0
            if missing:
                completion = await self.runner.call(
                    f"annotations/chapter_delta/batch_{number:04d}/missing_completion",
                    f"{self.annotation_chunk_cache_tag}\n"
                    f"{missing_required_prompt(chapter, baseline, missing, self.args.level)}",
                    DELTA_SCHEMA, self.args.annotation_repair_effort,
                    refresh=self.args.refresh,
                )
                completion, completion_collapsed = collapse_identical_overrides(completion)
                completion_overrides = completion.get("overrides", [])
                completion_indices = [item.get("index") for item in completion_overrides]
                if (any(not isinstance(index, int) for index in completion_indices)
                        or len(completion_indices) != len(set(completion_indices))
                        or set(completion_indices) != missing):
                    raise ValueError(
                        "missing-only delta completion must return every and only missing index"
                    )
                if completion.get("grammar_overlays") or completion.get("boundary_patches"):
                    raise ValueError(
                        "missing-only delta completion cannot add overlays or boundary patches"
                    )
                value["overrides"] = [*value.get("overrides", []), *completion_overrides]
                calls = 2
            return {
                "value": value, "calls": calls,
                "collapsed": collapsed + completion_collapsed,
                "completed": calls - 1,
                "boundary_evidence": boundary_evidence,
                "override_evidence": override_evidence,
            }

        batch_results = await gather_all_or_raise(*(
            one_batch(number, batch) for number, batch in enumerate(batches)
        ))
        raw = {
            "boundary_patches": sorted(
                [patch for item in batch_results
                 for patch in item["value"].get("boundary_patches", [])],
                key=lambda patch: (patch.get("start", -1), patch.get("end", -1)),
            ),
            "overrides": [override for item in batch_results
                          for override in item["value"].get("overrides", [])],
            "grammar_overlays": [overlay for item in batch_results
                                 for overlay in item["value"].get("grammar_overlays", [])],
        }
        raw, collapsed = collapse_identical_overrides(raw)
        self.annotation_duplicate_override_collapses = (
            collapsed + sum(item["collapsed"] for item in batch_results)
        )
        self.annotation_boundary_normalization = {
            "discarded_count": sum(
                item["boundary_evidence"]["discarded_count"] for item in batch_results
            ),
            "duplicate_collapses": sum(
                item["boundary_evidence"]["duplicate_collapses"] for item in batch_results
            ),
            "discarded": [entry for item in batch_results
                          for entry in item["boundary_evidence"]["discarded"]],
        }
        self.annotation_override_normalization = {
            "discarded_count": sum(
                item["override_evidence"]["discarded_count"] for item in batch_results
            ),
            "duplicate_collapses": sum(
                item["override_evidence"]["duplicate_collapses"] for item in batch_results
            ),
            "discarded": [entry for item in batch_results
                          for entry in item["override_evidence"]["discarded"]],
        }
        boundary_covered = boundary_covered_indices(chapter, baseline, raw)
        before = len(raw["overrides"])
        raw["overrides"] = [item for item in raw["overrides"]
                            if item.get("index") not in boundary_covered]
        self.annotation_boundary_override_drops = before - len(raw["overrides"])
        returned_indices = {item.get("index") for item in raw["overrides"]}
        if returned_indices != target_set - boundary_covered:
            raise ValueError("merged metadata batches do not cover every contextual target")
        raw, overlay_evidence = normalize_optional_overlays(chapter, raw)
        self.annotation_overlay_normalization = overlay_evidence
        self.annotation_metadata_batches = len(batches)
        self.annotation_metadata_completion_calls = sum(
            item["completed"] for item in batch_results
        )
        self.annotation_metadata_model_calls = sum(
            item["calls"] for item in batch_results
        )
        seeded = apply_delta(chapter, baseline, unknown, raw)
        policy = getattr(self.args, "annotation_review_policy", "chapter")
        if policy == "chapter":
            return await self.review_constrained_delta_chapter(chapter, chunks, seeded)
        if policy != "exhaustive-chunks":
            raise ValueError(f"unknown annotation review policy: {policy}")
        local = self.split_chapter_annotation(chapter, chunks, seeded)
        reviewed = await asyncio.gather(*(
            self.review_constrained_delta_chunk(index, chunk, annotation)
            for index, (chunk, annotation) in enumerate(zip(chunks, local))
        ), return_exceptions=True)
        failures = [
            f"chunk {index}: {type(item).__name__}: {item}"
            for index, item in enumerate(reviewed) if isinstance(item, BaseException)
        ]
        if failures:
            raise ValueError("; ".join(failures))
        self.annotation_semantic_review_calls = sum(
            len(item["attempts"]) for item in reviewed
        )
        return reviewed

    async def review_constrained_delta_chapter(
        self, chapter: str, chunks: list[str], seeded: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Comprehensive bounded whole-chapter semantic review and self-heal."""
        review = await self.review_annotation(
            0, chapter, seeded, "chapter_delta_initial",
            effort=self.args.annotation_review_effort,
        )
        attempts = [{
            "stage": "chapter_delta_initial", "annotation": seeded,
            "review": review,
        }]
        self.annotation_semantic_review_calls = 1
        result = seeded
        if review.get("verdict") != "pass":
            result, correction_evidence = await self.issue_scoped_chapter_correction(
                chapter, seeded, review, stage="chapter_delta_correction",
                effort=self.args.annotation_repair_effort,
            )
            final_review = await self.review_annotation(
                0, chapter, result, "chapter_delta_final",
                effort=self.args.annotation_review_effort,
            )
            self.annotation_semantic_review_calls = 2
            attempts.append({
                "stage": "chapter_delta_correction", "annotation": result,
                "review": final_review,
                "correction_effort": self.args.annotation_repair_effort,
                "review_effort": self.args.annotation_review_effort,
                "correction_evidence": correction_evidence,
            })
            if final_review.get("verdict") != "pass":
                result, remediation = await self.issue_scoped_chapter_correction(
                    chapter, result, final_review, stage="chapter_delta_remediation",
                    effort=self.args.annotation_final_effort,
                )
                if not remediation["exact_coverage"]:
                    raise ValueError("final remediation lacked exact finding coverage")
                self.annotation_remediation_calls = remediation["calls"]
                self.annotation_final_review_verdict = "revise"
                self.annotation_acceptance_state = "reviewed_and_remediated"
                attempts.append({
                    "stage": "chapter_delta_remediation", "annotation": result,
                    "review": final_review, "correction_effort": self.args.annotation_final_effort,
                    "correction_evidence": remediation,
                })
            else:
                self.annotation_remediation_calls = 0
                self.annotation_final_review_verdict = "pass"
                self.annotation_acceptance_state = "reviewed_pass"
        else:
            self.annotation_remediation_calls = 0
            self.annotation_final_review_verdict = "pass"
            self.annotation_acceptance_state = "reviewed_pass"
        local = self.split_chapter_annotation(chapter, chunks, result)
        for item in local:
            item.update({
                "attempts": copy.deepcopy(attempts), "resolved": True,
                "reviewed": True, "mode": "constrained-delta",
                "acceptance_state": self.annotation_acceptance_state,
            })
        return local

    async def run(self) -> dict[str, Any]:
        self.write_manifest("running")
        try:
            outline = await self.outline()
            results = await gather_all_or_raise(
                *(self.process_scene(scene) for scene in outline["scenes"])
            )
            chapter = "\n\n".join(result["text"].strip() for result in results) + "\n"
            verdicts = {
                r["scene"]["id"]: r["review"]["verdict"] for r in results
            }
            final_status = run_status_for_verdicts(verdicts)
            publish_chapter_candidate(self.run_dir, chapter, final_status)
            if final_status == "complete" and not self.args.skip_annotations:
                chunks = self.chinese_annotation_chunks(chapter)
                if getattr(self.args, "annotation_mode", "generative") == "constrained-delta":
                    annotated = await self.annotate_chapter_constrained_delta(chapter, chunks)
                else:
                    annotated = await gather_all_or_raise(
                        *(self.annotate_chunk(index, chunk) for index, chunk in enumerate(chunks))
                    )
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
                reader = {
                    "title": outline["chapter_title"], "level": self.args.level.upper(),
                    "text": chapter, "segments": segments,
                    "grammar_overlays": grammar_overlays,
                    "annotation_audit": {
                        "mode": getattr(self.args, "annotation_mode", "generative"),
                        "chunks": len(annotated),
                        "attempts_per_chunk": [len(item["attempts"]) for item in annotated],
                        "all_reviewed": all(
                            item["resolved"] and item.get("reviewed", True) is True
                            and (item["attempts"][-1].get("review", {}).get("verdict") == "pass"
                                 or item.get("acceptance_state") == "reviewed_and_remediated")
                            for item in annotated
                        ),
                        **({"metadata_model_calls": getattr(
                            self, "annotation_metadata_model_calls", 1
                        )}
                           if getattr(self.args, "annotation_mode", "generative")
                           == "constrained-delta" else {}),
                        **({
                            "review_policy": getattr(
                                self.args, "annotation_review_policy", "chapter"
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
                        } if getattr(self.args, "annotation_mode", "generative")
                           == "constrained-delta" else {}),
                    },
                }
                (self.run_dir / "reader.json").write_text(
                    json.dumps(reader, ensure_ascii=False, indent=2) + "\n"
                )
            report = {
                "status": final_status, "scenes": len(results),
                "chapter_cjk": cjk_count(chapter),
                "scene_verdicts": verdicts,
                "scene_attempts": {
                    r["scene"]["id"]: len(r["attempts"]) for r in results
                },
                "unresolved_scenes": [
                    scene_id for scene_id, verdict in verdicts.items()
                    if verdict != "pass"
                ],
            }
            (self.run_dir / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            )
            self.write_manifest(final_status)
            return report
        except BaseException:
            self.write_manifest("failed")
            raise


class AnnotationOnlyHarness(ChapterHarness):
    """Annotate already-approved prose without invoking the adaptation stages."""

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.source_path = Path(args.source).resolve()
        self.source = self.source_path.read_text(encoding="utf-8")
        self.chapter_path = Path(args.chapter).resolve()
        self.chapter = self.chapter_path.read_text(encoding="utf-8")
        self.run_dir = Path(args.run_dir).resolve()
        if self.run_dir.exists() and any(self.run_dir.iterdir()) and not args.resume:
            raise ValueError(
                f"annotation output directory is not empty: {self.run_dir}; "
                "pass --resume to explicitly target it"
            )
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.runner = CodexRunner(
            self.run_dir, args.model, asyncio.Semaphore(args.concurrency), args.timeout
        )

    def write_annotation_manifest(self, status: str) -> None:
        payload = {
            "run_id": self.run_dir.name,
            "kind": "annotation-only",
            "status": status,
            "source": str(self.source_path),
            "source_sha256": digest(self.source),
            "chapter_input": str(self.chapter_path),
            "chapter_sha256": hashlib.sha256(
                self.chapter.encode("utf-8")
            ).hexdigest(),
            "level": self.args.level,
            "model": self.args.model,
            "annotation_mode": self.args.annotation_mode,
            "annotation_review_policy": getattr(
                self.args, "annotation_review_policy", "chapter"
            ),
            "annotation_chunk_policy": CHINESE_ANNOTATION_CHUNK_POLICY,
            "annotation_chunk_target": self.annotation_chunk_target,
            "annotation_chunk_maximum": self.annotation_chunk_maximum,
            "annotation_review_effort": self.args.annotation_review_effort,
            "annotation_repair_effort": self.args.annotation_repair_effort,
            "annotation_final_effort": self.args.annotation_final_effort,
            "max_annotation_repairs": self.args.max_annotation_repairs,
            "updated_at": utc_now(),
        }
        (self.run_dir / "manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    async def run_annotation_only(self) -> dict[str, Any]:
        self.write_annotation_manifest("running")
        try:
            chunks = self.chinese_annotation_chunks(self.chapter)
            if self.args.annotation_mode == "constrained-delta":
                annotated = await self.annotate_chapter_constrained_delta(
                    self.chapter, chunks
                )
            else:
                annotated = await gather_all_or_raise(*(
                    self.annotate_chunk(index, chunk)
                    for index, chunk in enumerate(chunks)
                ))
            segments = [segment for item in annotated for segment in item["segments"]]
            reconstruction = "".join(segment["text"] for segment in segments)
            if reconstruction != self.chapter:
                raise ValueError("assembled annotations do not reconstruct chapter")
            overlays, offset = [], 0
            for chunk, item in zip(chunks, annotated):
                overlays.extend({
                    **overlay,
                    "start": overlay["start"] + offset,
                    "end": overlay["end"] + offset,
                } for overlay in item["grammar_overlays"])
                offset += len(chunk)
            attempts = [len(item["attempts"]) for item in annotated]
            all_reviewed = all(
                item["resolved"] and item.get("reviewed", True) is True
                and (item["attempts"][-1].get("review", {}).get("verdict") == "pass"
                     or item.get("acceptance_state") == "reviewed_and_remediated")
                for item in annotated
            )
            audit = {
                "mode": self.args.annotation_mode,
                "chunks": len(annotated),
                "attempts_per_chunk": attempts,
                "all_reviewed": all_reviewed,
                **({"metadata_model_calls": getattr(
                    self, "annotation_metadata_model_calls", 1
                )}
                   if self.args.annotation_mode == "constrained-delta" else {}),
                **({
                    "review_policy": getattr(
                        self.args, "annotation_review_policy", "chapter"
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
            }
            reader = {
                "title": self.args.title or self.chapter_path.stem,
                "level": self.args.level.upper(),
                "text": self.chapter,
                "segments": segments,
                "grammar_overlays": overlays,
                "annotation_audit": audit,
            }
            reader_path = self.run_dir / "reader.json"
            reader_path.write_text(
                json.dumps(reader, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            # Preserve an exact local copy only in this explicitly selected,
            # isolated run. Never modify the input chapter in place.
            (self.run_dir / "chapter.txt").write_text(self.chapter, encoding="utf-8")
            report = {
                "status": "complete" if all_reviewed else "blocked",
                "mode": self.args.annotation_mode,
                "chunks": len(chunks),
                "attempts_per_chunk": attempts,
                "model_calls": sum(
                    1 for meta_path in (self.run_dir / "agents").glob("**/meta.json")
                    if json.loads(meta_path.read_text(encoding="utf-8"))
                    .get("return_code") == 0
                ),
                "exact_reconstruction": reconstruction == self.chapter,
                "chapter_sha256": hashlib.sha256(
                    self.chapter.encode("utf-8")
                ).hexdigest(),
                "reader_sha256": hashlib.sha256(reader_path.read_bytes()).hexdigest(),
            }
            (self.run_dir / "annotation-report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self.write_annotation_manifest(report["status"])
            return report
        except BaseException:
            model_calls = sum(
                1 for meta_path in (self.run_dir / "agents").glob("**/meta.json")
                if json.loads(meta_path.read_text(encoding="utf-8"))
                .get("return_code") == 0
            )
            chunks = self.chinese_annotation_chunks(self.chapter)
            (self.run_dir / "annotation-report.json").write_text(
                json.dumps({
                    "status": "failed",
                    "mode": self.args.annotation_mode,
                    "chunks": len(chunks),
                    "model_calls": model_calls,
                    "exact_input_preservation": (
                        self.chapter_path.read_text(encoding="utf-8") == self.chapter
                    ),
                    "exact_reconstruction": False,
                    "chapter_sha256": hashlib.sha256(
                        self.chapter.encode("utf-8")
                    ).hexdigest(),
                }, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self.write_annotation_manifest("failed")
            raise


class BookHarness:
    """Coordinate chapter/level runs under one global agent-process budget."""

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.run_dir = Path(args.runs_dir).resolve() / args.book_run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.agent_semaphore = asyncio.Semaphore(args.concurrency)
        self.chapter_semaphore = asyncio.Semaphore(args.chapter_concurrency)

    def write_report(
        self, state: str, runs: list[dict[str, Any]],
        violations: list[dict[str, Any]] | None = None,
    ) -> None:
        payload = {
            "book_run_id": self.args.book_run_id, "status": state,
            "updated_at": utc_now(),
            "global_agent_concurrency": self.args.concurrency,
            "chapter_concurrency": self.args.chapter_concurrency, "runs": runs,
            "length_violations": violations or [],
        }
        (self.run_dir / "book-report.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        )

    async def run_one(
        self, source: str, level: str, target_chars: int | None = None
    ) -> dict[str, Any]:
        source_path = Path(source).resolve()
        run_id = f"{self.args.book_run_id}/{source_path.stem}-{level}"
        chapter_args = copy.copy(self.args)
        chapter_args.command, chapter_args.source = "run", str(source_path)
        chapter_args.level, chapter_args.run_id = level, run_id
        chapter_args.target_chars = target_chars or (
            self.args.target_chars or DEFAULT_LEVEL_TARGETS.get(level, 5000)
        )
        last_error = ""
        async with self.chapter_semaphore:
            for attempt in range(self.args.chapter_retries + 1):
                try:
                    report = await ChapterHarness(
                        chapter_args, semaphore=self.agent_semaphore
                    ).run()
                    if report["status"] != "complete":
                        last_error = (
                            "quality gates unresolved: "
                            + ", ".join(report.get("unresolved_scenes", []))
                        )
                        if attempt < self.args.chapter_retries:
                            # A completed-but-rejected final candidate is cached
                            # correctly. A genuinely fresh chapter attempt must
                            # bypass that cache to self-heal unattended.
                            chapter_args.refresh = True
                            await asyncio.sleep(min(60, 5 * (3 ** attempt)))
                            continue
                    return {
                        "source": str(source_path), "level": level,
                        "run_id": run_id, "status": report["status"],
                        "chapter_cjk": report["chapter_cjk"],
                        "target_chars": chapter_args.target_chars,
                        "attempts": attempt + 1,
                    }
                except Exception as exc:
                    # Valid prior jobs resume; only failed/incomplete jobs execute again.
                    last_error = f"{type(exc).__name__}: {exc}"
                    if attempt < self.args.chapter_retries:
                        # Reusing the fingerprint-identical result would repeat
                        # malformed JSON, invalid boundaries, or another
                        # deterministic cached failure forever.
                        chapter_args.refresh = True
                        # Avoid synchronized retry storms when the model service
                        # throttles several chapters at once.
                        await asyncio.sleep(min(60, 5 * (3 ** attempt)))
            return {
                "source": str(source_path), "level": level, "run_id": run_id,
                "status": "failed", "attempts": self.args.chapter_retries + 1,
                "error": last_error,
            }

    async def run(self) -> dict[str, Any]:
        planned = [
            {"source": str(Path(source).resolve()), "level": level, "status": "pending"}
            for source in self.args.source for level in self.args.levels
        ]
        self.write_report("running", planned)
        tasks = [
            asyncio.create_task(self.run_one(item["source"], item["level"]))
            for item in planned
        ]
        results: list[dict[str, Any]] = []
        for task in asyncio.as_completed(tasks):
            results.append(await task)
            self.write_report("running", results)
        # Length is a publication invariant, not a manual-review request. Re-run
        # only the deficient upper-level chapter with a stronger target; changed
        # prompt fingerprints invalidate affected generation jobs while unrelated
        # chapters remain cached.
        for _ in range(self.args.length_repair_rounds):
            violations = length_violations(results, self.args.levels)
            if not violations:
                break
            for problem in violations:
                current = next(
                    item for item in results
                    if (item["source"], item["level"]) ==
                    (problem["source"], problem["upper_level"])
                )
                target = max(
                    DEFAULT_LEVEL_TARGETS.get(problem["upper_level"], 5000),
                    problem["lower_cjk"] + max(50, problem["lower_cjk"] // 10),
                    int(current.get("target_chars", 0) * 1.25),
                )
                repaired = await self.run_one(
                    problem["source"], problem["upper_level"], target
                )
                for index, item in enumerate(results):
                    if (item["source"], item["level"]) == (
                        problem["source"], problem["upper_level"]
                    ):
                        results[index] = repaired
                        break
                self.write_report("running", results)
        statuses = {item["status"] for item in results}
        violations = length_violations(results, self.args.levels)
        final_state = (
            "complete" if statuses == {"complete"} and not violations else "blocked"
        )
        self.write_report(final_state, results, violations)
        return {"status": final_state, "runs": results}


def status(run_dir: Path) -> int:
    if not run_dir.exists():
        print(f"Run not found: {run_dir}", file=sys.stderr)
        return 1
    for name in ("manifest.json", "report.json"):
        path = run_dir / name
        if path.exists():
            print(path.read_text(), end="")
    agents = list((run_dir / "agents").glob("**/meta.json")) if (run_dir / "agents").exists() else []
    print(f"agent_jobs={len(agents)}")
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--source", required=True)
    run.add_argument("--level", default="hsk4")
    run.add_argument("--run-id")
    run.add_argument("--runs-dir", default=str(DEFAULT_RUNS))
    run.add_argument("--model", default="gpt-5.6-luna")
    run.add_argument("--adapt-effort", default="xhigh")
    run.add_argument("--review-effort", default="medium")
    run.add_argument("--repair-effort", default="xhigh")
    run.add_argument("--final-effort", default="max")
    run.add_argument("--annotation-effort", default="low")
    run.add_argument(
        "--annotation-mode", choices=("generative", "constrained", "constrained-delta"),
        default="generative",
        help="opt in to deterministic proposals and strictly lossless correction patches",
    )
    run.add_argument("--annotation-review-effort", default="medium")
    run.add_argument(
        "--annotation-review-policy",
        choices=("chapter", "exhaustive-chunks"), default="chapter",
        help="whole-chapter production gate or expensive chunk-by-chunk smoke audit",
    )
    run.add_argument("--annotation-repair-effort", default="medium")
    run.add_argument("--annotation-final-effort", default="high")
    run.add_argument("--annotation-metadata-batch-targets", type=int, default=150)
    run.add_argument("--max-annotation-repairs", type=int, default=2)
    run.add_argument("--max-repairs", type=int, default=3)
    run.add_argument(
        "--no-fresh-rewrite", dest="fresh_rewrite", action="store_false"
    )
    run.set_defaults(fresh_rewrite=True)
    run.add_argument(
        "--annotation-chunk", type=int,
        default=DEFAULT_CHINESE_ANNOTATION_CHUNK_TARGET,
    )
    run.add_argument(
        "--annotation-chunk-maximum", type=int,
        default=None,
    )
    run.add_argument("--target-chars", type=int)
    run.add_argument("--concurrency", type=int, default=3)
    run.add_argument("--timeout", type=int, default=900)
    run.add_argument("--skip-annotations", action="store_true")
    run.add_argument("--refresh", action="store_true")
    annotate = sub.add_parser(
        "annotate", help="annotate an existing exact chapter without adapting prose"
    )
    annotate.add_argument("--chapter", required=True,
                          help="approved adapted chapter.txt to annotate read-only")
    annotate.add_argument("--source", required=True,
                          help="original source chapter retained for provenance")
    annotate.add_argument("--run-dir", required=True,
                          help="isolated output directory")
    annotate.add_argument("--level", required=True)
    annotate.add_argument("--title")
    annotate.add_argument("--model", default="gpt-5.6-luna")
    annotate.add_argument("--annotation-effort", default="low")
    annotate.add_argument(
        "--annotation-mode", choices=("generative", "constrained", "constrained-delta"),
        default="constrained-delta",
    )
    annotate.add_argument("--annotation-review-effort", default="medium")
    annotate.add_argument(
        "--annotation-review-policy",
        choices=("chapter", "exhaustive-chunks"), default="chapter",
        help="whole-chapter production gate or expensive chunk-by-chunk smoke audit",
    )
    annotate.add_argument("--annotation-repair-effort", default="medium")
    annotate.add_argument("--annotation-final-effort", default="high")
    annotate.add_argument("--annotation-metadata-batch-targets", type=int, default=150)
    annotate.add_argument("--max-annotation-repairs", type=int, default=2)
    annotate.add_argument(
        "--annotation-chunk", type=int,
        default=DEFAULT_CHINESE_ANNOTATION_CHUNK_TARGET,
    )
    annotate.add_argument(
        "--annotation-chunk-maximum", type=int,
        default=None,
    )
    annotate.add_argument("--concurrency", type=int, default=1)
    annotate.add_argument("--timeout", type=int, default=900)
    annotate.add_argument("--refresh", action="store_true")
    annotate.add_argument("--resume", action="store_true",
                          help="explicitly allow reuse of a nonempty output directory")
    book = sub.add_parser("book", help="run multiple chapters and levels")
    book.add_argument("--source", action="append",
                      help="chapter source path; repeat for more chapters")
    book.add_argument(
        "--source-dir", help="directory containing chapter_*.txt sources"
    )
    book.add_argument("--levels", nargs="+", default=["hsk1", "hsk2", "hsk3", "hsk4", "hsk5", "hsk6"])
    book.add_argument("--book-run-id", required=True)
    book.add_argument("--runs-dir", default=str(DEFAULT_RUNS))
    book.add_argument("--model", default="gpt-5.6-luna")
    book.add_argument("--adapt-effort", default="xhigh")
    book.add_argument("--review-effort", default="medium")
    book.add_argument("--repair-effort", default="xhigh")
    book.add_argument("--final-effort", default="max")
    book.add_argument("--annotation-effort", default="low")
    book.add_argument(
        "--annotation-mode", choices=("generative", "constrained", "constrained-delta"),
        default="generative",
        help="opt in to deterministic proposals and strictly lossless correction patches",
    )
    book.add_argument("--annotation-review-effort", default="medium")
    book.add_argument(
        "--annotation-review-policy",
        choices=("chapter", "exhaustive-chunks"), default="chapter",
        help="whole-chapter production gate or expensive chunk-by-chunk smoke audit",
    )
    book.add_argument("--annotation-repair-effort", default="medium")
    book.add_argument("--annotation-final-effort", default="high")
    book.add_argument("--annotation-metadata-batch-targets", type=int, default=150)
    book.add_argument("--max-annotation-repairs", type=int, default=2)
    book.add_argument("--max-repairs", type=int, default=3)
    book.add_argument("--no-fresh-rewrite", dest="fresh_rewrite", action="store_false")
    book.set_defaults(fresh_rewrite=True)
    book.add_argument(
        "--annotation-chunk", type=int,
        default=DEFAULT_CHINESE_ANNOTATION_CHUNK_TARGET,
    )
    book.add_argument(
        "--annotation-chunk-maximum", type=int,
        default=None,
    )
    book.add_argument("--target-chars", type=int)
    book.add_argument("--concurrency", type=int, default=6,
                      help="global maximum simultaneous agent processes")
    book.add_argument("--chapter-concurrency", type=int, default=2)
    book.add_argument("--chapter-retries", type=int, default=2)
    book.add_argument("--length-repair-rounds", type=int, default=2)
    book.add_argument("--timeout", type=int, default=900)
    book.add_argument("--skip-annotations", action="store_true")
    book.add_argument("--refresh", action="store_true")
    stat = sub.add_parser("status")
    stat.add_argument("run_dir")
    return p


def main() -> int:
    args = parser().parse_args()
    if args.command == "status":
        return status(Path(args.run_dir))
    if args.command == "book":
        if args.source_dir:
            discovered = sorted(Path(args.source_dir).glob("chapter_*.txt"))
            args.source = (args.source or []) + [str(path) for path in discovered]
        if not args.source:
            parser().error("book requires --source or --source-dir with chapter_*.txt")
        result = asyncio.run(BookHarness(args).run())
        return 0 if result["status"] == "complete" else 2
    if args.command == "annotate":
        result = asyncio.run(AnnotationOnlyHarness(args).run_annotation_only())
        return 0 if result["status"] == "complete" else 2
    asyncio.run(ChapterHarness(args).run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
