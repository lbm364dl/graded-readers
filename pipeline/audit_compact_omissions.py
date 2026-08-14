#!/usr/bin/env python3
"""Audit and recover compact Chinese adaptations with explicit omissions.

The command never changes generation runs.  It writes a resumable audit tree
containing classifications and, only where a material omission is confirmed,
a separately repaired and independently reviewed chapter candidate.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any
import unicodedata

from pipeline.agent_harness import (
    CodexRunner, ROOT, SCHEMAS, cjk_count, digest, discard_incorrect_length_findings,
    simplified, utc_now,
)
from pipeline.adaptation_policy import policy_for


CHAPTER_BACKUP = "chapter.before-omission-repair.txt"
REPORT_BACKUP = "report.before-omission-repair.json"


@dataclass(frozen=True)
class RiskyScene:
    run_dir: Path
    scene_path: Path
    scene_id: str
    source: str
    adaptation: str
    review: dict[str, Any]
    omissions: tuple[str, ...]
    critical_findings: tuple[tuple[str, str], ...]


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve_manifest_source(manifest: dict[str, Any]) -> Path:
    """Resolve a source recorded by a run, including after checkout relocation."""
    recorded = Path(str(manifest["source"]))
    if recorded.is_file():
        return recorded
    relocated = ROOT / "books" / "chinese" / "sanguoyanyi" / recorded.name
    if not relocated.is_file():
        raise FileNotFoundError(recorded)
    expected = manifest.get("source_sha256")
    if expected and digest(relocated.read_text(encoding="utf-8")) != expected:
        raise ValueError(f"relocated source hash mismatch: {relocated}")
    return relocated


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    temporary.replace(path)


def promote_passed_candidate(run_dir: Path, candidate_path: Path) -> dict[str, Any]:
    """Atomically promote one passed candidate and invalidate annotations.

    The first pre-repair chapter and report are retained forever. Repeating a
    promotion is therefore safe and never turns a previously promoted chapter
    into the backup of record.
    """
    chapter_path = run_dir / "chapter.txt"
    report_path = run_dir / "report.json"
    original = chapter_path.read_bytes()
    candidate = candidate_path.read_bytes()
    if not candidate.strip():
        raise ValueError(f"refusing to promote empty candidate: {candidate_path}")
    # Validate mutable metadata before making any filesystem change.
    report = _object(report_path)

    chapter_backup = run_dir / CHAPTER_BACKUP
    report_backup = run_dir / REPORT_BACKUP
    if not chapter_backup.exists():
        _atomic_bytes(chapter_backup, original)
    if report_path.exists() and not report_backup.exists():
        _atomic_bytes(report_backup, report_path.read_bytes())

    invalidated: list[str] = []
    reader = run_dir / "reader.json"
    reader_backup = run_dir / "reader.before-omission-repair.json"
    if reader.exists():
        if not reader_backup.exists():
            reader.replace(reader_backup)
        else:
            reader.unlink()
        invalidated.append("reader.json")
    annotation_agents = run_dir / "agents" / "annotations"
    annotation_backup = run_dir / "agents" / "annotations.before-omission-repair"
    if annotation_agents.exists():
        if not annotation_backup.exists():
            annotation_agents.replace(annotation_backup)
        else:
            shutil.rmtree(annotation_agents)
        invalidated.append("agents/annotations")

    _atomic_bytes(chapter_path, candidate)
    report["chapter_cjk"] = cjk_count(candidate.decode("utf-8"))
    report["omission_audit_promotion"] = {
        "promoted_at": utc_now(),
        "candidate": str(candidate_path.resolve()),
        "candidate_sha256": _sha256(candidate),
        "original_backup": str(chapter_backup.resolve()),
        "original_sha256": _sha256(chapter_backup.read_bytes()),
        "annotations_invalidated": invalidated,
        "annotation_rerun_required": True,
    }
    _atomic_bytes(
        report_path,
        (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    return {
        "status": "promoted", "run_dir": str(run_dir),
        "candidate_sha256": _sha256(candidate),
        "backup": str(chapter_backup), "annotations_invalidated": invalidated,
        "annotation_rerun_required": True,
    }


def expand_run_dirs(inputs: list[Path]) -> list[Path]:
    """Accept chapter runs directly or batch roots containing chapter runs."""
    result: set[Path] = set()
    for supplied in inputs:
        supplied = supplied.resolve()
        if (supplied / "manifest.json").is_file() and (supplied / "scenes").is_dir():
            result.add(supplied)
            continue
        if not supplied.is_dir():
            raise ValueError(f"run directory does not exist: {supplied}")
        result.update(
            path.parent for path in supplied.glob("**/manifest.json")
            if (path.parent / "scenes").is_dir()
        )
    return sorted(result)


def authoritative_scene_paths(run_dir: Path) -> tuple[list[Path], dict[str, int]]:
    """Resolve only current-outline scene files; stale extras are inert evidence."""
    outline = _object(run_dir / "outline.json")
    report = _object(run_dir / "report.json")
    scenes = outline.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError(f"current outline has no scenes: {run_dir}")
    if report.get("scenes") != len(scenes):
        raise ValueError(f"report scene count does not match current outline: {run_dir}")
    ids: list[str] = []
    for item in scenes:
        scene_id = item.get("id") if isinstance(item, dict) else None
        if (not isinstance(scene_id, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", scene_id) is None):
            raise ValueError(f"unsafe authoritative scene id: {scene_id!r}")
        if scene_id in ids:
            raise ValueError(f"duplicate authoritative scene id: {scene_id}")
        ids.append(scene_id)
    scene_dir = (run_dir / "scenes").resolve()
    paths: list[Path] = []
    for scene_id in ids:
        path = (scene_dir / f"{scene_id}.json").resolve()
        if path.parent != scene_dir or not path.is_file():
            raise ValueError(f"authoritative scene file missing: {path}")
        paths.append(path)
    all_scene_files = set(scene_dir.glob("scene_*.json"))
    return paths, {
        "authoritative_scene_count": len(paths),
        "ignored_stale_scene_count": len(all_scene_files - set(paths)),
    }


def discover_risky_scenes(run_dirs: list[Path]) -> list[RiskyScene]:
    """Find accepted scene reviews which still contain explicit omissions."""
    found: list[RiskyScene] = []
    for run_dir in expand_run_dirs(run_dirs):
        manifest = _object(run_dir / "manifest.json")
        if not policy_for(str(manifest.get("level", ""))).audit_source_omissions:
            continue
        report_path = run_dir / "report.json"
        chapter_path = run_dir / "chapter.txt"
        if (
            manifest.get("status") != "complete"
            or not report_path.is_file()
            or _object(report_path).get("status") != "complete"
            or not chapter_path.is_file()
        ):
            continue
        report = _object(report_path)
        promotion = report.get("omission_audit_promotion")
        if isinstance(promotion, dict):
            if _sha256(chapter_path.read_bytes()) == promotion.get("candidate_sha256"):
                # Scene artifacts describe the pre-promotion chapter. A retry
                # must not rebuild and overwrite accepted repaired prose.
                continue
            raise ValueError(
                f"accepted chapter changed after omission promotion: {run_dir}"
            )
        source_text = resolve_manifest_source(manifest).read_text(encoding="utf-8")
        scene_paths, _scene_evidence = authoritative_scene_paths(run_dir)
        for scene_path in scene_paths:
            scene = _object(scene_path)
            review = scene.get("review", {})
            omissions = review.get("omissions", [])
            critical_items: list[tuple[str, str]] = []
            for field in ("unsupported_additions", "distortions", "language_problems"):
                values = review.get(field, [])
                if isinstance(values, list):
                    critical_items.extend((field, str(note)) for note in values)
            critical = tuple(critical_items)
            # Any passed scene with explicit omissions is accepted prose and
            # needs the same strict materiality audit. Reviewer-originated
            # passes are not safer than passes normalized by harness policy.
            if (
                review.get("verdict") != "pass"
                or not isinstance(omissions, list)
                or (not omissions and not critical)
            ):
                continue
            metadata = scene.get("scene", {})
            start, end = metadata.get("source_start"), metadata.get("source_end")
            if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(source_text):
                raise ValueError(f"invalid source bounds: {scene_path}")
            found.append(RiskyScene(
                run_dir=run_dir, scene_path=scene_path,
                scene_id=str(metadata.get("id", scene_path.stem)),
                source=source_text[start:end], adaptation=str(scene["text"]),
                review=review,
                omissions=tuple(str(item) for item in omissions),
                critical_findings=critical,
            ))
    return found


def _label_key(value: Any) -> str:
    return simplified("".join(
        char for char in str(value)
        if not char.isspace() and not unicodedata.category(char).startswith("P")
    ))


def _normalize_positional_labels(
    result: dict[str, Any], authoritative: list[str], label_field: str,
    scene_path: Path, *, threshold: float = 0.86,
) -> None:
    """Repair only close positional label transcription, never model judgment."""
    assessments = result.get("assessments", [])
    if not isinstance(assessments, list) or len(assessments) != len(authoritative):
        raise ValueError(
            f"classification must reproduce labels once and in order: {scene_path}"
        )
    canonical_keys = [_label_key(item) for item in authoritative]
    evidence = []
    for index, (assessment, canonical) in enumerate(zip(assessments, authoritative)):
        if not isinstance(assessment, dict):
            raise ValueError(f"classification assessment must be an object: {scene_path}")
        returned = assessment.get(label_field)
        if returned == canonical:
            continue
        returned_key = _label_key(returned)
        score = SequenceMatcher(None, returned_key, canonical_keys[index]).ratio()
        other_scores = [
            SequenceMatcher(None, returned_key, key).ratio()
            for other, key in enumerate(canonical_keys) if other != index
        ]
        # Reordered labels match another authoritative position better than
        # their own; do not silently relabel them by position.
        if (not returned_key or score < threshold
                or (other_scores and max(other_scores) >= score)):
            raise ValueError(
                f"classification must reproduce labels once and in order: {scene_path}"
            )
        assessment[label_field] = canonical
        evidence.append({
            "index": index, "field": label_field, "similarity": round(score, 6),
            "returned_sha256": _sha256(str(returned).encode("utf-8")),
            "authoritative_sha256": _sha256(canonical.encode("utf-8")),
        })
    if evidence:
        result.setdefault("label_normalization_evidence", []).extend(evidence)


def validate_classification(scene: RiskyScene, result: dict[str, Any]) -> None:
    try:
        _normalize_positional_labels(
            result, list(scene.omissions), "omission_note", scene.scene_path,
        )
    except ValueError as exc:
        raise ValueError(
            f"classification must reproduce omission notes once and in order: {scene.scene_path}"
        ) from exc
    for assessment in result.get("assessments", []):
        classification = assessment.get("classification")
        required_fix = str(assessment.get("required_fix", "")).strip()
        if classification == "permissible_compression" and required_fix:
            raise ValueError("permissible compression must have empty required_fix")
        if classification == "material" and not required_fix:
            raise ValueError("material omission must have a concrete required_fix")


def validate_finding_classification(scene: RiskyScene, result: dict[str, Any]) -> None:
    assessments = result.get("assessments", [])
    fields = [item.get("field") if isinstance(item, dict) else None
              for item in assessments if isinstance(assessments, list)]
    expected_fields = [field for field, _note in scene.critical_findings]
    if fields != expected_fields:
        raise ValueError(
            f"classification must reproduce critical findings once and in order: {scene.scene_path}"
        )
    try:
        _normalize_positional_labels(
            result, [note for _field, note in scene.critical_findings],
            "finding_note", scene.scene_path,
        )
    except ValueError as exc:
        raise ValueError(
            f"classification must reproduce critical findings once and in order: {scene.scene_path}"
        ) from exc
    for assessment in result.get("assessments", []):
        classification = assessment.get("classification")
        required_fix = str(assessment.get("required_fix", "")).strip()
        if classification == "minor_or_negated" and required_fix:
            raise ValueError("minor/negated finding must have empty required_fix")
        if classification == "material" and not required_fix:
            raise ValueError("material finding must have a concrete required_fix")


# These phrases describe objective identity/agency errors, not matters of
# adaptation style.  A model materiality pass may otherwise dismiss them as
# "slight imprecision" even though they teach the reader the wrong name or
# credit an action to the wrong character.  Keep this deliberately narrow;
# ordinary compression and weakened detail still go through model judgment.
_DETERMINISTIC_MATERIAL_FINDING = re.compile(
    r"(?:"
    r"(?:人物|角色|武将|官职)?(?:姓名|名字)(?:有误|错误|不准确)|"
    r"行动主体(?:有误|错误|不准确)|"
    r"(?:功劳|行动|行为|决定|命令)(?:错误地)?(?:归给|归于|归因于)|"
    r"(?:归因|归属)(?:有误|错误|偏差|不当)|"
    r"(?:主语|主体)(?:有误|错误|错置)"
    r")"
)


def enforce_deterministic_material_findings(result: dict[str, Any]) -> None:
    """Fail closed for explicit wrong-name and wrong-actor review findings."""
    for assessment in result.get("assessments", []):
        note = str(assessment.get("finding_note", ""))
        if not _DETERMINISTIC_MATERIAL_FINDING.search(note):
            continue
        assessment["classification"] = "material"
        if not str(assessment.get("required_fix", "")).strip():
            assessment["required_fix"] = (
                "Correct the stated name or actor/agency error to match the "
                "exact original scene."
            )


def assemble_chapter(run_dir: Path) -> str:
    paths, _scene_evidence = authoritative_scene_paths(run_dir)
    scenes = [_object(path) for path in paths]
    if not scenes:
        raise ValueError(f"no accepted scene files: {run_dir}")
    assembled = "\n\n".join(str(item["text"]).strip() for item in scenes) + "\n"
    accepted = (run_dir / "chapter.txt").read_text(encoding="utf-8")
    if assembled != accepted:
        backup = run_dir / CHAPTER_BACKUP
        if backup.exists() and assembled == backup.read_text(encoding="utf-8"):
            return assembled
        raise ValueError(f"scene texts do not exactly assemble accepted chapter: {run_dir}")
    return accepted


class OmissionAuditor:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.output = Path(args.output_dir).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.runner = CodexRunner(
            self.output, args.model, asyncio.Semaphore(args.concurrency), args.timeout
        )

    def job_prefix(self, scene: RiskyScene) -> str:
        return f"{scene.run_dir.name}/{scene.scene_id}"

    async def classify(self, scene: RiskyScene) -> dict[str, Any]:
        notes = json.dumps(list(scene.omissions), ensure_ascii=False, indent=2)
        exact_review = json.dumps(scene.review, ensure_ascii=False, indent=2)
        prompt = f"""Return only JSON matching the supplied schema. Strictly audit
each omission note against the exact ORIGINAL SCENE and ACCEPTED SCENE. Preserve
the notes verbatim, once each and in the same order. Classify `material` when
the missing content changes an event, outcome, cause, motivation, relationship,
identity, chronology, important characterization, or information needed for a
coherent later chapter. Classify only atmosphere, repetition, ornamental
description, or nonessential digression as `permissible_compression`. When in
doubt, choose material. For permissible compression, required_fix must be empty.

ORIGINAL SCENE:\n{scene.source}\n\nACCEPTED SCENE:\n{scene.adaptation}

ACCEPTED SOURCE REVIEW (EXACT JSON):\n{exact_review}

OMISSION NOTES:\n{notes}"""
        result = await self.runner.call(
            f"{self.job_prefix(scene)}/classify", prompt,
            SCHEMAS / "omission-classification.schema.json",
            self.args.classify_effort, refresh=self.args.refresh,
        )
        validate_classification(scene, result)
        return result

    async def classify_findings(self, scene: RiskyScene) -> dict[str, Any]:
        exact_review = json.dumps(scene.review, ensure_ascii=False, indent=2)
        notes = json.dumps([
            {"field": field, "finding_note": note}
            for field, note in scene.critical_findings
        ], ensure_ascii=False, indent=2)
        prompt = f"""Return only JSON matching the supplied schema. Independently
classify every REVIEW FINDING against the exact ORIGINAL SCENE and ACCEPTED
SCENE. Reproduce each field and finding_note verbatim, once and in order.
Choose `material` only for a real unsupported fact, wrong person/name/actor,
changed event or outcome, causal/chronological distortion, identity error, or
language defect that materially changes meaning or reader comprehension.
Choose `minor_or_negated` for explicitly negated findings (for example “无明显
新增”), harmless generalization, stylistic preference, slight imprecision, or
compression that preserves the event and causal chain. The user's policy is
close-enough natural adaptation: do not repair merely pedantic differences.
When genuinely uncertain about a factual/causal/identity error, choose material.
Material required_fix must state the concrete source-grounded correction;
minor_or_negated required_fix must be empty.

ORIGINAL SCENE:\n{scene.source}\n\nACCEPTED SCENE:\n{scene.adaptation}

ACCEPTED SOURCE REVIEW (EXACT JSON):\n{exact_review}

REVIEW FINDINGS:\n{notes}"""
        result = await self.runner.call(
            f"{self.job_prefix(scene)}/classify_findings", prompt,
            SCHEMAS / "finding-materiality.schema.json",
            self.args.classify_effort, refresh=self.args.refresh,
        )
        enforce_deterministic_material_findings(result)
        validate_finding_classification(scene, result)
        return result

    async def repair_chapter(
        self, run_dir: Path, risks: list[tuple[RiskyScene, dict[str, Any]]]
    ) -> dict[str, Any]:
        manifest = _object(run_dir / "manifest.json")
        source = resolve_manifest_source(manifest).read_text(encoding="utf-8")
        accepted = assemble_chapter(run_dir)
        material = [
            assessment
            for _, result in risks for assessment in result["assessments"]
            if assessment["classification"] == "material"
        ]
        if not material:
            return {"status": "no_material_omissions"}
        target = int(manifest.get("target_chars", cjk_count(accepted)))
        low, high = int(target * .7), int(target * 1.3)
        active_low, active_high = low, high
        arrays = ("material_omissions", "unsupported_additions", "distortions", "language_problems")
        candidate = accepted
        required = material
        review: dict[str, Any] = {}
        attempts: list[dict[str, Any]] = []
        passed = False
        max_rounds = max(1, int(getattr(self.args, "max_repair_rounds", 3)))
        for round_no in range(1, max_rounds + 1):
            # After five unsuccessful editorial passes, HSK1 needs enough room
            # to state core causes/events naturally instead of degenerating
            # into semicolon-separated telegram prose.  Earlier bounds and
            # prompt bytes remain unchanged so all established caches survive.
            active_low, active_high = low, high
            if round_no >= 6 and manifest["level"] == "hsk1":
                active_high = int(target * 1.7)
            suffix = "" if round_no == 1 else f"_round_{round_no:02d}"
            if round_no == 1:
                # Keep the original production prompt byte-for-byte stable so
                # adding later self-heal rounds never invalidates hundreds of
                # already reviewed round-one artifacts.
                prompt = f"""Return only JSON matching the supplied schema. Repair the
complete ACCEPTED CHAPTER using the exact ORIGINAL CHAPTER. Correct every
confirmed material omission or factual/causal/identity/language finding listed
below. Preserve all already-correct facts,
event order, point of view, naturalness, and the reading level {manifest['level'].upper()}.
Do not restore merely ornamental details or invent anything. Keep length close
to the accepted compact chapter and within 70%-130% of target {target} Chinese
characters. Return the complete repaired chapter in `text`.

ORIGINAL CHAPTER:\n{source}\n\nACCEPTED CHAPTER:\n{accepted}\n\nMATERIAL FINDINGS:
{json.dumps(material, ensure_ascii=False, indent=2)}"""
            elif round_no <= 5:
                prompt = f"""Return only JSON matching the supplied schema. Repair the
complete CURRENT CANDIDATE using the exact ORIGINAL CHAPTER. Correct every
confirmed material omission or factual/causal/identity/language finding listed
below. Preserve all already-correct facts, event order, point of view,
naturalness, and the reading level {manifest['level'].upper()}. Do not restore
merely ornamental details or invent anything. Keep length close to the accepted
compact chapter and within 70%-130% of target {target} Chinese characters.
Return the complete repaired chapter in `text`.

ORIGINAL CHAPTER:\n{source}\n\nCURRENT CANDIDATE:\n{candidate}\n\nREQUIRED CORRECTIONS:
{json.dumps(required, ensure_ascii=False, indent=2)}"""
            else:
                prompt = f"""Return only JSON matching the supplied schema. Repair the
complete CURRENT CANDIDATE using the exact ORIGINAL CHAPTER. Correct every
confirmed material omission or factual/causal/identity/language finding listed
below. Preserve all already-correct facts, event order, point of view,
naturalness, and the reading level {manifest['level'].upper()}. Do not restore
merely ornamental details or invent anything. Use the extra compact-summary
room to write natural complete sentences; keep the complete text within
{active_low}-{active_high} Chinese characters. Return the complete repaired
chapter in `text`.

ORIGINAL CHAPTER:\n{source}\n\nCURRENT CANDIDATE:\n{candidate}\n\nREQUIRED CORRECTIONS:
{json.dumps(required, ensure_ascii=False, indent=2)}"""
            repaired = await self.runner.call(
                f"{run_dir.name}/chapter_repair{suffix}", prompt,
                SCHEMAS / "adaptation.schema.json", self.args.repair_effort,
                refresh=self.args.refresh,
            )
            candidate = repaired["text"]
            if round_no == 1:
                review_prompt = f"""Return only JSON matching the supplied schema.
Independently compare REPAIRED CHAPTER with exact ORIGINAL CHAPTER. It is an
intentionally compact {manifest['level'].upper()} graded reader. Check that all
confirmed material findings were corrected without unsupported additions,
distortions, unnatural language, or loss of readability. `material_omissions`
must contain only missing facts/events that matter to plot, causality,
motivation, relationships, chronology, characterization, or later coherence.
Pass only if fidelity, naturalness, and readability are each at least 8 and all
problem arrays are empty.

ORIGINAL CHAPTER:\n{source}\n\nREPAIRED CHAPTER:\n{candidate}\n\nREQUIRED CORRECTIONS:
{json.dumps(material, ensure_ascii=False, indent=2)}"""
            else:
                review_prompt = f"""Return only JSON matching the supplied schema.
Independently compare REPAIRED CHAPTER with exact ORIGINAL CHAPTER. It is an
intentionally compact {manifest['level'].upper()} graded reader. Check that all
required corrections were made without unsupported additions, distortions,
unnatural language, or loss of readability. `material_omissions` must contain
only missing facts/events that matter to plot, causality, motivation,
relationships, chronology, characterization, or later coherence. Pass only if
fidelity, naturalness, and readability are each at least 8 and all problem
arrays are empty.

ORIGINAL CHAPTER:\n{source}\n\nREPAIRED CHAPTER:\n{candidate}\n\nREQUIRED CORRECTIONS:
{json.dumps(required, ensure_ascii=False, indent=2)}"""
            review = await self.runner.call(
                f"{run_dir.name}/chapter_repair_review{suffix}", review_prompt,
                SCHEMAS / "omission-repair-review.schema.json",
                self.args.review_effort, refresh=self.args.refresh,
            )
            count = cjk_count(candidate)
            if active_low <= count <= active_high:
                # Models frequently repeat a stale qualitative length complaint
                # from REQUIRED CORRECTIONS even after the deterministic count
                # satisfies the active (possibly expanded HSK1) range.  The
                # mechanical counter is authoritative; preserve every factual,
                # causal, and ordinary language finding.
                review = discard_incorrect_length_findings(review)
                if (
                    review.get("verdict") != "pass"
                    and min(
                        review.get("source_fidelity", 0),
                        review.get("naturalness", 0),
                        review.get("readability", 0),
                    ) >= 8
                    and all(not review.get(key) for key in arrays)
                ):
                    review["reviewer_verdict"] = review.get("verdict")
                    review["verdict"] = "pass"
                    review["harness_decision"] = (
                        "accepted_after_deterministic_length_verification"
                    )
            passed = (
                review.get("verdict") == "pass"
                and min(review.get("source_fidelity", 0), review.get("naturalness", 0), review.get("readability", 0)) >= 8
                and all(not review.get(key) for key in arrays)
                and active_low <= count <= active_high
            )
            attempts.append({
                "round": round_no, "candidate_cjk": count,
                "review": review, "passed": passed,
            })
            if passed:
                break
            required = [
                {"field": key, "finding_note": str(finding),
                 "required_fix": str(finding)}
                for key in arrays for finding in review.get(key, [])
            ]
            if count < active_low or count > active_high:
                required.append({
                    "field": "length", "finding_note": f"candidate has {count} CJK",
                    "required_fix": (
                        f"keep complete text within {active_low}-{active_high} CJK"
                    ),
                })
            if not required:
                required.append({
                    "field": "scores", "finding_note": "review scores below threshold",
                    "required_fix": "improve fidelity, naturalness, and readability to at least 8",
                })
        chapter_dir = self.output / "chapters" / run_dir.name
        chapter_dir.mkdir(parents=True, exist_ok=True)
        (chapter_dir / "candidate.txt").write_text(candidate, encoding="utf-8")
        result = {
            "status": "complete" if passed else "blocked", "source_run": str(run_dir),
            "level": manifest["level"], "target_chars": target, "candidate_cjk": count,
            "required_range": [active_low, active_high], "material_findings": material,
            "material_omissions": material, "review": review,
            "repair_rounds": attempts,
        }
        (chapter_dir / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        return result

    async def run(self, run_dirs: list[Path]) -> dict[str, Any]:
        risky = discover_risky_scenes(run_dirs)
        grouped: dict[Path, list[tuple[RiskyScene, dict[str, Any]]]] = {}
        tasks: list[tuple[RiskyScene, Any]] = []
        for scene in risky:
            if scene.omissions:
                tasks.append((scene, self.classify(scene)))
            if scene.critical_findings:
                tasks.append((scene, self.classify_findings(scene)))
        classified = await asyncio.gather(*(task for _, task in tasks))
        for (scene, _), result in zip(tasks, classified):
            grouped.setdefault(scene.run_dir, []).append((scene, result))
        repairs = await asyncio.gather(*(self.repair_chapter(run, items) for run, items in grouped.items()))
        promotions: list[dict[str, Any]] = []
        if getattr(self.args, "promote_passed", False):
            for item in repairs:
                if item["status"] != "complete":
                    continue
                run_dir = Path(item["source_run"])
                candidate = self.output / "chapters" / run_dir.name / "candidate.txt"
                promotion = promote_passed_candidate(run_dir, candidate)
                item["promotion"] = promotion
                promotions.append(promotion)
        report = {
            "status": "complete" if all(item["status"] in {"complete", "no_material_omissions"} for item in repairs) else "blocked",
            "updated_at": utc_now(), "risky_scenes": len(risky),
            "chapters_audited": len(grouped), "chapter_results": repairs,
            "promotion_requested": bool(getattr(self.args, "promote_passed", False)),
            "promotions": promotions,
        }
        (self.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        return report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--run-dir", action="append", required=True)
    result.add_argument("--output-dir", required=True)
    result.add_argument("--model", default="gpt-5.6-luna")
    result.add_argument("--classify-effort", default="high")
    result.add_argument("--repair-effort", default="xhigh")
    result.add_argument("--review-effort", default="high")
    result.add_argument("--concurrency", type=int, default=8)
    result.add_argument("--timeout", type=int, default=900)
    result.add_argument(
        "--max-repair-rounds", type=int, default=3,
        help="bounded repair/review self-healing rounds for each material chapter",
    )
    result.add_argument("--refresh", action="store_true")
    result.add_argument(
        "--promote-passed", action="store_true",
        help="atomically replace chapter.txt only for independently passed repairs",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    report = asyncio.run(OmissionAuditor(args).run([Path(item) for item in args.run_dir]))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
