"""Isolated typed authority for adding one missing grammar occurrence row.

This is proposal code. It is intentionally opt-in and does not change the
historical typed-review/repair packet contract.
"""
from __future__ import annotations

import hashlib
import json
import re


POLICY_VERSION = 1


def _digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def identity_bindings(grammar_knowledge, *, run_dir=None, language=None):
    """Normalize canonical identities and authenticated run-reviewed lessons.

    A digest-shaped string in caller context is not provenance. New run-only
    identities must come from descriptors replayed by the shared independent
    writer/critic lesson importer.
    """
    if not isinstance(grammar_knowledge, dict):
        raise ValueError("missing-layer authority requires host grammar_knowledge")
    bindings = {}
    status = grammar_knowledge.get("catalog_status")
    entries = grammar_knowledge.get("approved_entries", [])
    if status == "reviewed" and isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict):
                identity = entry.get("id") or entry.get("entry_id")
                if isinstance(identity, str) and identity:
                    binding = {"status": "approved", "digest": _digest(entry)}
                    if identity in bindings and bindings[identity] != binding:
                        raise ValueError("grammar knowledge contains conflicting duplicate identities")
                    bindings[identity] = binding
    # Chinese has no approved grammar registry. Existing provisional keys are
    # usable as clustering identities, never represented as approved lessons.
    if status == "provisional_keys_only":
        for identity in grammar_knowledge.get("candidate_keys_in_base", []):
            if isinstance(identity, str) and identity:
                binding = {"status": "provisional", "digest": _digest({
                    "identity": identity, "catalog_status": status,
                    "identity_policy": grammar_knowledge.get("identity_policy", "")})}
                if identity in bindings and bindings[identity] != binding:
                    raise ValueError("grammar knowledge contains conflicting duplicate identities")
                bindings[identity] = binding
    if 'reviewed_function_identities' in grammar_knowledge:
        raise ValueError('unverified reviewed_function_identities are not an authentication source')
    run_sources = grammar_knowledge.get('reviewed_run_lesson_sources', [])
    if run_sources:
        if run_dir is None or language not in {'zh', 'ja', 'ko'}:
            raise ValueError('run-reviewed grammar identity requires host run directory and language')
        from pipeline.annotation_run_lessons import authenticated_run_lesson_catalog
        catalog = authenticated_run_lesson_catalog(run_dir, run_sources, language=language)
        if len(catalog['sources']) != len(catalog['lessons']):
            raise ValueError('authenticated run catalog source and lesson rows differ')
        for source, lesson in zip(catalog['sources'], catalog['lessons']):
            identity = lesson.get('id') if isinstance(lesson, dict) else None
            if not isinstance(identity, str) or not identity:
                raise ValueError('authenticated run lesson lacks an exact grammar identity')
            binding = {'status': 'reviewed_for_run', 'digest': _digest({
                'language': language, 'source_descriptor': source, 'lesson': lesson})}
            prior = bindings.get(identity)
            if prior is not None and prior['status'] == 'approved':
                continue
            bindings[identity] = binding
    return bindings


def _source_geometry(candidate, source_text, representation):
    if not isinstance(source_text, str):
        raise ValueError("missing-layer declaration requires immutable source_text")
    segments = candidate.get("segments")
    if not isinstance(segments, list):
        raise ValueError("missing-layer candidate has no segments")
    intervals = []
    if representation == "korean-v4":
        cursor = 0
        for row in segments:
            start, end = row.get("source_start"), row.get("source_end")
            if type(start) is not int or type(end) is not int or start != cursor or end <= start:
                raise ValueError("Korean v4 taps do not form exact contiguous source geometry")
            intervals.append((start, end))
            cursor = end
        if cursor != len(source_text):
            raise ValueError("Korean v4 taps do not cover immutable source_text")
    else:
        cursor = 0
        for row in segments:
            text = row.get("surface") if representation == "japanese-annotation" else row.get("text")
            if not isinstance(text, str) or not text:
                raise ValueError("annotation tap has no source text")
            end = cursor + len(text)
            intervals.append((cursor, end))
            cursor = end
        if "".join(row.get("surface") if representation == "japanese-annotation" else row.get("text")
                   for row in segments) != source_text:
            raise ValueError("candidate taps do not reconstruct immutable source_text")
    return intervals


def validate_declaration(issue, candidate, representation, source_text, grammar_knowledge,
                         *, run_dir=None, language=None):
    declaration = issue.get("missing_layer") if isinstance(issue, dict) else None
    if declaration is None:
        return None
    if not isinstance(declaration, dict) or set(declaration) != {
            "version", "anchor_paths", "collection_path", "identity", "identity_status",
            "identity_digest", "source_interval"}:
        raise ValueError("missing_layer must use the exact version-1 declaration schema")
    if declaration.get("version") != POLICY_VERSION or type(declaration.get("version")) is not int:
        raise ValueError("unknown missing-layer authority version")
    paths = issue.get("candidate_paths", issue.get("paths"))
    if not isinstance(paths, list) or declaration["anchor_paths"] != paths:
        raise ValueError("missing-layer anchors must equal the exact typed issue paths")
    identity = declaration.get("identity")
    if not isinstance(identity, str) or not identity:
        raise ValueError("missing-layer identity must be a nonempty exact identity")
    if language is None:
        language = {'chinese-annotation': 'zh', 'japanese-annotation': 'ja',
                    'korean-flat': 'ko', 'korean-v4': 'ko'}.get(representation)
    binding = identity_bindings(grammar_knowledge, run_dir=run_dir, language=language).get(identity)
    if (binding is None or declaration.get("identity_status") != binding["status"]
            or declaration.get("identity_digest") != binding["digest"]):
        raise ValueError("missing-layer identity is not bound to host grammar knowledge")
    interval = declaration.get("source_interval")
    if (not isinstance(interval, dict) or set(interval) != {"start", "end", "surface"}
            or type(interval.get("start")) is not int or type(interval.get("end")) is not int
            or not 0 <= interval["start"] < interval["end"] <= len(source_text)
            or not isinstance(interval.get("surface"), str)
            or source_text[interval["start"]:interval["end"]] != interval["surface"]):
        raise ValueError("missing-layer interval does not bind exact immutable source text")
    intervals = _source_geometry(candidate, source_text, representation)
    owners = []
    anchor_ranges = []
    for path in paths:
        # Match the v4 nested ranged-link form before the broad segment-field
        # form. Otherwise /segments/N/grammar_links/M/field is misread as a
        # scalar owned by tap N and its recorded multi-tap interval is ignored.
        local_link_match = re.fullmatch(r"/segments/(\d+)/grammar_links/(\d+)/[^/]+", path)
        match = re.fullmatch(r"/segments/(\d+)/[^/]+(?:/[^/]+)*", path)
        if local_link_match and representation == "korean-v4":
            owner, link_index = int(local_link_match.group(1)), int(local_link_match.group(2))
            if not 0 <= owner < len(intervals):
                raise ValueError('Korean v4 grammar-link anchor has invalid owner')
            row = candidate['segments'][owner]['grammar_links'][link_index]
            left, right = row.get('source_start'), row.get('source_end')
            if (type(left) is not int or type(right) is not int or not 0 <= left < right <= len(source_text)
                    or left not in {span[0] for span in intervals}
                    or right not in {span[1] for span in intervals}):
                raise ValueError('Korean v4 grammar-link anchor has invalid recorded source range')
            owners.append(owner); anchor_ranges.append((left, right))
        elif match:
            owner = int(match.group(1))
            owners.append(owner)
            anchor_ranges.append(intervals[owner] if 0 <= owner < len(intervals) else None)
        else:
            overlay_match = re.fullmatch(r"/grammar_overlays/(\d+)/[^/]+", path)
            flat_link_match = re.fullmatch(r"/grammar_links/(\d+)/[^/]+", path)
            if overlay_match:
                row = candidate['grammar_overlays'][int(overlay_match.group(1))]
                left, right = row.get('start'), row.get('end')
                surface = row.get('surface') if representation == 'japanese-annotation' else row.get('text')
                if (type(left) is not int or type(right) is not int or not 0 <= left < right <= len(source_text)
                        or surface != source_text[left:right]):
                    raise ValueError('overlay diagnostic anchor has no exact recorded source identity')
                owners.append(next((i for i, span in enumerate(intervals) if span[0] <= left < span[1]), -1))
                anchor_ranges.append((left, right))
            elif flat_link_match and representation == 'korean-flat':
                row = candidate['grammar_links'][int(flat_link_match.group(1))]
                first, last = row.get('segment_index'), row.get('display_end_segment_index')
                if type(first) is not int or type(last) is not int or not 0 <= first <= last < len(intervals):
                    raise ValueError('Korean grammar-link anchor has invalid segment geometry')
                left, right = intervals[first][0], intervals[last][1]
                if row.get('display_form') != source_text[left:right]:
                    raise ValueError('Korean grammar-link anchor does not reconstruct its exact source range')
                owners.append(first); anchor_ranges.append((left, right))
            else:
                raise ValueError('missing-layer anchors must resolve to source-owned scalar fields')
    if not owners or any(type(owner) is not int or not 0 <= owner < len(intervals)
                         or anchor_range is None
                         or not (anchor_range[0] < interval["end"]
                                 and anchor_range[1] > interval["start"])
                         or not (interval["start"] <= anchor_range[0]
                                 and anchor_range[1] <= interval["end"])
                         for owner, anchor_range in zip(owners, anchor_ranges)):
        raise ValueError("missing-layer interval is detached from its exact diagnostic anchors")
    if representation in {"chinese-annotation", "japanese-annotation"}:
        expected_collection = "/grammar_overlays"
    elif representation == "korean-flat":
        expected_collection = "/grammar_links"
    elif representation == "korean-v4":
        expected_collection = f"/segments/{owners[0]}/grammar_links"
        if any(owner != owners[0] for owner in owners):
            raise ValueError("Korean v4 missing-layer append must use its exact owning tap")
        if (interval['start'] not in {span[0] for span in intervals}
                or interval['end'] not in {span[1] for span in intervals}):
            raise ValueError("Korean v4 missing-layer interval must align to immutable tap boundaries")
    else:
        raise ValueError("missing-layer append is unsupported for this representation")
    if declaration.get("collection_path") != expected_collection:
        raise ValueError("missing-layer collection does not match representation geometry")
    collection = candidate
    for part in expected_collection.strip("/").split("/"):
        collection = collection[int(part)] if isinstance(collection, list) else collection[part]
    if not isinstance(collection, list):
        raise ValueError("missing-layer append collection is not an existing list")
    if _duplicate(candidate, representation, expected_collection, identity, interval):
        raise ValueError("same grammar identity already exists at this source position")
    return {**declaration, "anchor_owners": owners,
            "identity_field": "grammar_candidate_key" if representation in {
                "chinese-annotation", "japanese-annotation"} else "entry_id"}


def _duplicate(candidate, representation, collection_path, identity, interval):
    collection = candidate
    for part in collection_path.strip("/").split("/"):
        collection = collection[int(part)] if isinstance(collection, list) else collection[part]
    for row in collection:
        field = "grammar_candidate_key" if representation in {"chinese-annotation", "japanese-annotation"} else "entry_id"
        if row.get(field) != identity:
            continue
        if representation in {"chinese-annotation", "japanese-annotation"}:
            start, end = row.get("start"), row.get("end")
        elif representation == "korean-flat":
            segments = candidate["segments"]
            first, last = row.get("segment_index"), row.get("display_end_segment_index")
            if type(first) is not int or type(last) is not int or not 0 <= first <= last < len(segments):
                continue
            start = sum(len(part["text"]) for part in segments[:first])
            end = sum(len(part["text"]) for part in segments[:last + 1])
        else:
            start, end = row.get("source_start"), row.get("source_end")
        if (start, end) == (interval["start"], interval["end"]):
            return True
    return False


def validate_append(before, after, edits, authority, all_authorities=None):
    """Prove this issue's appended row preserves its source geometry and siblings."""
    appends = [edit for edit in edits if edit.get("op") == "append_row"]
    declaration = authority["declaration"]
    interval = declaration["source_interval"]
    representation = authority["representation"]

    def row_geometry(row):
        if not isinstance(row, dict):
            return None
        if representation in {"chinese-annotation", "japanese-annotation"}:
            surface = row.get("text") if representation == "chinese-annotation" else row.get("surface")
            return row.get("start"), row.get("end"), surface
        if representation == "korean-flat":
            first, last = row.get("segment_index"), row.get("display_end_segment_index")
            segments = before["segments"]
            if type(first) is not int or type(last) is not int or not 0 <= first <= last < len(segments):
                return None
            start = sum(len(part["text"]) for part in segments[:first])
            end = sum(len(part["text"]) for part in segments[:last + 1])
            return start, end, row.get("display_form")
        return row.get("source_start"), row.get("source_end"), interval["surface"]

    matching = [edit for edit in appends
        if edit.get("path") == declaration["collection_path"]
        and isinstance(edit.get("value"), dict)
        and edit["value"].get(authority["identity_field"]) == declaration["identity"]
        and row_geometry(edit["value"]) == (interval["start"], interval["end"], interval["surface"])]
    if len(matching) != 1:
        raise ValueError("missing-layer issue requires one append bound to its identity and source interval")
    edit = matching[0]
    row = edit["value"]
    if _duplicate(before, representation, edit["path"], declaration["identity"], interval):
        raise ValueError("duplicate grammar layer at the same source position")
    if representation == "korean-v4":
        path_parts = edit["path"].strip("/").split("/")
        if set(before) != set(after):
            raise ValueError("missing-layer append changed candidate top-level keys")
        owner = int(path_parts[1])
        if len(before.get('segments', [])) != len(after.get('segments', [])):
            raise ValueError("missing-layer append changed primary tap count")
        for old, new in zip(before['segments'], after['segments']):
            if (old.get('source_start'), old.get('source_end')) != (new.get('source_start'), new.get('source_end')):
                raise ValueError("missing-layer append changed primary tap source geometry")
        old_rows = before['segments'][owner][path_parts[-1]]
        new_rows = after['segments'][owner][path_parts[-1]]
        same_collection = [item for item in (all_authorities or [authority])
                           if item["declaration"]["collection_path"] == edit["path"]]
        authorized_appends = []
        for item in same_collection:
            item_decl = item["declaration"]
            matched = [candidate_edit for candidate_edit in appends
                if candidate_edit.get("path") == item_decl["collection_path"]
                and isinstance(candidate_edit.get("value"), dict)
                and candidate_edit["value"].get(item["identity_field"]) == item_decl["identity"]
                and row_geometry(candidate_edit["value"]) == (
                    item_decl["source_interval"]["start"], item_decl["source_interval"]["end"],
                    item_decl["source_interval"]["surface"])]
            if len(matched) != 1:
                raise ValueError("each missing-layer authority must bind exactly one append")
            authorized_appends.append(matched[0]["value"])
        if sum(candidate_edit.get("path") == edit["path"] for candidate_edit in appends) != len(authorized_appends):
            raise ValueError("append collection contains an unbound row alongside missing-layer authority")
        if new_rows != [*old_rows, *authorized_appends]:
            raise ValueError("missing-layer append altered its owner or prior rows")
    else:
        path = edit["path"].strip("/")
        old_rows = before[path]
        if set(before) != set(after):
            raise ValueError("missing-layer append changed candidate top-level keys")
        if len(after["segments"]) != len(before["segments"]):
            raise ValueError("missing-layer append changed primary tap count")
        geometry_field = 'surface' if representation == 'japanese-annotation' else 'text'
        if [part.get(geometry_field) for part in after['segments']] != [
                part.get(geometry_field) for part in before['segments']]:
            raise ValueError("missing-layer append changed primary source taps")
        same_collection = [item for item in (all_authorities or [authority])
                           if item["declaration"]["collection_path"] == edit["path"]]
        authorized_appends = []
        for item in same_collection:
            item_decl = item["declaration"]
            matched = [candidate_edit for candidate_edit in appends
                if candidate_edit.get("path") == item_decl["collection_path"]
                and isinstance(candidate_edit.get("value"), dict)
                and candidate_edit["value"].get(item["identity_field"]) == item_decl["identity"]
                and row_geometry(candidate_edit["value"]) == (
                    item_decl["source_interval"]["start"], item_decl["source_interval"]["end"],
                    item_decl["source_interval"]["surface"])]
            if len(matched) != 1:
                raise ValueError("each missing-layer authority must bind exactly one append")
            authorized_appends.append(matched[0]["value"])
        if sum(candidate_edit.get("path") == edit["path"] for candidate_edit in appends) != len(authorized_appends):
            raise ValueError("append collection contains an unbound row alongside missing-layer authority")
        if after[path] != [*old_rows, *authorized_appends]:
            raise ValueError("missing-layer append changed or reordered existing rows")
