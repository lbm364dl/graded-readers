"""Bind opt-in missing-layer repair policy to an authenticated review context."""
from __future__ import annotations


def missing_layer_repair_context(context, issues):
    """Preserve legacy contexts; bind exact run-lesson sources for v1 appends."""
    if not isinstance(context, dict):
        raise ValueError("semantic repair context must be a host-built object")
    if not isinstance(issues, list):
        raise ValueError("semantic repair issues must be a list")
    if not any(isinstance(issue, dict) and "missing_layer" in issue for issue in issues):
        return context
    existing_version = context.get("missing_layer_authority_policy_version")
    if existing_version is not None and (
        type(existing_version) is not int or existing_version != 1
    ):
        raise ValueError("unsupported missing-layer repair policy version")
    knowledge = context.get("grammar_knowledge")
    if not isinstance(knowledge, dict):
        raise ValueError("missing-layer repair requires host grammar knowledge")
    result = dict(context)
    packet = result.get("reviewed_run_lessons")
    if isinstance(packet, dict) and packet.get("lessons"):
        from pipeline.candidate_linked_lexical_audit import run_lesson_source_descriptors

        descriptors = run_lesson_source_descriptors(result)
        result["grammar_knowledge"] = {
            **knowledge,
            "reviewed_run_lesson_sources": descriptors,
        }
    result["missing_layer_authority_policy_version"] = 1
    return result


def drop_stale_adjudication_authority(context):
    """Keep history and carried evidence, removing only the prior verdict binding."""
    if not isinstance(context, dict) or "prior_review_adjudication" not in context:
        return context
    result = dict(context)
    result.pop("prior_review_adjudication", None)
    return result


def missing_layer_adjudication_context(context, issues, grammar_knowledge):
    """Add v8 host authority beside, never inside, the immutable review context."""
    if not isinstance(context, dict):
        raise ValueError("adjudication context must be a host-built object")
    if not isinstance(issues, list):
        raise ValueError("adjudication issues must be a list")
    if not any(isinstance(issue, dict) and "missing_layer" in issue for issue in issues):
        return context
    if not isinstance(grammar_knowledge, dict):
        raise ValueError("missing-layer adjudication requires host grammar knowledge")
    result = dict(context)
    nested = result.get("chunk_review_context")
    if isinstance(nested, dict):
        packet = nested.get("reviewed_run_lessons")
        if isinstance(packet, dict) and packet.get("lessons"):
            from pipeline.candidate_linked_lexical_audit import run_lesson_source_descriptors

            grammar_knowledge = {
                **grammar_knowledge,
                "reviewed_run_lesson_sources": run_lesson_source_descriptors(nested),
            }
    result["missing_layer_authority_policy_version"] = 1
    result["grammar_knowledge"] = grammar_knowledge
    return result
