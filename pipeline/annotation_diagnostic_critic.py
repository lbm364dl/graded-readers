"""Language-independent contracts for review of assigned research evidence."""
from __future__ import annotations

import copy

POLICY_VERSION = 1
GUIDANCE = """Review the RESEARCH FINDINGS, not whether the annotation is correct.
A cited fact that establishes an annotation defect is relevant evidence. A cited
fact that rebuts an inaccurate earlier review is also relevant evidence. Neither
result approves the annotation or authorizes a repair.
Use relevant when the fact and its citations support the assigned research task
at the exact occurrence. Use evidence_mismatch when the fact or its citations do
not match that task, observation or occurrence; do not use it merely because the
fact contradicts the candidate or an earlier objection. Use irrelevant for
citations that do not substantiate the assigned fact. For unresolved findings,
assess the stated gap as unresolved_appropriate or gap_inadequate.
For example, evidence that a selected moon identity is wrong for a month-counting
occurrence supports the identity research finding: relevant. Evidence that the
actual target says 'take', contrary to an earlier objection alleging 'took',
supports that observation finding: relevant. A fact about another occurrence,
or an unsupported tense claim, remains rejectable.
For each task, citation_checks must mirror EXACTLY that finding's citations:
one check for each reference_id and path, no duplicates, missing checks or extra
contextual references. Explain other context in rationale without adding a check.
Return every task once. Global approved concerns research evidence only and is
true only when every supported finding is relevant with all citations relevant,
and every unresolved finding has an appropriate gap. Never change the findings.
"""

WORKSPACE_ENTRY_GUIDANCE = """Use INDEX.json to find these separate input files:
1. annotation_assigned_diagnostic_review is the CURRENT review target. Read its
research_result.findings first. These are the only research findings you assess.
2. diagnostic_task_source_context holds the candidate, exact task observations,
source positions and linguistic records. Query the assigned task and cited
records as needed; it is supporting source context, not a research submission.
3. diagnostic_review_history holds superseded findings and earlier verdicts for
audit. They are NOT current research findings. Do not review them instead of the
current research_result, even if their task IDs or source text match.
Read the small current findings file directly. Avoid printing the entire source
context: truncated output may hide the fields needed for this review. All
history remains available in its separate file; earlier verdicts do not bind
your judgment of the current evidence.
"""


def build_review_context(request: dict) -> dict:
    """Give current evidence, source context and history distinct indexed files.

    Keeping these roles separate prevents a large candidate packet from hiding
    the current findings behind truncated output. No historical data is dropped.
    """
    if request.get("diagnostic_critic_policy_version") != POLICY_VERSION:
        raise ValueError("Unknown diagnostic critic input policy")
    source = request.get("assigned_diagnostic_inputs")
    history = request.get("successor_history")
    if not isinstance(source, dict) or not isinstance(history, dict):
        raise ValueError("Diagnostic review requires separate source and history records")
    current = copy.deepcopy({k: v for k, v in request.items()
                             if k not in {"assigned_diagnostic_inputs", "successor_history"}})
    findings = current["research_result"]["findings"]
    if current.get("critic_contract") != build_contract(findings):
        raise ValueError("Current review contract differs from current research findings")
    return {
        "annotation_assigned_diagnostic_review": current,
        "diagnostic_task_source_context": copy.deepcopy(source),
        "diagnostic_review_history": copy.deepcopy(history),
        "annotation_diagnostic_critic_validation": {
            "input_field": "annotation_assigned_diagnostic_review"},
    }


def build_contract(findings: list[dict]) -> dict:
    """Expose the exact citation matrix without generating linguistic verdicts."""
    return {
        "policy_version": POLICY_VERSION,
        "guidance": GUIDANCE,
        "citation_matrix": [
            {"task_id": row["issue_id"], "candidate_path": row["candidate_path"],
             "research_status": row["status"],
             "citations": copy.deepcopy(row["citations"])}
            for row in findings
        ],
        "candidate_approval": None, "repair_authority": None,
    }


def validate_review(review: dict, findings: list[dict]) -> None:
    """Validate payload coverage; semantic relevance remains independently judged."""
    if not isinstance(review, dict) or type(review.get("approved")) is not bool:
        raise ValueError("Diagnostic critic result is malformed")
    expected = {(row["issue_id"], row["candidate_path"]): row for row in findings}
    if len(expected) != len(findings):
        raise ValueError("Research findings duplicate an exact task/path")
    rows = review.get("task_reviews")
    if not isinstance(rows, list):
        raise ValueError("Diagnostic critic omitted task-level coverage")
    seen = set()
    sufficient = True
    for row in rows:
        key = (row.get("task_id"), row.get("candidate_path"))
        finding = expected.get(key)
        if finding is None or key in seen:
            raise ValueError("Diagnostic critic has an unknown or duplicate task/path")
        seen.add(key)
        assessment = row.get("assessment")
        checks = row.get("citation_checks", [])
        if finding["status"] == "supported":
            if assessment not in {"relevant", "irrelevant", "evidence_mismatch"}:
                raise ValueError("Supported research needs an explicit relevance verdict")
            cited = {(c["reference_id"], c["path"]) for c in finding["citations"]}
            checked = {(c.get("reference_id"), c.get("path")) for c in checks}
            if len(checks) != len(cited) or checked != cited:
                raise ValueError("Critic must assess every exact research citation once")
            sufficient &= assessment == "relevant" and all(c.get("relevant") is True for c in checks)
        elif finding["status"] == "unresolved":
            if assessment not in {"unresolved_appropriate", "gap_inadequate"}:
                raise ValueError("Unresolved research needs an explicit gap review")
            if checks:
                raise ValueError("Unresolved task cannot be reviewed with invented citations")
            sufficient &= assessment == "unresolved_appropriate"
        else:
            raise ValueError("Unknown research finding status")
        if not isinstance(row.get("rationale"), str) or not row["rationale"].strip():
            raise ValueError("Critic must explain each task-specific assessment")
    if seen != set(expected):
        raise ValueError("Diagnostic critic omitted an assigned task/path")
    if review["approved"] != sufficient:
        raise ValueError("Global critic approval conflicts with task-level dispositions")


def validate_submission(output: dict, request: dict) -> None:
    if request.get("diagnostic_critic_policy_version") != POLICY_VERSION:
        raise ValueError("Unknown diagnostic critic submission policy")
    findings = request["research_result"]["findings"]
    if request.get("critic_contract") != build_contract(findings):
        raise ValueError("Diagnostic critic contract differs from exact research findings")
    validate_review(output, findings)
