"""Deterministic, scope-limited edits to reviewed annotation candidates.

The caller supplies the exact candidate digest, representation contract, and
reviewed JSON-pointer allowlist. The contracts below protect actual source
surfaces and positional ranges while leaving derived semantic data (including
Japanese/Korean ``form_steps[*].form``) editable. This module has no linguistic
heuristics and does not replace language-specific validation after editing.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any, Iterable


class AnnotationEditError(ValueError):
    """Base class for a rejected annotation edit."""


class StaleCandidateError(AnnotationEditError):
    """The patch was written for a different prior candidate."""


class EditScopeError(AnnotationEditError):
    """An edit target was not explicitly allowed by the review plan."""


class ImmutableFieldError(AnnotationEditError):
    """An edit attempted to change source text or an existing source surface."""


class BoundaryChangeNotSupported(ImmutableFieldError):
    """Changing an existing source/tap range requires a separate operation."""


class EditConflictError(AnnotationEditError):
    """Edits duplicate, overlap, or structurally conflict at a target."""


class EditCoverageError(AnnotationEditError):
    """A planned issue target did not produce an effective candidate change."""


class InvalidEditError(AnnotationEditError):
    """The patch, representation, or target path is malformed."""


_REPRESENTATIONS = frozenset({
    "chinese-fixed", "chinese-annotation", "japanese-annotation", "korean-flat", "korean-v4",
})
_OPERATIONS = frozenset({"set_field", "replace_row", "replace_list", "append_row", "remove_row"})


def _is_prefix(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return len(left) <= len(right) and right[:len(left)] == left


def candidate_digest(candidate: Any) -> str:
    """Return a stable SHA-256 digest for JSON-compatible candidate data."""
    try:
        encoded = json.dumps(candidate, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise InvalidEditError(f"candidate is not JSON-compatible: {exc}") from exc
    return hashlib.sha256(encoded).hexdigest()


def _pointer_parts(pointer: Any, *, allow_dash: bool = False) -> tuple[str, ...]:
    if not isinstance(pointer, str) or not pointer.startswith("/") or pointer == "/":
        raise InvalidEditError(f"edit path must be a non-root JSON pointer: {pointer!r}")
    parts = pointer[1:].split("/")
    decoded = []
    for part in parts:
        if re.search(r"~(?![01])", part):
            raise InvalidEditError(f"invalid JSON pointer escape in {pointer!r}")
        part = part.replace("~1", "/").replace("~0", "~")
        if part == "-" and not allow_dash:
            raise InvalidEditError(f"append marker is not valid in this path: {pointer!r}")
        decoded.append(part)
    return tuple(decoded)


def _pointer(parts: tuple[str, ...]) -> str:
    return "/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts)


def _resolve(root: Any, parts: tuple[str, ...]) -> Any:
    current = root
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise InvalidEditError(f"edit path does not exist: {_pointer(parts)}")
    return current


def _resolve_parent(root: Any, parts: tuple[str, ...]) -> tuple[Any, str]:
    if not parts:
        raise InvalidEditError("root replacement is not supported")
    return _resolve(root, parts[:-1]), parts[-1]


def _obj(value: Any, path: str) -> dict:
    if not isinstance(value, dict):
        raise InvalidEditError(f"{path} must be an object for its annotation representation")
    return value


def _array(value: Any, path: str) -> list:
    if not isinstance(value, list):
        raise InvalidEditError(f"{path} must be an array for its annotation representation")
    return value


def _required(row: dict, keys: tuple[str, ...], path: str) -> None:
    missing = [key for key in keys if key not in row]
    if missing:
        raise InvalidEditError(f"{path} is missing protected representation fields: {missing}")


def _projection(candidate: Any, representation: str) -> dict[str, Any]:
    """Map immutable source/tap paths to values, including protected list sizes."""
    if representation not in _REPRESENTATIONS:
        raise InvalidEditError(f"unknown annotation representation: {representation!r}")
    root = _obj(candidate, "candidate")
    protected: dict[str, Any] = {}

    def value(path: str, item: Any) -> None:
        protected[path] = deepcopy(item)

    def rows(path: str, raw: Any) -> list:
        result = _array(raw, path)
        value(path + "/@length", len(result))
        return result

    if representation in {"chinese-fixed", "chinese-annotation"}:
        segments = rows("/segments", root.get("segments"))
        for i, raw in enumerate(segments):
            row = _obj(raw, f"/segments/{i}")
            _required(row, ("text",), f"/segments/{i}")
            if "index" in row:
                value(f"/segments/{i}/index", row["index"])
            value(f"/segments/{i}/text", row["text"])
        overlays = rows("/grammar_overlays", root.get("grammar_overlays"))
        for i, raw in enumerate(overlays):
            row = _obj(raw, f"/grammar_overlays/{i}")
            _required(row, ("start", "end", "text"), f"/grammar_overlays/{i}")
            for key in ("start", "end", "text"):
                value(f"/grammar_overlays/{i}/{key}", row[key])

    elif representation == "japanese-annotation":
        segments = rows("/segments", root.get("segments"))
        for i, raw in enumerate(segments):
            row = _obj(raw, f"/segments/{i}")
            _required(row, ("surface", "form_steps"), f"/segments/{i}")
            value(f"/segments/{i}/surface", row["surface"])
            _array(row["form_steps"], f"/segments/{i}/form_steps")
        overlays = rows("/grammar_overlays", root.get("grammar_overlays"))
        for i, raw in enumerate(overlays):
            path = f"/grammar_overlays/{i}"
            row = _obj(raw, path)
            _required(row, ("start", "end", "surface", "components"), path)
            for key in ("start", "end", "surface"):
                value(f"{path}/{key}", row[key])
            components = rows(path + "/components", row["components"])
            for j, component_raw in enumerate(components):
                component_path = f"{path}/components/{j}"
                component = _obj(component_raw, component_path)
                _required(component, ("start", "end", "surface"), component_path)
                for key in ("start", "end", "surface"):
                    value(f"{component_path}/{key}", component[key])

    elif representation == "korean-flat":
        segments = rows("/segments", root.get("segments"))
        for i, raw in enumerate(segments):
            row = _obj(raw, f"/segments/{i}")
            _required(row, ("text",), f"/segments/{i}")
            value(f"/segments/{i}/text", row["text"])
            if "form_steps" in row:
                _array(row["form_steps"], f"/segments/{i}/form_steps")
        for list_name, fields in (
            ("grammar_links", ("segment_index", "display_end_segment_index", "display_form")),
            ("expression_links", ("segment_index", "end_segment_index", "form")),
        ):
            if list_name not in root and list_name == "expression_links":
                continue
            items = rows(f"/{list_name}", root.get(list_name))
            for i, raw in enumerate(items):
                path = f"/{list_name}/{i}"
                row = _obj(raw, path)
                protected_fields = fields
                if list_name == "grammar_links" and "display_form" not in row:
                    protected_fields = ("segment_index",)
                _required(row, protected_fields, path)
                for key in protected_fields:
                    value(f"{path}/{key}", row[key])
        if "inflected_segment_indices" not in root:
            raise InvalidEditError("/inflected_segment_indices is required by korean-flat")
        indices = _array(root["inflected_segment_indices"], "/inflected_segment_indices")
        if any(type(index) is not int for index in indices):
            raise InvalidEditError("/inflected_segment_indices must contain only integer segment indices")

    else:  # korean-v4, source-span-links-annotation-v4
        if root.get("format") != "source-span-links-annotation-v4":
            raise InvalidEditError("korean-v4 requires source-span-links-annotation-v4 format")
        value("/format", root["format"])
        segments = rows("/segments", root.get("segments"))
        for i, raw in enumerate(segments):
            path = f"/segments/{i}"
            row = _obj(raw, path)
            _required(row, ("source_start", "source_end", "form_steps", "grammar_links", "expression_links"), path)
            for key in ("source_start", "source_end"):
                value(f"{path}/{key}", row[key])
            _array(row["form_steps"], f"{path}/form_steps")
            for list_name in ("grammar_links", "expression_links"):
                items = rows(f"{path}/{list_name}", row[list_name])
                for j, link_raw in enumerate(items):
                    link_path = f"{path}/{list_name}/{j}"
                    link = _obj(link_raw, link_path)
                    _required(link, ("source_start", "source_end"), link_path)
                    for key in ("source_start", "source_end"):
                        value(f"{link_path}/{key}", link[key])
    return protected


def _raise_projection_change(before: dict[str, Any], after: dict[str, Any]) -> None:
    for path in sorted(before.keys() | after.keys()):
        if before.get(path, _MISSING) != after.get(path, _MISSING):
            if path.endswith(("/text", "/surface", "/display_form")) or path.endswith("/form"):
                # The latter is only projected for Korean flat expression links;
                # form_steps[*].form is deliberately absent from the projection.
                raise ImmutableFieldError(f"edit changes protected source surface at {path}")
            raise BoundaryChangeNotSupported(f"edit changes protected source/tap structure at {path}")


_MISSING = object()


def _pattern_matches(pattern: tuple[str, ...], actual: tuple[str, ...]) -> bool:
    return len(pattern) == len(actual) and all(p == "*" or p == a for p, a in zip(pattern, actual))


def _mutable_list_path(representation: str, parts: tuple[str, ...]) -> bool:
    patterns = {
        "chinese-fixed": (("grammar_overlays",),),
        "chinese-annotation": (("grammar_overlays",),),
        "japanese-annotation": (("segments", "*", "form_steps"), ("grammar_overlays",)),
        "korean-flat": (("segments", "*", "form_steps"), ("grammar_links",),
                        ("expression_links",), ("inflected_segment_indices",)),
        "korean-v4": (("segments", "*", "form_steps"), ("segments", "*", "grammar_links"),
                      ("segments", "*", "expression_links")),
    }
    # Korean form-stage grammar identities are semantic lists. Replacing that
    # list is supported; scalar set_field must never replace it wholesale.
    if representation in {"korean-flat", "korean-v4"}:
        patterns[representation] += (("segments", "*", "form_steps", "*", "grammar_entry_ids"),)
    return any(_pattern_matches(pattern, parts) for pattern in patterns[representation])


def _object_row_list_path(representation: str, parts: tuple[str, ...]) -> bool:
    """Whether append/remove row operations can preserve this list's row type."""
    patterns = {
        "chinese-fixed": (("grammar_overlays",),),
        "chinese-annotation": (("grammar_overlays",),),
        "japanese-annotation": (("segments", "*", "form_steps"), ("grammar_overlays",)),
        "korean-flat": (("segments", "*", "form_steps"), ("grammar_links",),
                        ("expression_links",)),
        "korean-v4": (("segments", "*", "form_steps"), ("segments", "*", "grammar_links"),
                      ("segments", "*", "expression_links")),
    }
    return any(_pattern_matches(pattern, parts) for pattern in patterns[representation])


def validate_target_contract(
    candidate: Any,
    targets: Iterable[dict[str, str]],
    *,
    representation: str,
) -> None:
    """Check operation/path compatibility against the exact repair base.

    This runs on the plan before a patch job is launched. It catches invalid
    operation choices (such as ``set_field`` on an array) without granting any
    new target scope or changing protected source/tap projections.
    """
    protected = _projection(candidate, representation)
    for target in targets:
        if (not isinstance(target, dict) or set(target) != {"op", "path"}
                or target.get("op") not in _OPERATIONS):
            raise InvalidEditError(f"invalid planned semantic target: {target!r}")
        operation, path = target["op"], target["path"]
        parts = _pointer_parts(path)
        if operation == "set_field":
            parent, key = _resolve_parent(candidate, parts)
            if not isinstance(parent, dict) or key not in parent:
                raise InvalidEditError(f"set_field target must be an existing object field: {path}")
            if not _is_json_scalar(parent[key]):
                suggestion = "replace_list" if isinstance(parent[key], list) else "replace_row"
                raise InvalidEditError(
                    f"set_field target is not a scalar field: {path}; use {suggestion} for this value")
            if path in protected:
                raise ImmutableFieldError(f"planned scalar target is protected source/tap data: {path}")
        elif operation == "replace_row":
            parent, key = _resolve_parent(candidate, parts)
            if (not isinstance(parent, list) or not key.isdigit() or int(key) >= len(parent)
                    or not isinstance(parent[int(key)], dict)):
                raise InvalidEditError(f"replace_row target must be an existing object row: {path}")
        elif operation == "replace_list":
            if not _mutable_list_path(representation, parts):
                raise InvalidEditError(f"replace_list is not supported at planned path: {path}")
            try:
                current = _resolve(candidate, parts)
            except InvalidEditError:
                parent, key = _resolve_parent(candidate, parts)
                if not (representation == "korean-flat" and key == "form_steps"
                        and isinstance(parent, dict) and key not in parent):
                    raise
            else:
                if not isinstance(current, list):
                    raise InvalidEditError(f"replace_list target is not an array: {path}")
        elif operation == "append_row":
            # Canonical form targets the list path itself, matching the plan.
            # Preserve support for older indexed append paths in cached plans.
            if _object_row_list_path(representation, parts):
                try:
                    current = _resolve(candidate, parts)
                except InvalidEditError:
                    parent, key = _resolve_parent(candidate, parts)
                    if not (representation == "korean-flat" and key == "form_steps"
                            and isinstance(parent, dict) and key not in parent):
                        raise
                else:
                    if not isinstance(current, list):
                        raise InvalidEditError(f"append_row target is not an array: {path}")
            elif parts and _object_row_list_path(representation, parts[:-1]):
                parent = _resolve(candidate, parts[:-1])
                if not isinstance(parent, list) or not parts[-1].isdigit() or int(parts[-1]) != len(parent):
                    raise InvalidEditError(f"legacy indexed append must target the current list end: {path}")
            else:
                raise InvalidEditError(f"append_row target must name a supported semantic list: {path}")
        else:  # remove_row always names an existing indexed row.
            if not parts or not _object_row_list_path(representation, parts[:-1]):
                raise InvalidEditError(f"remove_row is not supported at planned path: {path}")
            parent = _resolve(candidate, parts[:-1])
            if not isinstance(parent, list) or not parts[-1].isdigit() or int(parts[-1]) >= len(parent):
                raise InvalidEditError(f"remove_row target must be an existing list row: {path}")


_MISSING = object()


def _value_at_or_missing(value: Any, path: str) -> Any:
    try:
        return _resolve(value, _pointer_parts(path))
    except InvalidEditError:
        return _MISSING


def uncovered_issue_targets(
    before: Any,
    after: Any,
    edits: list[dict[str, Any]],
    plan: dict[str, Any],
    *,
    representation: str,
) -> list[dict[str, Any]]:
    """Return planned targets which had no effective change in the result.

    A changed enclosing object/list replacement can cover a planned scalar or
    row replacement when the exact descendant value changed. Canonical appends
    are counted per issue, so repeated targets to one list need distinct rows.
    This proves declared scope was exercised; it does not decide whether a
    linguistic finding was correct. Use :func:`validate_issue_target_coverage`
    when a missing target should reject a patch submission.
    """
    append_counts: dict[str, int] = {}
    for edit in edits:
        if edit.get("op") == "append_row":
            append_counts[edit["path"]] = append_counts.get(edit["path"], 0) + 1
    append_claims: dict[str, int] = {}
    remove_claims: dict[tuple[str, str], int] = {}
    unresolved: list[dict[str, Any]] = []

    for issue in plan.get("issues", []):
        if issue.get("boundary_change_needed"):
            continue
        for target in issue.get("targets", []):
            op, path = target["op"], target["path"]
            if op == "append_row":
                claim_count = append_claims.get(path, 0) + 1
                if append_counts.get(path, 0) < claim_count:
                    unresolved.append({"issue_index": issue["issue_index"],
                        "target": {"op": op, "path": path},
                        "diagnostic": f"planned append target was not applied for issue {issue['issue_index']}: {path}"})
                append_claims[path] = claim_count
                continue

            matching = [edit for edit in edits
                        if edit.get("op") == op and edit.get("path") == path]
            effective = False
            if matching:
                if op == "remove_row":
                    parts = _pointer_parts(path)
                    base_row = _resolve(before, parts)
                    base_rows = _resolve(before, parts[:-1])
                    final_rows = _value_at_or_missing(after, _pointer(parts[:-1]))
                    row_key = (_pointer(parts[:-1]), candidate_digest(base_row))
                    required = remove_claims.get(row_key, 0) + 1
                    if isinstance(final_rows, list):
                        base_count = sum(row == base_row for row in base_rows)
                        final_count = sum(row == base_row for row in final_rows)
                        effective = base_count - final_count >= required
                    remove_claims[row_key] = required
                else:
                    old = _value_at_or_missing(before, path)
                    new = matching[0].get("value", _MISSING)
                    effective = old is not _MISSING and new is not _MISSING and old != new

            # A whole-row/list replacement is equivalent only when it changes
            # the exact descendant field named by the plan.
            if not effective and op in {"set_field", "replace_row"}:
                target_parts = _pointer_parts(path)
                for edit in edits:
                    if edit.get("op") not in {"replace_row", "replace_list"}:
                        continue
                    edit_parts = _pointer_parts(edit["path"])
                    if not _is_prefix(edit_parts, target_parts) or edit_parts == target_parts:
                        continue
                    old = _value_at_or_missing(before, path)
                    suffix = target_parts[len(edit_parts):]
                    try:
                        new = _resolve(edit["value"], suffix)
                    except InvalidEditError:
                        new = _MISSING
                    if old is not _MISSING and new is not _MISSING and old != new:
                        effective = True
                        break
            if not effective:
                unresolved.append({"issue_index": issue["issue_index"],
                    "target": {"op": op, "path": path},
                    "diagnostic": f"planned target had no effective patch change for issue {issue['issue_index']}: {op} {path}"})
    return unresolved


def validate_issue_target_coverage(
    before: Any,
    after: Any,
    edits: list[dict[str, Any]],
    plan: dict[str, Any],
    *,
    representation: str,
) -> None:
    """Reject a patch when any planned target has no effective change."""
    unresolved = uncovered_issue_targets(before, after, edits, plan,
                                         representation=representation)
    if unresolved:
        raise EditCoverageError(unresolved[0]["diagnostic"])


def _is_json_scalar(value: Any) -> bool:
    return value is None or type(value) in (str, int, float, bool)


def apply_edits(
    candidate: Any,
    patch: dict[str, Any],
    *,
    allowed_targets: Iterable[dict[str, str]],
    representation: str,
) -> Any:
    """Apply reviewed semantic edits without changing existing source anchors.

    Supported operations are scalar ``set_field``, object ``replace_row``,
    contract-approved ``replace_list``, and explicit row append/removal from a
    contract-approved semantic list. Structural row operations preserve every
    remaining row byte-for-byte and in order. Callers must run their normal
    language-specific schema and semantic validation on the returned value.
    """
    if not isinstance(patch, dict) or set(patch) != {"base_digest", "edits"}:
        raise InvalidEditError("patch must contain exactly base_digest and edits")
    if patch["base_digest"] != candidate_digest(candidate):
        raise StaleCandidateError("patch base_digest does not match the exact candidate")
    if not isinstance(patch["edits"], list):
        raise InvalidEditError("patch edits must be an array")
    _projection(candidate, representation)  # Validate shape before accepting edits.

    allowed: set[tuple[str, str]] = set()
    for target in allowed_targets:
        if (not isinstance(target, dict) or set(target) != {"op", "path"}
                or target["op"] not in _OPERATIONS or not isinstance(target["path"], str)):
            raise InvalidEditError("allowed targets need exactly a supported op and path")
        _pointer_parts(target["path"])
        allowed.add((target["op"], target["path"]))

    parsed: list[tuple[dict[str, Any], tuple[str, ...]]] = []
    for edit in patch["edits"]:
        if not isinstance(edit, dict):
            raise InvalidEditError("each edit must be an object")
        operation, path = edit.get("op"), edit.get("path")
        if operation not in _OPERATIONS:
            raise InvalidEditError(f"unsupported edit operation: {operation!r}")
        expected_keys = {"op", "path"} if operation == "remove_row" else {"op", "path", "value"}
        if set(edit) != expected_keys:
            raise InvalidEditError(f"{operation} edit needs exactly {sorted(expected_keys)}")
        parts = _pointer_parts(path)
        if (operation, path) not in allowed:
            raise EditScopeError(f"edit target is outside the reviewed allowlist: {operation} {path}")
        if operation == "set_field":
            parent, key = _resolve_parent(candidate, parts)
            if not isinstance(parent, dict) or key not in parent or not _is_json_scalar(edit["value"]):
                raise InvalidEditError(f"set_field can only replace an existing scalar object field: {path}")
        elif operation == "replace_row":
            parent, key = _resolve_parent(candidate, parts)
            if (not isinstance(parent, list) or not key.isdigit() or int(key) >= len(parent)
                    or not isinstance(parent[int(key)], dict) or not isinstance(edit["value"], dict)):
                raise InvalidEditError(f"replace_row requires an existing object row: {path}")
        elif operation == "replace_list":
            if not _mutable_list_path(representation, parts):
                raise InvalidEditError(f"replace_list is not allowed for this representation path: {path}")
            try:
                current_list = _resolve(candidate, parts)
            except InvalidEditError:
                parent, key = _resolve_parent(candidate, parts)
                if not (key == "form_steps" and isinstance(parent, dict)
                        and representation == "korean-flat" and key not in parent):
                    raise
                current_list = None
            if (current_list is not None and not isinstance(current_list, list)) or not isinstance(edit["value"], list):
                raise InvalidEditError(f"replace_list requires a semantic list target and list value: {path}")
        else:
            if operation == "append_row":
                if not isinstance(edit["value"], dict):
                    raise InvalidEditError(f"append_row value must be an object: {path}")
                if _object_row_list_path(representation, parts):
                    try:
                        parent = _resolve(candidate, parts)
                    except InvalidEditError:
                        parent_obj, key = _resolve_parent(candidate, parts)
                        if not (representation == "korean-flat" and key == "form_steps"
                                and isinstance(parent_obj, dict) and key not in parent_obj):
                            raise
                        parent = []
                    if not isinstance(parent, list):
                        raise InvalidEditError(f"append_row target must be a semantic list: {path}")
                elif parts and _object_row_list_path(representation, parts[:-1]):
                    parent = _resolve(candidate, parts[:-1])
                    if not isinstance(parent, list) or not parts[-1].isdigit() or int(parts[-1]) != len(parent):
                        raise InvalidEditError(f"legacy indexed append must target the current list end: {path}")
                else:
                    raise InvalidEditError(f"append_row is not allowed for this representation path: {path}")
            else:
                if not parts or not _object_row_list_path(representation, parts[:-1]):
                    raise InvalidEditError(f"{operation} is not allowed for this representation path: {path}")
                parent = _resolve(candidate, parts[:-1])
                if not isinstance(parent, list) or not parts[-1].isdigit():
                    raise InvalidEditError(f"{operation} target must be an indexed semantic list row: {path}")
                if int(parts[-1]) >= len(parent):
                    raise InvalidEditError(f"remove_row target must be an existing list row: {path}")
        parsed.append((edit, parts))

    # Structural row operations use indices from the immutable base. A removal
    # can coexist with semantic edits to other original rows in that list; edits
    # to the row being removed and whole-list operations remain ambiguous.
    for i, (edit, parts) in enumerate(parsed):
        for other, other_parts in parsed[i + 1:]:
            same_canonical_append_list = (
                edit["op"] == other["op"] == "append_row"
                and _object_row_list_path(representation, parts)
                and _object_row_list_path(representation, other_parts)
                and parts == other_parts
            )
            distinct_base_index_removals = (
                edit["op"] == other["op"] == "remove_row"
                and parts[:-1] == other_parts[:-1]
                and parts[-1] != other_parts[-1]
            )
            def removal_with_other_row(removal, removal_parts, other_edit, other_path):
                if (removal.get("op") == "remove_row"
                        and other_edit.get("op") == "append_row"
                        and other_path == removal_parts[:-1]
                        and _object_row_list_path(representation, other_path)):
                    return True
                if removal.get("op") != "remove_row" or other_edit.get("op") not in {
                        "set_field", "replace_row", "replace_list"}:
                    return False
                list_parts = removal_parts[:-1]
                return (len(other_path) > len(list_parts)
                        and other_path[:len(list_parts)] == list_parts
                        and other_path[len(list_parts)].isdigit()
                        and other_path[len(list_parts)] != removal_parts[-1])

            safe_mixed_removal = (
                removal_with_other_row(edit, parts, other, other_parts)
                or removal_with_other_row(other, other_parts, edit, parts)
            )
            compatible_structural_pair = distinct_base_index_removals or safe_mixed_removal
            edit_list = ((parts if _object_row_list_path(representation, parts) else parts[:-1])
                         if edit["op"] == "append_row" else
                         parts[:-1] if edit["op"] == "remove_row" else None)
            other_list = ((other_parts if _object_row_list_path(representation, other_parts) else other_parts[:-1])
                          if other["op"] == "append_row" else
                          other_parts[:-1] if other["op"] == "remove_row" else None)
            if (not same_canonical_append_list and not compatible_structural_pair
                    and edit["path"] == other["path"]) or (
                not same_canonical_append_list and not compatible_structural_pair and
                edit_list is not None and _is_prefix(edit_list, other_parts)
            ) or (
                not same_canonical_append_list and not compatible_structural_pair and
                other_list is not None and _is_prefix(other_list, parts)
            ):
                raise EditConflictError(f"conflicting edits are not allowed: {edit['path']} and {other['path']}")
            if not same_canonical_append_list and not compatible_structural_pair and (
                    _is_prefix(parts, other_parts) or _is_prefix(other_parts, parts)):
                raise EditConflictError(f"overlapping edits are not allowed: {edit['path']} and {other['path']}")

    result = deepcopy(candidate)
    removed_base_indexes: dict[tuple[str, ...], set[int]] = {}
    def application_key(row):
        edit, parts = row
        if edit["op"] == "remove_row":
            return (1, _pointer(parts[:-1]), -int(parts[-1]))
        if edit["op"] == "append_row":
            return (2, _pointer(parts if _object_row_list_path(representation, parts) else parts[:-1]), 0)
        return (0, "", 0)

    parsed = sorted(enumerate(parsed), key=lambda row: (*application_key(row[1]), row[0]))
    for _, (edit, parts) in parsed:
        before_result = deepcopy(result)
        before_projection = _projection(result, representation)
        operation = edit["op"]
        if operation == "set_field":
            parent, key = _resolve_parent(result, parts)
            parent[key] = deepcopy(edit["value"])
        elif operation == "replace_row":
            parent, key = _resolve_parent(result, parts)
            parent[int(key)] = deepcopy(edit["value"])
        elif operation == "replace_list":
            parent, key = _resolve_parent(result, parts)
            parent[key] = deepcopy(edit["value"])
        else:
            if operation == "append_row":
                if _object_row_list_path(representation, parts):
                    try:
                        parent = _resolve(result, parts)
                    except InvalidEditError:
                        parent_obj, key = _resolve_parent(result, parts)
                        parent = parent_obj.setdefault(key, [])
                    list_parts = parts
                    index = len(parent)
                else:
                    parent = _resolve(result, parts[:-1])
                    index = int(parts[-1])
                    list_parts = parts[:-1]
                before_list = deepcopy(parent)
                parent.append(deepcopy(edit["value"]))
            else:
                parent = _resolve(result, parts[:-1])
                base_index = int(parts[-1])
                prior_removed = removed_base_indexes.setdefault(parts[:-1], set())
                index = base_index - sum(1 for removed in prior_removed if removed < base_index)
                list_parts = parts[:-1]
                before_list = deepcopy(parent)
                del parent[index]
                prior_removed.add(base_index)
        after_projection = _projection(result, representation)
        if operation in {"append_row", "remove_row"}:
            old_list = before_list
            new_list = _resolve(result, list_parts)
            if operation == "append_row":
                expected = old_list + [deepcopy(edit["value"])]
            else:
                expected = old_list[:index] + old_list[index + 1:]
            if new_list != expected:
                raise InvalidEditError(f"{operation} did not preserve the untargeted rows at {edit['path']}")
            list_path = _pointer(list_parts)
            before_other = {p: v for p, v in before_projection.items()
                            if not (p == list_path or p.startswith(list_path + "/"))}
            after_other = {p: v for p, v in after_projection.items()
                           if not (p == list_path or p.startswith(list_path + "/"))}
            _raise_projection_change(before_other, after_other)
        else:
            _raise_projection_change(before_projection, after_projection)
    candidate_digest(result)
    return result
