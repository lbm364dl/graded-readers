"""Per-issue target authority for annotation repair plan contract v3."""
from __future__ import annotations
import hashlib, json, re
from typing import Any

PROJECTION_FIELDS = frozenset({"display_form", "display_end_segment_index", "segment_index",
    "surface", "source_start", "source_end", "start_segment", "end_segment"})


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _resolve(root: Any, pointer: str) -> Any:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("invalid JSON pointer")
    current = root
    for raw in pointer[1:].split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and part in current: current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current): current = current[int(part)]
        else: raise ValueError(f"v3 repair path is not present in immutable candidate: {pointer}")
    return current


def _paths(issue: dict[str, Any]) -> list[str]:
    paths = issue.get("paths", issue.get("candidate_paths"))
    if not isinstance(paths, list) or not paths or any(not isinstance(p, str) for p in paths):
        raise ValueError("v3 repair issue requires exact typed candidate paths")
    if len(paths) != len(set(paths)): raise ValueError("v3 repair paths must be unique")
    dispositions = issue.get("target_dispositions")
    if dispositions is not None:
        actionable = [d.get("path") for d in dispositions if isinstance(d, dict) and d.get("disposition") == "actionable"]
        if actionable != paths: raise ValueError("v3 diagnosis paths must equal actionable target dispositions")
    return paths


def _projected_pair(path: str, *, policy_version=1) -> set[tuple[str, str]]:
    m = re.fullmatch(r"/(grammar_links|grammar_overlays)/(\d+)/([^/]+)", path)
    if not m: return set()
    collection, index, field = m.groups()
    projected_fields = set(PROJECTION_FIELDS)
    if policy_version >= 2 and collection == "grammar_overlays":
        projected_fields.update({"start", "end", "text"})
    if field not in projected_fields: return set()
    return {("remove_row", f"/{collection}/{index}"), ("append_row", f"/{collection}")}


def _row_field(pointer):
    match = re.fullmatch(r"/(grammar_links|grammar_overlays)/(\d+)/([^/]+)", pointer)
    if not match: return None
    collection,index,field=match.groups()
    return f"/{collection}/{index}",f"/{collection}",int(index),field


def _row_pair_for_issue(pointer, paths, policy_version):
    direct=_projected_pair(pointer,policy_version=policy_version)
    if direct: return direct
    if policy_version < 2: return set()
    row=_row_field(pointer)
    if row is None: return set()
    row_path,_,_,_=row
    for other in paths:
        other_row=_row_field(other)
        if other_row and other_row[0]==row_path:
            pair=_projected_pair(other,policy_version=policy_version)
            if pair: return pair
    return set()


def _node_targets(candidate: Any, path: str, *, policy_version=1) -> set[tuple[str, str]]:
    value = _resolve(candidate, path)
    if isinstance(value, list):
        targets = {("replace_list", path)}
        # A typed target on a semantic row collection may add an occurrence or
        # form stage while preserving every existing row and its order. The
        # primary segment collection and scalar identity/index arrays are not
        # appendable through this route.
        field = path.rsplit("/", 1)[-1]
        row_collections = {"grammar_overlays", "grammar_links", "expression_links",
                           "form_steps", "components"}
        if policy_version >= 2 and field in row_collections:
            return {("append_row", path)}
        return targets
    if isinstance(value, dict):
        # replace_row is meaningful only when the object is itself an array row.
        parent = path.rsplit("/", 1)[0]
        try: container = _resolve(candidate, parent)
        except Exception: container = None
        if isinstance(container, list):
            targets = {("replace_row", path)}
            if policy_version >= 2: targets.add(("remove_row", path))
            return targets
        return set()
    return {("set_field", path)}


def _dependency_targets(issues, candidate, representation, *, policy_version=1):
    """Explicit structural closure edges; never used as independent defect authority."""
    from pipeline.annotation_repair_dependencies import repair_dependency_constraints
    packet = repair_dependency_constraints(candidate, representation)
    result = []
    for index, issue in enumerate(issues):
        paths = _paths(issue)
        for dep in packet["dependencies"]:
            identity_trigger = dep["identity_path"]
            stage_row = identity_trigger.rsplit("/", 1)[0]
            trigger = next((path for path in paths if path == identity_trigger
                            or (policy_version >= 2 and path == stage_row)), None)
            if trigger is None: continue
            for identity_path in dep["direct_link_identity_paths"]:
                row_path = identity_path.rsplit("/", 1)[0]
                collection_path = row_path.rsplit("/", 1)[0]
                edge = {"issue_index": index, "trigger_path": trigger,
                    "dependent_identity_path": identity_path,
                    "allowed_targets": [["set_field", identity_path], ["remove_row", row_path]]}
                if policy_version >= 2:
                    edge["stage_identity_path"] = identity_trigger
                    edge["stage_row_path"] = stage_row
                    edge["trigger_kind"] = "stage_row" if trigger == stage_row else "identity_list"
                    edge["old_identity"] = dep["old_identity"]
                    edge["retained_stage_identity_paths"] = dep["retained_stage_identity_paths"]
                result.append(edge)
    return result


def build_authority_packet(issues, candidate, representation, candidate_digest, *, dependency_authority_policy_version=2,
                           projected_row_authority_policy_version=2):
    if type(dependency_authority_policy_version) is not int or dependency_authority_policy_version not in (1, 2):
        raise ValueError("unknown dependency authority policy version")
    if type(projected_row_authority_policy_version) is not int or projected_row_authority_policy_version not in (1, 2):
        raise ValueError("unknown projected row authority policy version")
    if not isinstance(issues, list) or not issues: raise ValueError("v3 repair requires typed issues")
    rows = []
    for index, issue in enumerate(issues):
        if not isinstance(issue, dict): raise ValueError("v3 repair issue must be an object")
        paths = _paths(issue); authorized = set(); observations=[]
        for pointer in paths:
            value = _resolve(candidate, pointer)
            observations.append({"path": pointer, "value_digest": _digest(value)})
            authorized |= _node_targets(candidate, pointer,
                                         policy_version=dependency_authority_policy_version)
            authorized |= _projected_pair(pointer,policy_version=projected_row_authority_policy_version)
        # Add only candidate-derived dependency closure, tied to an authorized identity edit.
        for edge in _dependency_targets(issues, candidate, representation,
                                        policy_version=dependency_authority_policy_version):
            if edge["issue_index"] == index:
                authorized |= {tuple(x) for x in edge["allowed_targets"]}
        row_authorities=[]
        if projected_row_authority_policy_version >= 2:
            grouped={}
            for pointer in paths:
                info=_row_field(pointer)
                if info is None: continue
                row_path,collection,index_in_list,field=info
                pair=_row_pair_for_issue(pointer,paths,projected_row_authority_policy_version)
                if pair and field != "components":
                    grouped.setdefault((row_path,collection,index_in_list),[]).append((field,pointer,pair))
            for (row_path,collection,index_in_list),members in sorted(grouped.items()):
                fields=sorted({field for field,_,_ in members})
                row_authorities.append({"row_path":row_path,"collection_path":collection,
                    "base_index":index_in_list,"fields":fields,
                    "target_paths":sorted(pointer for _,pointer,_ in members),
                    "operations":[["remove_row",row_path],["append_row",collection]]})
        row={"issue_index": index, "issue_id": issue.get("issue_id"), "paths": paths,
             "observations": observations,
             "allowed_targets": sorted([list(t) for t in authorized])}
        if projected_row_authority_policy_version >= 2:
            row["row_replacement_authorities"]=row_authorities
        rows.append(row)
    packet = {"version": 3, "candidate_digest": candidate_digest,
        "representation": representation, "issues_digest": _digest(issues), "issues": rows,
        "dependency_edges": _dependency_targets(issues, candidate, representation,
                                                   policy_version=dependency_authority_policy_version)}
    if dependency_authority_policy_version >= 2:
        packet["dependency_authority_policy_version"] = dependency_authority_policy_version
    if projected_row_authority_policy_version >= 2:
        packet["projected_row_authority_policy_version"] = projected_row_authority_policy_version
    return packet


def validate_authority_packet(packet, issues, candidate, representation, candidate_digest):
    policy = packet.get("dependency_authority_policy_version", 1) if isinstance(packet, dict) else 1
    projection_policy = packet.get("projected_row_authority_policy_version", 1) if isinstance(packet, dict) else 1
    expected = build_authority_packet(issues, candidate, representation, candidate_digest,
                                     dependency_authority_policy_version=policy,
                                     projected_row_authority_policy_version=projection_policy)
    if packet != expected: raise ValueError("v3 target authority differs from immutable issues/candidate")


def validate_plan_authority(plan, packet):
    auth_issues = packet.get("issues", [])
    projection_policy=packet.get("projected_row_authority_policy_version",1)
    if packet.get("version") != 3 or len(plan.get("issues", [])) != len(auth_issues):
        raise ValueError("v3 plan and target authority cardinality differ")
    for index, row in enumerate(plan["issues"]):
        auth = auth_issues[index]
        if row.get("issue_index") != index: raise ValueError("v3 issue index differs from authority")
        if row.get("boundary_change_needed"):
            if row.get("targets"): raise ValueError("boundary route cannot carry semantic targets")
            continue
        permitted = {tuple(t) for t in auth["allowed_targets"]}
        proposed = {(t["op"], t["path"]) for t in row.get("targets", [])}
        if not proposed or not proposed.issubset(permitted):
            raise ValueError(f"repair plan issue {index} exceeds authenticated target authority")
        for pointer in auth["paths"]:
            pair=_row_pair_for_issue(pointer,auth["paths"],projection_policy)
            if pair and pair.issubset(proposed):
                if ("set_field",pointer) in proposed:
                    raise ValueError("paired projected-row replacement cannot overlap a scalar field edit")
                authorities = auth.get("row_replacement_authorities", [])
                if any(pointer in authority.get("target_paths", [])
                       and {tuple(item) for item in authority.get("operations", [])}.issubset(proposed)
                       for authority in authorities):
                    continue
            needed = _node_targets_from_packet(auth, pointer)
            direct_pair=_projected_pair(pointer,policy_version=projection_policy)
            if not (needed & proposed) and not (direct_pair and direct_pair.issubset(proposed)):
                raise ValueError(f"repair plan issue {index} omits authorized target {pointer}")


def _node_targets_from_packet(auth, path):
    # `allowed_targets` binds exact op/path pairs. For coverage use the field's
    # direct target, excluding structural and dependency closure entries.
    return {tuple(t) for t in auth["allowed_targets"] if t[1] == path and t[0] in
        {"set_field", "replace_list", "replace_row", "remove_row", "append_row"}}


def validate_replay_contract(meta):
    request = meta.get("repair_request_policy_version")
    plan = meta.get("plan_target_contract_version", 1)
    if request not in (None, 3) or isinstance(request, bool) or plan not in (1, 2, 3) or isinstance(plan, bool):
        raise ValueError("unknown repair replay contract version")
    if meta.get("repair_target_authority") is not None and plan != 3:
        raise ValueError("v3 authority cannot downgrade to an older plan contract")
    if request is None and plan == 3:
        raise ValueError("v3 plan cannot replay without v3 request binding")
    if request == 3 and plan == 1:
        raise ValueError("v3 request cannot downgrade to plan contract v1")
    if plan == 3:
        if request != 3 or not isinstance(meta.get("repair_target_authority"), dict):
            raise ValueError("v3 replay requires matching request and target authority")


def _pointer_parts(pointer: str) -> list[str]:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("invalid edit pointer in v3 effect check")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def _parent(root, parts):
    current = root
    for raw in parts[:-1]:
        current = current[int(raw)] if isinstance(current, list) else current[raw]
    return current, parts[-1]


def _put(root, pointer, value):
    parent, key = _parent(root, _pointer_parts(pointer))
    if isinstance(parent, list): parent[int(key)] = json.loads(json.dumps(value, ensure_ascii=False))
    else: parent[key] = json.loads(json.dumps(value, ensure_ascii=False))


def _apply_effect_edits(before, edits):
    """Replay schema-checked edits using their immutable-base indexes."""
    result = json.loads(json.dumps(before, ensure_ascii=False))
    rows = list(enumerate(edits))
    # Value edits address the immutable base and therefore precede structural shifts.
    for _, edit in rows:
        if edit["op"] in {"set_field", "replace_row", "replace_list"}:
            _put(result, edit["path"], edit["value"])
    removals = {}
    for _, edit in rows:
        if edit["op"] == "remove_row":
            parts = _pointer_parts(edit["path"]); collection = "/" + "/".join(parts[:-1]); index = int(parts[-1])
            removals.setdefault(collection, []).append(index)
    removed = {}
    for collection in sorted(removals, key=lambda value: (value.count("/"), value)):
        for base_index in sorted(set(removals[collection]), reverse=True):
            target = result
            for part in _pointer_parts(collection):
                target = target[int(part)] if isinstance(target, list) else target[part]
            already = removed.setdefault(collection, set())
            current_index = base_index - sum(1 for old in already if old < base_index)
            del target[current_index]; already.add(base_index)
    for _, edit in rows:
        if edit["op"] == "append_row":
            target = result
            for part in _pointer_parts(edit["path"]):
                target = target[int(part)] if isinstance(target, list) else target[part]
            target.append(json.loads(json.dumps(edit["value"], ensure_ascii=False)))
    return result


def validate_derived_effects(before, after, plan, packet, edits=None):
    """Bind complete derived effects to exact plan targets and immutable-base edit paths."""
    if not isinstance(packet, dict) or packet.get("version") != 3:
        raise ValueError("missing v3 effect authority")
    if not isinstance(edits, list):
        raise ValueError("v3 effect validation requires the exact applied edit list")
    planned = {(target.get("op"), target.get("path"))
               for issue in plan.get("issues", []) for target in issue.get("targets", [])}
    dependency = {(op, path) for edge in packet.get("dependency_edges", [])
                  for op, path in edge.get("allowed_targets", [])}
    allowed = planned | dependency
    for edit in edits:
        if (edit.get("op"), edit.get("path")) not in allowed:
            raise ValueError("patch edit exceeds exact issue/dependency authority")

    dependency_policy = packet.get("dependency_authority_policy_version", 1)
    if dependency_policy >= 2:
        direct_authorized = {(operation, path)
            for auth_issue in packet.get("issues", [])
            for pointer in auth_issue.get("paths", [])
            for operation, path in _node_targets_from_packet(auth_issue, pointer)}
        direct_authorized |= {pair for auth_issue in packet.get("issues", [])
                              for pointer in auth_issue.get("paths", [])
                              for pair in _projected_pair(pointer)}
        for edge in packet.get("dependency_edges", []):
            issue_index = edge.get("issue_index")
            if type(issue_index) is not int or not 0 <= issue_index < len(plan.get("issues", [])):
                raise ValueError("dependency edge has an invalid issue binding")
            issue_targets = {(target.get("op"), target.get("path"))
                             for target in plan["issues"][issue_index].get("targets", [])}
            trigger = edge.get("trigger_path")
            if not any(path == trigger for _, path in issue_targets):
                raise ValueError("dependency edge is detached from its exact issue target")
            stage_row = edge.get("stage_row_path")
            stage_identity = edge.get("stage_identity_path")
            dependent_identity = edge.get("dependent_identity_path")
            dependent_row = dependent_identity.rsplit("/", 1)[0]
            try:
                old_ids = _resolve(before, stage_identity)
                old_link_identity = _resolve(before, dependent_identity)
            except (KeyError, ValueError, TypeError):
                raise ValueError("dependency edge does not resolve in immutable candidate")
            if (not isinstance(old_ids, list) or edge.get("old_identity") not in old_ids
                    or old_link_identity != edge.get("old_identity")):
                raise ValueError("dependency edge does not bind the exact original stage/link identity")

            if edge.get("trigger_kind") == "identity_list":
                trigger_edit = next((edit for edit in edits
                                     if edit.get("path") == stage_identity
                                     and edit.get("op") in {"set_field", "replace_list"}), None)
                new_ids = trigger_edit.get("value") if trigger_edit else old_ids
                trigger_changed = isinstance(new_ids, list) and new_ids != old_ids
            elif edge.get("trigger_kind") == "stage_row":
                trigger_edit = next((edit for edit in edits
                                     if edit.get("path") == stage_row
                                     and edit.get("op") in {"remove_row", "replace_row"}), None)
                if trigger_edit is None:
                    new_ids = old_ids
                    trigger_changed = False
                elif trigger_edit["op"] == "remove_row":
                    new_ids = None
                    trigger_changed = True
                else:
                    replacement = trigger_edit.get("value")
                    new_ids = replacement.get("grammar_entry_ids") if isinstance(replacement, dict) else None
                    trigger_changed = isinstance(new_ids, list) and new_ids != old_ids
            else:
                raise ValueError("dependency edge has an unknown trigger kind")

            dep_ops = [edit for edit in edits
                       if (edit.get("op"), edit.get("path")) in
                       {tuple(target) for target in edge.get("allowed_targets", [])}
                       and (edit.get("op"), edit.get("path")) not in direct_authorized]
            if dep_ops and not trigger_changed:
                raise ValueError("dependent identity edits require an actual stage identity change")
            if not trigger_changed:
                continue

            # Every direct row for the removed identity must be handled. A field
            # update may only rebind to an identity on the replacement stage.
            still_retained = False
            for retained_path in edge.get("retained_stage_identity_paths", []):
                try: retained_ids = _resolve(before, retained_path)
                except (KeyError, ValueError, TypeError): continue
                if isinstance(retained_ids, list) and edge["old_identity"] in retained_ids:
                    still_retained = True
                    break
            if still_retained:
                if dep_ops:
                    raise ValueError("retained stage identity does not authorize a redundant dependent edit")
                continue
            matching_ops = [edit for edit in edits
                            if (edit.get("op"), edit.get("path")) in
                            {tuple(target) for target in edge.get("allowed_targets", [])}]
            if not matching_ops:
                raise ValueError("changed stage identity requires its exact direct-link disposition")
            for edit in matching_ops:
                if edit.get("op") == "set_field":
                    if not isinstance(new_ids, list) or edit.get("value") not in new_ids:
                        raise ValueError("dependent identity may only rebind to the replacement stage identity")
                elif edit.get("op") == "remove_row":
                    if edit.get("path") != dependent_row:
                        raise ValueError("dependency removal must name the exact dependent row")
                else:
                    raise ValueError("unsupported dependent identity closure operation")

    projected_fields = {}
    projection_policy=packet.get("projected_row_authority_policy_version",1)
    if projection_policy >= 2:
        for index,issue in enumerate(packet.get("issues",[])):
            if index >= len(plan.get("issues",[])):
                raise ValueError("projection authority has no matching plan issue")
            issue_planned={(target.get("op"),target.get("path"))
                           for target in plan["issues"][index].get("targets",[])}
            for authority in issue.get("row_replacement_authorities",[]):
                pair={tuple(item) for item in authority.get("operations",[])}
                if not pair or not pair.issubset(issue_planned):
                    raise ValueError("same-row semantic targets require their exact issue-bound remove/append pair")
                key=(authority.get("collection_path"),authority.get("base_index"))
                projected_fields.setdefault(key,set()).update(authority.get("fields",[]))
    else:
        for issue in packet.get("issues", []):
            for pointer in issue.get("paths", []):
                match = re.fullmatch(r"/(grammar_links|grammar_overlays)/(\d+)/([^/]+)", pointer)
                if not match or not _projected_pair(pointer): continue
                collection, raw_index, field = match.groups()
                key = ("/" + collection, int(raw_index))
                remove = ("remove_row", f"{key[0]}/{key[1]}")
                append = ("append_row", key[0])
                if remove in planned and append in planned:
                    projected_fields.setdefault(key, set()).add(field)
    remove_edits = [edit for edit in edits if edit["op"] == "remove_row"]
    all_append_edits = [edit for edit in edits if edit["op"] == "append_row"]
    projected_keys = set(projected_fields)
    for collection, index in projected_keys:
        if (("remove_row", f"{collection}/{index}") not in planned
                or ("append_row", collection) not in planned):
            raise ValueError("projected row effect lacks its exact remove/append plan pair")
    projected_removals = {(edit["path"].rsplit("/", 1)[0], int(edit["path"].rsplit("/", 1)[1]))
                          for edit in remove_edits
                          if (edit["path"].rsplit("/", 1)[0], int(edit["path"].rsplit("/", 1)[1])) in projected_keys}
    if projected_removals != projected_keys:
        raise ValueError("projected row effects do not cover exact targeted base rows")
    projected_appends = [edit for edit in all_append_edits
                         if any(edit["path"] == collection for collection, _ in projected_keys)]
    direct_collection_appends = [edit for edit in all_append_edits if edit not in projected_appends]
    if len(projected_appends) != len(projected_keys):
        raise ValueError("append operations do not match explicitly targeted projected rows")
    for edit in direct_collection_appends:
        if ("append_row", edit["path"]) not in planned:
            raise ValueError("append operation has no exact collection authority")
        if packet.get("dependency_authority_policy_version", 1) < 2:
            raise ValueError("historical target authority cannot add a direct collection append")
        if edit["path"] == "/segments":
            raise ValueError("primary segment collection append cannot be authorized as a semantic edit")

    # A projected row can also carry its exact dependency identity change when the
    # same issue repairs the identity list. No other field gains permission.
    dependency_fields = {}
    for edge in packet.get("dependency_edges", []):
        identity_path = edge.get("dependent_identity_path", "")
        parts = _pointer_parts(identity_path) if identity_path.startswith("/") else []
        if len(parts) >= 2 and parts[0] in {"grammar_links", "grammar_overlays"} and parts[-1] == "entry_id" and parts[-2].isdigit():
            dependency_fields.setdefault(("/" + parts[0], int(parts[-2])), set()).add("entry_id")

    # Match each appended value only to explicitly targeted immutable base rows.
    # Untargeted equal siblings are never candidates, so an index shift cannot
    # transfer one row's authority to another base row.
    candidate_matches = []
    for edit in projected_appends:
        collection, replacement = edit["path"], edit.get("value"); matches = []
        for key in sorted(projected_keys):
            row_collection, index = key
            if row_collection != collection: continue
            old_row = _resolve(before, f"{collection}/{index}"); fields = projected_fields[key]
            if not isinstance(old_row, dict) or not isinstance(replacement, dict) or set(old_row) != set(replacement): continue
            changed = {name for name in old_row if old_row[name] != replacement[name]}
            allowed_fields = fields | dependency_fields.get(key, set())
            if changed.intersection(fields) and changed.issubset(allowed_fields): matches.append(key)
        candidate_matches.append(matches)

    def assign(position, used):
        if position == len(candidate_matches): return {}
        for key in candidate_matches[position]:
            if key in used: continue
            found = assign(position + 1, used | {key})
            if found is not None: return {position:key,**found}
        return None
    assigned = assign(0, set())
    if assigned is None or set(assigned.values()) != projected_keys:
        raise ValueError("appended rows do not map one-to-one to projected base-row targets")
    if projection_policy >= 2:
        authorities_by_key={}
        for issue in packet.get("issues",[]):
            for authority in issue.get("row_replacement_authorities",[]):
                key=(authority.get("collection_path"),authority.get("base_index"))
                authorities_by_key.setdefault(key,[]).append(authority)
        for append_position,key in assigned.items():
            replacement=projected_appends[append_position].get("value")
            old_row=_resolve(before,f"{key[0]}/{key[1]}")
            if not isinstance(replacement,dict):
                raise ValueError("projected row replacement must be an object")
            for authority in authorities_by_key.get(key,[]):
                for field in authority.get("fields",[]):
                    if field not in old_row or field not in replacement or old_row[field]==replacement[field]:
                        raise ValueError("every explicitly targeted same-row field must change in its paired replacement")

    # Replay authorized operations in the same immutable-base coordinate system as
    # apply_edits, then compare the complete value. This preserves sibling identity
    # through multiple removals and mixed scalar edits without remapping paths after
    # an array shift.
    reconstructed = _apply_effect_edits(before, edits)
    if reconstructed != after:
        raise ValueError("derived annotation differs from exact base-bound authorized edits")
