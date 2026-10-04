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

from pipeline.chunk_scheduler import map_chunks
from pipeline.annotation_review_guidance import FORM_STAGE_EVIDENCE_GUIDANCE
from pipeline.annotation_issue_targets import ISSUE_TARGET_GUIDANCE, validate_issue_targets
from pipeline.annotation_publication import (
    bind_review_job, child_job_receipt, normal_review_receipt,
    persist_chunk_attempts, verify_normal_review_receipt,
    verify_child_job_receipt,
)

from jsonschema import Draft202012Validator
from opencc import OpenCC
from pipeline.chinese_translation_policy import CHINESE_TRANSLATION_POLICY

from pipeline.adaptation_policy import (
    policy_for,
    target_length_bounds,
)
from pipeline.chinese_readability import (
    PROSE_SHAPE_LIMITS,
    hsk1_prompt_guidance,
    lower_level_qualification_ratio,
    validate_beginner_chinese,
    validate_level_distinctiveness,
    validate_paragraph_structure,
    words_at_level,
)
from pipeline.validate_text import load_charset, validate_characters


ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = Path(__file__).resolve().parent / "schemas"
DEFAULT_RUNS = ROOT / "runs" / "graded-readers"
_T2S = OpenCC("t2s")
_GRAMMAR_CANDIDATE_KEY = re.compile(r"^[a-z][a-z0-9_.-]*$")


def chinese_semantic_repair_grammar_knowledge(annotation: dict[str, Any]) -> dict[str, Any]:
    """Describe Chinese grammar identity availability without implying approval.

    Chinese annotations use provisional clustering keys and have no canonical
    reviewed grammar registry for this pipeline to load.
    """
    keys = sorted({
        str(overlay.get("grammar_candidate_key", ""))
        for overlay in annotation.get("grammar_overlays", [])
        if isinstance(overlay, dict) and overlay.get("grammar_candidate_key")
    })
    return {
        "catalog_status": "provisional_keys_only",
        "catalog_source": None,
        "approved_entries": [],
        "candidate_keys_in_base": keys,
        "identity_policy": (
            "Chinese grammar_candidate_key values are provisional clustering hints, "
            "not approved lesson identities. Correct or introduce a distinct "
            "evidence-supported provisional key only when the reviewed function "
            "requires it; downstream grammar unification and review determine "
            "canonical lesson identities. Do not infer a function from spelling alone."
        ),
    }
CHINESE_PINYIN_POLICY = """PINYIN POLICY: For learner-facing Standard Mandarin,
represent natural pronunciation in ordinary, unstressed connected speech.
Infer each syllable's pronunciation from its grammatical role and local context,
especially in directional complements: grammaticalized, unstressed syllables
that normally take neutral tone should be unmarked, even when dictionary hints
show their full lexical tones. For example, 泛上来 `fàn shànglai` and 走进去
`zǒu jìnqu` illustrate this principle; apply it to other constructions as context
warrants. Preserve full tones for independent lexical verbs or clearly stressed
uses. Do not mechanically neutralize every complement syllable. Review whether
the written pinyin matches this contextual pronunciation, not just a dictionary."""


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


def scene_repair_strategy(
    adaptation: str,
    review: dict[str, Any],
    minimum_chars: int,
    maximum_chars: int,
) -> str:
    """Give repairs a narrow operation instead of inviting another rewrite.

    Long-form chapters are assembled from independently accepted scenes.  A
    scene that is already accurate but mechanically short should therefore be
    expanded in place, while a scene with one factual defect should change only
    the implicated sentence.  Broad rewrites made these two cases oscillate.
    """
    length = cjk_count(adaptation)
    findings = []
    for key in (
        "omissions", "unsupported_additions", "distortions",
        "language_problems",
    ):
        for finding in review.get(key, []):
            text = str(finding).strip()
            if (
                text
                and not text.startswith("mechanical ")
                and text != "review scores must each be at least 8/10"
            ):
                findings.append(text)

    if length < minimum_chars and not findings:
        deficit = minimum_chars - length
        return f"""RECOVERY MODE: accurate length-only expansion.
The existing adaptation has {length} Chinese characters and no semantic
finding. Preserve every existing sentence and claim. Add approximately
{deficit + 12} Chinese characters ({deficit} is the strict minimum), using
only concrete details stated in ORIGINAL and belonging to REQUIRED EVENTS.
Do not replace the passage with a fresh retelling, add conclusions, invent
causes, or delete material. End within {minimum_chars}-{maximum_chars}."""

    if findings:
        length_note = (
            f" It is also {minimum_chars - length} characters below the strict "
            f"minimum, so after fixing the cited sentence, add source-grounded "
            f"detail until it reaches {minimum_chars}-{maximum_chars}."
            if length < minimum_chars else
            f" Keep the result within {minimum_chars}-{maximum_chars} characters."
        )
        return f"""RECOVERY MODE: surgical factual repair.
Keep all sentences not implicated by the explicit findings. Change or remove
only the smallest sentence span needed to resolve them; do not recast the
whole scene. Replace removed material with accurate detail from the same place
in ORIGINAL when necessary.{length_note}"""

    return f"""RECOVERY MODE: localized prose repair.
Preserve the scene's event selection and unaffected sentences. Make the
smallest changes needed for the review scores and remain within
{minimum_chars}-{maximum_chars} Chinese characters."""


def scene_attempt_rank(
    attempt: dict[str, Any],
    minimum_chars: int,
    maximum_chars: int,
) -> tuple[int, float, int, int, float]:
    """Rank failed attempts without allowing length to hide factual defects."""
    review = attempt.get("review", {})
    substantive = 0
    other_mechanical = 0
    for key in (
        "omissions", "unsupported_additions", "distortions",
        "language_problems",
    ):
        for finding in review.get(key, []):
            text = str(finding).strip()
            if not text or text == "review scores must each be at least 8/10":
                continue
            if text.startswith("mechanical length gate:"):
                continue
            if text.startswith("mechanical "):
                other_mechanical += 1
            else:
                substantive += 1
    scores = [
        float(review.get(key, 0) or 0)
        for key in ("source_fidelity", "naturalness", "readability")
    ]
    minimum_score = min(scores)
    score_deficit = max(0.0, 8.0 - minimum_score)
    length = cjk_count(str(attempt.get("text", "")))
    length_distance = (
        minimum_chars - length if length < minimum_chars else
        length - maximum_chars if length > maximum_chars else 0
    )
    return (
        substantive,
        score_deficit,
        other_mechanical,
        length_distance,
        -minimum_score,
    )


def best_scene_attempt(
    attempts: list[dict[str, Any]],
    minimum_chars: int,
    maximum_chars: int,
) -> dict[str, Any]:
    """Return the strongest preserved attempt instead of blindly using last."""
    return min(
        attempts,
        key=lambda attempt: scene_attempt_rank(
            attempt, minimum_chars, maximum_chars
        ),
    )


def accept_distributed_scene_lengths(
    results: list[dict[str, Any]],
    chapter: str,
    level: str,
    target_chars: int,
) -> list[str]:
    """Let naturally short scenes pass when the chapter carries its full weight.

    This never suppresses a semantic, readability, distinctiveness, paragraph,
    or score failure.  It only moves the mechanical length budget from every
    individual scene to the already assembled chapter.
    """
    chapter_low, chapter_high = target_length_bounds(target_chars, level)
    chapter_length = cjk_count(chapter)
    if not chapter_low <= chapter_length <= chapter_high:
        return []
    accepted = []
    for result in results:
        review = result.get("review", {})
        if review.get("verdict") == "pass":
            continue
        scores = [
            float(review.get(key, 0) or 0)
            for key in ("source_fidelity", "naturalness", "readability")
        ]
        if min(scores) < 8:
            continue
        length_findings = []
        other_findings = []
        for key in (
            "omissions", "unsupported_additions", "distortions",
            "language_problems",
        ):
            for finding in review.get(key, []):
                text = str(finding).strip()
                if not text or text == "review scores must each be at least 8/10":
                    continue
                if text.startswith("mechanical length gate:"):
                    length_findings.append(text)
                else:
                    other_findings.append(text)
        if not length_findings or other_findings:
            continue
        updated = dict(review)
        updated["language_problems"] = [
            item for item in review.get("language_problems", [])
            if not str(item).startswith("mechanical length gate:")
            and str(item) != "review scores must each be at least 8/10"
        ]
        updated["verdict"] = "pass"
        updated["harness_decision"] = (
            "accepted_distributed_scene_length_with_passing_chapter_budget"
        )
        result["review"] = updated
        result["resolved"] = True
        accepted.append(result["scene"]["id"])
    return accepted


def normalize_review_score_scale(review: dict[str, Any]) -> dict[str, Any]:
    """Normalize occasional 0-1 reviewer scores to the schema's 0-10 scale."""
    keys = ("source_fidelity", "naturalness", "readability")
    scores = [review.get(key) for key in keys]
    if all(isinstance(value, (int, float)) and 0 <= value <= 1 for value in scores):
        result = dict(review)
        for key, value in zip(keys, scores):
            result[key] = round(float(value) * 10, 3)
        result["harness_score_normalization"] = "proportion_to_zero_ten"
        return result
    return review


def length_violations(runs: list[dict[str, Any]], levels: list[str]) -> list[dict[str, Any]]:
    """Compatibility hook; chapter length is no longer a cross-level invariant."""
    del runs, levels
    return []


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


def is_verified_adjudicated_annotation(item: dict[str, Any], run_dir: Path | None = None) -> bool:
    """Replay the saved adjudication against its exact candidate and inputs."""
    effective = item.get("effective_review")
    proof = effective.get("evidence") if isinstance(effective, dict) else None
    attempts = item.get("attempts", [])
    attempt = attempts[-1] if attempts and isinstance(attempts[-1], dict) else None
    replay = attempt.get("adjudication_replay") if isinstance(attempt, dict) else None
    review = attempt.get("review") if isinstance(attempt, dict) else None
    if (not isinstance(proof, dict) or effective.get("kind") != "adjudicated"
            or not isinstance(replay, dict) or not isinstance(review, dict)
            or review.get("verdict") != "revise" or run_dir is None):
        return False
    try:
        from pipeline.annotation_adjudication import (
            digest as evidence_digest, verify_adjudication_evidence,
        )
        candidate = {"segments": item.get("segments"),
                     "grammar_overlays": item.get("grammar_overlays")}
        verified = verify_adjudication_evidence(
            Path(run_dir), proof, language="zh", representation="chinese-annotation",
            candidate=candidate, current_review=review,
            prior_history=replay["prior_history"], context=replay["context"],
            known_reference_input=replay["known_reference_input"],
            deterministic_gate_evidence=replay["deterministic_gate_evidence"],
            normal_review_receipt=replay["normal_review_receipt"],
            source_text=replay["source_text"])
        if (verified != proof or verified.get("status") != "cleared"
                or verified.get("approved") is not True):
            return False
        source_text = replay["source_text"]
        if (replay.get("context") != {
                "chunk_text": source_text,
                "grammar_knowledge": chinese_semantic_repair_grammar_knowledge(candidate)}
                or not isinstance(source_text, str)
                or "".join(str(row.get("text", "")) for row in candidate["segments"]
                           if isinstance(row, dict)) != source_text
                or not ChapterHarness.annotation_reconstructs(source_text, candidate)
                or ChapterHarness.annotation_contract_issues(source_text, candidate)):
            return False
        manifest = json.loads((Path(run_dir) / "manifest.json").read_text(encoding="utf-8"))
        focus = json.loads((Path(run_dir) / "focus-vocabulary-plan.json").read_text(encoding="utf-8"))
        target, maximum = manifest.get("annotation_chunk_target"), manifest.get("annotation_chunk_maximum")
        if not isinstance(target, int) or not isinstance(maximum, int):
            return False
        semantic_guidance = json.dumps({
            "names": [{"surface": str(row["surface"])} for row in focus.get("names", [])],
            "story_terms": [{"surface": str(row["surface"]),
                             "meaning_en": str(row.get("meaning_en", ""))}
                            for row in focus.get("story_terms", [])],
        }, ensure_ascii=False, separators=(",", ":"))
        expected_references = {"review-policy": {"kind": "explicit_review_policy",
            "content": {
                "review_prompt": (f"CHINESE_ANNOTATION_CHUNK_POLICY={CHINESE_ANNOTATION_CHUNK_POLICY};"
                                  f"target={target};maximum={maximum}"),
                "semantic_guidance": semantic_guidance,
                "translation_policy": CHINESE_TRANSLATION_POLICY,
                "pinyin_policy": CHINESE_PINYIN_POLICY,
            }}}
        if replay.get("known_reference_input") != expected_references:
            return False
        expected_gate = {"passed": True, "issues": [],
                         "candidate_digest": evidence_digest(candidate),
                         "source_text_digest": evidence_digest(source_text)}
        if replay.get("deterministic_gate_evidence") != expected_gate:
            return False
        if replay.get("prior_history") != attempts[:-1]:
            return False
        receipt = replay.get("normal_review_receipt")
        components = receipt.get("components", []) if isinstance(receipt, dict) else []
        if len(components) != 1:
            return False
        component = components[0]
        job = component.get("job", "")
        if (not isinstance(job, str) or not job or Path(job).is_absolute()
                or any(part in {"", ".", ".."} for part in Path(job).parts)):
            return False
        if (component.get("candidate_digest") != evidence_digest(candidate)
                or component.get("source_digest") != evidence_digest(source_text)):
            return False
        job_dir = Path(run_dir) / "agents" / job
        if job_dir.is_symlink() or not job_dir.resolve(strict=True).is_relative_to(
                (Path(run_dir) / "agents").resolve(strict=True)):
            return False
        meta = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
        raw_review = verify_child_job_receipt(run_dir, component)
        if (meta.get("return_code") != 0 or meta.get("model") != "gpt-6-luna"
                or meta.get("effort") != "low"
                or meta.get("fingerprint") != component.get("input_digest")
                or evidence_digest(raw_review) != component.get("result_digest")):
            return False
        saved_review = dict(review)
        offset_audit = saved_review.pop("offset_audit", None)
        if saved_review != raw_review:
            return False
        valid = legacy = invalid = 0
        for issue in raw_review.get("issues", []):
            if not isinstance(issue, dict):
                invalid += 1
            elif "start" not in issue and "end" not in issue:
                legacy += 1
            elif (isinstance(issue.get("start"), int) and isinstance(issue.get("end"), int)
                  and 0 <= issue["start"] < issue["end"] <= len(source_text)
                  and issue.get("segment_text") == source_text[issue["start"]:issue["end"]]):
                valid += 1
            else:
                invalid += 1
        if offset_audit != {"valid": valid, "legacy_missing": legacy, "invalid": invalid}:
            return False
        CodexRunner._check_tool_profile(job_dir, meta.get("tool_profile", "offline"), meta)
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def is_verified_ordinary_chinese_review(item: dict[str, Any], run_dir: Path) -> bool:
    """Require the saved ordinary review child behind a Chinese pass."""
    attempts = item.get("attempts", [])
    if not attempts or not isinstance(attempts[-1], dict):
        return False
    attempt = attempts[-1]
    review, candidate = attempt.get("review"), attempt.get("annotation")
    if (not isinstance(review, dict) or review.get("verdict") != "pass"
            or not isinstance(candidate, dict)):
        return False
    source_text = "".join(str(row.get("text", "")) for row in candidate.get("segments", [])
                          if isinstance(row, dict))
    if (candidate.get("segments") != item.get("segments")
            or candidate.get("grammar_overlays") != item.get("grammar_overlays")
            or not ChapterHarness.annotation_reconstructs(source_text, candidate)
            or ChapterHarness.annotation_contract_issues(source_text, candidate)):
        return False
    try:
        results = verify_normal_review_receipt(
            run_dir, attempt.get("normal_review_receipt"), review=review,
            candidate=candidate, source_text=source_text, expected_children=1)
        raw = results[0]
        normalized = dict(review)
        offset = normalized.pop("offset_audit", None)
        if normalized != raw:
            return False
        valid = legacy = invalid = 0
        for issue in raw.get("issues", []):
            if not isinstance(issue, dict):
                invalid += 1
            elif "start" not in issue and "end" not in issue:
                legacy += 1
            elif (isinstance(issue.get("start"), int) and not isinstance(issue.get("start"), bool)
                  and isinstance(issue.get("end"), int) and not isinstance(issue.get("end"), bool)
                  and 0 <= issue["start"] < issue["end"] <= len(source_text)
                  and issue.get("segment_text") == source_text[issue["start"]:issue["end"]]):
                valid += 1
            else:
                invalid += 1
        return offset == {"valid": valid, "legacy_missing": legacy, "invalid": invalid}
    except (OSError, ValueError, KeyError, TypeError):
        return False


def annotation_review_is_complete(item: dict[str, Any], run_dir: Path | None = None) -> bool:
    attempts = item.get("attempts", [])
    last_review = attempts[-1].get("review") if attempts and isinstance(attempts[-1], dict) else None
    last_review_passed = (isinstance(last_review, dict) and last_review.get("verdict") == "pass"
                          and run_dir is not None
                          and is_verified_ordinary_chinese_review(item, run_dir))
    chapter_scoped_delta = (
        item.get("mode") == "constrained-delta"
        and item.get("acceptance_state") in {"reviewed_pass", "reviewed_and_remediated"}
    )
    return bool(item.get("resolved") and item.get("reviewed", True) is True and (
        last_review_passed
        or chapter_scoped_delta
        or (item.get("acceptance_state") == "reviewed_and_adjudicated"
            and is_verified_adjudicated_annotation(item, run_dir))
    ))


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
CHINESE_ANNOTATION_POLICY_VERSION = "contextual-delta-v4-focus-semantics"


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


class CachedCallUnavailable(RuntimeError):
    """A cache-only lookup cannot launch an agent."""


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
    benchmark_effort: str | None = None
    legacy_tool_restrictions: bool = False

    def __post_init__(self):
        # Repository-wide worker policy, including research and independent
        # review. Legacy callers cannot silently select a costlier model.
        self.model = "gpt-6-luna"
        if self.benchmark_effort not in (None, "low", "medium", "high"):
            raise ValueError("Benchmark effort must be low, medium or high")

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

    @staticmethod
    def _check_tool_profile(job_dir: Path, profile: str | None, meta: dict) -> None:
        from pipeline.worker_runtime import reject_repository_mutations
        reject_repository_mutations(job_dir, ROOT)
        if meta.get('kind') == 'annotation_patch_assembly':
            if meta.get('return_code') != 0 or meta.get('status') != 'applied':
                raise ValueError('Only an applied annotation patch assembly can be reused')

            raw_job_dir = Path(job_dir)
            if '..' in raw_job_dir.parts:
                raise ValueError('Annotation patch assembly path contains traversal')
            if not raw_job_dir.is_absolute():
                raw_job_dir = Path.cwd() / raw_job_dir
            agent_root = next((parent for parent in raw_job_dir.parents
                               if parent.name == 'agents'), None)
            if agent_root is None:
                raise ValueError('Annotation patch assembly is outside a run agents directory')
            run_dir = agent_root.parent
            relative_assembly = raw_job_dir.relative_to(agent_root)
            if not relative_assembly.parts or any(part in {'', '.', '..'} for part in relative_assembly.parts):
                raise ValueError('Invalid annotation patch assembly path')
            agent_root_real = agent_root.resolve()

            def checked_path(relative: str | Path) -> Path:
                if not isinstance(relative, (str, Path)):
                    raise ValueError(f'Invalid annotation patch evidence path: {relative!r}')
                relative_path = Path(relative)
                parts = relative_path.parts if isinstance(relative, Path) else relative.split('/')
                if (not parts or relative_path.is_absolute()
                        or any(part in {'', '.', '..'} for part in parts)):
                    raise ValueError(f'Invalid annotation patch evidence path: {relative!r}')
                target = agent_root.joinpath(*parts)
                current = agent_root
                if current.is_symlink():
                    raise ValueError('Annotation patch evidence agents root is a symlink')
                for part in parts:
                    current = current / part
                    if current.is_symlink():
                        raise ValueError(f'Annotation patch evidence uses a symlink: {current}')
                resolved = target.resolve(strict=True)
                if not resolved.is_relative_to(agent_root_real):
                    raise ValueError('Annotation patch evidence path escaped the run agents directory')
                return target

            assembly_dir = checked_path(relative_assembly)
            actual_meta_path = checked_path(relative_assembly / 'meta.json')
            actual_result_path = checked_path(relative_assembly / 'result.json')
            actual_meta = json.loads(actual_meta_path.read_text(encoding='utf-8'))
            if actual_meta != meta:
                raise ValueError('Annotation patch assembly metadata changed after it was loaded')

            for field in ('plan_job', 'patch_job'):
                child_job = meta.get(field)
                child_dir = checked_path(child_job)
                child_meta_path = checked_path(Path(child_job) / 'meta.json')
                checked_path(Path(child_job) / 'result.json')
                child_meta = json.loads(child_meta_path.read_text(encoding='utf-8'))
                if child_meta.get('return_code') != 0 or child_meta.get('tool_profile') != 'workspace':
                    raise ValueError(f'Annotation patch child is not a completed workspace job: {child_job}')
                checked_path(Path(child_job) / 'workspace')
                CodexRunner._check_tool_profile(child_dir, 'workspace', child_meta)

            replan = meta.get('replan')
            if replan is not None:
                # The first patch was rejected by the derived-candidate gate,
                # so it has no successful runner result. Verify its recorded
                # workspace and exact bounded-correction evidence separately.
                for field in ('first_plan_job', 'first_patch_job'):
                    child_job = replan.get(field)
                    child_dir = checked_path(child_job)
                    child_meta_path = checked_path(Path(child_job) / 'meta.json')
                    child_meta = json.loads(child_meta_path.read_text(encoding='utf-8'))
                    failed_job = field == 'first_patch_job'
                    if (child_meta.get('tool_profile') != 'workspace'
                            or (child_meta.get('return_code') == 0 if failed_job else
                                child_meta.get('return_code') != 0)):
                        raise ValueError(f'Annotation replan child is not a workspace job: {child_job}')
                    checked_path(Path(child_job) / 'workspace')
                    CodexRunner._check_tool_profile(child_dir, 'workspace', child_meta)
                first_patch_dir = checked_path(replan['first_patch_job'])
                recovery_path = checked_path(Path(replan['first_patch_job']) / 'submission-recovery.json')
                recovery = json.loads(recovery_path.read_text(encoding='utf-8'))
                if recovery != replan.get('runner_recovery'):
                    raise ValueError('Annotation replan submission-recovery evidence changed')
                initial = recovery.get('initial_rejection', {})
                final = recovery.get('repair_rejection', {})
                if (recovery.get('status') != 'rejected'
                        or initial.get('category') != 'derived_annotation_rejection'
                        or final.get('category') != 'derived_annotation_rejection'
                        or final.get('artifact_sha256') != replan.get('failed_artifact_sha256')):
                    raise ValueError('Annotation replan lacks exhausted derived-gate evidence')
                initial_record_path = checked_path(Path(replan['first_patch_job']) / 'workspace' /
                                                   'submission-rejection.json')
                if json.loads(initial_record_path.read_text(encoding='utf-8')) != initial:
                    raise ValueError('Initial patch rejection record differs from recovery evidence')
                rejected_initial = checked_path(Path(replan['first_patch_job']) / 'workspace' /
                                                'rejected-candidate-attempt-01.json')
                if hashlib.sha256(rejected_initial.read_bytes()).hexdigest() != initial.get('artifact_sha256'):
                    raise ValueError('Initial rejected annotation patch artifact changed')
                first_candidate = checked_path(Path(replan['first_patch_job']) / 'workspace' / 'candidate.json')
                if hashlib.sha256(first_candidate.read_bytes()).hexdigest() != initial.get('artifact_sha256'):
                    raise ValueError('First annotation patch candidate differs from rejection evidence')
                repair_job = recovery.get('job')
                repair_dir = checked_path(repair_job)
                repair_meta_path = checked_path(Path(repair_job) / 'meta.json')
                repair_meta = json.loads(repair_meta_path.read_text(encoding='utf-8'))
                if (repair_meta.get('tool_profile') != 'workspace'
                        or repair_meta.get('return_code') == 0):
                    raise ValueError('Bounded submission correction is not a workspace job')
                checked_path(Path(repair_job) / 'workspace')
                CodexRunner._check_tool_profile(repair_dir, 'workspace', repair_meta)
                final_artifact = final.get('artifact_path')
                if not isinstance(final_artifact, str):
                    raise ValueError('Final rejected patch has no recorded artifact path')
                artifact_path = checked_path(Path(repair_job) / 'workspace' / final_artifact)
                final_bytes = artifact_path.read_bytes()
                if hashlib.sha256(final_bytes).hexdigest() != final.get('artifact_sha256'):
                    raise ValueError('Final rejected annotation patch artifact changed')
                try:
                    final_patch = json.loads(final_bytes)
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise ValueError('Final rejected annotation patch artifact is not valid JSON') from exc
                if final_patch != replan.get('failed_patch'):
                    raise ValueError('Final rejected artifact differs from the recorded failed patch')

            from pipeline.annotation_repairs import replay_annotation_repair
            from pipeline.annotation_edits import candidate_digest
            replayed = replay_annotation_repair(
                run_dir, relative_assembly.as_posix(), validate_candidate=lambda _value: None,
            )
            stored = json.loads(actual_result_path.read_text(encoding='utf-8'))
            if (replayed.get('status') != 'applied'
                    or replayed.get('candidate') != stored
                    or candidate_digest(stored) != meta.get('result_digest')):
                raise ValueError('Annotation patch assembly failed exact evidence replay')
            return
        if meta.get('tool_profile') == 'workspace':
            from pipeline.worker_workspace import verify, submit
            verify(job_dir / 'workspace', meta['workspace_digest'])
            if 'artifact_path' in meta:
                value, artifact = submit(job_dir / 'workspace', {'candidate_path': meta['artifact_path']})
                if artifact['artifact_digest'] != meta['artifact_digest'] or value != json.loads((job_dir / 'result.json').read_text()):
                    raise ValueError('Worker submitted artifact changed')
            return
        if profile is None:
            return
        events = job_dir / f"events.attempt-{meta.get('attempt_count', 1):02d}.jsonl"
        # JSONL uses LF delimiters. Unicode line separators may legally occur
        # inside JSON strings (notably web snippets); splitlines() corrupts them.
        items = [json.loads(line).get('item', {}) for line in events.read_text().split('\n')
                 if line.strip()] if events.exists() else []
        tool_types = {'command_execution', 'mcp_tool_call', 'collab_tool_call',
                      'file_change', 'web_search'}
        used = {item.get('type') for item in items} & tool_types
        allowed = {'web_search'} if profile == 'research' else set()
        if used - allowed:
            raise ValueError(f'Worker used tools outside its {profile} role: {used - allowed}')
        if profile == 'research' and 'web_search' not in used:
            raise ValueError('Research worker did not actually use web search')

    async def call(
        self,
        job: str,
        prompt: str,
        schema: Path,
        effort: str,
        *,
        refresh: bool = False,
        tool_profile: str | None = None,
        cache_only: bool = False,
        workspace_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run one worker, with one bounded correction for rejected submissions."""
        from pipeline.worker_workspace import CandidateSubmissionError

        try:
            return await self._call_once(job, prompt, schema, effort, refresh=refresh,
                tool_profile=tool_profile, cache_only=cache_only,
                workspace_context=workspace_context)
        except CandidateSubmissionError as rejected:
            effective_profile = tool_profile
            if not self.legacy_tool_restrictions:
                effective_profile = 'workspace'
            if effective_profile != 'workspace':
                raise
            initial_rejection = rejected

        record = initial_rejection.record()
        rejected_bytes = initial_rejection.artifact_bytes
        original_dir = self.run_dir / 'agents' / job
        workspace = original_dir / 'workspace'
        workspace.mkdir(parents=True, exist_ok=True)
        if rejected_bytes is not None:
            (workspace / 'rejected-candidate-attempt-01.json').write_bytes(rejected_bytes)
        (workspace / 'submission-rejection.json').write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

        repair_identity = digest(job, json.dumps(record, ensure_ascii=False, sort_keys=True))[:16]
        repair_job = f'{job}-submission-repair-{repair_identity}'
        submission_repair = {'record': record, 'artifact_bytes': rejected_bytes}
        try:
            value = await self._call_once(repair_job, prompt, schema, effort,
                refresh=refresh, tool_profile=tool_profile, cache_only=cache_only,
                workspace_context=workspace_context, submission_repair=submission_repair)
        except CandidateSubmissionError as repair_rejected:
            recovery = {'status': 'rejected', 'job': repair_job,
                        'initial_rejection': record,
                        'repair_rejection': repair_rejected.record()}
            (original_dir / 'submission-recovery.json').write_text(
                json.dumps(recovery, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            meta_path = original_dir / 'meta.json'
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding='utf-8'))
                meta['submission_repair'] = recovery
                meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            raise CandidateSubmissionError(
                f'Initial worker submission rejected: {initial_rejection}; bounded correction rejected: {repair_rejected}',
                artifact_path=repair_rejected.artifact_path,
                artifact_bytes=repair_rejected.artifact_bytes,
                category='bounded_repair_rejected') from repair_rejected

        repair_dir = self.run_dir / 'agents' / repair_job
        repair_workspace = repair_dir / 'workspace'
        repair_meta = json.loads((repair_dir / 'meta.json').read_text(encoding='utf-8'))
        source = (repair_workspace / repair_meta['artifact_path']).resolve()
        if not source.is_relative_to(repair_workspace.resolve()):
            raise ValueError('Repaired worker artifact escaped its workspace')
        accepted_bytes = source.read_bytes()
        # Keep the original job's inputs and fingerprint while promoting the
        # exact repaired artifact into its untracked output slot. The rejected
        # bytes remain preserved beside it for diagnosis.
        accepted_path = workspace / 'candidate.json'
        accepted_path.write_bytes(accepted_bytes)
        result_path = original_dir / 'result.json'
        result_path.write_text(json.dumps(value, ensure_ascii=False) + '\n', encoding='utf-8')
        recovery = {'status': 'repaired', 'job': repair_job,
                    'initial_rejection': record,
                    'accepted_artifact': str(source.relative_to(self.run_dir)),
                    'accepted_artifact_sha256': hashlib.sha256(accepted_bytes).hexdigest()}
        (original_dir / 'submission-recovery.json').write_text(
            json.dumps(recovery, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        meta_path = original_dir / 'meta.json'
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        meta.update(return_code=0, artifact_path='candidate.json',
                    artifact_digest=hashlib.sha256(accepted_bytes).hexdigest(),
                    recovered_after_submission_rejection=True,
                    submission_repair=recovery)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        self._check_tool_profile(original_dir, 'workspace', meta)
        return value

    async def _call_once(
        self,
        job: str,
        prompt: str,
        schema: Path,
        effort: str,
        *,
        refresh: bool = False,
        tool_profile: str | None = None,
        cache_only: bool = False,
        workspace_context: dict[str, Any] | None = None,
        submission_repair: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from pipeline.worker_workspace import VERSION
        effort = self.benchmark_effort or "low"
        from pipeline.worker_runtime import safe_job_directory
        from pipeline.worker_paths import checked_directory
        job_dir = safe_job_directory(self.run_dir, job)
        result_path = job_dir / "result.json"
        meta_path = job_dir / "meta.json"
        fingerprint = digest(prompt, schema.read_text(), self.model, effort)
        if tool_profile not in (None, 'offline', 'research', 'workspace'):
            raise ValueError('Unknown worker tool profile')
        requested_profile = tool_profile
        if not self.legacy_tool_restrictions:
            tool_profile = 'workspace'
        if tool_profile is not None:
            fingerprint = digest(fingerprint, tool_profile, 'tool-profile-v1')
        if tool_profile == 'workspace':
            fingerprint = digest(fingerprint, VERSION)
            if workspace_context is not None:
                fingerprint = digest(fingerprint, json.dumps(workspace_context, sort_keys=True))
        if not refresh and result_path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text())
            retained_fingerprint = None
            legacy_compatible = (tool_profile == 'workspace' and meta.get('tool_profile') != 'workspace'
                                 and meta.get('model') == self.model and meta.get('effort') == effort)
            if (cache_only or legacy_compatible) and meta.get('model') and meta.get('effort'):
                retained_fingerprint = digest(prompt, schema.read_text(), meta['model'], meta['effort'])
                if meta.get('tool_profile') == 'workspace':
                    retained_fingerprint = digest(retained_fingerprint, 'workspace', 'tool-profile-v1')
                    retained_fingerprint = digest(retained_fingerprint, VERSION)
                    if workspace_context is not None:
                        retained_fingerprint = digest(retained_fingerprint, json.dumps(workspace_context, sort_keys=True))
                elif requested_profile is not None:
                    retained_fingerprint = digest(retained_fingerprint, requested_profile, 'tool-profile-v1')
            matching = meta.get('fingerprint') == fingerprint or (
                retained_fingerprint is not None and meta.get('fingerprint') == retained_fingerprint)
            if matching and meta.get("return_code") == 0:
                try:
                    self._check_tool_profile(job_dir, requested_profile if legacy_compatible else tool_profile, meta)
                except ValueError:
                    # A capability violation is not a reusable successful result.
                    # Cache-only probes must miss; normal retries rerun this job.
                    pass
                else:
                    return json.loads(result_path.read_text())

        if cache_only:
            raise CachedCallUnavailable(job)

        workspace_meta = {}
        worker_cwd = ROOT
        worker_result_path, worker_schema = result_path, schema
        original_prompt = prompt
        if tool_profile == 'workspace':
            worker_cwd = checked_directory(job_dir / 'workspace', create=True)
            checked_directory(job_dir / 'runtime', create=True)
            worker_result_path = job_dir / 'runtime' / 'receipt.json'
            worker_schema = worker_cwd / 'receipt.schema.json'
        def consume_result(receipt):
            if tool_profile != 'workspace':
                return receipt
            from pipeline.worker_workspace import submit
            value, artifact = submit(worker_cwd, receipt)
            workspace_meta.update(artifact)
            result_path.write_text(json.dumps(value, ensure_ascii=False) + '\n')
            return value
        command = [
            "codex", "exec", "--json", "--ephemeral",
            "-s", "danger-full-access" if tool_profile == 'workspace' else "read-only",
            "-m", self.model, "-c", f'model_reasoning_effort="{effort}"',
            "-C", str(worker_cwd), "--output-schema", str(worker_schema.absolute()),
            "-o", str(worker_result_path.absolute()), "-",
        ]
        if tool_profile == 'workspace':
            command[2:2] = ['-c', 'web_search="live"', '-c', 'sandbox_workspace_write.network_access=true']
        elif tool_profile is not None:
            # Explicit role capabilities; never enable shell/network execution.
            config = [f'web_search="{"live" if tool_profile == "research" else "disabled"}"',
                      'features.shell_tool=false', 'features.unified_exec=false',
                      'tools.view_image=false', 'features.multi_agent=false',
                      'mcp_servers={}']
            command[2:2] = [arg for value in config for arg in ('-c', value)]
            command.insert(2, '--ignore-user-config')
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
                    if tool_profile == 'workspace':
                        from pipeline.worker_workspace import build
                        from pipeline.worker_runtime import preserve_prior_source_mutation
                        preserve_prior_source_mutation(job_dir, ROOT)
                        prompt, workspace_digest = build(worker_cwd, original_prompt, json.loads(schema.read_text()),
                            context=workspace_context, submission_repair=submission_repair)
                        workspace_meta.update(tool_profile='workspace', workspace_digest=workspace_digest)
                    result_path.unlink(missing_ok=True)
                    worker_result_path.unlink(missing_ok=True)
                    launch_command, launch_env = command, None
                    runtime = job_dir / 'runtime'
                    if tool_profile == 'workspace':
                        from pipeline.worker_runtime import confined_launch
                        # The receipt is outside immutable workspace inputs but
                        # still inside the explicitly writable runtime root.
                        launch_command, launch_env = confined_launch(
                            command, repository=ROOT, workspace=worker_cwd,
                            runtime=runtime, events=events_path, stderr=stderr_path)
                        workspace_meta['filesystem_policy'] = 'landlock-write-scope-v1'
                    with events_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                        try:
                            async with asyncio.timeout(
                                min(self.launch_timeout_seconds, float(self.timeout))
                            ):
                                proc = await asyncio.create_subprocess_exec(
                                    *launch_command,
                                    stdin=asyncio.subprocess.PIPE,
                                    stdout=stdout,
                                    stderr=stderr,
                                    start_new_session=True,
                                    env=launch_env,
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
                                if tool_profile == 'workspace':
                                    from pipeline.worker_runtime import retain_logs
                                    retain_logs(runtime, events_path, stderr_path)
                            except TimeoutError:
                                try:
                                    os.killpg(proc.pid, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass
                                await proc.wait()
                                if tool_profile == 'workspace':
                                    from pipeline.worker_runtime import retain_logs
                                    retain_logs(runtime, events_path, stderr_path)
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
                                    worker_result_path, events_path, worker_schema
                                )
                                if recovered is not None:
                                    recovered = consume_result(recovered)
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
                                        **workspace_meta,
                                    }
                                    meta_path.write_text(
                                        json.dumps(meta, ensure_ascii=False, indent=2) + "\n"
                                    )
                                    self._check_tool_profile(job_dir, tool_profile, meta)
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
        if tool_profile is not None:
            meta['tool_profile'] = tool_profile
        meta.update(workspace_meta)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
        if proc.returncode == 0 and worker_result_path.exists():
            try:
                consume_result(json.loads(worker_result_path.read_text()))
            except Exception as error:
                meta.update(return_code=1, submission_error=str(error))
                meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
                raise
            meta.update(workspace_meta)
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
        if proc.returncode != 0 or not result_path.exists():
            raise RuntimeError(f"agent failed: {job} (exit {proc.returncode})")
        self._check_tool_profile(job_dir, tool_profile, meta)
        return json.loads(result_path.read_text())


class ChapterHarness:
    def __init__(
        self, args: argparse.Namespace, semaphore: asyncio.Semaphore | None = None
    ):
        self.args = args
        self.source_path = Path(args.source).resolve()
        self.source = self.source_path.read_text(encoding="utf-8")
        self.editorial_plan_path = (
            Path(args.editorial_plan).resolve()
            if getattr(args, "editorial_plan", None) else None
        )
        run_id = args.run_id or f"{self.source_path.stem}-{args.level}"
        self.run_dir = Path(args.runs_dir).resolve() / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.runner = CodexRunner(
            self.run_dir, args.model,
            semaphore or asyncio.Semaphore(args.concurrency), args.timeout,
        )
        self.focus_vocabulary: dict[str, Any] = {
            "names": [], "story_terms": [],
        }

    @property
    def target_chars(self) -> int | None:
        """Return a size only when the caller explicitly requested one."""
        return getattr(self.args, "target_chars", None)

    @property
    def fixed_size_requested(self) -> bool:
        return self.target_chars is not None

    @property
    def scene_count(self) -> int:
        """Keep compact original chapters coherent; split only very long outputs."""
        if hasattr(self, "active_scene_count"):
            return self.active_scene_count
        if self.target_chars is None:
            return getattr(self, "active_scene_count", 1)
        return max(1, min(12, round(self.target_chars / 1500)))

    def scene_length_bounds(self, target: int) -> tuple[int, int]:
        """Keep HSK1 substance without forcing readability-damaging padding."""
        if not self.fixed_size_requested:
            return (0, 0)
        return target_length_bounds(target, self.args.level)

    @property
    def scene_length_guidance(self) -> str:
        return "70%-117%" if self.args.level == "hsk1" else "85%-115%"

    @property
    def scene_max_above_level_ratio(self) -> float:
        """Allow harder action scenes while enforcing the budget chapter-wide.

        A compact multi-scene chapter naturally concentrates names and plot
        vocabulary in battles and rituals.  Applying the complete chapter
        budget independently to every scene caused fluent drafts to be padded
        or distorted.  Single-scene chapters keep the exact budget; multi-scene
        chapters get a bounded local ceiling and are checked again as a whole.
        """
        maximum = policy_for(self.args.level).max_above_level_ratio
        if getattr(self, "active_scene_count", 1) == 1:
            return maximum
        return min(1.0, maximum + 0.10)

    @property
    def target_band_vocabulary_guidance(self) -> str:
        """Provide an exact level inventory as a reference, never a quota."""
        if self.args.level == "hsk1":
            return ""
        inventory = "、".join(sorted(words_at_level(self.args.level)))
        return (
            f"Reference inventory for natural, useful {self.args.level.upper()}-band "
            "word choices when the source meaning calls for them. This is not a "
            "minimum or coverage quota: do not add unrelated facts, pad, or replace "
            "natural wording just to demonstrate inventory items.\n" + inventory
        )

    @property
    def readability_charset(self) -> set[str]:
        return load_charset(
            ROOT / "pipeline" / "charsets" / "chinese"
            / f"{self.args.level}_chars.txt"
        )

    @property
    def glossary_chars(self) -> set[str]:
        glossary = ROOT / "output" / "chinese" / "sanguoyanyi" / "glossary.txt"
        if not glossary.is_file():
            return set()
        return {
            char for line in glossary.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
            for char in line.strip()
            if "\u3400" <= char <= "\u9fff"
        }

    @property
    def focus_vocabulary_words(self) -> set[str]:
        vocabulary = getattr(
            self, "focus_vocabulary", {"names": [], "story_terms": []}
        )
        return {
            str(item["surface"])
            for key in ("names", "story_terms")
            for item in vocabulary.get(key, [])
        }

    @property
    def focus_vocabulary_chars(self) -> set[str]:
        return {
            char for word in self.focus_vocabulary_words for char in word
            if "\u3400" <= char <= "\u9fff"
        }

    @property
    def focus_vocabulary_names(self) -> set[str]:
        vocabulary = getattr(
            self, "focus_vocabulary", {"names": [], "story_terms": []}
        )
        return {str(item["surface"]) for item in vocabulary.get("names", [])}

    @property
    def focus_vocabulary_story_terms(self) -> set[str]:
        vocabulary = getattr(
            self, "focus_vocabulary", {"names": [], "story_terms": []}
        )
        return {
            str(item["surface"]) for item in vocabulary.get("story_terms", [])
        }

    @property
    def focus_vocabulary_semantic_guidance(self) -> str:
        vocabulary = getattr(
            self, "focus_vocabulary", {"names": [], "story_terms": []}
        )
        return json.dumps({
            "names": [
                {"surface": str(item["surface"])}
                for item in vocabulary.get("names", [])
            ],
            "story_terms": [
                {
                    "surface": str(item["surface"]),
                    "meaning_en": str(item.get("meaning_en", "")),
                }
                for item in vocabulary.get("story_terms", [])
            ],
        }, ensure_ascii=False, separators=(",", ":"))

    @property
    def focus_vocabulary_guidance(self) -> str:
        vocabulary = getattr(
            self, "focus_vocabulary", {"names": [], "story_terms": []}
        )
        names = "、".join(
            str(item["surface"]) for item in vocabulary["names"]
        ) or "none"
        terms = "、".join(
            str(item["surface"]) for item in vocabulary["story_terms"]
        ) or "none"
        return (
            "Pre-reviewed proper names: " + names + ". "
            "Pre-reviewed indispensable story terms: " + terms + ". "
            "These are the complete exception inventory for this draft. Do "
            "not introduce any other proper name, courtesy name, title, fixed "
            "phrase, or specialized historical/military term merely for color "
            "or compression. Omit an optional detail or express it in plain "
            "level-appropriate words instead. When you retain a planned "
            "detail, use its exact listed surface; a partial name, alternate "
            "name form, unused term, or paraphrased term receives no exemption."
        )

    @property
    def prose_shape_guidance(self) -> str:
        sentence, clause = PROSE_SHAPE_LIMITS[self.args.level]
        return (
            f"Keep every sentence at or below {sentence} Chinese characters "
            f"and every comma/semicolon-delimited clause at or below {clause}. "
            "Use a full stop and start a new natural sentence when needed."
        )

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
            "fixed_size_requested": self.fixed_size_requested,
            "editorial_plan": (
                str(self.editorial_plan_path) if self.editorial_plan_path else None
            ),
            "updated_at": utc_now(),
        }
        (self.run_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        )

    async def outline(self) -> dict[str, Any]:
        if getattr(self, "editorial_plan_path", None) is not None:
            return self.outline_from_editorial_plan()
        policy = policy_for(self.args.level)
        beginner_selection = ""
        if self.args.level == "hsk1":
            beginner_selection = """
HSK1 selection rule: choose a clear chronological thread with the actions
needed to introduce and motivate the main characters. Do not inventory every
source event, office, battle, or minor name; omit material that adds difficulty
without helping the coherent beginner retelling."""
        size_instruction = (
            f"The user explicitly requested a fixed adaptation size of {self.target_chars} Chinese characters. "
            "Plan scene targets that sum to that requested size; do not use padding or omit necessary source meaning to meet it."
            if self.fixed_size_requested else
            "Choose the retained coverage and natural stopping point from source events, coherence, and learner level. "
            "Do not set a character, word, sentence, paragraph-count, or scene-count quota. Explain the length decision in length_reason_en."
        )
        prompt = f"""Return only JSON matching the supplied schema.
Read the complete original Chinese chapter and divide it into the number of
consecutive adaptation scenes needed to represent the selected source coverage.
Every source_start_quote must be an exact, unique substring
of ORIGINAL. Scenes must cover the source in order. Capture every important
event and causal link selected for a coherent level-appropriate retelling.
Editorial scope: {policy.scope}
Fidelity rule: {policy.fidelity}
{beginner_selection}
{size_instruction}

ORIGINAL:\n{self.source}"""
        outline = await self.runner.call(
            "outline", prompt, SCHEMAS / "scene-outline.schema.json",
            self.args.review_effort, refresh=self.args.refresh,
        )
        scenes = outline["scenes"]
        self.active_scene_count = len(scenes)
        if not str(outline.get("length_reason_en", "")).strip():
            raise ValueError("outline must explain its retained coverage and length decision")
        if self.fixed_size_requested:
            base, remainder = divmod(self.target_chars, len(scenes))
            for index, scene in enumerate(scenes):
                scene["target_chars"] = base + (1 if index < remainder else 0)
        else:
            for scene in scenes:
                scene["target_chars"] = 0
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

    def outline_from_editorial_plan(self) -> dict[str, Any]:
        """Materialize one level from a shared, source-anchored chapter plan."""
        plan = json.loads(self.editorial_plan_path.read_text(encoding="utf-8"))
        expected_hash = hashlib.sha256(self.source.encode("utf-8")).hexdigest()
        if plan.get("source_sha256") != expected_hash:
            raise ValueError("editorial plan does not match the source chapter")
        level_plan = plan.get("level_plans", {}).get(self.args.level)
        if not isinstance(level_plan, dict) or not level_plan.get("adaptation_scenes"):
            raise ValueError(f"editorial plan has no {self.args.level} adaptation scenes")
        canonical = {
            item["id"]: item for item in plan.get("canonical_scenes", [])
        }
        raw_scenes = copy.deepcopy(level_plan["adaptation_scenes"])
        weights = [float(scene.get("target_weight", 1)) for scene in raw_scenes]
        if any(weight <= 0 for weight in weights):
            raise ValueError("editorial scene target weights must be positive")
        total_weight = sum(weights)
        assigned = 0
        scenes: list[dict[str, Any]] = []
        for index, (scene, weight) in enumerate(zip(raw_scenes, weights), 1):
            quote = str(scene["source_start_quote"])
            start = 0 if index == 1 else self.source.find(quote)
            if start < 0:
                raise ValueError(f"editorial scene anchor not found: {quote!r}")
            required_events: list[str] = []
            for scene_id in scene.get("canonical_scene_ids", []):
                if scene_id not in canonical:
                    raise ValueError(f"unknown canonical scene: {scene_id}")
                required_events.extend(canonical[scene_id]["required_events"])
            required_events.extend(scene.get("required_events", []))
            if not self.fixed_size_requested:
                target = 0
            elif index == len(raw_scenes):
                target = self.target_chars - assigned
            else:
                target = round(self.target_chars * weight / total_weight)
                assigned += target
            scenes.append({
                "id": f"scene_{index:02d}",
                "title": scene["title"],
                "source_start_quote": quote,
                "required_events": required_events,
                "target_chars": target,
                "source_start": start,
                "min_paragraphs": int(scene["min_paragraphs"]),
                "max_paragraphs": int(scene["max_paragraphs"]),
                "max_paragraph_cjk": int(scene["max_paragraph_cjk"]),
                "strict_event_scope": bool(
                    scene.get("strict_event_scope", True)
                ),
            })
        starts = [scene["source_start"] for scene in scenes]
        if starts != sorted(starts) or len(starts) != len(set(starts)):
            raise ValueError("editorial scene anchors are not unique and ordered")
        for index, scene in enumerate(scenes):
            scene["source_end"] = (
                scenes[index + 1]["source_start"]
                if index + 1 < len(scenes) else len(self.source)
            )
            raw_beats = raw_scenes[index].get("recovery_beats")
            if raw_beats and self.fixed_size_requested:
                scene["recovery_beats"] = materialize_recovery_beats(
                    self.source, scene, raw_beats
                )
        self.active_scene_count = len(scenes)
        outline = {
            "chapter_title": plan["chapter_title"],
            "length_reason_en": (
                str(level_plan.get("coverage_note", "")).strip()
                or "The source-anchored editorial plan selects the retained scenes; independent review will assess natural coverage and stopping point at this level."
            ),
            "scenes": scenes,
            "editorial_plan": str(self.editorial_plan_path),
            "coverage_note": level_plan.get("coverage_note", ""),
        }
        (self.run_dir / "outline.json").write_text(
            json.dumps(outline, ensure_ascii=False, indent=2) + "\n"
        )
        return outline

    async def plan_focus_vocabulary(
        self, outline: dict[str, Any]
    ) -> dict[str, Any]:
        term_caps = {
            "hsk1": 5, "hsk2": 8, "hsk3": 10,
            "hsk4": 12, "hsk5": 15, "hsk6": 18,
        }
        events = "\n".join(
            f"- {event}"
            for scene in outline["scenes"]
            for event in scene.get("required_events", [])
        )
        cap = term_caps[self.args.level]
        prompt = f"""Return only JSON matching the supplied schema. Plan the
small exception vocabulary for a {self.args.level.upper()} adaptation before
prose is written.

List proper names separately from story terms. Include only names likely to
appear in the selected compact retelling. Select at most {cap} indispensable
historical, ritual, military, or plot-specific story terms; ordinary actions,
relations, locations, counters, objects, and unfamiliar-but-optional detail do
not qualify. Use the exact simplified-Chinese surface the adaptation should
use. Prefer the smallest reusable lexical core (for example, a recurring story
noun rather than that noun wrapped in an ordinary action) unless the entire
fixed phrase is itself important. Prefer a central fixed ritual phrase or quotation over a label that will
not appear verbatim. The prose writer must use an item's exact surface whenever
it retains that detail; an unused item gives no vocabulary-budget benefit. The
plan is still an upper bound: omit both an optional detail and its term rather
than forcing the term into the retelling.

SELECTED EVENTS:
{events}

SOURCE:
{self.source}
"""
        result = await self.runner.call(
            "focus_vocabulary_plan", prompt,
            SCHEMAS / "focus-vocabulary-plan.schema.json",
            self.args.review_effort, refresh=self.args.refresh,
        )
        if len(result["story_terms"]) > cap:
            raise ValueError(
                f"focus vocabulary has {len(result['story_terms'])} terms; max {cap}"
            )
        surfaces: set[str] = set()
        for key in ("names", "story_terms"):
            for item in result[key]:
                surface = str(item["surface"]).strip()
                if (not surface or surface in surfaces
                        or not any("\u3400" <= char <= "\u9fff" for char in surface)):
                    raise ValueError(f"invalid or duplicate focus surface: {surface!r}")
                item["surface"] = surface
                surfaces.add(surface)
        (self.run_dir / "focus-vocabulary-plan.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.focus_vocabulary = result
        return result

    async def adapt_scene(self, scene: dict[str, Any]) -> dict[str, Any]:
        policy = policy_for(self.args.level)
        source_coverage_guidance = {
            "hsk4": (
                "Work through ORIGINAL in order and retain every major episode, "
                "motivation, and causal transition in this source span. Compress "
                "minor description instead of turning the passage into a synopsis."
            ),
            "hsk5": (
                "Work through ORIGINAL in order and retain its consequential "
                "events, motivations, important secondary characters, and meaningful "
                "dialogue. Omit only genuinely minor detail or repetition."
            ),
            "hsk6": (
                "Retell every substantive event, motivation, identity, and exchange "
                "in ORIGINAL in the same order. Omit only ornamental verse, repetition, "
                "and detail that does not advance characterization or events; this must "
                "be a detailed modern retelling, never a synopsis."
            ),
        }.get(self.args.level, "")
        beginner_guidance = (
            hsk1_prompt_guidance() if self.args.level == "hsk1" else ""
        )
        charset_hint = ""
        if self.args.level in {"hsk1", "hsk2", "hsk3"}:
            charset_hint = (
                " Prefer these cumulative level characters wherever natural: "
                + "".join(sorted(self.readability_charset))
            )
        original = self.source[scene["source_start"] : scene["source_end"]]
        events = "\n".join(f"- {event}" for event in scene.get("required_events", []))
        event_scope = (
            "The REQUIRED EVENTS are the exact editorial scope for this "
            "adaptation. Include every one, but do not add other source "
            "episodes merely because ORIGINAL contains them. Supporting facts "
            "needed to connect the selected events are allowed."
            if scene.get("strict_event_scope") else
            "Treat REQUIRED EVENTS as candidate story material, not a demand "
            "for exhaustive coverage."
        )
        paragraph_guidance = (
            f"Break paragraphs naturally at changes of speaker, action, time, "
            "or scene. Do not add headings or repeat the chapter title. Keep "
            f"each paragraph at or below {scene.get('max_paragraph_cjk', 250)} Chinese characters."
            if not self.fixed_size_requested else
            f"Write {scene.get('min_paragraphs', 1)}-"
            f"{scene.get('max_paragraphs', 6)} paragraphs, separated by blank lines; "
            f"no paragraph may exceed {scene.get('max_paragraph_cjk', 250)} Chinese characters."
        )
        if self.fixed_size_requested:
            minimum_chars, maximum_chars = self.scene_length_bounds(scene["target_chars"])
            length_guidance = (
                f"The user-requested hard range is {minimum_chars}-{maximum_chars} "
                "Chinese characters. Meet it only while preserving accurate source meaning."
            )
            target_guidance = f"Target about {scene['target_chars']} Chinese characters."
        else:
            length_guidance = (
                "There is no character or total-length target. Preserve the selected "
                "meaningful narrative naturally for this learner level; do not pad or compress needlessly."
            )
            target_guidance = ""
        prompt = f"""Return only JSON matching the supplied schema, with the
adapted scene in `text`. Adapt the verbatim ORIGINAL into natural, engaging
modern Chinese for an annotated {self.args.level.upper()} literary reader.
Write only simplified Chinese, even though ORIGINAL uses traditional Chinese.
{event_scope} {policy.scope} {policy.fidelity}
{source_coverage_guidance}
{target_guidance} {length_guidance}
Core grammar should be
comfortable at {self.args.level.upper()}. {policy.language}
Use vocabulary natural to the requested level and source meaning. Do not make
the adaptation longer or less accurate to demonstrate a vocabulary inventory.
{self.target_band_vocabulary_guidance}
Use annotations for the small number of indispensable names and story terms,
not as permission to make the surrounding prose advanced. Do not invent facts
or outcomes.
{self.focus_vocabulary_guidance}
The deterministic readability gates allow at most
{policy.max_above_level_ratio:.0%} above-level characters and at most that same
share of naturally segmented word tokens after exempting the book glossary.
Familiar characters do not make an advanced word level-appropriate.{charset_hint}
{self.prose_shape_guidance}
{paragraph_guidance}
{beginner_guidance}

REQUIRED EVENTS:\n{events}\n\nORIGINAL:\n{original}"""
        result = await self.runner.call(
            f"{scene['id']}/adapt", prompt, SCHEMAS / "adaptation.schema.json",
            self.args.adapt_effort, refresh=self.args.refresh,
        )
        result["text"] = simplified(result["text"])
        return result

    async def review_scene(
        self,
        scene: dict[str, Any],
        adaptation: str,
        suffix: str = "review",
        prior_findings: list[str] | None = None,
    ) -> dict[str, Any]:
        original = self.source[scene["source_start"] : scene["source_end"]]
        policy = policy_for(self.args.level)
        source_coverage_review_guidance = {
            "hsk4": (
                "Natural condensation is expected, but identify any omitted major "
                "episode, motivation, or causal transition in this source span."
            ),
            "hsk5": (
                "Identify omissions of consequential events, motivations, important "
                "secondary characters, or meaningful dialogue; allow only minor-detail "
                "and repetition cuts."
            ),
            "hsk6": (
                "Identify every omitted substantive event, motivation, identity, or "
                "exchange. Allow omission only of ornamental verse, repetition, and "
                "detail that does not advance characterization or events. Reject a "
                "synopsis even when its broad story remains true."
            ),
        }.get(
            self.args.level,
            "Natural condensation, merging, and level-appropriate omission are expected. "
            "Do not list an omitted source event merely because it is absent.",
        )
        source_review_acceptance_guidance = {
            "hsk4": (
                "Interpret source_fidelity as both truthfulness and retention of the "
                "major source events selected by the HSK4 policy. Set verdict=pass only "
                "when that major-event retelling is coherent and level-appropriate."
            ),
            "hsk5": (
                "Interpret source_fidelity as truthfulness plus retention of the "
                "consequential source content required by the HSK5 policy. Set "
                "verdict=pass only when that close abridgment is coherent and "
                "level-appropriate."
            ),
            "hsk6": (
                "Interpret source_fidelity as truthfulness plus substantially complete "
                "coverage of substantive source content. Set verdict=pass only for a "
                "detailed, coherent, level-appropriate modern retelling."
            ),
        }.get(
            self.args.level,
            "Interpret source_fidelity as truthfulness of what the adaptation actually "
            "says, not percentage of source events retained. Set verdict=pass when the "
            "broad story remains true, the selected retelling is coherent, and language "
            "is level-appropriate.",
        )
        beginner_guidance = (
            hsk1_prompt_guidance() if self.args.level == "hsk1" else ""
        )
        events = "\n".join(f"- {event}" for event in scene.get("required_events", []))
        event_scope = (
            "Every REQUIRED EVENT must appear accurately. Events outside this "
            "list may be omitted and should not be reported as omissions. Flag "
            "an outside episode when it crowds out the selected scope or makes "
            "the adaptation less coherent."
            if scene.get("strict_event_scope") else
            "REQUIRED EVENTS are level-appropriate priorities."
        )
        prior_section = "" if not prior_findings else f"""
PRIOR REJECTED TRAPS FROM THIS SAME RUN:
{json.dumps(prior_findings, ensure_ascii=False, indent=2)}
Do not assume these remain present, but explicitly check that none has been
reintroduced. If the same defect appears again, report it and return revise.
"""
        length_review_guidance = (
            f"The user explicitly requested {scene['target_chars']} characters for this scene. Assess the length in length_reason_en after source accuracy and natural level-appropriate expression."
            if self.fixed_size_requested else
            "Assess whether the amount of narrative naturally fits the retained source coverage and requested learner level. Explain in length_reason_en why this length and stopping point are suitable, whether meaningful source content was compressed or omitted, and whether more retained detail would help. Do not request changes merely to meet an unstated size target."
        )
        prompt = f"""Return only JSON matching the supplied schema. Compare
ADAPTATION directly against VERBATIM ORIGINAL and the reviewed editorial scope.
{length_review_guidance}
Editorial scope: {policy.scope}
Fidelity rule: {policy.fidelity}
{event_scope}
{source_coverage_review_guidance}
Only populate an issue array with a blocking defect that requires a prose
revision. Never list an omission or compression that you consider minor,
allowed, acceptable, or harmless. If an issue type has no blocking defect,
return an empty array; never put statements such as "no obvious issue" in it.
Identify unsupported
additions, factual/causal distortions, awkward Chinese, and readability issues.
The adaptation must use simplified Chinese consistently; any traditional-only
characters or mixed simplified/traditional prose requires verdict=revise.
{source_review_acceptance_guidance}
Judge whether the language suits the requested learner level from the actual
passage and source demands. A short or lexically simple source scene may
naturally contain few or no words unique to the target HSK band; do not require
a minimum count or request vocabulary additions only to demonstrate band
coverage. If the wording is genuinely too elementary or otherwise unsuitable,
identify that as a substantive language problem.
Use the schema's 0-10 score scale, where 9 means excellent, not 0.9. For HSK1,
actively reject stilted word-list substitutions, missing causal explanations,
vague referents, or phrases a native speaker would not naturally say, even if
every individual character is simple. A necessary annotated story word is
better than an unnatural paraphrase.
{beginner_guidance}
{prior_section}

REQUIRED EVENTS:\n{events}\n\nORIGINAL:\n{original}\n\nADAPTATION:\n{adaptation}"""
        result = await self.runner.call(
            f"{scene['id']}/{suffix}", prompt, SCHEMAS / "source-review.schema.json",
            self.args.review_effort, refresh=self.args.refresh,
        )
        result = normalize_review_score_scale(result)
        if not str(result.get("length_reason_en", "")).strip():
            raise ValueError("independent prose review must explain its length assessment")
        length = cjk_count(adaptation)
        low, high = self.scene_length_bounds(scene["target_chars"])
        in_range = not self.fixed_size_requested or low <= length <= high
        # Length is measured exactly below. Never let a model-authored length
        # claim conflict with the deterministic direction given to repairs.
        if self.fixed_size_requested and in_range:
            result = discard_incorrect_length_findings(result)
        result = apply_compact_review_policy(result)
        if any(
            result.get(key) for key in (
                "omissions", "unsupported_additions", "distortions",
                "language_problems",
            )
        ):
            result = dict(result)
            result["verdict"] = "revise"
            result["harness_decision"] = (
                "rejected_review_with_explicit_findings"
            )
        if min(
            result.get("source_fidelity", 0), result.get("naturalness", 0),
            result.get("readability", 0),
        ) < 8:
            result = dict(result)
            result["verdict"] = "revise"
            result.setdefault("language_problems", []).append(
                "review scores must each be at least 8/10"
            )
        if self.fixed_size_requested and not in_range:
            result = dict(result)
            result["verdict"] = "revise"
            result.setdefault("language_problems", []).append(
                f"mechanical length gate: {length} CJK, required {low}-{high}"
            )
            result["harness_decision"] = "rejected_by_mechanical_length_gate"
        readability = validate_characters(
            adaptation, self.readability_charset,
            self.glossary_chars | self.focus_vocabulary_chars,
            self.scene_max_above_level_ratio,
        )
        if not readability["passes"]:
            result = dict(result)
            result["verdict"] = "revise"
            chars = "".join(readability["above_level_list"][:60])
            result.setdefault("language_problems", []).append(
                "mechanical readability gate: "
                f"{readability['above_level_percent']}% above-level, maximum "
                f"{self.scene_max_above_level_ratio * 100:.0f}% for one section; "
                f"the complete chapter must remain at or below "
                f"{policy.max_above_level_ratio * 100:.0f}%; replace where natural: {chars}"
            )
            result["harness_decision"] = "rejected_by_mechanical_readability_gate"
        beginner_readability = validate_beginner_chinese(
            adaptation, self.args.level,
            allowed_words=self.focus_vocabulary_words,
            max_above_level_word_ratio=self.scene_max_above_level_ratio,
        )
        if not beginner_readability["passes"]:
            result = dict(result)
            result["verdict"] = "revise"
            problems = []
            if not beginner_readability["lexical_pass"]:
                words = "、".join(beginner_readability["above_level_words"][:40])
                problems.append(
                    f"{beginner_readability['above_level_word_percent']}% "
                    f"naturally segmented word tokens are above {self.args.level.upper()} "
                    "after story-term exemptions "
                    f"(maximum {self.scene_max_above_level_ratio * 100:.0f}% "
                    f"for one section; chapter maximum "
                    f"{policy.max_above_level_ratio * 100:.0f}%): {words}"
                )
            if not beginner_readability["sentence_pass"]:
                problems.append(
                    "sentence lengths exceed the beginner guardrail of "
                    f"{beginner_readability['max_sentence_cjk']} CJK: "
                    f"{beginner_readability['long_sentences']}"
                )
            if not beginner_readability["clause_pass"]:
                problems.append(
                    "clause lengths exceed the beginner guardrail of "
                    f"{beginner_readability['max_clause_cjk']} CJK: "
                    f"{beginner_readability['long_clauses']}"
                )
            result.setdefault("language_problems", []).append(
                f"mechanical {self.args.level.upper()} word/sentence gate: "
                + "; ".join(problems)
            )
            result["harness_decision"] = (
                "rejected_by_mechanical_level_word_sentence_gate"
            )
        level_diagnostics = validate_level_distinctiveness(
            adaptation,
            self.args.level,
            allowed_words=self.focus_vocabulary_words,
            lower_level_max_above_ratio=(
                lower_level_qualification_ratio(self.args.level)
            ),
        )
        result = dict(result)
        result["level_diagnostics"] = level_diagnostics
        paragraph_structure = (
            validate_paragraph_structure(
                adaptation,
                self.args.level,
                min_paragraphs=scene["min_paragraphs"] if self.fixed_size_requested else 0,
                max_paragraphs=scene["max_paragraphs"] if self.fixed_size_requested else 10_000,
                max_paragraph_cjk=scene["max_paragraph_cjk"],
            )
            if "min_paragraphs" in scene else None
        )
        if paragraph_structure is not None and not paragraph_structure["passes"]:
            result = dict(result)
            result["verdict"] = "revise"
            result.setdefault("language_problems", []).append(
                "mechanical paragraph structure gate: "
                f"{paragraph_structure['paragraph_count']} paragraphs; require "
                f"{paragraph_structure['min_paragraphs']}-"
                f"{paragraph_structure['max_paragraphs']}, maximum "
                f"{paragraph_structure['max_paragraph_cjk']} CJK per paragraph; "
                f"long paragraphs: {paragraph_structure['long_paragraphs']}"
            )
            result["harness_decision"] = (
                "rejected_by_mechanical_paragraph_structure_gate"
            )
        return result

    @staticmethod
    def prior_review_traps(attempts: list[dict[str, Any]]) -> list[str]:
        """Carry semantic/naturalness failures across stateless reviews."""
        traps: list[str] = []
        for attempt in attempts:
            review = attempt.get("review", {})
            for key in (
                "omissions", "unsupported_additions", "distortions",
                "language_problems",
            ):
                for finding in review.get(key, []):
                    text = str(finding).strip()
                    if (
                        text
                        and not text.startswith("mechanical ")
                        and text != "review scores must each be at least 8/10"
                        and text not in traps
                    ):
                        traps.append(text)
        return traps[-30:]

    async def repair_scene(
        self,
        scene: dict[str, Any],
        adaptation: str,
        review: dict[str, Any],
        attempt: int,
        prior_findings: list[str] | None = None,
    ) -> dict[str, Any]:
        original = self.source[scene["source_start"] : scene["source_end"]]
        findings = json.dumps(
            {key: value for key, value in review.items() if key != "level_diagnostics"},
            ensure_ascii=False,
            indent=2,
        )
        policy = policy_for(self.args.level)
        charset_hint = "".join(sorted(self.readability_charset))
        beginner_guidance = (
            hsk1_prompt_guidance() if self.args.level == "hsk1" else ""
        )
        events = "\n".join(f"- {event}" for event in scene.get("required_events", []))
        paragraph_guidance = (
            f"Break paragraphs naturally at changes of speaker, action, time, or scene; "
            f"keep each paragraph at or below {scene.get('max_paragraph_cjk', 250)} Chinese characters."
            if not self.fixed_size_requested else
            f"Keep {scene.get('min_paragraphs', 1)}-"
            f"{scene.get('max_paragraphs', 6)} semantic paragraphs, separated "
            f"by blank lines, with no paragraph over "
            f"{scene.get('max_paragraph_cjk', 250)} Chinese characters."
        )
        minimum_chars, maximum_chars = self.scene_length_bounds(scene["target_chars"])
        recovery_strategy = (
            scene_repair_strategy(adaptation, review, minimum_chars, maximum_chars)
            if self.fixed_size_requested else
            "RECOVERY MODE: localized prose repair. Preserve unaffected wording and source meaning; "
            "change only what the cited review finding requires. Keep the selected coverage natural for the requested learner level; do not pad or compress to reach a count."
        )
        prior_section = "" if not prior_findings else f"""
PREVIOUS FAILURES FROM THIS SAME RUN:
{json.dumps(prior_findings, ensure_ascii=False, indent=2)}
Do not reintroduce any of these defects while repairing the current review.
"""
        prompt = f"""Return only JSON matching the supplied schema, with the
revised scene in `text`. Repair ADAPTATION using the source-grounded REVIEW.
{recovery_strategy}
Preserve good prose and the selected broad story, but rewrite whole sentences
when necessary to satisfy the readability finding. Do not restore intentionally
omitted source events. Remove unsupported additions, correct distortions, fix
listed language problems, and use only simplified Chinese. The deterministic
gate permits at most {policy.max_above_level_ratio:.0%} above-level characters
and word tokens after the reviewed focus-vocabulary exemptions.
Use vocabulary that fits the requested level and retained source meaning; do not
rewrite or add content merely to demonstrate level-band vocabulary coverage.
{self.target_band_vocabulary_guidance}
{self.focus_vocabulary_guidance}
{self.prose_shape_guidance}
{paragraph_guidance}
Prefer this cumulative character set wherever
natural: {charset_hint}
{beginner_guidance}
{(f"Keep within the user-requested {minimum_chars}-{maximum_chars} character range." if self.fixed_size_requested else "There is no numeric length target; judge the repaired passage by source coverage, coherence, and learner-level naturalness.")}
{prior_section}

REQUIRED EVENTS (preserve all of them and do not restore optional outside
episodes):\n{events}\n\nORIGINAL:\n{original}\n\nADAPTATION:\n{adaptation}\n\nREVIEW:\n{findings}"""
        result = await self.runner.call(
            f"{scene['id']}/repair_{attempt:02d}", prompt,
            SCHEMAS / "adaptation.schema.json",
            self.args.repair_effort, refresh=self.args.refresh,
        )
        result["text"] = simplified(result["text"])
        return result

    async def recover_scene_by_source_beats(
        self,
        scene: dict[str, Any],
        prior_review: dict[str, Any],
    ) -> dict[str, Any]:
        """Regenerate only a repeatedly failing dense scene in smaller beats."""
        policy = policy_for(self.args.level)
        prior_findings = json.dumps(
            self.prior_review_traps([{"review": prior_review}]),
            ensure_ascii=False,
            indent=2,
        )

        async def adapt_beat(index: int, beat: dict[str, Any]) -> str:
            original = self.source[beat["source_start"] : beat["source_end"]]
            events = "\n".join(
                f"- {event}" for event in beat.get("required_events", [])
            )
            target = int(beat.get("target_chars", 0))
            low = max(1, round(target * 0.90)) if self.fixed_size_requested else 0
            high = max(low, round(target * 1.10)) if self.fixed_size_requested else 0
            beat_length = (
                f"Target {target} Chinese characters and stay within {low}-{high}."
                if self.fixed_size_requested else
                "Use enough source-grounded detail for the selected beat to read naturally; no numeric length target."
            )
            prompt = f"""Return only JSON matching the supplied schema, with this
internal source beat in `text`. This is beat {index} of a larger scene that has
repeatedly failed review when rewritten all at once. Adapt VERBATIM ORIGINAL in
its exact order into natural simplified Chinese for an annotated
{self.args.level.upper()} literary reader. Do not add a heading, recap earlier
beats, anticipate later beats, or invent a transition outside this source span.
Retain the listed events and their chronology. {policy.scope} {policy.fidelity}
Core grammar should be comfortable at {self.args.level.upper()}.
{self.target_band_vocabulary_guidance}
{self.focus_vocabulary_guidance}
{self.prose_shape_guidance}
Break paragraphs naturally. {beat_length}
Expand only with accurate details, motivations, and causal links from this
exact source beat. These defects were found in the rejected whole scene; avoid
them when relevant to this beat:
{prior_findings}

REQUIRED EVENTS:\n{events}\n\nVERBATIM ORIGINAL:\n{original}"""
            result = await self.runner.call(
                f"{scene['id']}/source_beat_recovery_{index:02d}",
                prompt,
                SCHEMAS / "adaptation.schema.json",
                self.args.repair_effort,
                refresh=self.args.refresh,
            )
            return simplified(result["text"]).strip()

        beats = scene.get("recovery_beats", [])
        if len(beats) < 2:
            raise ValueError(f"{scene['id']} has no source recovery beats")
        texts = await gather_all_or_raise(*(
            adapt_beat(index, beat) for index, beat in enumerate(beats, 1)
        ))
        return {"text": "\n\n".join(texts)}

    async def rewrite_scene(
        self,
        scene: dict[str, Any],
        attempts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Fresh final rewrite after local repair attempts fail."""
        original = self.source[scene["source_start"] : scene["source_end"]]
        policy = policy_for(self.args.level)
        charset_hint = "".join(sorted(self.readability_charset))
        beginner_guidance = (
            hsk1_prompt_guidance() if self.args.level == "hsk1" else ""
        )
        events = "\n".join(f"- {event}" for event in scene.get("required_events", []))
        paragraph_guidance = (
            f"Break paragraphs naturally at changes of speaker, action, time, or scene; "
            f"keep each paragraph at or below {scene.get('max_paragraph_cjk', 250)} Chinese characters."
            if not self.fixed_size_requested else
            f"Write {scene.get('min_paragraphs', 1)}-"
            f"{scene.get('max_paragraphs', 6)} semantic paragraphs separated "
            f"by blank lines, with no paragraph over "
            f"{scene.get('max_paragraph_cjk', 250)} Chinese characters."
        )
        minimum_chars, maximum_chars = self.scene_length_bounds(scene["target_chars"])
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
the broad outcome, major identities, and selected causal thread; do not try to
restore every source fact, name, number, or event. Write natural modern Chinese
for an {self.args.level.upper()} reader. Use only simplified Chinese. The
deterministic gate permits at most {policy.max_above_level_ratio:.0%} above-level
characters and word tokens after the reviewed focus-vocabulary exemptions.
Use vocabulary that fits the requested level and retained source meaning; do not
rewrite or add content merely to demonstrate level-band vocabulary coverage.
{self.target_band_vocabulary_guidance}
{self.focus_vocabulary_guidance}
{self.prose_shape_guidance}
{paragraph_guidance}
Prefer this cumulative character set:
{charset_hint}
{beginner_guidance}
Preserve every REQUIRED EVENT and omit optional outside episodes. Do not invent
facts. {(f"The user requested {scene['target_chars']} characters; target {minimum_chars}-{maximum_chars} if that remains natural and source-faithful." if self.fixed_size_requested else "Choose a natural length from meaningful source coverage, learner level, and a coherent stopping point. Do not pad or compress to meet a number.")} Develop accurate details, dialogue, motivations, and transitions from ORIGINAL.

REQUIRED EVENTS:\n{events}\n\nORIGINAL:\n{original}\n\nFAILED ATTEMPTS AND REVIEWS:\n{history}"""
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
                scene,
                adapted["text"],
                review,
                attempt,
                prior_findings=self.prior_review_traps(attempts),
            )
            review = await self.review_scene(
                scene,
                adapted["text"],
                suffix=f"repair_{attempt:02d}_review",
                prior_findings=self.prior_review_traps(attempts),
            )
            attempts.append(
                {"stage": f"repair_{attempt:02d}", "text": adapted["text"], "review": review}
            )
        if review["verdict"] != "pass" and self.args.fresh_rewrite:
            adapted = await self.rewrite_scene(scene, attempts)
            review = await self.review_scene(
                scene,
                adapted["text"],
                suffix="fresh_rewrite_review",
                prior_findings=self.prior_review_traps(attempts),
            )
            attempts.append(
                {"stage": "fresh_rewrite", "text": adapted["text"], "review": review}
            )
        if review["verdict"] != "pass":
            if self.fixed_size_requested:
                minimum_chars, maximum_chars = self.scene_length_bounds(scene["target_chars"])
                best = best_scene_attempt(attempts, minimum_chars, maximum_chars)
            else:
                best = next((item for item in reversed(attempts)
                             if item["review"].get("verdict") == "pass"), attempts[-1])
            adapted = {"text": best["text"]}
            review = best["review"]
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
phrases or clauses. Exception: keep a compact verb plus directional or
resultative complement together as one learner-tappable predicate when the
combined form is what a reader must interpret (for example 泛上来, 追上来,
走进去, 拿出来, or 做好). Do not absorb its subject, object, aspect marker, or
surrounding clause. Its contextual meaning must describe the complete predicate,
and a grammar overlay must explain what the complement contributes. Include
whitespace and punctuation as punctuation segments.
For every non-punctuation segment, give pinyin and a concise contextual English
meaning for that segment alone.
{CHINESE_PINYIN_POLICY}
{CHINESE_TRANSLATION_POLICY}
Separately add grammar_overlays for grammatical
constructions spanning one or more segments. Overlay start/end are zero-based
Python character offsets into TEXT (end exclusive), and overlay text must equal
TEXT[start:end]. Every overlay must also have a concise lowercase
grammar_candidate_key. This is only a provisional clustering hint for a later
unification pass, not a canonical grammar lesson ID; choose a useful descriptive
slug without assuming that another agent will use the identical wording. A grammar
overlay explains the construction; it must not
replace word segmentation or paraphrase an ordinary sentence. Concatenating
segment text must exactly reproduce TEXT.

TEXT:\n{chunk}{repair_context}"""
        return await self.runner.call(
            f"annotations/chunk_{index:04d}/{stage}", prompt,
            SCHEMAS / "annotation.schema.json", effort or self.args.annotation_effort,
            refresh=self.args.refresh,
            workspace_context={'chunk_text': chunk, 'language': 'zh'},
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
The following short surfaces were separately pre-reviewed as indispensable,
learner-tappable story terms and may remain whole even when compositional:
{compact(sorted(
    term for term in self.focus_vocabulary_story_terms
    if 1 < cjk_count(term) <= 4
))}
Do not reject one of those exact surfaces merely for being compositional.
Longer planned phrases are story overlays across ordinary lexical segments,
not permission to exceed the four-Han-character non-name segment cap. Never
request or propose merging a longer planned term into one segment, even if it
is an established weapon/title/name-like phrase; do not report its lexical
segmentation as an issue when the pieces have correct contextual meanings.
Use this pre-reviewed semantic inventory to catch misleading readings or
meanings inside historical terms and weapon names:
{self.focus_vocabulary_semantic_guidance}
Do not fuse a personal name with a neighboring verb, or a subject with its
predicate, into one lexical segment. Do not invent lexical definitions for
arbitrary clipped prefixes of names, weapons or titles. When a complete term
uses component boundaries, its parts must be meaningful lexical/morphemic units,
and a whole-term story overlay can explain their connection.
Meanings must explain the individual segment in its local context, not paraphrase
a whole clause. Dictionary entries explain reusable words, not every contextual
use: a dictionary link does not excuse missing help for a non-obvious grammatical
role or participation in a larger expression. Short translations suffice for
straightforward uses; flag confusing constructions whose logic remains unexplained.
Grammar overlays should explain genuine, useful, localized
grammar patterns separately from lexical segments. Each overlay's
grammar_candidate_key is a provisional clustering hint, not a canonical lesson
identifier; judge the pattern and explanation rather than demanding that keys from
different agents already match. A missing overlay for a
meaning-changing directional or resultative complement is material, not
optional: the learner must see both the main verb and what the complement
contributes. Reject a complement whose gloss incorrectly steals the main verb's
meaning, such as glossing 上来 itself as “surge upward” in 泛上来. Do not accept
that mistake, and reject generic sentence or clause summaries.
{CHINESE_PINYIN_POLICY}
{CHINESE_TRANSLATION_POLICY}
This is a
pragmatic learner reader, not a lexicography paper:
report only problems that materially mislead a learner, hide a useful word or
particle inside a larger span, split a real word/name/idiom, or turn an ordinary
clause into a fake expression. Do not fail an otherwise useful annotation for a
minor wording nuance, an optional grammar overlay, or another defensible gloss.
Set verdict=revise only when at least one such material problem remains;
otherwise return verdict=pass with no issues.
Choose `under_grouped` or `over_grouped`, not merely `meaning`, whenever the
suggested correction requires merging or splitting segment boundaries.
For every issue, start/end are REQUIRED zero-based Python character offsets into
the exact TEXT, end exclusive. Copy segment_text exactly from TEXT[start:end].
Identify the specific occurrence: repeated surface text must use the offsets of
the occurrence that is actually wrong. Self-check TEXT[start:end] == segment_text.
Use tools when useful. Copy issue offsets directly from the supplied
segment/overlay rows and verify them against TEXT.

{ISSUE_TARGET_GUIDANCE}
The exact candidate JSON is supplied below so every target has an explicit pointer.
ANNOTATION:
{compact(annotation)}

{FORM_STAGE_EVIDENCE_GUIDANCE}

TEXT:
{chunk}

SEGMENT ROWS:
COLUMNS=[CHAR_START,CHAR_END,TEXT,TYPE,PINYIN,MEANING_EN]
{compact(segment_rows)}

GRAMMAR OVERLAYS (existing exact offset objects):
{compact(annotation.get("grammar_overlays", []))}"""
        review = await self.runner.call(
            f"annotations/chunk_{index:04d}/{stage}_review", prompt,
            SCHEMAS / "annotation-review-targets.schema.json",
            effort or self.args.annotation_review_effort, refresh=self.args.refresh,
            workspace_context={"annotation_issue_targets_validation": {
                "candidate": annotation, "source_text": chunk,
                "representation": "chinese-annotation", "require_typed": True}},
        )
        validate_issue_targets(review, annotation, source_text=chunk,
                               representation="chinese-annotation", require_typed=True)
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
        run_dir = getattr(self, "run_dir", None)
        if run_dir is not None:
            job = f"annotations/chunk_{index:04d}/{stage}_review"
            try:
                candidate = {"segments": annotation.get("segments"),
                             "grammar_overlays": annotation.get("grammar_overlays")}
                component = bind_review_job(
                    Path(run_dir), job, candidate=candidate, source_text=chunk)
            except (OSError, ValueError, KeyError, TypeError):
                component = None
            receipts = getattr(self, "_annotation_review_receipts", None)
            if receipts is None:
                receipts = self._annotation_review_receipts = {}
            receipts[(index, stage)] = [component] if component else []
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
            candidate_key = str(overlay.get("grammar_candidate_key", ""))
            if not _GRAMMAR_CANDIDATE_KEY.fullmatch(candidate_key):
                issues.append({"segment_text": str(overlay.get("text", "")), "problem": "grammar", "explanation": "Provisional grammar candidate key is missing or malformed.", "suggested_fix": "Supply a concise provisional slug. It is a clustering hint, not a canonical lesson identifier."})
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

    def prepare_annotation_candidate(
        self, chunk: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        """Apply deterministic, policy-selected annotation normalization."""
        value = self.canonicalize_annotation_candidate(chunk, result)
        if getattr(self.args, "no_grammar_overlays", False):
            # Grammar overlays are optional enrichment.  The initial reader
            # release is allowed to ship reviewed lexical tap meanings alone,
            # avoiding low-value overlay churn without changing segmentation.
            value["grammar_overlays"] = []
        return value

    async def annotate_chunk(self, index: int, chunk: str) -> dict[str, Any]:
        mode = getattr(self.args, "annotation_mode", "generative")
        if mode == "constrained":
            return await self.annotate_chunk_constrained(index, chunk)
        if mode == "constrained-delta":
            raise ValueError("constrained-delta annotations must be seeded once at chapter scope")
        result = self.prepare_annotation_candidate(
            chunk, await self.annotation_candidate(index, chunk)
        )
        attempts: list[dict[str, Any]] = []
        adjudication_used = False
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
            attempt_record = {
                "stage": "initial" if attempt == 0 else f"repair_{attempt:02d}",
                "annotation": result, "review": review,
            }
            if getattr(self, "run_dir", None):
                try:
                    attempt_record["normal_review_receipt"] = normal_review_receipt(
                        Path(self.run_dir),
                        [f"annotations/chunk_{index:04d}/{attempt_record['stage']}_review"],
                        review, result, chunk)
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            if review["verdict"] == "pass":
                attempts.append(attempt_record)
                return {"segments": result["segments"],
                        "grammar_overlays": result["grammar_overlays"],
                        "attempts": attempts, "resolved": True}
            effective_review = None
            repair_findings = review
            adjudication_context_extra = {}
            previous = attempts[-1] if attempts else {}
            if (not adjudication_used and attempt > 0
                    and previous.get("semantic_repair", {}).get("status") == "applied"
                    and self.annotation_reconstructs(chunk, result)
                    and not self.annotation_contract_issues(chunk, result)
                    and getattr(self, "run_dir", None)
                    and (Path(self.run_dir) / "agents" /
                         str(previous.get("semantic_repair", {}).get("assembly_job", "")) /
                         "meta.json").is_file()):
                from pipeline.annotation_adjudication import (
                    adjudicate_annotation_review, digest as evidence_digest,
                )
                from pipeline.annotation_repairs import replay_annotation_repair
                repair_job = previous["semantic_repair"]["assembly_job"]
                schema = json.loads((SCHEMAS / "annotation.schema.json").read_text())

                def validate_replayed(candidate: dict[str, Any]) -> None:
                    errors = list(Draft202012Validator(schema).iter_errors(candidate))
                    if errors:
                        raise ValueError(errors[0].message)
                    contract = self.annotation_contract_issues(chunk, candidate)
                    if contract:
                        raise ValueError(str(contract))

                replayed = replay_annotation_repair(Path(self.run_dir), repair_job,
                    validate_candidate=validate_replayed)
                if replayed.get("status") != "applied" or replayed.get("candidate") != result:
                    raise ValueError("Chinese applied semantic-repair evidence does not match reviewed candidate")
                run_dir = Path(self.run_dir)
                review_job = f"annotations/chunk_{index:04d}/{attempt_record['stage']}_review"
                review_meta_path = run_dir / "agents" / review_job / "meta.json"
                review_meta = json.loads(review_meta_path.read_text(encoding="utf-8"))
                normalized_review_digest = evidence_digest(review)
                raw_review = json.loads((review_meta_path.parent / "result.json").read_text(encoding="utf-8"))
                receipt = {"kind": "composite", "review_digest": normalized_review_digest,
                           "candidate_digest": evidence_digest(result),
                           "source_digest": evidence_digest(chunk),
                           "components": [child_job_receipt(run_dir, review_job)]}
                receipt["components"][0].update(
                    candidate_digest=evidence_digest(result),
                    source_digest=evidence_digest(chunk))
                policy_reference = {
                    "review-policy": {"kind": "explicit_review_policy",
                        "content": {"review_prompt": self.annotation_chunk_cache_tag,
                            "semantic_guidance": self.focus_vocabulary_semantic_guidance,
                            "translation_policy": CHINESE_TRANSLATION_POLICY,
                            "pinyin_policy": CHINESE_PINYIN_POLICY}}
                }
                gate = {"passed": True, "issues": [],
                        "candidate_digest": evidence_digest(result),
                        "source_text_digest": evidence_digest(chunk)}
                adjudication_context = {"chunk_text": chunk,
                    "grammar_knowledge": chinese_semantic_repair_grammar_knowledge(result)}
                adjudication_history = copy.deepcopy(attempts)
                adjudication_used = True
                adjudication = await adjudicate_annotation_review(
                    self.runner, run_dir, language="zh", representation="chinese-annotation",
                    candidate=result, current_review=review, prior_history=adjudication_history,
                    context=adjudication_context,
                    known_reference_input=policy_reference,
                    deterministic_gate_evidence=gate, normal_review_receipt=receipt,
                    source_text=chunk)
                if adjudication.get("status") == "cleared":
                    from pipeline.annotation_adjudication import verify_adjudication_evidence
                    adjudication = verify_adjudication_evidence(
                        run_dir, adjudication, language="zh",
                        representation="chinese-annotation", candidate=result,
                        current_review=review, prior_history=adjudication_history,
                        context=adjudication_context,
                        known_reference_input=policy_reference,
                        deterministic_gate_evidence=gate,
                        normal_review_receipt=receipt, source_text=chunk)
                attempt_record["adjudication"] = adjudication
                if adjudication["status"] == "cleared":
                    attempt_record["adjudication_replay"] = {
                        "prior_history": adjudication_history,
                        "context": adjudication_context,
                        "known_reference_input": policy_reference,
                        "deterministic_gate_evidence": gate,
                        "normal_review_receipt": receipt,
                        "source_text": chunk,
                    }
                    attempt_record["effective_review"] = {"kind": "adjudicated",
                                                           "evidence": adjudication}
                    attempts.append(attempt_record)
                    return {"segments": result["segments"],
                            "grammar_overlays": result["grammar_overlays"],
                            "attempts": attempts, "resolved": True,
                            "reviewed": True,
                            "acceptance_state": "reviewed_and_adjudicated",
                            "effective_review": {"kind": "adjudicated",
                                                 "evidence": adjudication}}
                if adjudication["status"] == "actionable":
                    repair_findings = {"verdict": "revise",
                        "issues": adjudication.get("repair_diagnoses", [])}
                    adjudication_context_extra = {"prior_review_adjudication": adjudication,
                                                  "prior_review_history": attempts}
                else:
                    raise ValueError(
                        f"Chinese annotation adjudication did not clear chunk {index}: "
                        f"{adjudication['status']}"
                    )
            if attempt < self.args.max_annotation_repairs:
                if not getattr(self, "run_dir", None) or not repair_findings.get("issues"):
                    semantic = None
                else:
                    from pipeline.annotation_repairs import repair_annotation

                    schema = json.loads((SCHEMAS / "annotation.schema.json").read_text())

                    def validate_semantic_candidate(candidate: dict[str, Any]) -> None:
                        errors = list(Draft202012Validator(schema).iter_errors(candidate))
                        if errors:
                            raise ValueError(f"Chinese annotation patch violates its schema: {errors[0].message}")
                        contract = self.annotation_contract_issues(chunk, candidate)
                        if contract:
                            raise ValueError(f"Chinese annotation patch failed local checks: {contract}")

                    semantic = await repair_annotation(
                        self,
                        f"annotations/chunk_{index:04d}/semantic_repair_{attempt + 1:02d}",
                        result,
                        repair_findings["issues"],
                        representation="chinese-annotation",
                        language="zh",
                        context={"chunk_text": chunk,
                                 "grammar_knowledge": chinese_semantic_repair_grammar_knowledge(result),
                                 **adjudication_context_extra},
                        validate_candidate=validate_semantic_candidate,
                        refresh=self.args.refresh,
                    )
                if semantic is not None:
                    attempt_record["semantic_repair"] = {
                        "status": semantic["status"], **semantic["evidence"],
                    }
                if semantic is not None and semantic["status"] == "applied":
                    result = semantic["candidate"]
                elif semantic is not None and semantic["status"] == "boundary_change_needed":
                    # Source/tap changes stay on the pre-existing complete
                    # candidate path. Constrained fixed-boundary corrections
                    # remain in their independent lossless correction flow.
                    result = self.prepare_annotation_candidate(
                        chunk, await self.annotation_candidate(
                            index, chunk, stage=f"repair_{attempt + 1:02d}", prior=result,
                            findings=repair_findings, effort=self.args.annotation_repair_effort,
                        )
                    )
                elif semantic is None:
                    result = self.prepare_annotation_candidate(
                        chunk, await self.annotation_candidate(
                            index, chunk, stage=f"repair_{attempt + 1:02d}", prior=result,
                            findings=repair_findings, effort=self.args.annotation_repair_effort,
                        )
                    )
                else:
                    raise ValueError(
                        "Chinese semantic annotation patch was rejected: "
                        f"{semantic.get('patch_error') or semantic.get('validation_error')}"
                    )
            attempts.append(attempt_record)

        # A fresh low-effort attempt uses a new prompt/cache key instead of
        # repeatedly patching a bad segmentation.
        result = self.prepare_annotation_candidate(
            chunk, await self.annotation_candidate(
                index, chunk, stage="fresh", effort=self.args.annotation_final_effort
            )
        )
        if self.annotation_reconstructs(chunk, result):
            review = await self.review_annotation(index, chunk, result, "fresh")
        else:
            review = {"verdict": "revise",
                      "issues": self.annotation_contract_issues(chunk, result)}
        fresh_record = {"stage": "fresh", "annotation": result, "review": review}
        if getattr(self, "run_dir", None):
            try:
                fresh_record["normal_review_receipt"] = normal_review_receipt(
                    Path(self.run_dir),
                    [f"annotations/chunk_{index:04d}/fresh_review"],
                    review, result, chunk)
            except (OSError, ValueError, KeyError, TypeError):
                pass
        attempts.append(fresh_record)
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

        surfaces = refined_fixed_segments(
            chunk,
            protected_names=self.focus_vocabulary_names,
            protected_compounds=self.focus_vocabulary_story_terms,
        )
        raw = await self.runner.call(
            f"annotations/chunk_{index:04d}/constrained_{stage}",
            f"{self.annotation_chunk_cache_tag}\n"
            f"PRE-REVIEWED STORY SEMANTICS:\n"
            f"{self.focus_vocabulary_semantic_guidance}\n"
            f"{prompt_for(chunk, surfaces, self.args.level)}",
            SCHEMA, effort,
            refresh=self.args.refresh,
            workspace_context={'chunk_text': chunk, 'language': 'zh-fixed', 'surfaces': surfaces},
        )
        return validate_result(chunk, surfaces, raw)

    async def constrained_annotation_correction(
        self, index: int, chunk: str, annotation: dict[str, Any],
        findings: dict[str, Any], attempt: int, *,
        effort: str | None = None, stage: str | None = None,
    ) -> dict[str, Any]:
        """Apply only lossless, index-addressed patches proposed by a model."""
        from pipeline.fixed_boundary_annotation import (
            CORRECTION_SCHEMA, _review_scopes, apply_correction_patches, correction_prompt,
            review_scoped_correction,
        )

        raw_correction = await self.runner.call(
            f"annotations/chunk_{index:04d}/constrained_"
            f"{stage or f'correction_{attempt:02d}'}",
            f"{self.annotation_chunk_cache_tag}\n"
            f"{correction_prompt(chunk, annotation, findings)}", CORRECTION_SCHEMA,
            effort or self.args.annotation_repair_effort, refresh=self.args.refresh,
            workspace_context={'chunk_text': chunk, 'language': 'zh-patch', 'annotation': annotation},
        )
        correction, scope_evidence = review_scoped_correction(
            chunk, annotation, raw_correction, findings
        )
        # A reviewer can correctly flag an interior token of a long, planned
        # story term while the only faithful repair must span the complete
        # term (for example 八点 inside 丈八点钢矛). Permit that broader patch only
        # for an exact pre-reviewed story-term surface overlapping a valid
        # issue. The strict lossless/segment-size primitive below still owns
        # final acceptance.
        _ranges, issue_chars, _discarded = _review_scopes(
            chunk, annotation["segments"], findings, grammar=False,
        )
        offsets: list[tuple[int, int]] = []
        cursor = 0
        for segment in annotation["segments"]:
            end = cursor + len(segment["text"])
            offsets.append((cursor, end))
            cursor = end
        scoped_patches = correction.setdefault("patches", [])
        for patch in raw_correction.get("patches", []):
            if not isinstance(patch, dict):
                continue
            already_scoped = patch in scoped_patches
            start, end = patch.get("start_index"), patch.get("end_index")
            if (not isinstance(start, int) or isinstance(start, bool)
                    or not isinstance(end, int) or isinstance(end, bool)
                    or not 0 <= start < end <= len(offsets)):
                continue
            char_start, char_end = offsets[start][0], offsets[end - 1][1]
            surface = chunk[char_start:char_end]
            if (
                surface in self.focus_vocabulary_story_terms
                and any(char_start < right and left < char_end
                        for left, right in issue_chars)
            ):
                if not already_scoped:
                    scoped_patches.append(patch)
                story_evidence = {"range": [start, end], "surface": surface}
                entries = scope_evidence.setdefault(
                    "planned_story_scope_patches", []
                )
                if story_evidence not in entries:
                    entries.append(story_evidence)
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
            if isinstance(raw_issue, dict):
                no_op_evidence = " ".join(str(raw_issue.get(key, "")) for key in (
                    "explanation", "suggested_fix",
                )).lower()
                if ("no change needed" in no_op_evidence
                        or "current annotation itself is fine" in no_op_evidence):
                    discarded.append({
                        "kind": "issue", "reason": "reviewer_explicit_no_op",
                    })
                    continue
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
            async def request_local_correction(
                requested_ranges: list[tuple[int, int]],
                requested_review: dict[str, Any],
                request_stage: str,
            ) -> dict[str, Any]:
                """Give the model a small contiguous table, then rebase patches."""
                left_token = max(0, min(item[0] for item in requested_ranges) - 4)
                right_token = min(
                    len(offsets), max(item[1] for item in requested_ranges) + 4
                )
                char_start = offsets[left_token][0]
                char_end = offsets[right_token - 1][1]
                local_annotation = {
                    "segments": copy.deepcopy(
                        annotation["segments"][left_token:right_token]
                    ),
                    "grammar_overlays": [{
                        **copy.deepcopy(overlay),
                        "start": overlay["start"] - char_start,
                        "end": overlay["end"] - char_start,
                    } for overlay in annotation.get("grammar_overlays", [])
                        if (
                            isinstance(overlay.get("start"), int)
                            and isinstance(overlay.get("end"), int)
                            and char_start <= overlay["start"]
                            and overlay["end"] <= char_end
                        )],
                }
                local_review = copy.deepcopy(requested_review)
                for issue in local_review.get("issues", []):
                    if isinstance(issue.get("start"), int):
                        issue["start"] -= char_start
                    if isinstance(issue.get("end"), int):
                        issue["end"] -= char_start
                result = await self.constrained_annotation_correction(
                    number,
                    chapter[char_start:char_end],
                    local_annotation,
                    local_review,
                    number + 1,
                    effort=effort,
                    stage=request_stage,
                )
                evidence = result["correction_evidence"]
                evidence["applied_patches"] = [{
                    **patch,
                    "start_index": patch["start_index"] + left_token,
                    "end_index": patch["end_index"] + left_token,
                } for patch in evidence.get("applied_patches", [])]
                evidence["local_window"] = {
                    "token_start": left_token,
                    "token_end": right_token,
                    "character_start": char_start,
                    "character_end": char_end,
                }
                return result

            batch_review = {
                "verdict": "revise",
                "issues": [issue for token_range in ranges for issue in scoped[token_range]],
            }
            result = await request_local_correction(
                ranges, batch_review, f"{stage}_batch_{number:04d}"
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
                completion = await request_local_correction(
                    missing,
                    missing_review,
                    f"{stage}_batch_{number:04d}_missing",
                )
                extra = completion["correction_evidence"].get("applied_patches", [])
                # A model can repeat a valid sibling patch even when this call
                # is scoped to the missing findings. Do not let collateral or
                # duplicate output poison the exact-coverage retry.
                extra = [patch for patch in extra
                         if any(covered(token_range, [patch]) for token_range in missing)]
                patches = [*patches, *extra]
                completion_calls = 1
                missing = [token_range for token_range in ranges
                           if not covered(token_range, patches)]
            if missing:
                # One final one-finding-at-a-time request removes ambiguity in
                # a batch where the model kept repairing a neighboring span.
                # Retain only the exact requested coverage from each response.
                for retry_number, token_range in enumerate(missing):
                    single_review = {
                        "verdict": "revise",
                        "issues": scoped[token_range],
                    }
                    completion = await request_local_correction(
                        [token_range],
                        single_review,
                        (f"{stage}_batch_{number:04d}_missing_"
                         f"{retry_number:04d}"),
                    )
                    extra = completion["correction_evidence"].get(
                        "applied_patches", []
                    )
                    patches.extend(
                        patch for patch in extra if covered(token_range, [patch])
                    )
                    completion_calls += 1
                missing = [token_range for token_range in ranges
                           if not covered(token_range, patches)]
            for recovery_round in range(
                1, self.args.max_annotation_repairs + 1
            ):
                if not missing:
                    break
                # A schema-valid correction can still miss the requested
                # token span. Give that exact one-finding request a bounded
                # fresh stage instead of crashing the entire resumable
                # chapter after otherwise successful metadata work.
                for retry_number, token_range in enumerate(missing):
                    single_review = {
                        "verdict": "revise",
                        "issues": scoped[token_range],
                    }
                    completion = await request_local_correction(
                        [token_range],
                        single_review,
                        (
                            f"{stage}_batch_{number:04d}_coverage_retry_"
                            f"{recovery_round:02d}_{retry_number:04d}"
                        ),
                    )
                    extra = completion["correction_evidence"].get(
                        "applied_patches", []
                    )
                    patches.extend(
                        patch for patch in extra
                        if covered(token_range, [patch])
                    )
                    completion_calls += 1
                missing = [
                    token_range for token_range in ranges
                    if not covered(token_range, patches)
                ]
            if missing:
                raise ValueError(
                    "issue-scoped correction did not cover every finding span: "
                    f"missing={missing}; patches="
                    f"{[(item.get('start_index'), item.get('end_index')) for item in patches]}"
                )
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
            record = {"stage": stage, "annotation": result, "review": review}
            if getattr(self, "run_dir", None):
                try:
                    record["normal_review_receipt"] = normal_review_receipt(
                        Path(self.run_dir),
                        [f"annotations/chunk_{index:04d}/{stage}_review"],
                        review, result, chunk)
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            attempts.append(record)
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
        # not pass. This is a genuinely new enrichment attempt with a fresh
        # cache key, not an unreviewed acceptance or unconstrained rewrite.
        result = await self.constrained_annotation_candidate(
            index, chunk, stage="fresh", effort=self.args.annotation_final_effort
        )
        review = await self.review_annotation(index, chunk, result, "fresh")
        record = {"stage": "fresh", "annotation": result, "review": review}
        if getattr(self, "run_dir", None):
            try:
                record["normal_review_receipt"] = normal_review_receipt(
                    Path(self.run_dir),
                    [f"annotations/chunk_{index:04d}/fresh_review"],
                    review, result, chunk)
            except (OSError, ValueError, KeyError, TypeError):
                pass
        attempts.append(record)
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
            record = {"stage": stage, "annotation": result, "review": review}
            if getattr(self, "run_dir", None):
                try:
                    record["normal_review_receipt"] = normal_review_receipt(
                        Path(self.run_dir),
                        [f"annotations/chunk_{index:04d}/{stage}_review"],
                        review, result, chunk)
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            attempts.append(record)
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
        # and fresh correction, followed by a genuinely independent review.
        # Never accept the correction deterministically.
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
        record = {
            "stage": "delta_final_correction",
            "annotation": result,
            "review": review,
            "correction_effort": self.args.annotation_final_effort,
            "review_effort": self.args.annotation_final_effort,
        }
        if getattr(self, "run_dir", None):
            try:
                record["normal_review_receipt"] = normal_review_receipt(
                    Path(self.run_dir),
                    [f"annotations/chunk_{index:04d}/delta_final_review"],
                    review, result, chunk)
            except (OSError, ValueError, KeyError, TypeError):
                pass
        attempts.append(record)
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

        baseline, unknown = deterministic_baseline(
            chapter,
            protected_names=self.focus_vocabulary_names,
            protected_compounds=self.focus_vocabulary_story_terms,
        )
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
            await self.plan_focus_vocabulary(outline)
            results = await gather_all_or_raise(
                *(self.process_scene(scene) for scene in outline["scenes"])
            )
            chapter = "\n\n".join(result["text"].strip() for result in results) + "\n"
            policy = policy_for(self.args.level)
            chapter_character_readability = validate_characters(
                chapter,
                self.readability_charset,
                self.glossary_chars | self.focus_vocabulary_chars,
                policy.max_above_level_ratio,
            )
            chapter_word_readability = validate_beginner_chinese(
                chapter,
                self.args.level,
                allowed_words=self.focus_vocabulary_words,
                max_above_level_word_ratio=policy.max_above_level_ratio,
            )
            if (
                not chapter_character_readability["passes"]
                or not chapter_word_readability["passes"]
            ):
                # Attribute a whole-chapter budget failure to the locally
                # hardest scene so a blocked run remains actionable.
                local_word_evidence = [
                    validate_beginner_chinese(
                        result["text"],
                        self.args.level,
                        allowed_words=self.focus_vocabulary_words,
                        max_above_level_word_ratio=1.0,
                    )
                    for result in results
                ]
                index = max(
                    range(len(results)),
                    key=lambda item: local_word_evidence[item][
                        "above_level_word_ratio"
                    ],
                )
                result = results[index]
                review = copy.deepcopy(result["review"])
                review["verdict"] = "revise"
                problems = review.setdefault("language_problems", [])
                if not chapter_character_readability["passes"]:
                    problems.append(
                        "mechanical whole-chapter character budget: "
                        f"{chapter_character_readability['above_level_percent']}% "
                        f"above-level, maximum "
                        f"{policy.max_above_level_ratio * 100:.0f}%"
                    )
                if not chapter_word_readability["passes"]:
                    problems.append(
                        "mechanical whole-chapter word/sentence budget: "
                        f"{chapter_word_readability['above_level_word_percent']}% "
                        f"above-level word tokens, maximum "
                        f"{policy.max_above_level_ratio * 100:.0f}%"
                    )
                review["harness_decision"] = (
                    "rejected_by_mechanical_chapter_readability_gate"
                )
                result["review"] = review
                result["resolved"] = False
                result["attempts"][-1]["review"] = review
                scene_path = (
                    self.run_dir / "scenes" / f"{result['scene']['id']}.json"
                )
                scene_path.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n"
                )
            distributed_length_acceptances = (
                accept_distributed_scene_lengths(
                    results, chapter, self.args.level, self.target_chars
                ) if self.fixed_size_requested else []
            )
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
                    annotated = await map_chunks(
                        chunks, self.annotate_chunk,
                        getattr(self.args, "concurrency", 1),
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
                chunk_review_receipts = persist_chunk_attempts(
                    self.run_dir, chunks, annotated, surface_key="text")
                per_chunk_review_proof = not (
                    getattr(self.args, "annotation_mode", "generative") == "constrained-delta"
                    and getattr(self.args, "annotation_review_policy", "chapter") == "chapter")
                reader = {
                    "title": outline["chapter_title"], "level": self.args.level.upper(),
                    "text": chapter, "segments": segments,
                    "grammar_overlays": grammar_overlays,
                    "annotation_audit": {
                        "mode": getattr(self.args, "annotation_mode", "generative"),
                        "policy_version": CHINESE_ANNOTATION_POLICY_VERSION,
                        "chunks": len(annotated),
                        **({"chunk_review_receipts_version": 1,
                            "chunk_review_receipts": chunk_review_receipts}
                           if per_chunk_review_proof else {}),
                        "attempts_per_chunk": [len(item["attempts"]) for item in annotated],
                        "all_reviewed": all(annotation_review_is_complete(item, self.run_dir)
                                             for item in annotated),
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
                "distributed_scene_length_acceptances": (
                    distributed_length_acceptances
                ),
                "chapter_readability": {
                    "characters": chapter_character_readability,
                    "words_and_sentences": chapter_word_readability,
                },
            }
            (self.run_dir / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            )
            self.write_manifest(final_status)
            return report
        except BaseException:
            self.write_manifest("failed")
            raise


def load_promotion_candidate(spec: str) -> tuple[Path, str, str | None]:
    """Load plain text or one named attempt from a scene-result JSON file."""
    stage = None
    path_text = spec
    if "@" in spec:
        path_text, stage = spec.rsplit("@", 1)
    candidate_path = Path(path_text).resolve()
    candidate_source = candidate_path.read_text(encoding="utf-8")
    if candidate_path.suffix == ".json":
        candidate_object = json.loads(candidate_source)
        if stage is not None and stage.startswith("attempt:"):
            attempts = candidate_object.get("attempts", [])
            try:
                attempt_index = int(stage.split(":", 1)[1])
                selected = attempts[attempt_index]
            except (ValueError, IndexError, TypeError) as exc:
                raise ValueError(
                    f"candidate attempt selector {stage!r} is invalid: "
                    f"{candidate_path}"
                ) from exc
            if not isinstance(selected.get("text"), str):
                raise ValueError(
                    f"candidate attempt {attempt_index} has no text: "
                    f"{candidate_path}"
                )
            candidate_source = selected["text"]
            stage = f"attempt:{attempt_index}:{selected.get('stage', 'unknown')}"
        elif stage is not None:
            matches = [
                item for item in candidate_object.get("attempts", [])
                if item.get("stage") == stage
            ]
            if len(matches) != 1 or not isinstance(matches[0].get("text"), str):
                raise ValueError(
                    f"candidate stage {stage!r} not found exactly once: "
                    f"{candidate_path}"
                )
            candidate_source = matches[0]["text"]
        elif (
            not isinstance(candidate_object, dict)
            or not isinstance(candidate_object.get("text"), str)
        ):
            raise ValueError(f"JSON candidate has no text field: {candidate_path}")
        else:
            candidate_source = candidate_object["text"]
    elif stage is not None:
        raise ValueError("@stage selection requires a JSON candidate")
    candidate = simplified(candidate_source).strip()
    if not candidate:
        raise ValueError(f"candidate is empty: {candidate_path}")
    return candidate_path, candidate, stage


def reusable_passed_scene(prior: Any, candidate: str) -> bool:
    """Return true only for an unchanged, already reviewed passing scene."""
    return (
        isinstance(prior, dict)
        and prior.get("resolved") is True
        and isinstance(prior.get("review"), dict)
        and prior["review"].get("verdict") == "pass"
        and isinstance(prior.get("text"), str)
        and simplified(prior["text"]).strip() == candidate
    )


def materialize_recovery_beats(
    source: str,
    scene: dict[str, Any],
    raw_beats: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve optional editorial recovery beats inside one source scene.

    Beats are an escape hatch for a dense scene, not a second scene outline.
    Their anchors must partition the existing source span in order, and their
    numeric targets are allocated deterministically from the parent target.
    """
    if len(raw_beats) < 2:
        raise ValueError("source-beat recovery requires at least two beats")
    weights = [float(item.get("target_weight", 1)) for item in raw_beats]
    if any(weight <= 0 for weight in weights):
        raise ValueError("recovery beat target weights must be positive")
    starts: list[int] = []
    search_from = int(scene["source_start"])
    scene_end = int(scene["source_end"])
    for index, raw in enumerate(raw_beats):
        quote = str(raw["source_start_quote"])
        start = source.find(quote, search_from, scene_end)
        if start < 0:
            raise ValueError(f"recovery beat anchor not found in scene: {quote!r}")
        if index == 0 and start != int(scene["source_start"]):
            raise ValueError("first recovery beat must start at the scene boundary")
        starts.append(start)
        search_from = start + max(1, len(quote))
    if starts != sorted(starts) or len(starts) != len(set(starts)):
        raise ValueError("recovery beat anchors are not unique and ordered")

    total_weight = sum(weights)
    assigned = 0
    beats: list[dict[str, Any]] = []
    for index, (raw, weight, start) in enumerate(
        zip(raw_beats, weights, starts), 1
    ):
        if index == len(raw_beats):
            target = int(scene["target_chars"]) - assigned
        else:
            target = round(int(scene["target_chars"]) * weight / total_weight)
            assigned += target
        beats.append({
            "id": str(raw.get("id", f"beat_{index:02d}")),
            "source_start_quote": str(raw["source_start_quote"]),
            "source_start": start,
            "source_end": starts[index] if index < len(starts) else scene_end,
            "target_chars": target,
            "paragraphs": int(raw.get("paragraphs", 1)),
            "required_events": [
                str(item) for item in raw.get("required_events", [])
            ],
        })
    if any(item["paragraphs"] < 1 for item in beats):
        raise ValueError("recovery beat paragraph counts must be positive")
    return beats


def attach_editorial_recovery_beats(
    source: str,
    outline: dict[str, Any],
    editorial_plan_path: Path | None,
    level: str,
) -> None:
    """Hydrate recovery metadata for old resumable outlines when available."""
    if editorial_plan_path is None:
        return
    plan = json.loads(editorial_plan_path.read_text(encoding="utf-8"))
    raw_scenes = (
        plan.get("level_plans", {}).get(level, {}).get("adaptation_scenes", [])
    )
    scenes = outline.get("scenes", [])
    if len(raw_scenes) != len(scenes):
        raise ValueError("editorial recovery plan does not match saved outline")
    for scene, raw in zip(scenes, raw_scenes):
        raw_beats = raw.get("recovery_beats")
        if raw_beats:
            scene["recovery_beats"] = materialize_recovery_beats(
                source, scene, raw_beats
            )


async def promote_reviewed_book_candidates(
    args: argparse.Namespace,
    harness: ChapterHarness,
    manifest: dict[str, Any],
    outline: dict[str, Any],
) -> dict[str, Any]:
    """Independently review one selected candidate for every planned scene."""
    scenes = outline["scenes"]
    if harness.fixed_size_requested:
        attach_editorial_recovery_beats(
            harness.source, outline, harness.editorial_plan_path, harness.args.level,
        )
    harness.active_scene_count = len(scenes)
    specifications: dict[str, str] = {}
    for raw in args.candidate:
        if "=" not in raw:
            raise ValueError(
                "multi-scene candidates must use SCENE_ID=PATH[@STAGE]"
            )
        scene_id, spec = raw.split("=", 1)
        if scene_id in specifications:
            raise ValueError(f"duplicate candidate for {scene_id}")
        specifications[scene_id] = spec
    expected = {scene["id"] for scene in scenes}
    if set(specifications) != expected:
        raise ValueError(
            "candidate scene IDs must exactly match outline: "
            f"expected {sorted(expected)}, got {sorted(specifications)}"
        )

    results: list[dict[str, Any]] = []
    promotion_sources: dict[str, Any] = {}
    for scene in scenes:
        candidate_path, candidate, selected_stage = load_promotion_candidate(
            specifications[scene["id"]]
        )
        scene_path = harness.run_dir / "scenes" / f"{scene['id']}.json"
        prior = (
            json.loads(scene_path.read_text(encoding="utf-8"))
            if scene_path.is_file() else {"attempts": []}
        )
        if (
            getattr(args, "reuse_passed_scenes", False)
            and reusable_passed_scene(prior, candidate)
        ):
            review = copy.deepcopy(prior["review"])
            attempts = [{
                "stage": "candidate_reused_pass",
                "candidate_path": str(candidate_path),
                "selected_stage": selected_stage,
                "text": candidate,
                "review": review,
            }]
        else:
            review = await harness.review_scene(
                scene, candidate, suffix="candidate_promotion_review"
            )
            attempts = [{
                "stage": "candidate_promotion",
                "candidate_path": str(candidate_path),
                "selected_stage": selected_stage,
                "text": candidate,
                "review": review,
            }]
        for attempt in range(1, args.max_repairs + 1):
            if review["verdict"] == "pass":
                break
            repaired = await harness.repair_scene(
                scene,
                candidate,
                review,
                attempt,
                prior_findings=harness.prior_review_traps(attempts),
            )
            candidate = repaired["text"]
            review = await harness.review_scene(
                scene,
                candidate,
                suffix=f"candidate_promotion_repair_{attempt:02d}_review",
                prior_findings=harness.prior_review_traps(attempts),
            )
            attempts.append({
                "stage": f"candidate_promotion_repair_{attempt:02d}",
                "text": candidate,
                "review": review,
            })
        if (
            harness.fixed_size_requested
            and
            review["verdict"] != "pass"
            and getattr(args, "recover_with_source_beats", False)
            and scene.get("recovery_beats")
        ):
            recovered = await harness.recover_scene_by_source_beats(
                scene, review
            )
            candidate = recovered["text"]
            review = await harness.review_scene(
                scene,
                candidate,
                suffix="source_beat_recovery_review",
                prior_findings=harness.prior_review_traps(attempts),
            )
            attempts.append({
                "stage": "source_beat_recovery",
                "text": candidate,
                "review": review,
            })
            for beat_attempt in range(
                1, getattr(args, "max_source_beat_repairs", 1) + 1
            ):
                if review["verdict"] == "pass":
                    break
                repaired = await harness.repair_scene(
                    scene,
                    candidate,
                    review,
                    90 + beat_attempt,
                    prior_findings=harness.prior_review_traps(attempts),
                )
                candidate = repaired["text"]
                review = await harness.review_scene(
                    scene,
                    candidate,
                    suffix=(
                        f"source_beat_recovery_repair_{beat_attempt:02d}_review"
                    ),
                    prior_findings=harness.prior_review_traps(attempts),
                )
                attempts.append({
                    "stage": (
                        f"source_beat_recovery_repair_{beat_attempt:02d}"
                    ),
                    "text": candidate,
                    "review": review,
                })
        if review["verdict"] != "pass":
            if harness.fixed_size_requested:
                minimum_chars, maximum_chars = harness.scene_length_bounds(scene["target_chars"])
                best = best_scene_attempt(attempts, minimum_chars, maximum_chars)
            else:
                best = next((item for item in reversed(attempts)
                             if item["review"].get("verdict") == "pass"), attempts[-1])
            candidate = best["text"]
            review = best["review"]
        results.append({
            "scene": scene,
            "text": candidate,
            "review": review,
            "attempts": attempts,
            "resolved": review["verdict"] == "pass",
        })
        promotion_sources[scene["id"]] = {
            "candidate_path": str(candidate_path),
            "selected_stage": selected_stage,
            "repair_attempts": len(attempts) - 1,
        }

    chapter = "\n\n".join(item["text"].strip() for item in results) + "\n"
    policy = policy_for(harness.args.level)
    character_evidence = validate_characters(
        chapter,
        harness.readability_charset,
        harness.glossary_chars | harness.focus_vocabulary_chars,
        policy.max_above_level_ratio,
    )
    word_evidence = validate_beginner_chinese(
        chapter,
        harness.args.level,
        allowed_words=harness.focus_vocabulary_words,
        max_above_level_word_ratio=policy.max_above_level_ratio,
    )
    if not character_evidence["passes"] or not word_evidence["passes"]:
        local = [
            validate_beginner_chinese(
                result["text"], harness.args.level,
                allowed_words=harness.focus_vocabulary_words,
                max_above_level_word_ratio=1.0,
            )
            for result in results
        ]
        index = max(
            range(len(results)),
            key=lambda item: local[item]["above_level_word_ratio"],
        )
        review = copy.deepcopy(results[index]["review"])
        review["verdict"] = "revise"
        review["harness_decision"] = (
            "rejected_by_mechanical_chapter_readability_gate"
        )
        review.setdefault("language_problems", []).append(
            "whole-chapter readability budget failed after candidate promotion: "
            f"characters={character_evidence['above_level_percent']}%, "
            f"words={word_evidence['above_level_word_percent']}%, maximum="
            f"{policy.max_above_level_ratio * 100:.0f}%"
        )
        results[index]["review"] = review
        results[index]["resolved"] = False
        results[index]["attempts"][-1]["review"] = review

    distributed_length_acceptances = (
        accept_distributed_scene_lengths(
            results, chapter, harness.args.level, harness.target_chars
        ) if harness.fixed_size_requested else []
    )
    verdicts = {
        item["scene"]["id"]: item["review"]["verdict"] for item in results
    }
    final_status = run_status_for_verdicts(verdicts)
    publish_chapter_candidate(harness.run_dir, chapter, final_status)
    for result in results:
        scene_path = (
            harness.run_dir / "scenes" / f"{result['scene']['id']}.json"
        )
        prior = (
            json.loads(scene_path.read_text(encoding="utf-8"))
            if scene_path.is_file() else {"attempts": []}
        )
        result["attempts"] = list(prior.get("attempts", [])) + result["attempts"]
        scene_path.parent.mkdir(parents=True, exist_ok=True)
        scene_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    report = {
        "status": final_status,
        "scenes": len(results),
        "chapter_cjk": cjk_count(chapter),
        "scene_verdicts": verdicts,
        "scene_attempts": {
            item["scene"]["id"]: len(item["attempts"]) for item in results
        },
        "unresolved_scenes": [
            scene_id for scene_id, verdict in verdicts.items()
            if verdict != "pass"
        ],
        "distributed_scene_length_acceptances": (
            distributed_length_acceptances
        ),
        "chapter_readability": {
            "characters": character_evidence,
            "words_and_sentences": word_evidence,
        },
        "candidate_promotion": {
            "review_model": args.model,
            "review_effort": args.review_effort,
            "scenes": promotion_sources,
        },
    }
    (harness.run_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest.update({
        "status": final_status,
        "updated_at": utc_now(),
        "candidate_promotion": report["candidate_promotion"],
    })
    (harness.run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def fixed_size_target_from_manifest(manifest: dict[str, Any]) -> int | None:
    """Return only a target with explicit fixed-size provenance."""
    target = manifest.get("target_chars")
    if (
        manifest.get("fixed_size_requested") is True
        and isinstance(target, int) and not isinstance(target, bool) and target > 0
    ):
        return target
    return None


async def promote_reviewed_candidate(args: argparse.Namespace) -> dict[str, Any]:
    """Independently re-review and promote a selected one-scene candidate.

    Bounded low-cost repair runs can produce their best prose before their last
    attempt.  This command lets an operator select that draft (including a
    mechanical punctuation-only correction) without pretending the original
    run passed.  Promotion remains fail-closed: the same independent semantic,
    level, explicit-size (when requested), and prose-shape gates run again
    against the source.
    """
    run_dir = Path(args.run_dir).resolve()
    manifest_path = run_dir / "manifest.json"
    outline_path = run_dir / "outline.json"
    focus_path = run_dir / "focus-vocabulary-plan.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    outline = json.loads(outline_path.read_text(encoding="utf-8"))
    focus = json.loads(focus_path.read_text(encoding="utf-8"))
    scenes = outline.get("scenes", [])
    args.source = str(manifest["source"])
    args.level = str(manifest["level"])
    args.run_id = run_dir.name
    args.runs_dir = str(run_dir.parent)
    args.target_chars = fixed_size_target_from_manifest(manifest)
    args.editorial_plan = manifest.get("editorial_plan")
    harness = ChapterHarness(args)
    harness.focus_vocabulary = focus
    if len(scenes) != 1:
        return await promote_reviewed_book_candidates(
            args, harness, manifest, outline
        )
    if len(args.candidate) != 1 or "=" in args.candidate[0]:
        raise ValueError("one-scene promotion requires one plain PATH[@STAGE]")
    candidate_path, candidate, selected_stage = load_promotion_candidate(
        args.candidate[0]
    )
    review = await harness.review_scene(
        scenes[0], candidate, suffix="candidate_promotion_review"
    )
    promotion_attempts = [{
        "stage": "candidate_promotion",
        "candidate_path": str(candidate_path),
        "selected_stage": selected_stage,
        "text": candidate,
        "review": review,
    }]
    for attempt in range(1, args.max_repairs + 1):
        if review["verdict"] == "pass":
            break
        repaired = await harness.repair_scene(
            scenes[0],
            candidate,
            review,
            attempt,
            prior_findings=harness.prior_review_traps(promotion_attempts),
        )
        candidate = repaired["text"]
        review = await harness.review_scene(
            scenes[0],
            candidate,
            suffix=f"candidate_promotion_repair_{attempt:02d}_review",
            prior_findings=harness.prior_review_traps(promotion_attempts),
        )
        promotion_attempts.append({
            "stage": f"candidate_promotion_repair_{attempt:02d}",
            "text": candidate,
            "review": review,
        })
    final_status = "complete" if review["verdict"] == "pass" else "blocked"
    chapter = candidate + "\n"
    publish_chapter_candidate(run_dir, chapter, final_status)

    scene_path = run_dir / "scenes" / f"{scenes[0]['id']}.json"
    prior = (
        json.loads(scene_path.read_text(encoding="utf-8"))
        if scene_path.is_file() else {"scene": scenes[0], "attempts": []}
    )
    attempts = list(prior.get("attempts", []))
    attempts.extend(promotion_attempts)
    scene_result = {
        "scene": scenes[0], "text": candidate, "review": review,
        "attempts": attempts, "resolved": final_status == "complete",
    }
    scene_path.parent.mkdir(parents=True, exist_ok=True)
    scene_path.write_text(
        json.dumps(scene_result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report = {
        "status": final_status, "scenes": 1,
        "chapter_cjk": cjk_count(chapter),
        "scene_verdicts": {scenes[0]["id"]: review["verdict"]},
        "scene_attempts": {scenes[0]["id"]: len(attempts)},
        "unresolved_scenes": (
            [] if final_status == "complete" else [scenes[0]["id"]]
        ),
        "candidate_promotion": {
            "candidate_path": str(candidate_path),
            "review_model": args.model,
            "review_effort": args.review_effort,
            "repair_effort": args.repair_effort,
            "repair_attempts": len(promotion_attempts) - 1,
        },
    }
    (run_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest.update({
        "status": final_status,
        "updated_at": utc_now(),
        "candidate_promotion": report["candidate_promotion"],
    })
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


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
                annotated = await map_chunks(
                    chunks, self.annotate_chunk,
                    getattr(self.args, "concurrency", 1),
                )
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
            chunk_review_receipts = persist_chunk_attempts(
                self.run_dir, chunks, annotated, surface_key="text")
            all_reviewed = all(annotation_review_is_complete(item, self.run_dir) for item in annotated)
            per_chunk_review_proof = not (
                self.args.annotation_mode == "constrained-delta"
                and getattr(self.args, "annotation_review_policy", "chapter") == "chapter")
            audit = {
                "mode": self.args.annotation_mode,
                "chunks": len(annotated),
                **({"chunk_review_receipts_version": 1,
                    "chunk_review_receipts": chunk_review_receipts}
                   if per_chunk_review_proof else {}),
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
                **({"chunk_review_receipts_version": 1}
                   if per_chunk_review_proof else {}),
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
        chapter_args.target_chars = (
            target_chars if target_chars is not None else self.args.target_chars
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
    run.add_argument("--model", default="gpt-6-luna")
    run.add_argument("--adapt-effort", default="low")
    run.add_argument("--review-effort", default="low")
    run.add_argument("--repair-effort", default="low")
    run.add_argument("--final-effort", default="low")
    run.add_argument("--annotation-effort", default="low")
    run.add_argument(
        "--annotation-mode", choices=("generative", "constrained", "constrained-delta"),
        default="generative",
        help="opt in to deterministic proposals and strictly lossless correction patches",
    )
    run.add_argument("--annotation-review-effort", default="low")
    run.add_argument(
        "--annotation-review-policy",
        choices=("chapter", "exhaustive-chunks"), default="chapter",
        help="whole-chapter production gate or expensive chunk-by-chunk smoke audit",
    )
    run.add_argument("--annotation-repair-effort", default="low")
    run.add_argument("--annotation-final-effort", default="low")
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
    run.add_argument("--editorial-plan")
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
    annotate.add_argument("--model", default="gpt-6-luna")
    annotate.add_argument("--annotation-effort", default="low")
    annotate.add_argument(
        "--annotation-mode", choices=("generative", "constrained", "constrained-delta"),
        default="constrained-delta",
    )
    annotate.add_argument("--annotation-review-effort", default="low")
    annotate.add_argument(
        "--annotation-review-policy",
        choices=("chapter", "exhaustive-chunks"), default="chapter",
        help="whole-chapter production gate or expensive chunk-by-chunk smoke audit",
    )
    annotate.add_argument("--annotation-repair-effort", default="low")
    annotate.add_argument("--annotation-final-effort", default="low")
    annotate.add_argument("--annotation-metadata-batch-targets", type=int, default=150)
    annotate.add_argument("--max-annotation-repairs", type=int, default=2)
    annotate.add_argument(
        "--no-grammar-overlays", action="store_true",
        help="publish reviewed lexical tap meanings without optional grammar overlays",
    )
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
    promote = sub.add_parser(
        "promote",
        help="independently re-review and promote a selected one-scene candidate",
    )
    promote.add_argument("--run-dir", required=True)
    promote.add_argument(
        "--candidate", required=True, action="append",
        help=(
            "candidate PATH[@STAGE]; repeat as SCENE_ID=PATH[@STAGE] for "
            "multi-scene chapters"
        ),
    )
    promote.add_argument("--model", default="gpt-6-luna")
    promote.add_argument("--review-effort", default="low")
    promote.add_argument("--repair-effort", default="low")
    promote.add_argument("--max-repairs", type=int, default=3)
    promote.add_argument(
        "--recover-with-source-beats", action="store_true",
        help=(
            "regenerate a still-failing dense scene from its optional "
            "editorial source beats before selecting the best attempt"
        ),
    )
    promote.add_argument(
        "--max-source-beat-repairs", type=int, default=1,
        help="bounded whole-scene surgical repairs after beat recomposition",
    )
    promote.add_argument(
        "--reuse-passed-scenes", action="store_true",
        help="reuse unchanged scene prose with an existing reviewed pass",
    )
    promote.add_argument("--concurrency", type=int, default=1)
    promote.add_argument("--timeout", type=int, default=900)
    promote.add_argument("--refresh", action="store_true")
    book = sub.add_parser("book", help="run multiple chapters and levels")
    book.add_argument("--source", action="append",
                      help="chapter source path; repeat for more chapters")
    book.add_argument(
        "--source-dir", help="directory containing chapter_*.txt sources"
    )
    book.add_argument("--levels", nargs="+", default=["hsk1", "hsk2", "hsk3", "hsk4", "hsk5", "hsk6"])
    book.add_argument("--book-run-id", required=True)
    book.add_argument("--runs-dir", default=str(DEFAULT_RUNS))
    book.add_argument("--model", default="gpt-6-luna")
    book.add_argument("--adapt-effort", default="low")
    book.add_argument("--review-effort", default="low")
    book.add_argument("--repair-effort", default="low")
    book.add_argument("--final-effort", default="low")
    book.add_argument("--annotation-effort", default="low")
    book.add_argument(
        "--annotation-mode", choices=("generative", "constrained", "constrained-delta"),
        default="generative",
        help="opt in to deterministic proposals and strictly lossless correction patches",
    )
    book.add_argument("--annotation-review-effort", default="low")
    book.add_argument(
        "--annotation-review-policy",
        choices=("chapter", "exhaustive-chunks"), default="chapter",
        help="whole-chapter production gate or expensive chunk-by-chunk smoke audit",
    )
    book.add_argument("--annotation-repair-effort", default="low")
    book.add_argument("--annotation-final-effort", default="low")
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
    book.add_argument("--editorial-plan")
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
    if args.command == "promote":
        result = asyncio.run(promote_reviewed_candidate(args))
        return 0 if result["status"] == "complete" else 2
    asyncio.run(ChapterHarness(args).run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
