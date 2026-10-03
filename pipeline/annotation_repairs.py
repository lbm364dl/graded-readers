"""Issue-planned semantic repairs for Chinese and Japanese annotations.

This layer does not approve edits. It records the review findings, a narrow
per-finding edit plan, the exact patch, and the derived candidate; the owning
harness still validates and independently reviews the edited annotation.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from jsonschema import ValidationError, validate

from pipeline.annotation_edits import (
    AnnotationEditError,
    BoundaryChangeNotSupported,
    ImmutableFieldError,
    apply_edits,
    candidate_digest,
)


_TARGET = {
    "type": "object", "additionalProperties": False,
    "required": ["op", "path"],
    "properties": {
        "op": {"enum": ["set_field", "replace_row", "replace_list", "append_row", "remove_row"]},
        "path": {"type": "string", "minLength": 2},
    },
}
PLAN_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["issues"],
    "properties": {"issues": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["issue_index", "reason", "targets", "boundary_change_needed", "boundary_reason"],
        "properties": {
            "issue_index": {"type": "integer", "minimum": 0},
            "reason": {"type": "string", "minLength": 1},
            "targets": {"type": "array", "items": _TARGET},
            "boundary_change_needed": {"type": "boolean"},
            "boundary_reason": {"type": "string"},
        },
    }}},
}

_PATCH_EDIT_SCHEMAS = []
for op in ("set_field", "replace_row", "replace_list", "append_row"):
    _PATCH_EDIT_SCHEMAS.append({
        "type": "object", "additionalProperties": False,
        "required": ["op", "path", "value"],
        "properties": {"op": {"const": op}, "path": {"type": "string", "minLength": 2},
                       "value": {}},
    })
_PATCH_EDIT_SCHEMAS.append({
    "type": "object", "additionalProperties": False,
    "required": ["op", "path"],
    "properties": {"op": {"const": "remove_row"}, "path": {"type": "string", "minLength": 2}},
})
PATCH_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["base_digest", "edits"],
    "properties": {
        "base_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "edits": {"type": "array", "items": {"oneOf": _PATCH_EDIT_SCHEMAS}},
    },
}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_job_path(run_dir: Path, job: str) -> Path:
    path = Path(job)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"invalid annotation repair job reference: {job!r}")
    result = (run_dir / "agents" / path).resolve()
    if not result.is_relative_to((run_dir / "agents").resolve()):
        raise ValueError("annotation repair evidence path escaped the run directory")
    return result


def _schemas(run_dir: Path) -> tuple[Path, Path]:
    plan_path = run_dir / "annotation-repair-plan.schema.json"
    patch_path = run_dir / "annotation-semantic-patch.schema.json"
    _write_json(plan_path, PLAN_SCHEMA)
    _write_json(patch_path, PATCH_SCHEMA)
    return plan_path, patch_path


def _validate_plan(plan: Any, issue_count: int) -> None:
    validate(plan, PLAN_SCHEMA)
    rows = plan["issues"]
    indexes = [row["issue_index"] for row in rows]
    if indexes != list(range(issue_count)):
        raise ValueError(f"repair plan must cover supplied issue indices exactly once: {indexes!r}")
    for row in rows:
        if row["boundary_change_needed"]:
            if row["targets"] or not row["boundary_reason"].strip():
                raise ValueError("boundary-needed plan rows require a reason and no semantic targets")
        elif not row["targets"] or row["boundary_reason"].strip():
            raise ValueError("semantic plan rows need targets and an empty boundary reason")


def _targets(plan: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for issue in plan["issues"]:
        for target in issue["targets"]:
            key = target["op"], target["path"]
            if key not in seen:
                seen.add(key)
                result.append({"op": key[0], "path": key[1]})
    return result


def _save_assembly(run_dir: Path, assembly_job: str, meta: dict[str, Any], result: Any) -> None:
    directory = _safe_job_path(run_dir, assembly_job)
    _write_json(directory / "result.json", result)
    _write_json(directory / "meta.json", meta)


def _evidence_result(run_dir: Path, assembly_job: str, meta: dict[str, Any]) -> dict[str, Any]:
    return {"assembly_job": assembly_job, "assembly_meta": str(
        _safe_job_path(run_dir, assembly_job) / "meta.json"),
        "base_digest": meta["base_digest"], "issue_digest": meta["issue_digest"],
        "plan_job": meta["plan_job"], "plan_digest": meta["plan_digest"],
        "patch_job": meta.get("patch_job"), "patch_digest": meta.get("patch_digest"),
        "result_digest": meta.get("result_digest"),
    }


async def repair_annotation(
    harness: Any,
    job: str,
    candidate: dict[str, Any],
    issues: list[Any],
    *,
    representation: str,
    language: str,
    context: dict[str, Any] | None = None,
    validate_candidate: Callable[[dict[str, Any]], None],
    effort: str | None = None,
    refresh: bool | None = None,
) -> dict[str, Any]:
    """Plan, patch, locally validate, and persist one semantic repair attempt.

    ``validate_candidate`` is the owning harness's complete deterministic
    candidate gate. A return value of ``boundary_change_needed`` explicitly
    directs the caller to its separate boundary-capable correction path.
    """
    if not isinstance(candidate, dict) or not isinstance(issues, list) or not issues:
        raise ValueError("semantic annotation repair needs a candidate and nonempty review issues")
    run_dir = Path(harness.run_dir)
    plan_schema_path, patch_schema_path = _schemas(run_dir)
    plan_job, patch_job, assembly_job = f"{job}_plan", f"{job}_patch", f"{job}_assembly"
    shared_context = {**(context or {}), "language": language,
                      "representation": representation, "candidate": candidate,
                      "issues": issues}
    plan_prompt = f"""Return JSON matching the supplied repair-plan schema. Build a narrow diagnosis plan for every supplied independent review issue. `issue_index` must cover each input issue exactly once, in order. For each issue, give a concrete reason and exact JSON-pointer target(s) using only supported operations. The plan is diagnosis, never approval. Prefer a semantic field or explicit semantic-list row operation over changing a complete annotation.

If resolving an issue requires changing source text, a primary tap surface, an existing source range/index, or the segmentation, mark `boundary_change_needed: true`, give `boundary_reason`, and provide no targets for that issue. Never route such a change through semantic targets. Otherwise set it false and leave boundary_reason empty. Scope each target to the smallest existing semantic field or list row needed. Do not target unrelated rows. The caller will check these targets against a representation-specific source/tap contract.

INPUT:
{json.dumps(shared_context, ensure_ascii=False, indent=2)}"""
    harness_args = getattr(harness, "args", None)
    selected_effort = effort or getattr(harness_args, "annotation_repair_effort", "low")
    selected_refresh = (refresh if refresh is not None else
                        getattr(harness_args, "refresh", False))
    plan = await harness.runner.call(plan_job, plan_prompt, plan_schema_path, selected_effort,
        refresh=selected_refresh, workspace_context=shared_context)
    validate(plan, PLAN_SCHEMA)
    _validate_plan(plan, len(issues))
    base_digest = candidate_digest(candidate)
    issue_digest = digest(issues)
    plan_digest = digest(plan)
    if any(row["boundary_change_needed"] for row in plan["issues"]):
        meta = {"return_code": 0, "kind": "annotation_patch_assembly",
            "status": "boundary_change_needed", "representation": representation,
            "base": deepcopy(candidate), "base_digest": base_digest,
            "issues": deepcopy(issues), "issue_digest": issue_digest,
            "plan_job": plan_job, "plan_digest": plan_digest,
            "patch_job": None, "patch_digest": None,
            "boundary_issues": [row["issue_index"] for row in plan["issues"]
                                if row["boundary_change_needed"]]}
        meta["result_digest"] = base_digest
        _save_assembly(run_dir, assembly_job, meta, deepcopy(candidate))
        return {"status": "boundary_change_needed", "candidate": deepcopy(candidate),
                "evidence": _evidence_result(run_dir, assembly_job, meta), "plan": plan}

    allowed = _targets(plan)
    patch_validation = {**(context or {}), "base_candidate": candidate,
        "representation": representation, "allowed_targets": allowed,
        "language": language, "issues": issues}
    patch_context = {**shared_context, "repair_plan": plan,
                     "allowed_targets": allowed, "base_digest": base_digest,
                     "annotation_patch_validation": patch_validation}
    patch_prompt = f"""Return JSON matching the supplied semantic-patch schema. Repair only the supplied prior candidate. Return exactly `base_digest` and `edits`; never return a replacement candidate. Use only the exact op/path pairs in ALLOWED_TARGETS, and make each edit resolve its mapped review issue. Preserve all unedited data. Do not change source text, tap surfaces, segmentation, or existing source/tap ranges. Use an explicit append/remove operation only when the repair plan names that exact list operation. The local caller applies the patch and runs the full deterministic language gate; independent review still follows.

INPUT:
{json.dumps(patch_context, ensure_ascii=False, indent=2)}"""
    patch = await harness.runner.call(patch_job, patch_prompt, patch_schema_path, selected_effort,
        refresh=selected_refresh, workspace_context=patch_context)
    validate(patch, PATCH_SCHEMA)
    try:
        updated = apply_edits(candidate, patch, allowed_targets=allowed,
                              representation=representation)
    except (BoundaryChangeNotSupported, ImmutableFieldError) as exc:
        # Keep the rejected patch artifact for diagnosis. This result is a typed
        # routing signal; no boundary change is applied.
        meta = {"return_code": 0, "kind": "annotation_patch_assembly",
            "status": "boundary_change_needed", "representation": representation,
            "base": deepcopy(candidate), "base_digest": base_digest,
            "issues": deepcopy(issues), "issue_digest": issue_digest,
            "plan_job": plan_job, "plan_digest": plan_digest,
            "patch_job": patch_job, "patch_digest": digest(patch),
            "boundary_error": str(exc), "result_digest": base_digest}
        _save_assembly(run_dir, assembly_job, meta, deepcopy(candidate))
        return {"status": "boundary_change_needed", "candidate": deepcopy(candidate),
                "evidence": _evidence_result(run_dir, assembly_job, meta), "plan": plan,
                "boundary_error": str(exc)}
    except AnnotationEditError as exc:
        meta = {"return_code": 0, "kind": "annotation_patch_assembly",
            "status": "patch_rejected", "representation": representation,
            "base": deepcopy(candidate), "base_digest": base_digest,
            "issues": deepcopy(issues), "issue_digest": issue_digest,
            "plan_job": plan_job, "plan_digest": plan_digest,
            "patch_job": patch_job, "patch_digest": digest(patch),
            "patch_error": str(exc), "result_digest": base_digest}
        _save_assembly(run_dir, assembly_job, meta, deepcopy(candidate))
        return {"status": "patch_rejected", "candidate": deepcopy(candidate),
                "evidence": _evidence_result(run_dir, assembly_job, meta), "plan": plan,
                "patch_error": str(exc)}
    try:
        validate_candidate(updated)
    except (ValueError, TypeError, KeyError, IndexError, ValidationError) as exc:
        meta = {"return_code": 0, "kind": "annotation_patch_assembly",
            "status": "patch_rejected", "representation": representation,
            "base": deepcopy(candidate), "base_digest": base_digest,
            "issues": deepcopy(issues), "issue_digest": issue_digest,
            "plan_job": plan_job, "plan_digest": plan_digest,
            "patch_job": patch_job, "patch_digest": digest(patch),
            "validation_error": str(exc), "result_digest": base_digest}
        _save_assembly(run_dir, assembly_job, meta, deepcopy(candidate))
        return {"status": "patch_rejected", "candidate": deepcopy(candidate),
                "evidence": _evidence_result(run_dir, assembly_job, meta), "plan": plan,
                "validation_error": str(exc)}
    meta = {"return_code": 0, "kind": "annotation_patch_assembly",
        "status": "applied", "representation": representation,
        "base": deepcopy(candidate), "base_digest": base_digest,
        "issues": deepcopy(issues), "issue_digest": issue_digest,
        "plan_job": plan_job, "plan_digest": plan_digest,
        "patch_job": patch_job, "patch_digest": digest(patch),
        "result_digest": candidate_digest(updated)}
    _save_assembly(run_dir, assembly_job, meta, updated)
    return {"status": "applied", "candidate": updated,
            "evidence": _evidence_result(run_dir, assembly_job, meta), "plan": plan}


def replay_annotation_repair(
    run_dir: Path,
    assembly_job: str,
    *,
    validate_candidate: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """Verify stored child evidence and deterministically replay an assembly."""
    assembly_dir = _safe_job_path(run_dir, assembly_job)
    meta, stored = _read_json(assembly_dir / "meta.json"), _read_json(assembly_dir / "result.json")
    if meta.get("kind") != "annotation_patch_assembly" or meta.get("return_code") != 0:
        raise ValueError("invalid annotation semantic-patch assembly metadata")
    base = meta.get("base")
    if candidate_digest(base) != meta.get("base_digest") or digest(meta.get("issues")) != meta.get("issue_digest"):
        raise ValueError("annotation repair base or review issues changed")

    def child(job: str, expected_digest: str) -> Any:
        directory = _safe_job_path(run_dir, job)
        child_meta, value = _read_json(directory / "meta.json"), _read_json(directory / "result.json")
        if child_meta.get("return_code") != 0 or digest(value) != expected_digest:
            raise ValueError(f"annotation repair child evidence changed: {job}")
        return value

    plan = child(meta["plan_job"], meta["plan_digest"])
    _validate_plan(plan, len(meta["issues"]))
    if meta.get("status") in {"boundary_change_needed", "patch_rejected"}:
        if candidate_digest(stored) != meta.get("base_digest"):
            raise ValueError("rejected annotation repair modified the base candidate")
        if meta.get("patch_job"):
            patch = child(meta["patch_job"], meta["patch_digest"])
            try:
                updated = apply_edits(base, patch, allowed_targets=_targets(plan),
                                      representation=meta["representation"])
            except (BoundaryChangeNotSupported, ImmutableFieldError) as exc:
                if meta.get("status") != "boundary_change_needed" or str(exc) != meta.get("boundary_error"):
                    raise ValueError("replayed boundary diagnostic differs from stored evidence") from exc
            except AnnotationEditError as exc:
                if meta.get("status") != "patch_rejected" or str(exc) != meta.get("patch_error"):
                    raise ValueError("replayed rejected patch diagnostic differs from stored evidence") from exc
            else:
                if meta.get("status") == "boundary_change_needed":
                    raise ValueError("stored boundary outcome now applies as a semantic patch")
                try:
                    validate_candidate(updated)
                except (ValueError, TypeError, KeyError, IndexError, ValidationError) as exc:
                    if str(exc) != meta.get("validation_error"):
                        raise ValueError("replayed candidate rejection differs from stored validation evidence") from exc
                else:
                    raise ValueError("stored rejected candidate now passes its deterministic gate")
        return {"status": meta["status"], "candidate": stored,
                "evidence": _evidence_result(run_dir, assembly_job, meta), "plan": plan,
                "boundary_error": meta.get("boundary_error"),
                "patch_error": meta.get("patch_error"),
                "validation_error": meta.get("validation_error")}
    if meta.get("status") != "applied" or not meta.get("patch_job"):
        raise ValueError("unknown annotation repair assembly status")
    patch = child(meta["patch_job"], meta["patch_digest"])
    allowed = _targets(plan)
    updated = apply_edits(base, patch, allowed_targets=allowed,
                          representation=meta["representation"])
    validate_candidate(updated)
    if candidate_digest(updated) != meta.get("result_digest") or updated != stored:
        raise ValueError("replayed annotation semantic patch differs from its stored result")
    return {"status": "applied", "candidate": updated,
            "evidence": _evidence_result(run_dir, assembly_job, meta), "plan": plan}
