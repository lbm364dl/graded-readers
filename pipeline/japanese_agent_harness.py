#!/usr/bin/env python3
"""Unattended, source-grounded agent harness for Japanese graded readers."""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
from pathlib import Path
import re
from typing import Any

from pipeline.agent_harness import (
    BookHarness, ChapterHarness, DEFAULT_RUNS, SCHEMAS,
    apply_compact_review_policy, discard_incorrect_length_findings,
    digest, gather_all_or_raise, length_violations, run_status_for_verdicts, split_annotation_chunks,
    status, utc_now,
)


# Per source chapter. The strict increase is both prompted and audited.
DEFAULT_JLPT_TARGETS = {
    "n5": 1650, "n4": 2750, "n3": 4350, "n2": 6550, "n1": 9550,
}
JLPT_LEVELS = ["n5", "n4", "n3", "n2", "n1"]
CHAPTER_TARGETS = {
    "n5": [1200, 1900, 1750, 1500, 1500, 1500, 1550, 1650, 1600, 1750, 2100],
    "n4": [2000, 3200, 2950, 2500, 2500, 2550, 2600, 2750, 2650, 2950, 3350],
    "n3": [3200, 5100, 4750, 4000, 3950, 4050, 4200, 4350, 4250, 4750, 5400],
    "n2": [4800, 7700, 7100, 5950, 5950, 6100, 6300, 6550, 6400, 7100, 8050],
    "n1": [6950, 11200, 10350, 8700, 8650, 8900, 9150, 9550, 9350, 10350, 11850],
}


_EQUIVALENT_GROUPING_MARKERS = (
    "equally valid", "also valid", "acceptable analysis", "acceptable segmentation",
    "defensible analysis", "defensible segmentation", "depending on the analysis",
    "analysis choice", "category label", "part-of-speech", "pos label",
)


def apply_reader_useful_annotation_review_policy(
    review: dict[str, Any],
) -> dict[str, Any]:
    """Keep learner-harming findings blocking, not linguistic-analysis trivia."""
    value = copy.deepcopy(review)
    blocking: list[dict[str, Any]] = []
    for issue in value.get("issues", []):
        problem = str(issue.get("problem", ""))
        # Reconstruction and the deterministic annotation contract are checked
        # before model review. A POS-only dispute does not hurt a tap reader.
        if problem == "wrong_type":
            continue
        if problem in {"over_grouped", "under_grouped"}:
            detail = " ".join(
                str(issue.get(field, ""))
                for field in ("explanation", "suggested_fix")
            ).casefold()
            if any(marker in detail for marker in _EQUIVALENT_GROUPING_MARKERS):
                continue
        blocking.append(issue)
    value["issues"] = blocking
    value["verdict"] = "revise" if blocking else "pass"
    return value


def target_for_source(source: str | Path, level: str) -> int:
    match = re.fullmatch(r"chapter_(\d{2})", Path(source).stem)
    if match and 1 <= int(match.group(1)) <= 11:
        return CHAPTER_TARGETS[level][int(match.group(1)) - 1]
    return DEFAULT_JLPT_TARGETS[level]


def japanese_char_count(text: str) -> int:
    """Count Japanese letters, excluding punctuation, Latin text and spacing."""
    return sum(
        "\u3040" <= char <= "\u30ff"
        or "\u3400" <= char <= "\u4dbf"
        or "\u4e00" <= char <= "\u9fff"
        or "\uf900" <= char <= "\ufaff"
        or char == "々"
        for char in text
    )


_COMPOSITIONAL_SEGMENT_PATTERNS = (
    re.compile(r"(?:ず|ない|なかった|ていない|でいない)(?:(?:に|は|も)){0,2}(?:いられ|居られ)"),
    re.compile(r"(?:って|いて|んで|して)(?:いない|いなかった|いる|いた)$"),
)


def japanese_segment_issue(surface: str, kind: str) -> str | None:
    """Return a learner-facing segmentation failure, if mechanically certain.

    This is deliberately narrow: it catches inflected grammar chains that can
    never be one dictionary word, but leaves genuine lexicalized idioms and
    names to semantic review.
    """
    if kind == "name" and surface == "吾輩":
        return "吾輩 is a pronoun, not a name"
    if kind != "punctuation":
        for pattern in _COMPOSITIONAL_SEGMENT_PATTERNS:
            if pattern.search(surface):
                return "compositional verb/auxiliary grammar must be split into lexical units"
    return None


def material_review_findings(reviews: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Extract material findings while allowing vocabulary-only diagnostics."""
    findings: list[dict[str, str]] = []
    for review in reviews:
        for category in ("unsupported_additions", "distortions", "language_problems"):
            for finding in review.get(category, []):
                normalized = str(finding).lower()
                vocabulary_only = category == "language_problems" and (
                    "above-level" in normalized or "above level" in normalized
                ) and not any(x in normalized for x in ("unnatural", "incorrect", "ungrammatical"))
                if not vocabulary_only:
                    findings.append({"category": category, "finding": str(finding),
                                     "classification": "material"})
    return findings


_SOURCE_HEADER = re.compile(r"\A第\d+章[　 ]+[一二三四五六七八九十百]+\s*")


def strip_duplicate_source_header(text: str) -> str:
    """Remove an extracted chapter label if an adaptation echoed it as prose."""
    return _SOURCE_HEADER.sub("", text, count=1)


def _one_edit_apart(left: str, right: str) -> bool:
    """Return true only when the strings differ by exactly one edit."""
    if abs(len(left) - len(right)) > 1 or left == right:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) == 1
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    short_at = long_at = differences = 0
    while short_at < len(shorter) and long_at < len(longer):
        if shorter[short_at] == longer[long_at]:
            short_at += 1
        else:
            differences += 1
            if differences > 1:
                return False
        long_at += 1
    return True


def resolve_source_boundary(source: str, quote: str, search_from: int = 0) -> int:
    """Resolve an exact boundary, or one unique single-edit source match.

    The fallback deliberately rejects short quotes and every ambiguous or
    multi-edit mismatch. Its return value is always an offset into ``source``;
    callers continue slicing the original source rather than corrected model
    text.
    """
    exact = source.find(quote, search_from)
    if exact >= 0:
        return exact
    if len(quote) < 12:
        return -1

    candidates: set[int] = set()
    for width in (len(quote) - 1, len(quote), len(quote) + 1):
        if width <= 0:
            continue
        stop = len(source) - width + 1
        for start in range(search_from, max(search_from, stop)):
            if _one_edit_apart(quote, source[start:start + width]):
                candidates.add(start)
                if len(candidates) > 1:
                    return -1
    return next(iter(candidates)) if len(candidates) == 1 else -1


class JapaneseChapterHarness(ChapterHarness):
    """Japanese prompt specialization; orchestration and healing stay shared."""

    @property
    def target_chars(self) -> int:
        return self.args.target_chars or target_for_source(self.source_path, self.args.level)

    @property
    def scene_count(self) -> int:
        output_units = (self.target_chars + 1799) // 1800
        # Long originals need enough independent event ledgers even at N5,
        # where output length alone would otherwise collapse 45k source
        # characters into one or two scenes.
        source_units = (len(self.source) + 7999) // 8000
        return max(1, min(10, max(output_units, source_units)))

    def write_manifest(self, status_value: str) -> None:
        super().write_manifest(status_value)
        path = self.run_dir / "manifest.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["language"] = "japanese"
        value["length_unit"] = "Japanese letters (kanji, hiragana, katakana)"
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    async def outline(self) -> dict[str, Any]:
        prompt = f"""Return only JSON matching the supplied schema.
Read the complete verbatim ORIGINAL Japanese chapter and divide it into exactly
{self.scene_count} consecutive adaptation scene(s). Each source_start_quote
must be an exact, unique substring of ORIGINAL. Cover the source in order and
list every event, causal link, motivation, and character fact essential to a
coherent compressed retelling. The target_chars values must sum to about
{self.target_chars} Japanese characters.

ORIGINAL:\n{self.source}"""
        outline = await self.runner.call(
            "outline", prompt, SCHEMAS / "japanese-scene-outline.schema.json",
            self.args.review_effort, refresh=self.args.refresh,
        )
        scenes = outline["scenes"]
        if len(scenes) != self.scene_count:
            raise ValueError(
                f"outline returned {len(scenes)} scenes; expected {self.scene_count}"
            )
        base, remainder = divmod(self.target_chars, len(scenes))
        starts, search_from = [], 0
        for index, scene in enumerate(scenes, 1):
            scene["id"] = f"scene_{index:02d}"
            # Length is an editorial constraint, not an LLM judgment. Ignore
            # mistaken allocations while preserving the model's source plan.
            scene["target_chars"] = base + (1 if index <= remainder else 0)
            quote = scene["source_start_quote"]
            position = resolve_source_boundary(self.source, quote, search_from)
            if position < 0:
                raise ValueError(f"scene boundary not found after prior boundary: {quote!r}")
            starts.append(position)
            search_from = position + max(1, len(quote))
        if starts:
            starts[0] = 0
        for index, scene in enumerate(scenes):
            scene["source_start"] = starts[index]
            scene["source_end"] = starts[index + 1] if index + 1 < len(starts) else len(self.source)
        (self.run_dir / "outline.json").write_text(
            json.dumps(outline, ensure_ascii=False, indent=2) + "\n"
        )
        return outline

    async def adapt_scene(self, scene: dict[str, Any]) -> dict[str, Any]:
        original = self.source[scene["source_start"]:scene["source_end"]]
        events = "\n".join(f"- {event}" for event in scene["required_events"])
        prompt = f"""Return only JSON matching the supplied schema, putting the
adapted scene in `text`. Rewrite VERBATIM ORIGINAL as natural, engaging modern
Japanese for an annotated {self.args.level.upper()} literary reader. Keep core
grammar and ordinary vocabulary comfortable at {self.args.level.upper()}.
Natural literary, cultural, and story-specific words above that level are
allowed because every word will be annotated. Preserve essential events,
causes, motivations, names, numbers, tone, and order. Intentional compression
is expected. Do not invent facts or translate into another language. Use normal
Japanese orthography. Target about {scene['target_chars']} Japanese characters.

REQUIRED EVENTS:\n{events}\n\nVERBATIM ORIGINAL:\n{original}"""
        result = await self.runner.call(
            f"{scene['id']}/adapt", prompt, SCHEMAS / "adaptation.schema.json",
            self.args.adapt_effort, refresh=self.args.refresh,
        )
        result["text"] = strip_duplicate_source_header(result["text"])
        return result

    async def review_scene(self, scene: dict[str, Any], adaptation: str, suffix: str = "review") -> dict[str, Any]:
        original = self.source[scene["source_start"]:scene["source_end"]]
        prompt = f"""Return only JSON matching the supplied schema. Independently
compare ADAPTATION with VERBATIM ORIGINAL. This is a compact {self.args.level.upper()}
Japanese graded reader, so condensation and paraphrase are intended. Target
{scene['target_chars']} Japanese characters, roughly 70%-130%. Identify only
material omissions, unsupported additions, factual or causal distortions,
unnatural Japanese, and language clearly unsuitable for the requested level.
Do not reject useful story vocabulary merely for being above-level: annotations
will explain it. Set verdict=pass only when source_fidelity, naturalness, and
readability are each at least 8 and no material distortion remains.

VERBATIM ORIGINAL:\n{original}\n\nADAPTATION:\n{adaptation}"""
        result = await self.runner.call(
            f"{scene['id']}/{suffix}", prompt, SCHEMAS / "source-review.schema.json",
            self.args.review_effort, refresh=self.args.refresh,
        )
        length = japanese_char_count(adaptation)
        low = int(scene["target_chars"] * 0.7)
        high = int(scene["target_chars"] * 1.3)
        in_range = low <= length <= high
        if in_range:
            result = discard_incorrect_length_findings(result)
        result = apply_compact_review_policy(result)
        problems = []
        if not in_range:
            problems.append(
                f"mechanical length gate: {length} Japanese letters, required {low}-{high}"
            )
        leaked = [marker for marker in ("［＃", "《", "》", "｜") if marker in adaptation]
        if leaked:
            problems.append("leaked Aozora source markers: " + " ".join(leaked))
        if problems:
            result = dict(result)
            result["verdict"] = "revise"
            result.setdefault("language_problems", []).extend(problems)
            result["harness_decision"] = "rejected_by_mechanical_gate"
        elif material_review_findings([result]):
            # A reviewer occasionally emits verdict=pass while simultaneously
            # listing a concrete distortion/addition/language fault. Never let
            # that contradiction bypass the bounded scene repair loop.
            result = dict(result)
            result["verdict"] = "revise"
            result["harness_decision"] = "rejected_material_findings"
        return result

    async def repair_scene(self, scene: dict[str, Any], adaptation: str, review: dict[str, Any], attempt: int) -> dict[str, Any]:
        original = self.source[scene["source_start"]:scene["source_end"]]
        prompt = f"""Return only JSON matching the supplied schema, with revised
Japanese in `text`. Repair ADAPTATION according to the independent REVIEW and
VERBATIM ORIGINAL. Change only what findings require. Preserve good prose,
event order, compactness, and {self.args.level.upper()}-readable core language.
Correct distortions and unnatural Japanese without inventing facts. Stay within
roughly 70%-130% of {scene['target_chars']} Japanese characters.

VERBATIM ORIGINAL:\n{original}\n\nADAPTATION:\n{adaptation}\n\nREVIEW:\n{json.dumps(review, ensure_ascii=False, indent=2)}"""
        result = await self.runner.call(
            f"{scene['id']}/repair_{attempt:02d}", prompt,
            SCHEMAS / "adaptation.schema.json", self.args.repair_effort,
            refresh=self.args.refresh,
        )
        result["text"] = strip_duplicate_source_header(result["text"])
        return result

    async def review_chapter(
        self, outline: dict[str, Any], chapter: str, *, stage: str = "review"
    ) -> dict[str, Any]:
        """Audit assembled continuity and the full required-event ledger."""
        ledger = [
            {
                "scene": scene["id"], "source_start": scene["source_start"],
                "source_end": scene["source_end"],
                "required_events": scene["required_events"],
            }
            for scene in outline["scenes"]
        ]
        prompt = f"""Return only JSON matching the supplied schema. Perform a
fresh whole-chapter audit of ADAPTATION against the complete VERBATIM ORIGINAL
and REQUIRED EVENT LEDGER. Scene-level reviews have already run; focus on
cross-scene continuity, event order, identities, motivations, causal links,
contradictions, duplicate transitions, and any ledger event lost during
assembly. Natural compression is expected, but every ledger event must remain
recognizable. Set pass only when source_fidelity, naturalness, and readability
are at least 8 and there are no material omissions or distortions.

REQUIRED EVENT LEDGER:
{json.dumps(ledger, ensure_ascii=False, indent=2)}

VERBATIM ORIGINAL:
{self.source}

ADAPTATION:
{chapter}"""
        result = await self.runner.call(
            f"chapter/{stage}", prompt, SCHEMAS / "source-review.schema.json",
            self.args.review_effort, refresh=self.args.refresh,
        )
        result = apply_compact_review_policy(result)
        count, low, high = (
            japanese_char_count(chapter), int(self.target_chars * 0.7),
            int(self.target_chars * 1.3),
        )
        if not low <= count <= high:
            result = dict(result)
            result["verdict"] = "revise"
            result.setdefault("language_problems", []).append(
                f"mechanical chapter length gate: {count}, required {low}-{high}"
            )
        elif material_review_findings([result]):
            result = dict(result)
            result["verdict"] = "revise"
            result["harness_decision"] = "rejected_material_findings"
        return result

    async def repair_chapter(
        self, outline: dict[str, Any], chapter: str,
        review: dict[str, Any], attempt: int,
    ) -> str:
        """Apply only the concrete findings from a whole-chapter review.

        The complete source and current, already scene-reviewed adaptation are
        supplied on every attempt. Stable attempt-numbered job names make the
        loop resumable through the runner cache.
        """
        ledger = [
            {"scene": scene["id"], "required_events": scene["required_events"]}
            for scene in outline["scenes"]
        ]
        prompt = f"""Return only JSON matching the supplied schema, with the
complete corrected Japanese chapter in `text`. This is an ISSUE-SCOPED repair
of an adaptation whose individual scenes already passed independent review.
Fix every concrete finding in REVIEW, but preserve all other wording, paragraph
order, transitions, and scene-reviewed text verbatim. Do not broadly rewrite,
summarize, embellish, or add facts. Use VERBATIM ORIGINAL only to restore or
correct what REVIEW identifies. Keep the {self.args.level.upper()} target and
stay within 70%-130% of {self.target_chars} Japanese characters.

REQUIRED EVENT LEDGER:
{json.dumps(ledger, ensure_ascii=False, indent=2)}

VERBATIM ORIGINAL:
{self.source}

CURRENT SCENE-REVIEWED ADAPTATION:
{chapter}

REVIEW FINDINGS:
{json.dumps(review, ensure_ascii=False, indent=2)}"""
        result = await self.runner.call(
            f"chapter/repair_{attempt:02d}", prompt,
            SCHEMAS / "adaptation.schema.json", self.args.repair_effort,
            refresh=self.args.refresh,
        )
        return strip_duplicate_source_header(result["text"]).rstrip() + "\n"

    async def heal_chapter_review(
        self, outline: dict[str, Any], chapter: str,
        initial_review: dict[str, Any],
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        """Boundedly repair and independently re-audit a rejected chapter."""
        candidate, review = chapter, initial_review
        attempts: list[dict[str, Any]] = []
        for attempt in range(1, self.args.max_repairs + 1):
            before_count = japanese_char_count(candidate)
            candidate = await self.repair_chapter(
                outline, candidate, review, attempt
            )
            review = await self.review_chapter(
                outline, candidate, stage=f"repair_{attempt:02d}_review"
            )
            attempts.append({
                "attempt": attempt,
                "repair_job": f"chapter/repair_{attempt:02d}",
                "review_job": f"chapter/repair_{attempt:02d}_review",
                "before_japanese_chars": before_count,
                "after_japanese_chars": japanese_char_count(candidate),
                "review": review,
            })
            if review["verdict"] == "pass":
                break
        return candidate, review, attempts

    async def rewrite_scene(self, scene: dict[str, Any], attempts: list[dict[str, Any]]) -> dict[str, Any]:
        original = self.source[scene["source_start"]:scene["source_end"]]
        history = json.dumps(
            [{"text": item["text"], "review": item["review"]} for item in attempts],
            ensure_ascii=False, indent=2,
        )
        prompt = f"""Return only JSON matching the supplied schema, with a fresh
Japanese adaptation in `text`. Start again from VERBATIM ORIGINAL because prior
attempts failed review; use their history only as traps to avoid. Write natural
modern Japanese for an annotated {self.args.level.upper()} reader. Preserve all
essential facts and event order, do not invent facts, and target about
{scene['target_chars']} Japanese characters.

VERBATIM ORIGINAL:\n{original}\n\nFAILED HISTORY:\n{history}"""
        result = await self.runner.call(
            f"{scene['id']}/fresh_rewrite", prompt,
            SCHEMAS / "adaptation.schema.json", self.args.final_effort,
            refresh=self.args.refresh,
        )
        result["text"] = strip_duplicate_source_header(result["text"])
        return result

    async def repair_after_fresh_rewrite(
        self, scene: dict[str, Any], adaptation: str, review: dict[str, Any]
    ) -> dict[str, Any]:
        """One bounded, issue-only correction when a fresh rewrite is close."""
        original = self.source[scene["source_start"]:scene["source_end"]]
        prompt = f"""Return only JSON matching the supplied schema, with the
complete corrected Japanese scene in `text`. The fresh source-grounded rewrite
below has only the concrete issues listed by its independent REVIEW. Fix those
issues only. Preserve every other word, event, and paragraph verbatim; do not
rewrite or embellish. Stay within 70%-130% of {scene['target_chars']} Japanese
characters and retain {self.args.level.upper()} readability.

VERBATIM ORIGINAL:
{original}

FRESH REWRITE:
{adaptation}

REVIEW FINDINGS:
{json.dumps(review, ensure_ascii=False, indent=2)}"""
        result = await self.runner.call(
            f"{scene['id']}/post_fresh_repair", prompt,
            SCHEMAS / "adaptation.schema.json", self.args.repair_effort,
            refresh=self.args.refresh,
        )
        result["text"] = strip_duplicate_source_header(result["text"])
        return result

    async def process_scene(self, scene: dict[str, Any]) -> dict[str, Any]:
        result = await super().process_scene(scene)
        if result["review"]["verdict"] == "pass" or not self.args.fresh_rewrite:
            return result
        adapted = await self.repair_after_fresh_rewrite(
            scene, result["text"], result["review"]
        )
        review = await self.review_scene(
            scene, adapted["text"], suffix="post_fresh_repair_review"
        )
        result["text"], result["review"] = adapted["text"], review
        result["attempts"].append({
            "stage": "post_fresh_repair", "text": adapted["text"],
            "review": review,
        })
        result["resolved"] = review["verdict"] == "pass"
        scene_path = self.run_dir / "scenes" / f"{scene['id']}.json"
        scene_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        return result

    async def annotation_candidate(self, index: int, chunk: str, *, stage: str = "initial", prior=None, findings=None, effort=None) -> dict[str, Any]:
        repair = ""
        if prior is not None:
            repair = "\n\nPRIOR ANNOTATION:\n" + json.dumps(prior, ensure_ascii=False, indent=2)
            repair += "\n\nREVIEW FINDINGS:\n" + json.dumps(findings, ensure_ascii=False, indent=2)
        prompt = f"""Return only JSON matching the supplied schema. Annotate
Japanese TEXT without changing, omitting, or reordering any character. Segment
into individual natural dictionary words, auxiliaries, and grammatical
particles. Use type `auxiliary` for inflecting auxiliaries. Keep personal and
place names together. Use idiom only for a genuine
lexicalized idiom, never for an ordinary phrase or clause. Include spaces and
punctuation as punctuation segments. For every segment give `surface`, its
dictionary-form lemma, `kana` reading (not romaji), and concise contextual English meaning;
punctuation may have empty lemma, kana, and meaning. Concatenated segment surfaces must reproduce TEXT
exactly. Also return grammar_overlays for grammatical constructions that span
one or more lexical segments. Overlay start/end are zero-based Python character
offsets into TEXT (end exclusive); surface must equal TEXT[start:end]. Explain
the construction itself, not an ordinary sentence meaning.{repair}\n\nTEXT:\n{chunk}"""
        return await self.runner.call(
            f"annotations/chunk_{index:04d}/{stage}", prompt,
            SCHEMAS / "japanese-annotation.schema.json",
            effort or self.args.annotation_effort, refresh=self.args.refresh,
        )

    async def review_annotation(self, index: int, chunk: str, annotation: dict[str, Any], stage: str) -> dict[str, Any]:
        prompt = f"""Return only JSON matching the supplied schema. Independently
audit this Japanese learner annotation. Segment text must concatenate exactly
to TEXT. Split ordinary compositional language into dictionary words,
auxiliaries, and particles; do not group clauses. Keep names and genuine fixed
idioms together. Lemmas must be valid dictionary forms. Kana readings must be
correct kana readings in context, including inflection, and meanings must explain the individual segment rather than
paraphrasing a sentence. Grammar overlay offsets and surfaces must be exact and
must describe genuine grammar spanning their surface. List every concrete issue and pass only if none remain.

TEXT:\n{chunk}\n\nANNOTATION:\n{json.dumps(annotation, ensure_ascii=False, indent=2)}"""
        review = await self.runner.call(
            f"annotations/chunk_{index:04d}/{stage}_review", prompt,
            SCHEMAS / "japanese-annotation-review.schema.json",
            self.args.annotation_review_effort, refresh=self.args.refresh,
        )
        return apply_reader_useful_annotation_review_policy(review)

    @staticmethod
    def canonicalize_annotation_candidate(
        chunk: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        """Repair only unambiguous Japanese surface/offset bookkeeping."""
        value = copy.deepcopy(result)
        segments = value.get("segments")
        if isinstance(segments, list):
            reconstructed = "".join(str(item.get("surface", "")) for item in segments)
            remainder = chunk[len(reconstructed):] if chunk.startswith(reconstructed) else ""
            if remainder and remainder.isspace():
                segments.append({
                    "surface": remainder, "type": "punctuation", "lemma": "",
                    "kana": "", "meaning_en": "",
                })
            elif reconstructed and chunk.endswith(reconstructed):
                prefix = chunk[:len(chunk) - len(reconstructed)]
                if prefix and prefix.isspace():
                    segments.insert(0, {
                        "surface": prefix, "type": "punctuation", "lemma": "",
                        "kana": "", "meaning_en": "",
                    })
        overlays = value.get("grammar_overlays")
        if isinstance(overlays, list):
            for overlay in overlays:
                surface = overlay.get("surface")
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
        segments = result.get("segments")
        overlays = result.get("grammar_overlays")
        if not isinstance(segments, list) or not isinstance(overlays, list):
            return False
        if "".join(str(item.get("surface", "")) for item in segments) != chunk:
            return False
        kana = re.compile(r"^[\u3040-\u30ffー・\s]*$")
        for item in segments:
            surface = item.get("surface", "")
            kind = item.get("type")
            if kind == "punctuation":
                continue
            if not item.get("lemma") or not item.get("meaning_en"):
                return False
            if not item.get("kana") or not kana.fullmatch(str(item["kana"])):
                return False
            if kind in {"word", "auxiliary", "particle"} and len(surface) > 12:
                return False
            if japanese_segment_issue(surface, str(kind)):
                return False
        for overlay in overlays:
            start, end = overlay.get("start"), overlay.get("end")
            if not isinstance(start, int) or not isinstance(end, int):
                return False
            if not (0 <= start < end <= len(chunk)):
                return False
            if overlay.get("surface") != chunk[start:end]:
                return False
            if not overlay.get("grammar") or not overlay.get("meaning_en"):
                return False
        return True

    @staticmethod
    def annotation_contract_issues(
        chunk: str, result: dict[str, Any]
    ) -> list[dict[str, str]]:
        if JapaneseChapterHarness.annotation_reconstructs(chunk, result):
            return []
        return [{
            "segment_text": "", "problem": "reconstruction",
            "explanation": "Japanese segments or grammar overlays violate exact reconstruction or field validation.",
            "suggested_fix": "Re-segment exact TEXT and correct kana, meanings, and zero-based grammar spans.",
        }]

    async def run(self) -> dict[str, Any]:
        # Same fail-closed state machine as the Chinese harness, with a Japanese
        # length metric and Japanese annotation schema/prompts.
        self.write_manifest("running")
        try:
            outline = await self.outline()
            results = await gather_all_or_raise(*(
                self.process_scene(scene) for scene in outline["scenes"]
            ))
            chapter = strip_duplicate_source_header(
                "\n\n".join(item["text"].strip() for item in results)
            ) + "\n"
            verdicts = {item["scene"]["id"]: item["review"]["verdict"] for item in results}
            final_status = run_status_for_verdicts(verdicts)
            chapter_review = None
            chapter_review_initial = None
            chapter_repair_attempts: list[dict[str, Any]] = []
            if final_status == "complete":
                chapter_review = await self.review_chapter(outline, chapter)
                chapter_review_initial = copy.deepcopy(chapter_review)
                if chapter_review["verdict"] != "pass":
                    chapter, chapter_review, chapter_repair_attempts = (
                        await self.heal_chapter_review(
                            outline, chapter, chapter_review
                        )
                    )
                    if chapter_review["verdict"] != "pass":
                        final_status = "blocked"
            material_findings = material_review_findings(
                [item["review"] for item in results] + ([chapter_review] if chapter_review else [])
            )
            if material_findings:
                final_status = "blocked"
            name = "chapter.txt" if final_status == "complete" else "chapter-candidate.txt"
            (self.run_dir / name).write_text(chapter, encoding="utf-8")
            if final_status == "complete" and not self.args.skip_annotations:
                chunks = split_annotation_chunks(chapter, self.args.annotation_chunk)
                annotated = await gather_all_or_raise(*(
                    self.annotate_chunk(i, chunk) for i, chunk in enumerate(chunks)
                ))
                segments = [segment for item in annotated for segment in item["segments"]]
                if "".join(segment["surface"] for segment in segments) != chapter:
                    raise ValueError("assembled annotations do not reconstruct chapter")
                grammar_overlays = []
                offset = 0
                for chunk, item in zip(chunks, annotated):
                    accepted = item["attempts"][-1]["annotation"]
                    for overlay in accepted.get("grammar_overlays", []):
                        grammar_overlays.append({
                            **overlay, "start": overlay["start"] + offset,
                            "end": overlay["end"] + offset,
                        })
                    offset += len(chunk)
                reader = {
                    "title": outline["chapter_title"], "language": "Japanese",
                    "level": self.args.level.upper(), "text": chapter,
                    "segments": segments, "grammar_overlays": grammar_overlays,
                    "annotation_audit": {"chunks": len(annotated), "attempts_per_chunk": [len(x["attempts"]) for x in annotated], "all_reviewed": all(x["resolved"] for x in annotated)},
                }
                (self.run_dir / "reader.json").write_text(json.dumps(reader, ensure_ascii=False, indent=2) + "\n")
            count = japanese_char_count(chapter)
            report = {
                "status": final_status, "scenes": len(results),
                "japanese_chars": count, "chapter_cjk": count,
                "scene_verdicts": verdicts,
                "chapter_review": chapter_review,
                "chapter_review_initial": chapter_review_initial,
                "chapter_repair_attempts": chapter_repair_attempts,
                "chapter_review_verdict": chapter_review.get("verdict") if chapter_review else "not_run",
                "materiality_audit": {
                    "reviewed": chapter_review is not None,
                    "unresolved_material_findings": material_findings,
                    "policy": "literary/story vocabulary is diagnostic, not a hard failure",
                },
                "scene_attempts": {x["scene"]["id"]: len(x["attempts"]) for x in results},
                "unresolved_scenes": [key for key, value in verdicts.items() if value != "pass"],
            }
            (self.run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            self.write_manifest(final_status)
            return report
        except BaseException:
            self.write_manifest("failed")
            raise


class JapaneseBookHarness(BookHarness):
    def completed_run(
        self, source_path: Path, level: str, run_id: str, target_chars: int
    ) -> dict[str, Any] | None:
        """Return a verified completed run, otherwise require a real rerun."""
        run_dir = Path(self.args.runs_dir).resolve() / run_id
        manifest_path, report_path = run_dir / "manifest.json", run_dir / "report.json"
        chapter_path, reader_path = run_dir / "chapter.txt", run_dir / "reader.json"
        if not all(path.is_file() for path in (manifest_path, report_path, chapter_path)):
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if manifest.get("status") != "complete" or report.get("status") != "complete":
            return None
        if manifest.get("source_sha256") != digest(source_path.read_text(encoding="utf-8")):
            return None
        if manifest.get("source") != str(source_path):
            return None
        if manifest.get("level") != level or manifest.get("target_chars") != target_chars:
            return None
        if not self.args.skip_annotations and not reader_path.is_file():
            return None
        count = japanese_char_count(chapter_path.read_text(encoding="utf-8"))
        if report.get("japanese_chars") != count or report.get("chapter_cjk") != count:
            return None
        return {
            "source": str(source_path), "level": level, "run_id": run_id,
            "status": "complete", "chapter_cjk": count,
            "japanese_chars": count, "target_chars": target_chars,
            "attempts": 0, "cached": True,
        }

    async def run_one(self, source: str, level: str, target_chars: int | None = None) -> dict[str, Any]:
        source_path = Path(source).resolve()
        run_id = f"{self.args.book_run_id}/{source_path.stem}-{level}"
        chapter_args = copy.copy(self.args)
        chapter_args.command, chapter_args.source = "run", str(source_path)
        chapter_args.level, chapter_args.run_id = level, run_id
        chapter_args.target_chars = target_chars or self.args.target_chars or target_for_source(source_path, level)
        completed = self.completed_run(source_path, level, run_id, chapter_args.target_chars)
        if completed is not None:
            return completed
        last_error = ""
        async with self.chapter_semaphore:
            for attempt in range(self.args.chapter_retries + 1):
                try:
                    report = await JapaneseChapterHarness(chapter_args, semaphore=self.agent_semaphore).run()
                    if report["status"] != "complete":
                        last_error = "quality gates unresolved: " + ", ".join(report.get("unresolved_scenes", []))
                        if attempt < self.args.chapter_retries:
                            chapter_args.refresh = True
                            await asyncio.sleep(min(60, 5 * (3 ** attempt)))
                            continue
                    return {"source": str(source_path), "level": level, "run_id": run_id, "status": report["status"], "chapter_cjk": report["chapter_cjk"], "japanese_chars": report["japanese_chars"], "target_chars": chapter_args.target_chars, "attempts": attempt + 1}
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    if attempt < self.args.chapter_retries:
                        chapter_args.refresh = True
                        await asyncio.sleep(min(60, 5 * (3 ** attempt)))
            return {"source": str(source_path), "level": level, "run_id": run_id, "status": "failed", "attempts": self.args.chapter_retries + 1, "error": last_error}

    async def run(self) -> dict[str, Any]:
        planned = [
            {"source": str(Path(source).resolve()), "level": level, "status": "pending"}
            for source in self.args.source for level in self.args.levels
        ]
        self.write_report("running", planned)
        tasks = [asyncio.create_task(self.run_one(x["source"], x["level"])) for x in planned]
        results: list[dict[str, Any]] = []
        for task in asyncio.as_completed(tasks):
            results.append(await task)
            self.write_report("running", results)
        for _ in range(self.args.length_repair_rounds):
            violations = length_violations(results, self.args.levels)
            if not violations:
                break
            for problem in violations:
                current = next(x for x in results if (x["source"], x["level"]) == (problem["source"], problem["upper_level"]))
                target = max(
                    target_for_source(problem["source"], problem["upper_level"]),
                    problem["lower_cjk"] + max(100, problem["lower_cjk"] // 10),
                    int(current.get("target_chars", 0) * 1.25),
                )
                replacement = await self.run_one(problem["source"], problem["upper_level"], target)
                results = [replacement if (x["source"], x["level"]) == (problem["source"], problem["upper_level"]) else x for x in results]
                self.write_report("running", results)
        violations = length_violations(results, self.args.levels)
        final = "complete" if {x["status"] for x in results} == {"complete"} and not violations else "blocked"
        self.write_report(final, results, violations)
        return {"status": final, "runs": results}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for command in ("run", "book"):
        item = sub.add_parser(command)
        item.add_argument("--source", action="append" if command == "book" else "store", required=command == "run")
        if command == "run":
            item.add_argument("--level", choices=JLPT_LEVELS, default="n3")
            item.add_argument("--run-id")
            item.add_argument("--concurrency", type=int, default=3)
        else:
            item.add_argument("--source-dir", help="directory containing chapter_*.txt")
            item.add_argument("--levels", nargs="+", choices=JLPT_LEVELS, default=JLPT_LEVELS)
            item.add_argument("--book-run-id", required=True)
            item.add_argument("--concurrency", type=int, default=12)
            item.add_argument("--chapter-concurrency", type=int, default=6)
            item.add_argument("--chapter-retries", type=int, default=2)
            item.add_argument("--length-repair-rounds", type=int, default=2)
        item.add_argument("--runs-dir", default=str(DEFAULT_RUNS))
        item.add_argument("--model", default="gpt-5.6-luna")
        item.add_argument("--adapt-effort", default="xhigh")
        item.add_argument("--review-effort", default="medium")
        item.add_argument("--repair-effort", default="xhigh")
        item.add_argument("--final-effort", default="max")
        item.add_argument("--annotation-effort", default="low")
        item.add_argument("--annotation-review-effort", default="medium")
        item.add_argument("--annotation-repair-effort", default="medium")
        item.add_argument("--annotation-final-effort", default="xhigh")
        item.add_argument("--max-annotation-repairs", type=int, default=2)
        item.add_argument("--max-repairs", type=int, default=3)
        item.add_argument("--no-fresh-rewrite", dest="fresh_rewrite", action="store_false")
        item.set_defaults(fresh_rewrite=True)
        item.add_argument("--annotation-chunk", type=int, default=200)
        item.add_argument("--target-chars", type=int)
        item.add_argument("--timeout", type=int, default=900)
        item.add_argument("--skip-annotations", action="store_true")
        item.add_argument("--refresh", action="store_true")
    stat = sub.add_parser("status")
    stat.add_argument("run_dir")
    return p


def main() -> int:
    command_parser = parser()
    args = command_parser.parse_args()
    if args.command == "status":
        return status(Path(args.run_dir))
    if args.command == "book":
        if args.source_dir:
            discovered = sorted(Path(args.source_dir).glob("chapter_*.txt"))
            args.source = (args.source or []) + [str(path) for path in discovered]
        if not args.source:
            command_parser.error("book requires --source or --source-dir with chapter_*.txt")
        result = asyncio.run(JapaneseBookHarness(args).run())
        return 0 if result["status"] == "complete" else 2
    asyncio.run(JapaneseChapterHarness(args).run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
