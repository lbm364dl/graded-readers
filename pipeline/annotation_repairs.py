"""Issue-planned semantic repairs for language annotations.

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
from pipeline.annotation_review_guidance import FORM_STAGE_EVIDENCE_GUIDANCE_V2 as FORM_STAGE_EVIDENCE_GUIDANCE

from pipeline.annotation_edits import (
    AnnotationEditError,
    BoundaryChangeNotSupported,
    ImmutableFieldError,
    apply_edits,
    candidate_digest,
    uncovered_issue_targets,
    validate_target_contract,
    validate_issue_target_coverage,
)


LEGACY_REPAIR_EXPLANATION_GUIDANCE_V3 = "Apply the same complete-form and meaning-scope criteria to your planned corrections and written patch as to independent review. Inspect comparable occurrences of each reported defect throughout this candidate and include all actually affected semantic targets in that issue's plan; leave correct contrasting occurrences unchanged. A finding is evidence to investigate, not permission to replace a defensible meaning with a stylistic preference. Use the supplied lessons, predicate frame and source timeline to support each correction.\n\nFor a finding about a form step or a multi-tap construction, inspect the related meaning fields together: the segment's occurrence `meaning_en`, each relevant complete `form_steps[*].meaning_en`, adjacent lexical/auxiliary taps in the exact construction span, and the linked grammar occurrence's meaning/context. Decide the scope of each field separately from its schema and supplied evidence. If a supported construction-level meaning was copied into more than one component field, include every field that actually needs correction in the same issue plan, even when the reviewer quoted only one of them. Do not assume the fields should contain identical English, or that every component must receive a different gloss; retain valid contextual translations and form-stage meanings when the evidence supports them. Do not invent a component meaning just to make the fields look different.\n\nWhen an issue includes host-verified `path_history`, inspect the earlier finding dispositions, reasons, and exact values for those overlapping fields. A `bound: true` row is tied to its declared affected path; an unbound legacy row is context only, not a binding on that field. Do not restore a value previously rejected or corrected on the same field merely to satisfy a later wording preference or a broader construction gloss. Preserve the distinction between a component form and any overlapping complete construction. If the supplied evidence now supports a legitimate rollback, explicitly identify the earlier finding and explain what new evidence changes that conclusion in the plan reason; this is an evidence requirement, not a ban on returning to an earlier value.\n\nLearner-facing explanations must positively explain the actual form or grammatical contribution. Do not copy reviewer objections, editorial uncertainty, or defensive denials into occurrence notes or meanings. A useful grammatical contrast is allowed when it explains the form itself; rebutting one reviewer's classification is not a learner explanation. Keep a complete form's meaning separate from its form label and passage-specific grammatical role. Do not import a larger construction's meaning into one component stage, concatenate component glosses, or invent contributions to satisfy a review.\n\nA dictionary construction explanation may use variables such as a person saying something while performing an action. These describe the construction, not additional events or words asserted by each occurrence. Ground the meaning of each field in its exact complete source form and represented scope. Do not paste schematic participants, actions, or placeholders into an occurrence or form-stage meaning. A connective or participial reporting rendering can already express the relevant relation to the following clause; a neighboring proposition-only stage may still omit that contribution. Judge those fields separately. Expand an issue's targets only after showing which contribution is actually missing from each additional field, with source or lesson evidence. Name a concrete simultaneous action only when the source attests it and it belongs to the field's represented span; do not import the following clause's action into a smaller tap. Preserve a correct contrasting meaning even when its wording differs from the revised stage. This guidance does not approve a candidate or prescribe an English phrase.\n\nDistinguish changing an identity on a retained, complete form stage from removing that stage or changing the supported occurrence shape. When the complete form and direct occurrence geometry are retained and the grammar identity is wrong, plan the exact identity-array change together with the matching direct link's entry_id field. A simultaneous, separately supported correction to its complete-form meaning does not itself require changing the occurrence shape. Preserve its source span and context unless a separate supported finding identifies a defect there. Do not remove and recreate that direct row as a displayed wider construction merely to change its lesson identity. In Korean, a link whose entry_id is already a retained stage identity on its owner must use the direct-link fields; a displayed construction row for that same staged identity is invalid. If the stage actually must be removed or the supported occurrence shape must change, use the planned remove and source-grounded append operations, or remove a redundant direct row when another complete occurrence supplies the needed coverage. A different unsupported full-span pattern is not automatically an identity-only repair. Related issues may share the same necessary scalar/list identity target; one effective change can satisfy those shared targets. Do not plan duplicate appended occurrences for the same correction: append targets across issue rows require distinct effective additions. These are structural distinctions, not linguistic approval or permission to change an unrelated tap."
LEGACY_DEPENDENCY_CLOSURE_GUIDANCE_V2 = "For each issue, inspect semantic dependencies through the exact observed tap surface before choosing targets. If changing a lemma, lexical identity, or form analysis for an inflected/derived surface, include only the dependent semantic field/list operations needed to keep its complete chain valid through that exact surface, with matching grammar links/overlays and the representation's form-audit membership when those fields exist. First distinguish an identity-only correction on a retained complete form step from an actual step removal or occurrence-shape change. For an identity-only correction, preserve valid geometry and context while handling its exact dependent links. Compare the intended corrected identity against same_anchor_complete_occurrences in the structural packet before choosing update versus removal. If a source-exact complete occurrence at that anchor already has the corrected identity, do not also change the compatibility direct row to that identity: it would duplicate the anchor/lesson pair. Plan removal of that redundant direct row, preserving the existing complete occurrence. If no such complete occurrence supplies the corrected identity, a supported retained stage can require updating its matching direct identity. These are structural alternatives, not a decision about which identity is linguistically correct. Before actually removing a form step, compare its grammar identity with occurrence links on that tap. If that step is the sole support for a direct grammar link (a link with empty display fields and the representation's direct-link range sentinel), removing it can make the remaining link invalid. Include that affected occurrence row in the SAME issue's plan. If the stage is retained and only its identity changes, target the direct entry_id field together with that identity. When the step actually must be removed or the occurrence shape changes, remove the unsupported direct row and append a corrected source-exact complete overlay using the representation-specific construction contract, or remove it only when another retained complete occurrence already explains that same lesson/construction. Do not mutate its protected source-projected range or display fields in place; do not leave an unsupported direct link behind or assume the patch may edit outside the declared plan. Removing an incorrect step or link is not sufficient when the remaining chain then fails to explain the observed surface; add or replace the supported semantic rows needed for the corrected analysis. Do not alter primary source/tap text, boundaries, or ranges. Contrast: a wrong occurrence gloss on an already-valid chain should target only that meaning field; removing the sole step that supports a direct lesson may also require a planned overlay-row conversion, while a separate unchanged same-identity step can continue to support a direct link; an existing complete occurrence may justify removing a redundant unsupported direct row, but does not make that empty direct row valid; a changed lemma/identity for an inflected surface may also require corrected ordered stages and their linked lessons. Inspect the supplied grammar knowledge and identity policy: reuse an exact approved entry when it fits; use a provisional/draft identity only when the language policy explicitly permits it, and leave it subject to the normal independent review. Never present a draft as approved, and never invent a lexical ID or lesson to complete the chain."

REPAIR_EXPLANATION_GUIDANCE = """Apply the same complete-form and meaning-scope criteria to your planned corrections and written patch as to independent review. Inspect comparable occurrences of each reported defect throughout this candidate and include all actually affected semantic targets in that issue's plan; leave correct contrasting occurrences unchanged. A finding is evidence to investigate, not permission to replace a defensible meaning with a stylistic preference. Use the supplied lessons, predicate frame and source timeline to support each correction.

For a finding about a form step or a multi-tap construction, inspect the related meaning fields together: the segment's occurrence `meaning_en`, each relevant complete `form_steps[*].meaning_en`, adjacent lexical/auxiliary taps in the exact construction span, and the linked grammar occurrence's meaning/context. Decide the scope of each field separately from its schema and supplied evidence. If a supported construction-level meaning was copied into more than one component field, include every field that actually needs correction in the same issue plan, even when the reviewer quoted only one of them. Do not assume the fields should contain identical English, or that every component must receive a different gloss; retain valid contextual translations and form-stage meanings when the evidence supports them. Do not invent a component meaning just to make the fields look different.

When an issue includes host-verified `path_history`, inspect the earlier finding dispositions, reasons, and exact values for those overlapping fields. A `bound: true` row is tied to its declared affected path; an unbound legacy row is context only, not a binding on that field. Do not restore a value previously rejected or corrected on the same field merely to satisfy a later wording preference or a broader construction gloss. Preserve the distinction between a component form and any overlapping complete construction. If the supplied evidence now supports a legitimate rollback, explicitly identify the earlier finding and explain what new evidence changes that conclusion in the plan reason; this is an evidence requirement, not a ban on returning to an earlier value.

Learner-facing explanations must positively explain the actual form or grammatical contribution. Do not copy reviewer objections, editorial uncertainty, or defensive denials into occurrence notes or meanings. A useful grammatical contrast is allowed when it explains the form itself; rebutting one reviewer's classification is not a learner explanation. Keep a complete form's meaning separate from its form label and passage-specific grammatical role. Do not import a larger construction's meaning into one component stage, concatenate component glosses, or invent contributions to satisfy a review."""

REPAIR_EXPLANATION_GUIDANCE_VERSION = 4
SCHEMATIC_SOURCE_MEANING_GUIDANCE = """A dictionary construction explanation may use variables such as a person saying something while performing an action. These describe the construction, not additional events or words asserted by each occurrence. Ground the meaning of each field in its exact complete source form and represented scope. Do not paste schematic participants, actions, or placeholders into an occurrence or form-stage meaning. A connective or participial reporting rendering can already express the relevant relation to the following clause; a neighboring proposition-only stage may still omit that contribution. Judge those fields separately. Expand an issue's targets only after showing which contribution is actually missing from each additional field, with source or lesson evidence. Name a concrete simultaneous action only when the source attests it and it belongs to the field's represented span; do not import the following clause's action into a smaller tap. Preserve a correct contrasting meaning even when its wording differs from the revised stage. This guidance does not approve a candidate or prescribe an English phrase."""
REPAIR_EXPLANATION_GUIDANCE += '\n\n' + SCHEMATIC_SOURCE_MEANING_GUIDANCE
RETAINED_STAGE_LINK_GUIDANCE = """Distinguish changing an identity on a retained, complete form stage from removing that stage or changing the supported occurrence shape. When the complete form and direct occurrence geometry are retained and the grammar identity is wrong, plan the exact identity-array change together with the matching direct link's entry_id field. A simultaneous, separately supported correction to its complete-form meaning does not itself require changing the occurrence shape. Preserve its source span and context unless a separate supported finding identifies a defect there. Do not remove and recreate that direct row as a displayed wider construction merely to change its lesson identity. In Korean, validate occurrence shape independently of whether its lesson also appears in the anchor tap’s form chain. A direct occurrence uses direct-link fields. A complete displayed occurrence must reconstruct its whole recorded source range and have a supported complete meaning. The same formation lesson may explain a tap-local stage and a larger ranged occurrence without making their complete forms or meanings identical. Preserve an existing correct complete ranged occurrence when changing the local stage identity; if updating a redundant compatibility direct row would duplicate that occurrence’s anchor and lesson, plan removal of that direct row instead. Do not add another duplicate occurrence or expand a word-form stage to the whole phrase. If the stage actually must be removed or the supported occurrence shape must change, use the planned remove and source-grounded append operations, or remove a redundant direct row when another complete occurrence supplies the needed coverage. A different unsupported full-span pattern is not automatically an identity-only repair. Related issues may share the same necessary scalar/list identity target; one effective change can satisfy those shared targets. Do not plan duplicate appended occurrences for the same correction: append targets across issue rows require distinct effective additions. These are structural distinctions, not linguistic approval or permission to change an unrelated tap."""
REPAIR_EXPLANATION_GUIDANCE += '\n\n' + RETAINED_STAGE_LINK_GUIDANCE


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

DEPENDENCY_CLOSURE_GUIDANCE = """For each issue, inspect semantic dependencies through the exact observed tap surface before choosing targets. If changing a lemma, lexical identity, or form analysis for an inflected/derived surface, include only the dependent semantic field/list operations needed to keep its complete chain valid through that exact surface, with matching grammar links/overlays and the representation's form-audit membership when those fields exist. First distinguish an identity-only correction on a retained complete form step from an actual step removal or occurrence-shape change. For an identity-only correction, preserve valid geometry and context while handling its exact dependent links. Compare the intended corrected identity against same_anchor_complete_occurrences in the structural packet before choosing update versus removal. If a source-exact complete occurrence at that anchor already has the corrected identity, do not also change the compatibility direct row to that identity: it would duplicate the anchor/lesson pair. Plan removal of that redundant direct row, preserving the existing complete occurrence. If no such complete occurrence supplies the corrected identity, a supported retained stage can require updating its matching direct identity. These are structural alternatives, not a decision about which identity is linguistically correct. Before actually removing a form step, compare its grammar identity with occurrence links on that tap. If that step is the sole support for a direct grammar link (a link with empty display fields and the representation's direct-link range sentinel), removing it can make the remaining link invalid. Include that affected occurrence row in the SAME issue's plan. If the stage is retained and its identity changes, include the matching direct entry_id field only when the resulting occurrence remains distinct and valid. If an existing source-exact complete occurrence at that anchor already supplies the corrected lesson, removing the redundant compatibility direct row can provide dependency closure while preserving the complete occurrence byte-for-byte. When the step actually must be removed or the occurrence shape changes, remove the unsupported direct row and append a corrected source-exact complete overlay using the representation-specific construction contract, or remove it only when another retained complete occurrence already explains that same lesson/construction. Do not mutate its protected source-projected range or display fields in place; do not leave an unsupported direct link behind or assume the patch may edit outside the declared plan. Removing an incorrect step or link is not sufficient when the remaining chain then fails to explain the observed surface; add or replace the supported semantic rows needed for the corrected analysis. Do not alter primary source/tap text, boundaries, or ranges. Contrast: a wrong occurrence gloss on an already-valid chain should target only that meaning field; removing the sole step that supports a direct lesson may also require a planned overlay-row conversion, while a separate unchanged same-identity step can continue to support a direct link; an existing complete occurrence may justify removing a redundant unsupported direct row, but does not make that empty direct row valid; a changed lemma/identity for an inflected surface may also require corrected ordered stages and their linked lessons. Inspect the supplied grammar knowledge and identity policy: reuse an exact approved entry when it fits; use a provisional/draft identity only when the language policy explicitly permits it, and leave it subject to the normal independent review. Never present a draft as approved, and never invent a lexical ID or lesson to complete the chain."""
REPAIR_DEPENDENCY_GUIDANCE_VERSION = 4
REPAIR_DEPENDENCY_GUIDANCE = """Inspect repair_dependency_constraints before submitting the plan. They record structural relationships in the immutable candidate, not additional linguistic findings or permission to change unrelated fields. If an explicit stage identity change or removal takes away the sole retained stage support for an empty direct grammar link, include that exact dependent link in the SAME issue's targets. A retained same-identity stage can continue supporting that row; a separate complete occurrence may justify removing a redundant direct row, but cannot make an unsupported empty direct row valid. Prefer exact meaning leaves for meaning-only changes rather than ambiguous identity-bearing ancestor replacements. For a retained complete stage identity-only correction, inspect the supplied same-anchor complete occurrences and their explicit lesson IDs. Pair the identity target with a direct entry_id update only when that will remain a distinct valid anchor/lesson occurrence. If the intended corrected lesson is already supplied by an existing complete occurrence at that anchor, target removal of the redundant compatibility direct row in the same issue instead; preserve the complete occurrence byte-for-byte. Do not convert a direct row into a displayed wider occurrence or append a duplicate. A complete occurrence at another anchor or with another lesson is not that same-identity coverage. Establish the intended identity from reviewed evidence, not from the mere presence of a structural alternative. The plan does not prescribe a replacement identity. Establish it from reviewed evidence, and ensure the derived result has the matching occurrence linkage required by its actual representation and full gate. These constraints do not import Korean relationships into Chinese or Japanese schemas."""

REPRESENTATION_STRUCTURE_GUIDANCE = """Use `representation_structure_contract` from INPUT when it is present, together with the candidate gate and supplied language schema. Respect every declared item cardinality exactly. If a grammar-identity array is constrained to one identity per form step, never put several identities on one step: represent separate ordered transformations as separate complete-form steps only when the exact surface, references, and candidate gate support those stages. A step's meaning describes its complete form; keep politeness/form information separate, and never create a bare-stem stage just to satisfy cardinality. If an existing step and a linked lesson describe different transformations, do not replace that step's identity alone; plan the supported dependent stage and matching link edits required by the gate. If the contract is absent, follow the current language's schema and policy without importing another language's cardinality. Do not invent stages or meanings to satisfy a guessed rule."""
SOURCE_TAP_PROJECTION_GUIDANCE = """Keep primary source/tap fields unchanged: segment text/surface, source offsets, and all primary tap boundaries. Do not mutate source-projected fields of a grammar occurrence row in place (for example grammar-link segment/range indices and display_form, or grammar-overlay start/end and text/surface/component spans). An empty Korean grammar-link display_form with display_end_segment_index -1 is a direct lesson link; do not turn it into a displayed source span in place. A grounded correction may instead remove an incorrect grammar occurrence row and append a corrected row at the same semantic-list path, with its complete span and any component spans exactly supported by unchanged source segments and the representation contract. This is a semantic overlay replacement, not source resegmentation; retain all unaffected rows and rerun the complete candidate gate. Do not invent a construction or its meaning. Use boundary_change_needed only when primary source text, tap surface/boundaries, or source segmentation itself must change, or when the required overlay cannot be expressed safely by allowed semantic row operations."""
ENDING_TAP_CONSTRUCTION_GUIDANCE = """When a grammatical ending is tapped separately from an adjacent lexical word or name, do not use the ending's grammar identity as the lexical root of a word-form chain. Keep the direct ending lesson linked to its ending tap. If the reviewed analysis requires a complete construction spanning the lexical/name tap and ending, represent that construction as a source-exact grammar occurrence anchored at the first included lexical/name tap, using the representation-specific `construction_occurrence_contract` in INPUT; preserve every tap and the direct ending lesson. This applies to supported noun/name + predication and lexical-verb + auxiliary constructions across distinct lesson identities. Use only the supplied attested lexical identity and independently supported grammar identities; do not add a copula-ID exception or invent a word/lesson. Contrast: a valid full-span occurrence may include the ending tap while the direct ending lesson remains on that ending; using a grammar-kind tap as a lexical form-chain root is invalid. The complete displayed form must exactly match the included source span and its complete meaning must be supported by the source and lessons."""


def _legacy_representation_structure_contract(representation: str) -> dict[str, Any] | None:
    """Expose only structural limits declared by the selected annotation contract."""
    if representation not in {"korean-flat", "korean-v4"}:
        return None
    from pipeline import korean_contracts

    identities = korean_contracts.STEP["properties"]["grammar_entry_ids"]
    minimum, maximum = identities.get("minItems"), identities.get("maxItems")
    if minimum is None and maximum is None:
        return None
    return {
        "source": "pipeline.korean_contracts.STEP",
        "path": "/segments/*/form_steps/*/grammar_entry_ids",
        "min_items": minimum,
        "max_items": maximum,
        "meaning": "The constraint applies within each form step, not across the ordered form-step chain.",
        "stage_link_identity_rule": {
            "source": "pipeline.korean_dictionary.build_assets",
            "relation": "link.entry_id is in its owner's retained form_steps[*].grammar_entry_ids",
            "when_staged": "Use direct occurrence fields and the representation-specific sentinel; displayed construction fields are forbidden.",
            "when_not_staged": "On a form-analyzed owner, a different construction identity needs a source-exact complete occurrence; supplied display fields must satisfy that occurrence contract. An independent direct ending/lesson tap without a form chain remains direct under the candidate gate. Remove only genuinely unsupported or redundant rows.",
            "identity_only_repair": "Retain the complete stage and valid direct geometry; change the stage identity and matching link entry_id together.",
        },
    }


def _legacy_construction_occurrence_contract(representation: str) -> dict[str, Any] | None:
    """Describe source-grounded grammar occurrence placement for each schema."""
    contracts = {
        "chinese-fixed": {
            "occurrence_list": "/grammar_overlays", "anchor": "start Unicode offset",
            "end": "exclusive end Unicode offset", "surface": "text",
            "source_basis": "exact source text span; offsets are source offsets",
            "ending_link": "retain any direct ending/particle annotation on its own tap",
        },
        "chinese-annotation": {
            "occurrence_list": "/grammar_overlays", "anchor": "start Unicode offset",
            "end": "exclusive end Unicode offset", "surface": "text",
            "source_basis": "exact source text span; offsets are source offsets",
            "ending_link": "retain any direct ending/particle annotation on its own tap",
        },
        "japanese-annotation": {
            "occurrence_list": "/grammar_overlays", "anchor": "start Unicode offset",
            "end": "exclusive end Unicode offset", "surface": "surface",
            "source_basis": "exact source text span; overlay component spans are relative to that surface",
            "ending_link": "retain the lexical/name segment and the ending/auxiliary segment as separate taps",
        },
        "korean-flat": {
            "occurrence_list": "/grammar_links", "anchor": "segment_index of first included lexical/name tap",
            "end": "inclusive display_end_segment_index", "surface": "display_form",
            "source_basis": "concatenation of exact unchanged source taps from anchor through end",
            "ending_link": "retain a direct lesson link on its ending tap with empty display fields and end index -1",
        },
        "korean-v4": {
            "occurrence_list": "grammar_links within the anchor segment", "anchor": "source_start of first included lexical/name tap",
            "end": "exclusive source_end of the complete construction", "surface": "source-exact display form",
            "source_basis": "exact unchanged source offsets across included taps",
            "ending_link": "retain the direct ending lesson on its own tap with source offsets (-1, -1)",
        },
    }
    contract = contracts.get(representation)
    if representation in {"korean-flat", "korean-v4"}:
        contract = {**contract, "staged_identity_occurrence": {
            "rule": "A link whose entry_id is a retained stage identity on its owner must be direct, never displayed.",
            "direct_fields": ({"display_form": "", "display_meaning_en": "", "display_end_segment_index": -1}
                if representation == "korean-flat" else
                {"source_start": -1, "source_end": -1, "display_meaning_en": ""}),
            "identity_field": "entry_id",
        }}
    return contract


def _representation_structure_contract(representation: str, *, occurrence_policy_version: int = 2):
    if type(occurrence_policy_version) is not int or occurrence_policy_version not in (1, 2):
        raise ValueError("Unknown stage occurrence policy")
    contract = _legacy_representation_structure_contract(representation)
    if contract is None or occurrence_policy_version == 1:
        return contract
    contract["stage_link_identity_rule"]["when_staged"] = "Direct occurrences use direct fields. Complete displayed occurrences independently require exact full source reconstruction and supported whole meaning; the same lesson may occur in the anchor form chain."
    contract["stage_link_identity_rule"]["identity_only_repair"] = "Retain valid geometry. Update a needed matching direct identity, or remove its redundant compatibility row when an existing complete occurrence already supplies the corrected lesson at that anchor. Preserve that complete occurrence."
    return contract


def _construction_occurrence_contract(representation: str, *, occurrence_policy_version: int = 2):
    if type(occurrence_policy_version) is not int or occurrence_policy_version not in (1, 2):
        raise ValueError("Unknown stage occurrence policy")
    contract = _legacy_construction_occurrence_contract(representation)
    if contract is None or occurrence_policy_version == 1 or "staged_identity_occurrence" not in contract:
        return contract
    contract["staged_identity_occurrence"]["rule"] = "Occurrence display shape is independent of anchor stage identity. Direct fields use the sentinel; complete displayed occurrences require exact source range and whole meaning. Reject partial displays and duplicate anchor/lesson occurrences."
    return contract


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


def _exhausted_derived_submission(run_dir: Path, patch_job: str, error: Exception) -> dict[str, Any] | None:
    """Return a final corrected patch only for two exhausted derived-gate rejections."""
    from pipeline.worker_workspace import CandidateSubmissionError

    if not isinstance(error, CandidateSubmissionError) or error.category != "bounded_repair_rejected":
        return None
    try:
        recovery = _read_json(_safe_job_path(run_dir, patch_job) / "submission-recovery.json")
    except (OSError, ValueError, KeyError, TypeError):
        return None
    initial = recovery.get("initial_rejection", {})
    final = recovery.get("repair_rejection", {})
    raw = error.artifact_bytes
    if (recovery.get("status") != "rejected"
            or initial.get("category") != "derived_annotation_rejection"
            or final.get("category") != "derived_annotation_rejection"
            or not isinstance(raw, bytes)
            or hashlib.sha256(raw).hexdigest() != final.get("artifact_sha256")):
        return None
    try:
        patch = json.loads(raw)
        validate(patch, PATCH_SCHEMA)
    except (UnicodeError, json.JSONDecodeError, ValidationError, TypeError, ValueError):
        return None
    return {"patch": patch, "recovery": recovery,
            "runner_diagnostic": final.get("diagnostic", ""),
            "artifact_sha256": hashlib.sha256(raw).hexdigest()}


def _safe_job_path(run_dir: Path, job: str) -> Path:
    path = Path(job)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"invalid annotation repair job reference: {job!r}")
    result = (run_dir / "agents" / path).resolve()
    if not result.is_relative_to((run_dir / "agents").resolve()):
        raise ValueError("annotation repair evidence path escaped the run directory")
    return result


def _schemas(run_dir: Path, *, candidate=None, job=None) -> tuple[Path, Path]:
    if candidate is None:
        plan_path = run_dir / "annotation-repair-plan.schema.json"
        patch_path = run_dir / "annotation-semantic-patch.schema.json"
        patch_schema = PATCH_SCHEMA
    else:
        directory = _safe_job_path(run_dir, job)
        plan_path = directory / "request-plan-v2.schema.json"
        patch_path = directory / "request-patch-v3.schema.json"
        patch_schema = deepcopy(PATCH_SCHEMA)
        patch_schema['properties']['base_digest'] = {'type': 'string', 'const': candidate_digest(candidate)}
    _write_json(plan_path, PLAN_SCHEMA)
    _write_json(patch_path, patch_schema)
    return plan_path, patch_path


def _validate_repair_context(language: str, representation: str,
                             context: dict[str, Any] | None) -> None:
    """Fail before model work if a language's full local gate cannot run."""
    korean_representation = representation in {"korean-flat", "korean-v4"}
    if not korean_representation and language != "ko":
        return
    if language != "ko" or not korean_representation:
        raise ValueError("Korean repair language and representation must match")
    value = context.get("candidate_gate") if isinstance(context, dict) else None
    required = ("focus", "title", "number", "plan", "level", "source_id")
    if not isinstance(value, dict):
        raise ValueError("Korean semantic repair requires candidate_gate before planning")
    missing = [key for key in required if key not in value]
    if missing:
        raise ValueError("Korean candidate_gate is missing required fields: " + ", ".join(missing))
    if not isinstance(value["focus"], dict):
        raise ValueError("Korean candidate_gate.focus must be an object")
    if not isinstance(value["title"], str) or not value["title"].strip():
        raise ValueError("Korean candidate_gate.title must be nonempty text")
    if type(value["number"]) is not int or value["number"] < 1:
        raise ValueError("Korean candidate_gate.number must be a positive integer")
    if not isinstance(value["plan"], dict):
        raise ValueError("Korean candidate_gate.plan must be an object")
    if type(value["level"]) is not int or value["level"] not in range(1, 7):
        raise ValueError("Korean candidate_gate.level must be an integer from 1 to 6")
    if not isinstance(value["source_id"], str) or not value["source_id"].strip():
        raise ValueError("Korean candidate_gate.source_id must be nonempty text")


def _validate_plan(plan: Any, issue_count: int, candidate: Any = None,
                   representation: str | None = None, *, target_contract_version: int = 1,
                   dependency_constraints=None, target_authority=None) -> None:
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
    if candidate is not None and representation is not None:
        validate_target_contract(candidate, _targets(plan), representation=representation)
        if target_contract_version in (2, 3):
            from pipeline.annotation_repair_dependencies import validate_plan_dependencies
            validate_plan_dependencies(plan, candidate, representation, dependency_constraints)
        if target_contract_version == 3:
            if not isinstance(target_authority, dict):
                raise ValueError('v3 annotation plan requires immutable target authority')
            from pipeline.annotation_repair_authority import validate_plan_authority
            validate_plan_authority(plan, target_authority)
        elif target_contract_version not in (1, 2):
            raise ValueError('Unsupported annotation plan target contract version')


def _validate_v3_effects(before, after, plan, authority, target_contract_version, edits):
    if target_contract_version == 3:
        from pipeline.annotation_repair_authority import validate_derived_effects
        validate_derived_effects(before, after, plan, authority, edits)
    return after


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
    meta["repair_request_policy_version"] = 3
    meta["complete_stage_instruction_policy_version"] = 2
    meta.setdefault("plan_target_contract_version", 2)
    meta.setdefault("target_contract_version", 1)
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
    _validate_repair_context(language, representation, context)
    run_dir = Path(harness.run_dir)
    if isinstance(context, dict) and context.get('reviewed_annotation_research') is not None:
        from pipeline.annotation_reference_carry import bind_carried_research, CARRIED_RESEARCH_GUIDANCE
        if not isinstance(context.get('chunk_text'), str):
            raise ValueError('Carried research repair requires the exact chunk_text')
        bind_carried_research(run_dir, context['reviewed_annotation_research'], candidate=candidate,
            source_text=context['chunk_text'], language=language, representation=representation, context=context)
        context = {**context, 'carried_research_guidance': CARRIED_RESEARCH_GUIDANCE}
    if isinstance(context, dict) and context.get('reviewed_run_lessons'):
        from pipeline.annotation_run_lessons import validate_run_lessons, run_lesson_guidance
        bound_run_lessons = validate_run_lessons(run_dir, context['reviewed_run_lessons'], candidate=candidate,
            source_text=context.get('chunk_text'), language=language, representation=representation, context=context)
        context = {**context, 'reviewed_run_grammar': bound_run_lessons['lessons'], 'reviewed_run_lesson_guidance': run_lesson_guidance(context['reviewed_run_lessons']),
            'annotation_run_lesson_validation': {'run_dir': str(run_dir.resolve()), 'candidate': candidate,
                'source_text': context.get('chunk_text'), 'language': language, 'representation': representation,
                'envelope': bound_run_lessons['packet'], 'lessons': bound_run_lessons['lessons'], 'context': context}}
    from pipeline.annotation_ranged_link_guidance import FIELD, with_policy, contextual_guidance
    contextual_guidance(context or {}, representation)
    plan_schema_path, patch_schema_path = _schemas(run_dir, candidate=candidate, job=job)
    from pipeline.annotation_repair_dependencies import repair_dependency_constraints
    dependency_constraints = repair_dependency_constraints(candidate, representation)
    plan_job, patch_job, assembly_job = f"{job}_plan", f"{job}_patch", f"{job}_assembly"
    from pipeline.annotation_repair_authority import build_authority_packet
    adjudication_bound = (context or {}).get("prior_review_adjudication") is not None
    if adjudication_bound and any(not isinstance(issue, dict) or "paths" not in issue
                                  or "target_dispositions" not in issue for issue in issues):
        raise ValueError("adjudication repairs require host-verified actionable path dispositions")
    typed_flags = [isinstance(issue, dict) and
        ("paths" in issue or "candidate_paths" in issue or "target_dispositions" in issue)
        for issue in issues]
    if any(typed_flags) and not all(typed_flags):
        raise ValueError("repair issue set mixes typed defect findings with untyped diagnostics")
    if all(typed_flags):
        # Typed requests fail closed: invalid pointers/dispositions never downgrade to v2.
        target_authority = build_authority_packet(issues, candidate, representation, candidate_digest(candidate))
        plan_target_contract_version = 3
    else:
        # Only the existing Korean deterministic-gate route is allowed to retain
        # untyped diagnostics; new review/adjudication requests fail closed.
        if ((context or {}).get("repair_issue_origin") != "deterministic_gate"
                or (context or {}).get("prior_review_adjudication") is not None):
            raise ValueError("untyped repair findings require an explicit deterministic_gate origin; adjudication must remain typed")
        target_authority = None
        plan_target_contract_version = 2
    shared_context = {**(context or {}), "repair_explanation_guidance_version": REPAIR_EXPLANATION_GUIDANCE_VERSION,
                      "repair_dependency_guidance_version": REPAIR_DEPENDENCY_GUIDANCE_VERSION, "stage_occurrence_policy_version": 2, "complete_stage_instruction_policy_version": 2, "language": language,
                      "representation": representation, "candidate": candidate,
                      "repair_dependency_constraints": dependency_constraints,
                      "issues": issues,
                      "representation_structure_contract":
                          _representation_structure_contract(representation),
                      "construction_occurrence_contract":
                          _construction_occurrence_contract(representation)}
    if target_authority is not None:
        shared_context["repair_target_authority"] = target_authority
    else:
        shared_context["repair_scope_policy"] = "legacy-korean-deterministic-gate-v2"
    plan_context = {**shared_context,
                    "annotation_plan_validation": {"issue_count": len(issues),
                        "target_contract_version": plan_target_contract_version}}
    projected_row_guidance = ""
    if (target_authority or {}).get("projected_row_authority_policy_version", 1) >= 2:
        projected_row_guidance = """For a `row_replacement_authorities` entry, its listed `target_paths` are all explicit defects on the same derived row. Resolve those fields together with exactly the listed `remove_row` + `append_row` pair for that row. Do not add overlapping `set_field` targets. Preserve every unlisted row field, all sibling rows and their order, primary taps, and nested source components. A supporting path is not a repair target."""
    plan_prompt = f"""Return JSON matching the supplied repair-plan schema. Build a narrow diagnosis plan for every supplied independent review issue. `issue_index` must cover each input issue exactly once, in order. For each issue, give a concrete reason and exact JSON-pointer target(s) using only supported operations. The plan is diagnosis, never approval. Prefer a semantic field or explicit semantic-list row operation over changing a complete annotation.

{REPAIR_EXPLANATION_GUIDANCE}

{FORM_STAGE_EVIDENCE_GUIDANCE}

{contextual_guidance(shared_context, representation)}

{DEPENDENCY_CLOSURE_GUIDANCE}

{REPAIR_DEPENDENCY_GUIDANCE}

{REPRESENTATION_STRUCTURE_GUIDANCE}

{ENDING_TAP_CONSTRUCTION_GUIDANCE}

{SOURCE_TAP_PROJECTION_GUIDANCE}

{projected_row_guidance}

Use the operation/path contract exactly: `set_field` is only for an existing JSON scalar; use `replace_list` for an existing semantic array (including Korean form-step `grammar_entry_ids`), and `replace_row` for an existing object row. `append_row` targets the list path itself (for example `/grammar_links` or `/segments/4/form_steps`); the patch must use that exact path and an object value. `remove_row` targets one existing numeric row path. Never add a guessed numeric suffix to `append_row`.

The issues array contains exactly {len(issues)} findings. Use exactly the indices {list(range(len(issues)))} in that order, one row per array item. An item can describe several defects: put all its necessary targets in that same row. Do not split subpoints into additional issue indices or count repeated references as new findings. Run the supplied local validation command; it checks this mapping as well as the JSON schema.

If resolving an issue requires changing source text, a primary tap surface, a primary source range/index, or the segmentation, mark `boundary_change_needed: true`, give `boundary_reason`, and provide no targets for that issue. Never route such a change through semantic targets. Replacing a wrong derived grammar-occurrence row with a source-grounded full-span row is allowed only through the explicit remove/append semantic-list operations described above; it does not change primary taps. Otherwise set boundary_change_needed false and leave boundary_reason empty. Scope each target to the smallest existing semantic field or list row needed. Do not target unrelated rows. The caller will check these targets against a representation-specific source/tap contract.

INPUT:
{json.dumps(plan_context, ensure_ascii=False, indent=2)}"""
    harness_args = getattr(harness, "args", None)
    selected_effort = effort or getattr(harness_args, "annotation_repair_effort", "low")
    selected_refresh = (refresh if refresh is not None else
                        getattr(harness_args, "refresh", False))
    plan = await harness.runner.call(plan_job, plan_prompt, plan_schema_path, selected_effort,
        refresh=selected_refresh, workspace_context=plan_context)
    validate(plan, PLAN_SCHEMA)
    _validate_plan(plan, len(issues), candidate, representation,
        target_contract_version=plan_target_contract_version,
        dependency_constraints=dependency_constraints, target_authority=target_authority)
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
        if FIELD in shared_context:meta[FIELD] = shared_context[FIELD]
        meta["plan_target_contract_version"] = plan_target_contract_version
        if target_authority is not None: meta["repair_target_authority"] = target_authority
        _save_assembly(run_dir, assembly_job, meta, deepcopy(candidate))
        return {"status": "boundary_change_needed", "candidate": deepcopy(candidate),
                "evidence": _evidence_result(run_dir, assembly_job, meta), "plan": plan}

    allowed = _targets(plan)
    patch_validation = {**(context or {}), "base_candidate": candidate,
        "representation": representation, "allowed_targets": allowed,
        "language": language, "issues": issues, "repair_plan": plan,
        "target_contract_version": plan_target_contract_version,
        "repair_target_authority": target_authority,
        "request_binding_policy_version": 3}
    patch_context = {**shared_context, "repair_plan": plan,
                     "allowed_targets": allowed, "base_digest": base_digest,
                     "annotation_patch_validation": patch_validation}
    patch_prompt = f"""Return JSON matching the supplied semantic-patch schema. Repair only the supplied prior candidate. Return exactly `base_digest` and `edits`; never return a replacement candidate. Use only the exact op/path pairs in ALLOWED_TARGETS, and make each edit resolve its mapped review issue. Apply every planned target with an effective value/list change; an enclosing `replace_row` or `replace_list` counts only when the exact planned descendant value changes. One append edit cannot satisfy two issue rows that each plan an append. Do not submit an empty or partial patch. Preserve all unedited data and all primary source/tap surfaces, boundaries, and source ranges. The only permitted change to a derived grammar-occurrence span is the planned remove+append replacement of its row with a complete span grounded in unchanged source; never mutate projected span fields in place. `append_row` uses the exact list path named by the plan, without a row-index suffix; `remove_row` uses one existing numeric row path. Use an explicit append/remove operation only when the repair plan names that exact list operation. The local caller applies the patch and runs the full deterministic language gate; independent review still follows.

{projected_row_guidance}

{SOURCE_TAP_PROJECTION_GUIDANCE}

{ENDING_TAP_CONSTRUCTION_GUIDANCE}

{REPAIR_EXPLANATION_GUIDANCE}

{FORM_STAGE_EVIDENCE_GUIDANCE}

{contextual_guidance(shared_context, representation)}

INPUT:
{json.dumps(patch_context, ensure_ascii=False, indent=2)}"""
    replan_lineage = None
    try:
        patch = await harness.runner.call(patch_job, patch_prompt, patch_schema_path, selected_effort,
            refresh=selected_refresh, workspace_context=patch_context)
    except Exception as runner_error:
        exhausted = _exhausted_derived_submission(run_dir, patch_job, runner_error)
        if exhausted is None:
            raise

        # One bounded replan is allowed only after the shared runner's own
        # correction also fails the derived-candidate gate.  It still edits
        # the immutable original base and must pass the same independent gate.
        failed_patch = exhausted["patch"]
        try:
            failed_candidate = apply_edits(candidate, failed_patch, allowed_targets=allowed,
                                           representation=representation)
            _validate_v3_effects(candidate, failed_candidate, plan, target_authority,
                                 plan_target_contract_version, failed_patch["edits"])
        except AnnotationEditError:
            # A recorded gate rejection that cannot be reproduced as an
            # in-scope patch is not eligible for a semantic replan.
            raise runner_error
        try:
            validate_candidate(failed_candidate)
        except (ValueError, TypeError, KeyError, IndexError, ValidationError) as gate_error:
            marker = "Submitted annotation patch failed derived-candidate validation: "
            recorded = exhausted["runner_diagnostic"]
            expected = recorded.rsplit(marker, 1)[-1] if marker in recorded else None
            if expected is None or expected != str(gate_error):
                # A current registry/catalog may have changed since the worker
                # rejection. Do not launch a replan unless this exact gate
                # failure is reproduced now against the recorded base.
                raise runner_error
            reproduced_gate_diagnostic = str(gate_error)
        else:
            # The old gate outcome may have healed as evidence/catalogs changed.
            # Keep the historical failure, but do not spend a replan on it.
            raise runner_error
        failed_patch_digest = digest(failed_patch)
        first_attempt = {
            "first_plan_job": plan_job, "first_plan_digest": plan_digest,
            "first_patch_job": patch_job, "first_patch_digest": failed_patch_digest,
            "failed_patch": deepcopy(failed_patch),
            "failed_candidate": deepcopy(failed_candidate),
            "failed_candidate_digest": candidate_digest(failed_candidate),
            "runner_recovery": exhausted["recovery"],
            "runner_diagnostic": exhausted["runner_diagnostic"],
            "reproduced_gate_diagnostic": reproduced_gate_diagnostic,
            "failed_artifact_sha256": exhausted["artifact_sha256"],
        }
        if plan_target_contract_version == 3:
            # A gate rejection is context for the same authenticated findings,
            # never a new source of semantic target authority.
            replan_issues = deepcopy(issues)
        else:
            replan_issues = [*deepcopy(issues), {
                "problem": "derived_annotation_rejection",
                "explanation": exhausted["runner_diagnostic"],
                "failed_patch": failed_patch,
                "failed_patch_digest": failed_patch_digest,
                "action": "Revise the repair diagnosis/targets so the resulting full candidate passes the same deterministic annotation gate.",
            }]
        replan_job = f"{job}_replan"
        replan_plan_job, replan_patch_job = f"{replan_job}_plan", f"{replan_job}_patch"
        replan_plan_context = {**shared_context, "issues": replan_issues,
            "repair_history": first_attempt,
            "annotation_plan_validation": {"issue_count": len(replan_issues),
                "target_contract_version": plan_target_contract_version}}
        replan_plan_prompt = f"""Return JSON matching the supplied repair-plan schema. This is the single bounded replan after the first semantic patch and the shared runner's bounded submission correction both failed the deterministic derived-candidate gate. Re-diagnose the supplied original review findings together with the exact gate diagnostic. You may change the diagnosis/targets, but do not change the immutable base candidate. The replan is not approval; the caller will run the same full deterministic gate and independent review.

{projected_row_guidance}

{DEPENDENCY_CLOSURE_GUIDANCE}

{REPAIR_DEPENDENCY_GUIDANCE}

{REPRESENTATION_STRUCTURE_GUIDANCE}

{ENDING_TAP_CONSTRUCTION_GUIDANCE}

{SOURCE_TAP_PROJECTION_GUIDANCE}

Use only compatible operations: scalar fields use `set_field`, arrays use `replace_list`, existing object rows use `replace_row`, and existing list paths use `append_row` (the path is the list itself, never a guessed numeric suffix). `remove_row` names an existing numeric row.

Cover exactly {len(replan_issues)} issue indices in order: {list(range(len(replan_issues)))}. If the gate failure cannot be addressed with semantic fields while preserving exact source/tap surfaces and ranges, mark the relevant finding boundary_change_needed and explain why. Do not retry on timeouts, malformed/schema-invalid patches, or source/tap violations; this path is available only for the recorded derived-candidate rejection.

{REPAIR_EXPLANATION_GUIDANCE}

{FORM_STAGE_EVIDENCE_GUIDANCE}

{contextual_guidance(shared_context, representation)}

INPUT:
{json.dumps(replan_plan_context, ensure_ascii=False, indent=2)}"""
        try:
            replan_plan = await harness.runner.call(replan_plan_job, replan_plan_prompt,
                plan_schema_path, selected_effort, refresh=selected_refresh,
                workspace_context=replan_plan_context)
        except Exception as replan_error:
            meta = {"return_code": 0, "kind": "annotation_patch_assembly",
                "status": "patch_rejected", "representation": representation,
                "base": deepcopy(candidate), "base_digest": base_digest,
                "issues": deepcopy(issues), "issue_digest": issue_digest,
                "plan_job": plan_job, "plan_digest": plan_digest,
                "patch_job": patch_job, "patch_digest": failed_patch_digest,
                "replan": {**first_attempt, "issues": replan_issues,
                    "issue_digest": digest(replan_issues),
                    "replan_plan_job": None, "replan_plan_error": str(replan_error),
                    "replan_patch_job": None},
                "patch_error": str(replan_error), "result_digest": base_digest}
            if FIELD in shared_context:meta[FIELD] = shared_context[FIELD]
            meta["plan_target_contract_version"] = plan_target_contract_version
            if target_authority is not None: meta["repair_target_authority"] = target_authority
            _save_assembly(run_dir, assembly_job, meta, deepcopy(candidate))
            return {"status": "patch_rejected", "candidate": deepcopy(candidate),
                    "evidence": _evidence_result(run_dir, assembly_job, meta),
                    "plan": plan, "patch_error": str(replan_error)}
        validate(replan_plan, PLAN_SCHEMA)
        _validate_plan(replan_plan, len(replan_issues), candidate, representation,
            target_contract_version=plan_target_contract_version,
            dependency_constraints=dependency_constraints, target_authority=target_authority)
        replan_plan_digest = digest(replan_plan)
        if any(row["boundary_change_needed"] for row in replan_plan["issues"]):
            meta = {"return_code": 0, "kind": "annotation_patch_assembly",
                "status": "boundary_change_needed", "representation": representation,
                "base": deepcopy(candidate), "base_digest": base_digest,
                "issues": deepcopy(issues), "issue_digest": issue_digest,
                "plan_job": plan_job, "plan_digest": plan_digest,
                "patch_job": patch_job, "patch_digest": failed_patch_digest,
                "replan": {**first_attempt, "issues": replan_issues,
                    "issue_digest": digest(replan_issues),
                    "replan_plan_job": replan_plan_job, "replan_plan_digest": replan_plan_digest,
                    "replan_patch_job": None, "replan_patch_digest": None},
                "boundary_issues": [row["issue_index"] for row in replan_plan["issues"]
                                    if row["boundary_change_needed"]],
                "result_digest": base_digest}
            if FIELD in shared_context:meta[FIELD] = shared_context[FIELD]
            meta["plan_target_contract_version"] = plan_target_contract_version
            if target_authority is not None: meta["repair_target_authority"] = target_authority
            _save_assembly(run_dir, assembly_job, meta, deepcopy(candidate))
            return {"status": "boundary_change_needed", "candidate": deepcopy(candidate),
                    "evidence": _evidence_result(run_dir, assembly_job, meta),
                    "plan": replan_plan}

        replan_allowed = _targets(replan_plan)
        replan_patch_validation = {**(context or {}), "base_candidate": candidate,
            "representation": representation, "allowed_targets": replan_allowed,
            "language": language, "issues": replan_issues, "repair_plan": replan_plan,
            "target_contract_version": plan_target_contract_version,
        "repair_target_authority": target_authority,
        "request_binding_policy_version": 3}
        replan_patch_context = {**shared_context, "issues": replan_issues,
            "repair_plan": replan_plan, "allowed_targets": replan_allowed,
            "base_digest": base_digest, "repair_history": first_attempt,
            "annotation_patch_validation": replan_patch_validation}
        replan_patch_prompt = f"""Return JSON matching the supplied semantic-patch schema. This is the one and only replan after a reproduced derived-candidate gate rejection. Apply edits to the exact ORIGINAL base candidate (base digest below), not to the rejected patch. The rejected patch and diagnostic are included as evidence. Use only the exact op/path pairs in ALLOWED_TARGETS. Effectively apply every planned target; an enclosing row/list replacement only covers a target when its exact descendant value changes, and repeated append targets need repeated append edits. Do not submit an empty or partial patch. Preserve all primary source/tap surfaces, boundaries, source ranges, and every unedited field. The only permitted change to a derived grammar-occurrence span is the planned remove+append replacement of its row with a complete span grounded in unchanged source; never mutate projected span fields in place. `append_row` uses the exact list path named by the plan, without a row-index suffix; `remove_row` uses one existing numeric row path. The caller applies this patch to the original base, runs the same complete deterministic candidate gate, and then obtains independent review; a successful gate is not approval.

{projected_row_guidance}

{SOURCE_TAP_PROJECTION_GUIDANCE}

{ENDING_TAP_CONSTRUCTION_GUIDANCE}

{REPAIR_EXPLANATION_GUIDANCE}

{FORM_STAGE_EVIDENCE_GUIDANCE}

{contextual_guidance(shared_context, representation)}

INPUT:
{json.dumps(replan_patch_context, ensure_ascii=False, indent=2)}"""
        try:
            patch = await harness.runner.call(replan_patch_job, replan_patch_prompt,
                patch_schema_path, selected_effort, refresh=selected_refresh,
                workspace_context=replan_patch_context)
        except Exception as replan_error:
            meta = {"return_code": 0, "kind": "annotation_patch_assembly",
                "status": "patch_rejected", "representation": representation,
                "base": deepcopy(candidate), "base_digest": base_digest,
                "issues": deepcopy(issues), "issue_digest": issue_digest,
                "plan_job": plan_job, "plan_digest": plan_digest,
                "patch_job": patch_job, "patch_digest": failed_patch_digest,
                "replan": {**first_attempt, "issues": replan_issues,
                    "issue_digest": digest(replan_issues),
                    "replan_plan_job": replan_plan_job, "replan_plan_digest": replan_plan_digest,
                    "replan_patch_attempt_job": replan_patch_job,
                    "replan_patch_job": None, "patch_error": str(replan_error)},
                "patch_error": str(replan_error), "result_digest": base_digest}
            if FIELD in shared_context:meta[FIELD] = shared_context[FIELD]
            meta["plan_target_contract_version"] = plan_target_contract_version
            if target_authority is not None: meta["repair_target_authority"] = target_authority
            _save_assembly(run_dir, assembly_job, meta, deepcopy(candidate))
            return {"status": "patch_rejected", "candidate": deepcopy(candidate),
                    "evidence": _evidence_result(run_dir, assembly_job, meta),
                    "plan": replan_plan, "patch_error": str(replan_error)}
        validate(patch, PATCH_SCHEMA)
        # These variables now identify the final accepted/rejected child pair;
        # the first pair remains fully recorded in `replan_lineage`.
        plan, plan_job, plan_digest = replan_plan, replan_plan_job, replan_plan_digest
        allowed, patch_job = replan_allowed, replan_patch_job
        replan_lineage = {**first_attempt, "issues": replan_issues,
            "issue_digest": digest(replan_issues), "replan_plan_job": replan_plan_job,
            "replan_plan_digest": replan_plan_digest, "replan_patch_job": replan_patch_job,
            "replan_patch_digest": digest(patch)}
    validate(patch, PATCH_SCHEMA)
    try:
        updated = apply_edits(candidate, patch, allowed_targets=allowed,
                              representation=representation)
        _validate_v3_effects(candidate, updated, plan, target_authority, plan_target_contract_version, patch["edits"])
        validate_issue_target_coverage(candidate, updated, patch["edits"], plan,
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
        if replan_lineage is not None:
            meta["replan"] = replan_lineage
        if FIELD in shared_context:meta[FIELD] = shared_context[FIELD]
        meta["plan_target_contract_version"] = plan_target_contract_version
        if target_authority is not None: meta["repair_target_authority"] = target_authority
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
        if replan_lineage is not None:
            meta["replan"] = replan_lineage
        if FIELD in shared_context:meta[FIELD] = shared_context[FIELD]
        meta["plan_target_contract_version"] = plan_target_contract_version
        if target_authority is not None: meta["repair_target_authority"] = target_authority
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
        if replan_lineage is not None:
            meta["replan"] = replan_lineage
        if FIELD in shared_context:meta[FIELD] = shared_context[FIELD]
        meta["plan_target_contract_version"] = plan_target_contract_version
        if target_authority is not None: meta["repair_target_authority"] = target_authority
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
    if replan_lineage is not None:
        meta["replan"] = replan_lineage
    if FIELD in shared_context:meta[FIELD] = shared_context[FIELD]
    meta["plan_target_contract_version"] = plan_target_contract_version
    if target_authority is not None: meta["repair_target_authority"] = target_authority
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
    from pipeline.annotation_ranged_link_guidance import FIELD, contextual_guidance
    contextual_guidance(meta, meta.get("representation"))
    from pipeline.annotation_repair_authority import validate_replay_contract
    validate_replay_contract(meta)
    request_version = meta.get("repair_request_policy_version")
    if 'complete_stage_instruction_policy_version' in meta and (type(meta.get('complete_stage_instruction_policy_version')) is not int or meta['complete_stage_instruction_policy_version'] != 2):
        raise ValueError('Repair complete-stage instruction version changed')
    plan_version = meta.get("plan_target_contract_version", 1)
    if plan_version not in (1, 2, 3) or isinstance(plan_version, bool):
        raise ValueError("Unknown repair plan target contract version")
    base = meta.get("base")
    if candidate_digest(base) != meta.get("base_digest") or digest(meta.get("issues")) != meta.get("issue_digest"):
        raise ValueError("annotation repair base or review issues changed")
    if plan_version == 3:
        from pipeline.annotation_repair_authority import validate_authority_packet
        validate_authority_packet(meta.get("repair_target_authority"), meta["issues"], base,
            meta["representation"], meta["base_digest"])

    def child(job: str, expected_digest: str) -> Any:
        directory = _safe_job_path(run_dir, job)
        child_meta, value = _read_json(directory / "meta.json"), _read_json(directory / "result.json")
        if child_meta.get("return_code") != 0 or digest(value) != expected_digest:
            raise ValueError(f"annotation repair child evidence changed: {job}")
        index_path = directory / 'workspace' / 'INDEX.json'
        replan_meta = meta.get("replan") or {}
        plan_jobs = {meta.get("plan_job"), replan_meta.get("first_plan_job"), replan_meta.get("replan_plan_job")}
        is_plan_child = job in plan_jobs
        # Request policy 3 predates exact target authority. Historical v1/v2
        # records without child workspaces retain their original replay contract.
        # New v3 plans require a workspace; retained workspace version evidence
        # still authenticates older pairs and rejects metadata-only downgrades.
        if is_plan_child and (plan_version == 3 or child_meta.get("workspace_digest") is not None or index_path.exists() or (directory / "workspace" / "validation-context.json").exists()):
            context_path = directory / 'workspace' / 'validation-context.json'
            if not index_path.is_file() or not context_path.is_file():
                raise ValueError("versioned plan child is missing authenticated workspace request")
            rows = _read_json(index_path)
            indexed = {row.get("field"): _read_json(directory / "workspace" / row["path"]) for row in rows}
            contexts = _read_json(context_path)
            pctx = next((c.get("annotation_plan_validation") for c in contexts
                         if isinstance(c, dict) and c.get("annotation_plan_validation")), None)
            expected_issues = (replan_meta.get("issues") if job == replan_meta.get("replan_plan_job")
                               else meta.get("issues"))
            if (pctx is None or pctx.get("target_contract_version") != plan_version
                    or indexed.get("candidate") != base
                    or indexed.get("issues") != expected_issues
                    or indexed.get("representation") != meta.get("representation")):
                raise ValueError("child plan request differs from authenticated assembly request")
            if plan_version == 3 and indexed.get("repair_target_authority") != meta.get("repair_target_authority"):
                raise ValueError("v3 child plan authority differs from authenticated assembly request")
        if index_path.exists():
            rows = _read_json(index_path)
            marker_rows = [row for row in rows if row.get('field') == FIELD]
            if FIELD in meta and len(marker_rows) != 1:raise ValueError('Missing authenticated ranged-link policy')
            if marker_rows:
                from pipeline.agent_harness import CodexRunner
                CodexRunner._check_tool_profile(directory, 'offline', child_meta)
                value_marker = _read_json(directory / 'workspace' / marker_rows[0]['path'])
                if FIELD in meta and value_marker != meta[FIELD]:raise ValueError('Ranged-link policy differs from assembly')
                if len(marker_rows) != 1:raise ValueError('Duplicate ranged-link policy')
                contextual_guidance({FIELD: _read_json(directory / 'workspace' / marker_rows[0]['path'])}, meta['representation'])
            if any(row.get('field') == 'reviewed_annotation_research' for row in rows):
                from pipeline.agent_harness import CodexRunner
                from pipeline.annotation_reference_carry import bind_carried_research
                CodexRunner._check_tool_profile(directory, 'offline', child_meta)
                context = {row['field']: _read_json(directory / 'workspace' / row['path']) for row in rows}
                bind_carried_research(run_dir, context['reviewed_annotation_research'], candidate=base,
                    source_text=context['chunk_text'], language=context['language'],
                    representation=context['representation'], context=context)
        if FIELD in meta and not index_path.exists():raise ValueError('Missing authenticated ranged-link workspace')
        if index_path.exists() and any(row.get('field') == 'reviewed_run_lessons' for row in rows):
            from pipeline.agent_harness import CodexRunner
            from pipeline.annotation_run_lessons import validate_run_lessons
            CodexRunner._check_tool_profile(directory, 'offline', child_meta)
            context = {row['field']: _read_json(directory / 'workspace' / row['path']) for row in rows}
            validate_run_lessons(run_dir, context['reviewed_run_lessons'], candidate=base,
                source_text=context['chunk_text'], language=context['language'],
                representation=context['representation'], context=context)
        return value

    replan = meta.get("replan")
    if replan is not None:
        if (not isinstance(replan, dict)
                or digest(replan.get("issues")) != replan.get("issue_digest")
                or not isinstance(replan.get("failed_patch"), dict)
                or digest(replan["failed_patch"]) != replan.get("first_patch_digest")):
            raise ValueError("annotation replan lineage is incomplete or changed")
        recovery = _read_json(_safe_job_path(run_dir, replan["first_patch_job"]) /
                              "submission-recovery.json")
        initial, final = recovery.get("initial_rejection", {}), recovery.get("repair_rejection", {})
        if (recovery.get("status") != "rejected"
                or initial.get("category") != "derived_annotation_rejection"
                or final.get("category") != "derived_annotation_rejection"
                or final.get("artifact_sha256") != replan.get("failed_artifact_sha256")
                or replan["failed_patch"].get("base_digest") != meta.get("base_digest")):
            raise ValueError("stored first-attempt derived-gate rejection evidence changed")
        first_workspace = _safe_job_path(run_dir, replan["first_patch_job"]) / "workspace"
        rejection_record = _read_json(first_workspace / "submission-rejection.json")
        if rejection_record != initial:
            raise ValueError("initial rejected-patch record differs from bounded-correction evidence")
        initial_candidate = first_workspace / "candidate.json"
        initial_copy = first_workspace / "rejected-candidate-attempt-01.json"
        for artifact in (initial_candidate, initial_copy):
            if (artifact.is_symlink() or not artifact.resolve(strict=True).is_relative_to(first_workspace.resolve())
                    or hashlib.sha256(artifact.read_bytes()).hexdigest() != initial.get("artifact_sha256")):
                raise ValueError("initial rejected patch bytes differ from bounded-correction evidence")
        repair_workspace = _safe_job_path(run_dir, recovery["job"]) / "workspace"
        final_relative = Path(final.get("artifact_path", ""))
        if (not final_relative.parts or final_relative.is_absolute()
                or any(part in {"", ".", ".."} for part in final_relative.parts)):
            raise ValueError("final rejected patch has an unsafe artifact path")
        final_artifact = repair_workspace.joinpath(*final_relative.parts)
        if (final_artifact.is_symlink()
                or not final_artifact.resolve(strict=True).is_relative_to(repair_workspace.resolve())):
            raise ValueError("final rejected patch artifact escaped its correction workspace")
        final_bytes = final_artifact.read_bytes()
        if hashlib.sha256(final_bytes).hexdigest() != final.get("artifact_sha256"):
            raise ValueError("final rejected patch bytes differ from bounded-correction evidence")
        try:
            if json.loads(final_bytes) != replan["failed_patch"]:
                raise ValueError("final rejected artifact differs from recorded failed patch")
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("final rejected artifact is not valid patch JSON") from exc
        first_plan = child(replan["first_plan_job"], replan["first_plan_digest"])
        # Historical accepted plans predate target feasibility validation.
        # Replay the exact actual patch below; an unused stale allowlist item
        # must not invalidate an already accepted assembly.
        _validate_plan(first_plan, len(meta["issues"]), base if plan_version >= 2 else None,
            meta["representation"] if plan_version >= 2 else None, target_contract_version=plan_version,
            target_authority=meta.get("repair_target_authority"))
        try:
            failed_candidate = apply_edits(base, replan["failed_patch"],
                allowed_targets=_targets(first_plan), representation=meta["representation"])
            _validate_v3_effects(base, failed_candidate, first_plan,
                meta.get("repair_target_authority"), plan_version, replan["failed_patch"]["edits"])
            if meta.get("target_contract_version", 0) >= 1:
                validate_issue_target_coverage(base, failed_candidate,
                    replan["failed_patch"]["edits"], first_plan,
                    representation=meta["representation"])
        except (AnnotationEditError, ValidationError, TypeError, ValueError) as exc:
            raise ValueError("stored rejected semantic patch cannot be replayed against its exact base") from exc
        if (failed_candidate != replan.get("failed_candidate")
                or candidate_digest(failed_candidate) != replan.get("failed_candidate_digest")):
            raise ValueError("stored first-attempt derived candidate differs from exact patch replay")
        # The first rejection was revalidated against the then-current gate
        # immediately before launching the replan. Do not run that historical
        # candidate through a potentially refreshed lexical catalog during
        # cache replay; the runner's hash-bound workspace/recovery records
        # preserve that prior diagnostic. The final candidate below is always
        # checked against the current full deterministic gate.
        if not replan.get("replan_plan_job"):
            if meta.get("status") != "patch_rejected" or candidate_digest(stored) != meta.get("base_digest"):
                raise ValueError("failed replan planning metadata changed the immutable-base outcome")
            return {"status": "patch_rejected", "candidate": stored,
                    "evidence": _evidence_result(run_dir, assembly_job, meta), "plan": first_plan,
                    "patch_error": replan.get("replan_plan_error")}
        replanned = child(replan["replan_plan_job"], replan["replan_plan_digest"])
        _validate_plan(replanned, len(replan["issues"]), base if plan_version >= 2 else None,
            meta["representation"] if plan_version >= 2 else None, target_contract_version=plan_version,
            target_authority=meta.get("repair_target_authority"))
        if meta.get("status") == "boundary_change_needed" and not replan.get("replan_patch_job"):
            if candidate_digest(stored) != meta.get("base_digest"):
                raise ValueError("boundary replan modified the immutable base")
            return {"status": meta["status"], "candidate": stored,
                    "evidence": _evidence_result(run_dir, assembly_job, meta), "plan": replanned}
        if not replan.get("replan_patch_job"):
            if meta.get("status") != "patch_rejected" or candidate_digest(stored) != meta.get("base_digest"):
                raise ValueError("terminal replan outcome changed the immutable base")
            return {"status": "patch_rejected", "candidate": stored,
                    "evidence": _evidence_result(run_dir, assembly_job, meta), "plan": replanned,
                    "patch_error": replan.get("patch_error")}
        final_patch = child(replan["replan_patch_job"], replan["replan_patch_digest"])
        try:
            updated = apply_edits(base, final_patch, allowed_targets=_targets(replanned),
                                  representation=meta["representation"])
            _validate_v3_effects(base, updated, replanned,
                meta.get("repair_target_authority"), plan_version, final_patch["edits"])
            if meta.get("target_contract_version", 0) >= 1:
                validate_issue_target_coverage(base, updated, final_patch["edits"], replanned,
                                               representation=meta["representation"])
        except (BoundaryChangeNotSupported, ImmutableFieldError) as exc:
            if meta.get("status") != "boundary_change_needed" or str(exc) != meta.get("boundary_error"):
                raise ValueError("replayed replan boundary diagnostic differs") from exc
            if candidate_digest(stored) != meta.get("base_digest"):
                raise ValueError("boundary-rejected replan changed its immutable base")
            return {"status": "boundary_change_needed", "candidate": stored,
                    "evidence": _evidence_result(run_dir, assembly_job, meta), "plan": replanned,
                    "boundary_error": str(exc)}
        except AnnotationEditError as exc:
            if meta.get("status") != "patch_rejected" or str(exc) != meta.get("patch_error"):
                raise ValueError("replayed replan scope diagnostic differs") from exc
            if candidate_digest(stored) != meta.get("base_digest"):
                raise ValueError("scope-rejected replan changed its immutable base")
            return {"status": "patch_rejected", "candidate": stored,
                    "evidence": _evidence_result(run_dir, assembly_job, meta), "plan": replanned,
                    "patch_error": str(exc)}
        if meta.get("status") == "patch_rejected":
            if candidate_digest(stored) != meta.get("base_digest"):
                raise ValueError("rejected replanned annotation modified the immutable base")
            try:
                validate_candidate(updated)
            except (ValueError, TypeError, KeyError, IndexError, ValidationError) as exc:
                if str(exc) != meta.get("validation_error"):
                    raise ValueError("replayed terminal replan diagnostic differs") from exc
            else:
                if meta.get("validation_error"):
                    raise ValueError("terminal replan now passes its deterministic gate")
            return {"status": "patch_rejected", "candidate": stored,
                    "evidence": _evidence_result(run_dir, assembly_job, meta), "plan": replanned,
                    "validation_error": meta.get("validation_error")}
        validate_candidate(updated)
        if candidate_digest(updated) != meta.get("result_digest") or updated != stored:
            raise ValueError("replayed replanned annotation differs from its stored result")
        return {"status": "applied", "candidate": updated,
                "evidence": _evidence_result(run_dir, assembly_job, meta), "plan": replanned}

    plan = child(meta["plan_job"], meta["plan_digest"])
    _validate_plan(plan, len(meta["issues"]), base if plan_version >= 2 else None,
            meta["representation"] if plan_version >= 2 else None, target_contract_version=plan_version,
            target_authority=meta.get("repair_target_authority"))
    if meta.get("status") in {"boundary_change_needed", "patch_rejected"}:
        if candidate_digest(stored) != meta.get("base_digest"):
            raise ValueError("rejected annotation repair modified the base candidate")
        if meta.get("patch_job"):
            patch = child(meta["patch_job"], meta["patch_digest"])
            try:
                updated = apply_edits(base, patch, allowed_targets=_targets(plan),
                                      representation=meta["representation"])
                _validate_v3_effects(base, updated, plan,
                    meta.get("repair_target_authority"), plan_version, patch["edits"])
                if meta.get("target_contract_version", 0) >= 1:
                    validate_issue_target_coverage(base, updated, patch["edits"], plan,
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
    _validate_v3_effects(base, updated, plan, meta.get("repair_target_authority"), plan_version, patch["edits"])
    if meta.get("target_contract_version", 0) >= 1:
        validate_issue_target_coverage(base, updated, patch["edits"], plan,
                                       representation=meta["representation"])
    validate_candidate(updated)
    if candidate_digest(updated) != meta.get("result_digest") or updated != stored:
        raise ValueError("replayed annotation semantic patch differs from its stored result")
    return {"status": "applied", "candidate": updated,
            "evidence": _evidence_result(run_dir, assembly_job, meta), "plan": plan}


def inspect_applied_repair_coverage(
    run_dir: Path,
    assembly_job: str,
) -> dict[str, Any] | None:
    """Inspect a verified applied repair for original findings it did not cover.

    Returns ``None`` for non-applied or fully covered assemblies. For an
    incomplete historical assembly, returns its integrity-verified derived
    candidate and the exact original review issues whose declared plan targets
    were not effectively changed. A caller can then replan those issues against
    that candidate without regenerating the whole annotation. This is structural
    coverage evidence, not a linguistic adjudication or an approval decision.
    """
    run_dir = Path(run_dir)
    assembly_dir = _safe_job_path(run_dir, assembly_job)
    meta = _read_json(assembly_dir / "meta.json")
    if (meta.get("kind") != "annotation_patch_assembly"
            or meta.get("return_code") != 0 or meta.get("status") != "applied"):
        return None

    # Standard replay verifies base/issue/child digests, exact edit application,
    # protected source/tap projections, and stored-result equality. Current
    # versioned assemblies also recheck coverage there; historical assemblies
    # omit that post-hoc requirement so this function can identify their gaps.
    replayed = replay_annotation_repair(
        run_dir, assembly_job, validate_candidate=lambda _candidate: None)
    if replayed.get("status") != "applied":
        return None

    original_issues = meta.get("issues")
    if not isinstance(original_issues, list):
        raise ValueError("applied annotation repair has no original issue list")
    plan = replayed["plan"]
    plan_rows = plan.get("issues", [])
    if meta.get("replan") is not None:
        lineage_issues = meta["replan"].get("issues")
        if (not isinstance(lineage_issues, list)
                or lineage_issues[:len(original_issues)] != original_issues):
            raise ValueError("replan lineage does not preserve exact original review issues")
    original_plan = {"issues": [row for row in plan_rows
                                if row.get("issue_index", len(original_issues)) < len(original_issues)]}
    if [row.get("issue_index") for row in original_plan["issues"]] != list(range(len(original_issues))):
        raise ValueError("applied repair plan does not map every original issue exactly once")
    if any(row.get("boundary_change_needed") for row in original_plan["issues"]):
        raise ValueError("applied repair plan still marks an original issue as requiring boundary changes")

    patch_job = meta.get("patch_job")
    patch_path = _safe_job_path(run_dir, patch_job) / "result.json"
    patch = _read_json(patch_path)
    if digest(patch) != meta.get("patch_digest"):
        raise ValueError("applied repair patch changed after verified replay")
    unresolved = uncovered_issue_targets(meta["base"], replayed["candidate"],
        patch.get("edits", []), original_plan, representation=meta["representation"])
    if not unresolved:
        return None

    by_index: dict[int, list[dict[str, Any]]] = {}
    for item in unresolved:
        by_index.setdefault(item["issue_index"], []).append(item)
    rows_by_index = {row["issue_index"]: row for row in original_plan["issues"]}
    unresolved_issues = [{
        "issue_index": index,
        "issue": deepcopy(original_issues[index]),
        "plan_reason": rows_by_index[index]["reason"],
        "uncovered_targets": [
            {**item["target"], "diagnostic": item["diagnostic"]}
            for item in by_index[index]
        ],
    } for index in sorted(by_index)]
    details = "; ".join(item["diagnostic"] for item in unresolved)
    return {"status": "incomplete", "assembly_job": assembly_job,
        "candidate": replayed["candidate"], "unresolved_issues": unresolved_issues,
        "diagnostic": f"Historical applied annotation repair left planned targets uncovered: {details}"}
