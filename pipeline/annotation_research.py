"""Targeted, evidence-bound research for uncertain annotation adjudication.

This module never edits a candidate or promotes a dictionary entry. It can add
only host-resolved, independently reviewed facts citing supplied references or
bounded host captures from an origin already supplied as a primary source.
"""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import ipaddress
import json
import re
import socket
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit, urljoin
from urllib.request import Request, HTTPRedirectHandler, ProxyHandler, build_opener
from typing import Any

from jsonschema import validate

from pipeline.annotation_adjudication import (
    AdjudicationError,
    LINGUISTIC_REFERENCE_KINDS,
    _substantive_reference,
    normalize_review,
    digest as adjudication_digest,
    verify_adjudication_evidence,
)
from pipeline.annotation_review_ledger import LedgerProtocolError, resolve_pointer
from pipeline.annotation_review_guidance import FORM_STAGE_EVIDENCE_GUIDANCE
from pipeline.worker_workspace import VERSION as WORKSPACE_VERSION, verify as verify_workspace


RESEARCH_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["findings"],
    "properties": {"findings": {
        "type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["issue_id", "status", "fact", "citations", "gap"],
            "properties": {
                "issue_id": {"type": "string", "minLength": 1},
                "status": {"enum": ["supported", "unresolved"]},
                "fact": {"type": "string"},
                "citations": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["reference_id", "path"],
                    "properties": {
                        "reference_id": {"type": "string", "minLength": 1},
                        "path": {"type": "string", "minLength": 1},
                    },
                }},
                "gap": {"type": "string"},
            },
        },
    }, "source_requests": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["url", "issue_ids", "reason"],
        "properties": {
            "url": {"type": "string", "minLength": 8, "maxLength": 2048},
            "issue_ids": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
            "reason": {"type": "string", "minLength": 1},
        },
    }},
    },
}

RESEARCH_POLICY = """You are doing narrow, reusable linguistic research for uncertain annotation findings. Use the supplied candidate, review, issue records and already approved/authoritative references. Tools are enabled: inspect the organized inputs, run checks, and use web search or other appropriate research tools when useful. A web finding is a lead, not admissible evidence. When a direct primary-source page on an already supplied primary-source site's origin is needed, request it in source_requests with exact issue IDs and a short reason. Do not request search pages, invent a supplied origin, or treat your own web observations as captured evidence. The coordinator may capture only those same-origin direct pages; a later research pass will receive the captured text. Do not fabricate a reference ID, citation, approval, or provenance. Do not treat a draft, prior reviewer opinion, candidate wording, or source passage alone as proof of a general linguistic fact.

Return one row for every supplied uncertain issue ID exactly once. A supported row states one concise reusable fact that helps decide that issue and cites one or more supplied linguistic references by exact reference_id and JSON Pointer into that reference's content. An unresolved row has an empty fact, no citations, and a concrete gap. `source_requests` is optional and defaults to []; request only direct primary pages that could resolve an unresolved finding, never search pages. Do not quote observed reference values; the host resolves them. Cite only references supplied with kind approved_lesson, primary_source, or explicit_review_policy. Preserve uncertainty when evidence is absent, indirect, conflicting, or does not support the exact point. This artifact is a research proposal for independent review, not a dictionary update or approval. Return only the requested JSON."""

REVIEW_POLICY = """Independently review this targeted linguistic research artifact. Check every supported fact against the exact cited values in the supplied approved lessons, primary sources, or explicit review policies. The researcher's statement is not evidence. Verify that citations support the specific finding and that the artifact does not turn passage context, reviewer opinion, or a draft into a general rule. An unresolved row is acceptable only when its gap accurately states what the supplied sources do not establish. Approve only when every fact is sound, every citation is relevant, all finding IDs are covered exactly once, and unresolved questions remain honestly unresolved. Return only the generic usage-dictionary-review schema."""

RESEARCH_POLICY += "\n\n" + FORM_STAGE_EVIDENCE_GUIDANCE + """

Research reusable component facts in their broader linguistic context. Do not demand a separate dictionary entry for every exact inflected substring or clause fragment. A dependent form can have a valid contribution without being a standalone assertion; use its linked lesson, supplied primary references, and complete form chain to distinguish the component from the span. Do not infer an idiom's combined meaning from its parts alone. If a specific component fact remains unsupported by supplied references, keep it unresolved.

When you determine that a direct primary-source page could resolve an unresolved issue, inspect whether its origin is already represented by a supplied primary_source reference. If so, request that exact direct page in `source_requests` before concluding the gap; the coordinator can capture at most three same-origin pages. Do not request a search-results page, a page from a new authority, or a page merely because it exists. If no relevant direct page on a supplied origin is available, explain that gap without forcing a request."""

REVIEW_POLICY += "\n\n" + FORM_STAGE_EVIDENCE_GUIDANCE + """

Review component and dependent-form facts in the larger form chain; do not demand a separate dictionary entry for every exact inflected substring, and do not treat a dependent contribution as a standalone assertion. Keep component and construction-span meanings separate, and do not infer an idiom's combined meaning from its components alone. Check whether the researcher requested a direct primary-source page when its own analysis says such evidence is needed and a plausible page on a supplied primary_source origin was available. Flag an avoidable omitted request instead of approving a gap that can be investigated. A capture failure or the absence of a relevant page is a valid unresolved limitation; do not require a supported fact when evidence remains insufficient."""

# These are the exact v2 policies used by existing job fingerprints. Keep them
# immutable so persisted evidence can still be replayed after the v3 correction.
RESEARCH_POLICY_V2 = RESEARCH_POLICY
REVIEW_POLICY_V2 = REVIEW_POLICY
RESEARCH_POLICY_V3 = RESEARCH_POLICY_V2 + """

Research the reusable linguistic fact needed to evaluate the reported defect;
do not repair or rewrite the candidate. The candidate is intentionally immutable
context during this stage and is expected to retain its current
field values. A supported fact may identify why a current field is wrong."""
REVIEW_POLICY_V3 = REVIEW_POLICY_V2 + """

Review the research artifact's evidence claim, not whether the candidate has
already been repaired. The candidate is intentionally immutable throughout this
stage. A supported fact may correctly identify why its current field is wrong;
do not reject that fact merely because the submitted candidate still contains
the defect. The separate adjudication and repair stages decide how to apply
sound evidence. Still reject unsupported facts, irrelevant citations, or claims
that do not address the exact issue."""
SCOPED_RESEARCH_GUIDANCE = """
The annotation_uncertainty_tasks packet is the authoritative task list for this
stage. For each issue_id, evaluate only its exact current_review_issue,
candidate_paths, candidate_observations and initial_uncertainty_reason. The
remaining review issues and prior history are context only: do not answer one
of them under an uncertain issue's ID. A supported fact must supply evidence
that bears on the precise unresolved point. A generally correct statement about
another construction is not support for this issue. If the relevant lexical or
grammatical contribution remains unestablished, return unresolved with an empty
fact and citations; do not combine unrelated supported facts with an admission
that the required evidence is absent. The reviewer must reject such a mismatch,
even when the unrelated claim and its citations are accurate.
The supplied_primary_origins list describes eligible capture origins, not facts
or candidate approval. Tools remain available to find a relevant direct primary
page on those origins. On the initial pass, request that exact page for the
unresolved issue; another headword's supplied page establishes an available
origin but does not itself establish the target headword's meaning. Do not invent
an entry number or cite a web observation as captured evidence. On the single
captured-source continuation, issue no further requests and retain any gap.
"""
RESEARCH_POLICY_V4 = RESEARCH_POLICY_V3 + "\n\n" + SCOPED_RESEARCH_GUIDANCE
REVIEW_POLICY_V4 = REVIEW_POLICY_V3 + "\n\n" + SCOPED_RESEARCH_GUIDANCE
PRIMARY_DISCOVERY_GUIDANCE = """
The organized annotation_primary_discovery_tools input lists available
language-specific official lookup adapters. Use an applicable adapter when web
search cannot establish a direct record URL; retain its exact request and result
in workspace scratch files. Use the supplied exact lemma or cited headword,
never infer one by stripping a suffix. Discovery returns record leads, not
linguistic evidence or approval. On the initial research pass, request relevant
same-origin direct records in source_requests for host capture. Cite supplied
authoritative references or host-captured content, never discovery leads. Zero matches or a
retrieval failure do not establish lexical nonexistence. Preserve homonyms and
verify exact headword, POS, sense and complete-form contribution independently.
The bounded captured-source continuation cannot start another capture cycle.
"""
RESEARCH_POLICY_V5 = RESEARCH_POLICY_V4 + "\n\n" + PRIMARY_DISCOVERY_GUIDANCE
REVIEW_POLICY_V5 = REVIEW_POLICY_V4 + "\n\n" + PRIMARY_DISCOVERY_GUIDANCE
SCOPED_FACT_REVIEW_GUIDANCE = """
For this research stage, supported describes the concise cited fact itself,
not acceptance of the candidate or the truth of every original defect claim.
A relevant fact may establish a defect at one assigned target and explain why
other assigned targets are acceptable. Do not require every original target to
be defective, and do not reject the fact because it contradicts part of the
original review. Evaluate each research claim against its citations and exact
field scope. A useful fact may establish only part of the issue, provided it
states its evidence limits and does not claim to settle an unestablished
contribution. This clarification supersedes earlier wording requiring an
unresolved row merely because a separate contribution remains unestablished.
Still reject unrelated facts, unsupported contributions, irrelevant citations,
and claims of resolution beyond what the sources establish. If no useful fact
for the assigned issue is supported, return unresolved with the concrete gap.
Research approval approves only the scoped evidence: it neither clears any
current finding nor authorizes a repair. The separate enriched adjudication
must preserve and evaluate every original target, including acceptable and
unresolved targets; any remaining uncertainty must remain unresolved.
"""
RESEARCH_POLICY_V6 = RESEARCH_POLICY_V5 + "\n\n" + SCOPED_FACT_REVIEW_GUIDANCE
REVIEW_POLICY_V6 = REVIEW_POLICY_V5 + "\n\n" + SCOPED_FACT_REVIEW_GUIDANCE
RESEARCH_POLICY_VERSION = 7
SUPPORTED_RESEARCH_POLICY_VERSIONS = {2, 3, 4, 5, 6, 7}

SUBMISSION_VALIDATION_VERSION = 2
RESEARCH_EVIDENCE_VERSION = 7
# Official dictionary pages include large inline scripts; keep the transfer
# bounded while allowing the observed 2.55 MB KRDict entry page.
MAX_CAPTURE_BYTES = 4_000_000
CAPTURE_TIMEOUT_SECONDS = 15
MAX_SOURCE_REQUESTS = 3
REVIEWED_LESSON_CACHE_VERSION = 1
REVIEWED_LESSON_SOURCE_MANIFEST_VERSION = 1
MAX_REVIEWED_LESSON_SOURCES = 32


class AnnotationResearchError(ValueError):
    """Research or replay evidence failed exact provenance validation."""


def _digest(value: Any) -> str:
    return adjudication_digest(value)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def _write_json(path: Path, value: Any) -> None:
    from pipeline.worker_paths import atomic_write_managed, checked_directory
    path = Path(path)
    checked_directory(path.parent, create=True)
    atomic_write_managed(path.parent, path.name, _json_bytes(value))


def _safe_job_dir(run_dir: Path, job: str) -> Path:
    from pipeline.worker_paths import checked_directory
    if not isinstance(job, str) or not job or "\\" in job:
        raise AnnotationResearchError("Worker job path is malformed")
    relative = Path(job)
    if (relative.is_absolute() or job != relative.as_posix()
            or any(part in {"", ".", ".."} for part in relative.parts)):
        raise AnnotationResearchError("Worker job path is unsafe")
    agents = run_dir / "agents"
    if agents.is_symlink():
        raise AnnotationResearchError("Worker agents directory is a symlink")
    try:
        agents_real = agents.resolve(strict=True)
        current = agents
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise AnnotationResearchError("Worker job path contains a symlink")
        job_dir = checked_directory(agents.joinpath(*relative.parts))
        if not job_dir.resolve(strict=True).is_relative_to(agents_real):
            raise AnnotationResearchError("Worker job path escaped the agents directory")
        return job_dir
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, AnnotationResearchError):
            raise
        raise AnnotationResearchError("Worker job path is unavailable") from exc


def _read_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        raise AnnotationResearchError(f"Required evidence file is missing or unsafe: {path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AnnotationResearchError(f"Evidence file is unreadable: {path.name}") from exc


def _workspace_inputs(job_dir: Path, meta: dict) -> dict[str, Any]:
    workspace = job_dir / "workspace"
    try:
        verify_workspace(workspace, meta["workspace_digest"])
        index = _read_json(workspace / "INDEX.json")
        if not isinstance(index, list):
            raise AnnotationResearchError("Worker input index is malformed")
        inputs: dict[str, Any] = {}
        for row in index:
            if not isinstance(row, dict) or not isinstance(row.get("field"), str) or not isinstance(row.get("path"), str):
                raise AnnotationResearchError("Worker input index row is malformed")
            path = Path(row["path"])
            if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
                raise AnnotationResearchError("Worker input index contains an unsafe path")
            value = _read_json(workspace / path)
            key = row["field"]
            if key in inputs and inputs[key] != value:
                raise AnnotationResearchError("Worker input field is ambiguously duplicated")
            inputs[key] = value
        return inputs
    except (OSError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, AnnotationResearchError):
            raise
        raise AnnotationResearchError("Worker organized inputs failed validation") from exc


def _check_runner_policy(runner: Any) -> None:
    if getattr(runner, "model", "gpt-6-luna") != "gpt-6-luna":
        raise AnnotationResearchError("Targeted research requires the shared gpt-6-luna policy")
    if getattr(runner, "benchmark_effort", None) not in (None, "low"):
        raise AnnotationResearchError("Targeted research requires low reasoning effort")
    if getattr(runner, "legacy_tool_restrictions", False):
        raise AnnotationResearchError("Targeted research requires the tools-enabled workspace profile")


def _worker_context(kind: str, inputs: dict) -> dict:
    context = {}
    if _policy_version(inputs) >= 5:
        adapters = []
        if (inputs["language"] == "ko"
                and ("https", "krdict.korean.go.kr", 443) in
                    _primary_origins(inputs["known_reference_input"])):
            adapters.append({"adapter": "korean-basic-dictionary", "adapter_version": 1,
                "origin": "https://krdict.korean.go.kr", "working_directory": "repository_root",
                "argv": [".venv/bin/python", "-m", "pipeline.korean_primary_discovery",
                         "--headword", "<exact_dictionary_headword>"],
                "output_scope": "Exact first-page result headings; direct record leads only, not approval.",
                "next_step": "Request relevant returned primary_url values for bounded host capture."})
        context["annotation_primary_discovery_tools"] = {"version": 1, "adapters": adapters}
    if _policy_version(inputs) >= 4:
        context["annotation_uncertainty_tasks"] = inputs["uncertainty_tasks"]
    context.update({
        "annotation_uncertainty_research": inputs,
        "annotation_uncertainty_research_role": kind,
    })
    if kind == "research":
        context["annotation_research_validation"] = {
            "input_field": "annotation_uncertainty_research",
        }
    return context


def _request_fingerprint(prompt: str, schema_text: str, workspace_context: dict) -> str:
    def runner_digest(*values: str) -> str:
        hasher = hashlib.sha256()
        for value in values:
            hasher.update(value.encode("utf-8"))
            hasher.update(b"\0")
        return hasher.hexdigest()
    value = runner_digest(prompt, schema_text, "gpt-6-luna", "low")
    value = runner_digest(value, "workspace", "tool-profile-v1")
    value = runner_digest(value, WORKSPACE_VERSION)
    return runner_digest(value, json.dumps(workspace_context, sort_keys=True))


def _verify_job(run_dir: Path, *, job: str, expected_prompt: str,
                schema_text: str, workspace_context: dict,
                expected_result: Any | None = None) -> tuple[dict, Any]:
    from pipeline.agent_harness import CodexRunner

    job_dir = _safe_job_dir(run_dir, job)
    meta = _read_json(job_dir / "meta.json")
    result = _read_json(job_dir / "result.json")
    fingerprint = _request_fingerprint(expected_prompt, schema_text, workspace_context)
    if (meta.get("job") != job or meta.get("return_code") != 0
            or meta.get("model") != "gpt-6-luna" or meta.get("effort") != "low"
            or meta.get("tool_profile") != "workspace"
            or meta.get("fingerprint") != fingerprint
            or meta.get("workspace_digest") is None):
        raise AnnotationResearchError("Worker receipt does not match the low-Luna workspace request")
    try:
        CodexRunner._check_tool_profile(job_dir, "workspace", meta)
        inputs = _workspace_inputs(job_dir, meta)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise AnnotationResearchError("Worker profile or organized inputs failed replay") from exc
    if inputs != workspace_context:
        raise AnnotationResearchError("Worker organized inputs differ from the exact request context")
    if expected_result is not None and result != expected_result:
        raise AnnotationResearchError("Worker result differs from the bound research/review artifact")
    return meta, result


def _schema_path(run_dir: Path, identity: str, schema: dict) -> Path:
    from pipeline.worker_paths import checked_directory
    schema_dir = run_dir / "annotation-research-schemas"
    checked_directory(schema_dir, create=True)
    path = schema_dir / f"{identity}.json"
    payload = _json_bytes(schema).decode("utf-8")
    if path.exists():
        if path.is_symlink() or path.read_text(encoding="utf-8") != payload:
            raise AnnotationResearchError("Stored research schema differs from the expected schema")
    else:
        _write_json(path, schema)
    return path


def _initial_uncertain_ids(initial: dict) -> list[str]:
    if not isinstance(initial, dict) or initial.get("status") != "uncertain":
        raise AnnotationResearchError("Targeted research is allowed only after uncertain adjudication")
    if initial.get("prose_requests") or initial.get("prose_revision_reason"):
        raise AnnotationResearchError("Prose-revision findings cannot be routed to targeted annotation research")
    rows = initial.get("classifications")
    if not isinstance(rows, list):
        raise AnnotationResearchError("Initial adjudication has no validated classifications")
    ids = [row.get("issue_id") for row in rows
           if isinstance(row, dict) and row.get("disposition") == "uncertain"]
    if not ids or len(ids) != len(set(ids)):
        raise AnnotationResearchError("Initial adjudication has no unique uncertain issue set")
    if any(not isinstance(identity, str) or not identity for identity in ids):
        raise AnnotationResearchError("Initial uncertain issue identity is malformed")
    return ids


def _validate_research_output(output: Any, issue_ids: list[str], known_references: dict, *, policy_version: int = 2) -> tuple[list[dict], list[dict]]:
    validate(output, RESEARCH_SCHEMA)
    rows = output["findings"]
    seen = set()
    fact_rows, unresolved_rows = [], []
    for row in rows:
        issue_id = row["issue_id"]
        if issue_id not in issue_ids:
            raise AnnotationResearchError(
                f"Unknown research issue ID {issue_id!r}; valid finding IDs: {sorted(issue_ids)!r}")
        if issue_id in seen:
            raise AnnotationResearchError(f"Duplicate research finding ID {issue_id!r}")
        seen.add(issue_id)
        if row["status"] == "supported":
            if not row["fact"].strip() or not row["citations"] or row["gap"].strip():
                raise AnnotationResearchError("Supported research facts need citations and no unresolved gap")
            resolved = []
            for citation in row["citations"]:
                reference_id = citation["reference_id"]
                reference = known_references.get(reference_id)
                valid_reference_ids = sorted(
                    identity for identity, known in known_references.items()
                    if isinstance(known, dict) and known.get("kind") in LINGUISTIC_REFERENCE_KINDS)
                if not isinstance(reference, dict) or reference.get("kind") not in LINGUISTIC_REFERENCE_KINDS:
                    raise AnnotationResearchError(
                        f"Unknown or non-authoritative citation reference ID {reference_id!r}; "
                        f"valid reference IDs: {valid_reference_ids!r}")
                content = reference['content']
                scope = content.get('_annotation_run_lesson_scope') if isinstance(content,dict) else None
                scoped_paths = None
                if policy_version >= 7 and scope is not None:
                    from pipeline.annotation_run_lessons import REFERENCE_POLICY_TEXT
                    if (not isinstance(scope,dict) or scope.get('version') not in (1,2)
                            or issue_id not in content.get('issue_ids',[])
                            or not scope.get('issue_paths',{}).get(issue_id)
                            or (scope.get('version')==2 and scope.get('policy_digest')!=_digest(REFERENCE_POLICY_TEXT))):
                        raise AnnotationResearchError('Theory citation is outside its authenticated issue scope')
                    scoped_paths = scope['issue_paths'][issue_id]
                try:
                    value = resolve_pointer(reference["content"], citation["path"])
                except (LedgerProtocolError, TypeError, KeyError) as exc:
                    raise AnnotationResearchError(
                        f"Citation path {citation['path']!r} does not resolve in reference "
                        f"{reference_id!r}: {exc}") from exc
                if not _substantive_reference({"path": citation["path"], "value": value,
                                               "reference_kind": reference["kind"]},
                                              reference_content=reference["content"]):
                    raise AnnotationResearchError("Research citation points only to reference metadata")
                resolved.append({"reference_id": reference_id, "path": citation["path"],
                                 "kind": reference["kind"], "value": value,
                                 "value_digest": _digest(value),
                                 "reference_content_digest": _digest(reference["content"]),
                                 **({"run_lesson_target_paths":scoped_paths} if scoped_paths is not None else {})})
            fact_rows.append({**row, "resolved_citations": resolved})
        elif row["status"] == "unresolved":
            if row["fact"].strip() or row["citations"] or not row["gap"].strip():
                raise AnnotationResearchError("Unresolved research rows must preserve a specific gap")
            unresolved_rows.append(row)
    if seen != set(issue_ids):
        raise AnnotationResearchError("Research did not account for every uncertain finding")
    _validated_source_requests(output, issue_ids, known_references, unresolved_rows)
    return fact_rows, unresolved_rows


def _origin(url: str) -> tuple[str, str, int]:
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.fragment):
            raise ValueError
        host = parts.hostname.lower().rstrip(".")
        port = parts.port or 443
        if port != 443:
            raise ValueError("nonstandard HTTPS port")
        return "https", host, port
    except (ValueError, TypeError) as exc:
        raise AnnotationResearchError("Source URL must be a direct HTTPS page without credentials or fragment") from exc


def _primary_origins(references: dict) -> set[tuple[str, str, int]]:
    origins: set[tuple[str, str, int]] = set()
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(child, str) and child.lower().startswith("https://"):
                    try:
                        origins.add(_origin(child))
                    except AnnotationResearchError:
                        pass
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    for reference in references.values():
        if isinstance(reference, dict) and reference.get("kind") == "primary_source":
            walk(reference.get("content"))
    return origins


def _validated_source_requests(output: Any, issue_ids: list[str], references: dict,
                               unresolved: list[dict]) -> list[dict]:
    requests = output.get("source_requests", []) if isinstance(output, dict) else []
    if not isinstance(requests, list):
        raise AnnotationResearchError("source_requests must be an array")
    if len(requests) > MAX_SOURCE_REQUESTS:
        raise AnnotationResearchError(f"At most {MAX_SOURCE_REQUESTS} direct source pages may be requested")
    allowed_origins = _primary_origins(references)
    unresolved_ids = {row["issue_id"] for row in unresolved}
    checked = []
    for row in requests:
        if (not isinstance(row, dict) or set(row) != {"url", "issue_ids", "reason"}
                or not isinstance(row.get("issue_ids"), list) or not row["issue_ids"]
                or any(identity not in issue_ids or identity not in unresolved_ids
                       for identity in row["issue_ids"])
                or len(row["issue_ids"]) != len(set(row["issue_ids"]))
                or not isinstance(row.get("reason"), str) or not row["reason"].strip()):
            raise AnnotationResearchError("Source request must identify unresolved issues and give a reason")
        url = row["url"]
        origin = _origin(url)
        parts = urlsplit(url)
        if (origin not in allowed_origins or not parts.path
                or re.search(r"/(?:search|search-list|search-results?)(?:\.(?:html?|php|do))?/?$", parts.path, re.I)):
            raise AnnotationResearchError("Source request is not a direct page on a supplied primary-source origin")
        checked.append({"url": url, "issue_ids": row["issue_ids"], "reason": row["reason"].strip()})
    return checked


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self.hidden += 1
    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "noscript", "svg"} and self.hidden:
            self.hidden -= 1
        elif tag.lower() in {"p", "div", "li", "br", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")
    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.parts.append(data.strip())


def _visible_text(raw: bytes, content_type: str) -> str:
    charset = "utf-8"
    match = re.search(r"charset\s*=\s*[\"']?([\w.-]+)", content_type, re.I)
    if match:
        charset = match.group(1)
    try:
        decoded = raw.decode(charset)
    except (UnicodeError, LookupError) as exc:
        raise AnnotationResearchError("Captured page has unsupported text encoding") from exc
    media = content_type.split(";", 1)[0].strip().lower()
    if media in {"text/html", "application/xhtml+xml"}:
        parser = _VisibleText()
        parser.feed(decoded)
        return re.sub(r"[ \t]+", " ", "\n".join(parser.parts)).strip()
    if media in {"text/plain", "application/json"}:
        return decoded.strip()
    if media == "application/pdf" or raw.startswith(b"%PDF-"):
        raise AnnotationResearchError("PDF capture is unsupported; the source remains an explicit evidence gap")
    raise AnnotationResearchError(f"Unsupported captured media type: {media or 'unknown'}")


def _check_public_host(host: str) -> None:
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise AnnotationResearchError("Source host resolves to a non-public address")
    except AnnotationResearchError:
        raise
    except (OSError, ValueError) as exc:
        raise AnnotationResearchError("Source host could not be safely resolved") from exc


class _SameOriginRedirect(HTTPRedirectHandler):
    def __init__(self, origin):
        self.origin = origin
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        joined = urljoin(req.full_url, newurl)
        if _origin(joined) != self.origin:
            raise AnnotationResearchError("Source redirected outside its supplied primary-source origin")
        _check_public_host(urlsplit(joined).hostname or "")
        return super().redirect_request(req, fp, code, msg, headers, joined)


def _fetch_primary_page(url: str, allowed_origins: set[tuple[str, str, int]]) -> dict:
    origin = _origin(url)
    if origin not in allowed_origins:
        raise AnnotationResearchError("Source URL origin was not supplied as a primary source")
    _check_public_host(origin[1])
    request = Request(url, headers={"User-Agent": "GradedReadersEvidenceCapture/1.0", "Accept": "text/html, text/plain, application/json"})
    opener = build_opener(ProxyHandler({}), _SameOriginRedirect(origin))
    try:
        with opener.open(request, timeout=CAPTURE_TIMEOUT_SECONDS) as response:
            final_url = response.geturl()
            if _origin(final_url) != origin:
                raise AnnotationResearchError("Captured page final URL changed origin")
            status = int(response.status)
            content_type = response.headers.get("Content-Type", "")
            raw = response.read(MAX_CAPTURE_BYTES + 1)
    except AnnotationResearchError:
        raise
    except (OSError, URLError, TimeoutError) as exc:
        raise AnnotationResearchError(f"Primary-source fetch failed: {type(exc).__name__}") from exc
    if status != 200:
        raise AnnotationResearchError(f"Primary-source returned HTTP {status}")
    if len(raw) > MAX_CAPTURE_BYTES:
        raise AnnotationResearchError("Primary-source page exceeded the capture size limit")
    text = _visible_text(raw, content_type)
    if not text:
        raise AnnotationResearchError("Captured primary-source page has no readable text")
    return {"requested_url": url, "final_url": final_url, "status": status,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "content_type": content_type, "bytes_sha256": hashlib.sha256(raw).hexdigest(),
            "text_sha256": _digest(text), "bytes": raw, "text": text}


def _persist_captures(run_dir: Path, request_digest: str, requests: list[dict],
                      references: dict) -> tuple[list[dict], list[dict]]:
    if not requests:
        return [], []
    allowed = _primary_origins(references)
    captured, failures = [], []
    root = _evidence_dir(run_dir, request_digest) / "captures"
    from pipeline.worker_paths import checked_directory, atomic_write_managed
    checked_directory(root, create=True)
    for index, request in enumerate(requests):
        try:
            page = _fetch_primary_page(request["url"], allowed)
            filename = page["bytes_sha256"] + ".bin"
            atomic_write_managed(root, filename, page["bytes"])
            page_meta = {key: value for key, value in page.items() if key != "bytes"}
            page_meta.update(index=index, issue_ids=request["issue_ids"], reason=request["reason"],
                             artifact=filename)
            _write_json(root / f"capture-{index:02d}.json", page_meta)
            reference_id = "captured-primary-" + _digest([request, page_meta["bytes_sha256"]])[:24]
            captured.append({"reference_id": reference_id, "kind": "primary_source",
                "issue_ids": request["issue_ids"], "content": {
                    "url": page["final_url"], "title": page["final_url"],
                    "captured_text": page["text"], "captured_text_sha256": page["text_sha256"],
                    "content_type": page["content_type"], "capture_digest": page["bytes_sha256"]}})
        except Exception as exc:
            failure = {"url": request["url"], "issue_ids": request["issue_ids"],
                       "reason": request["reason"], "gap": str(exc)}
            failures.append(failure)
            _write_json(root / f"capture-{index:02d}.json", {"index": index,
                "requested_url": request["url"], "issue_ids": request["issue_ids"],
                "reason": request["reason"], "failure": failure})
    return captured, failures


def _load_captures(run_dir: Path, request_digest: str, requests: list[dict],
                   references: dict) -> tuple[list[dict], list[dict]]:
    if not requests:
        return [], []
    root = _evidence_dir(run_dir, request_digest) / "captures"
    captured, failures = [], []
    allowed = _primary_origins(references)
    for index, request in enumerate(requests):
        try:
            meta = _read_json(root / f"capture-{index:02d}.json")
            if "failure" in meta:
                failure = meta["failure"]
                if (meta.get("requested_url") != request["url"]
                        or meta.get("issue_ids") != request["issue_ids"]
                        or meta.get("reason") != request["reason"]
                        or not isinstance(failure, dict) or failure.get("url") != request["url"]):
                    raise AnnotationResearchError("Captured source failure record changed")
                failures.append(failure)
                continue
            filename = meta.get("artifact")
            if (not isinstance(filename, str) or "/" in filename or "\\" in filename
                    or not filename.endswith(".bin")):
                raise AnnotationResearchError("Capture artifact path is unsafe")
            raw_path = root / filename
            if raw_path.is_symlink() or not raw_path.is_file():
                raise AnnotationResearchError("Captured source bytes are missing or unsafe")
            raw = raw_path.read_bytes()
            if len(raw) > MAX_CAPTURE_BYTES or hashlib.sha256(raw).hexdigest() != meta.get("bytes_sha256"):
                raise AnnotationResearchError("Captured primary-source bytes changed")
            if (meta.get("requested_url") != request["url"] or meta.get("issue_ids") != request["issue_ids"]
                    or meta.get("reason") != request["reason"] or meta.get("status") != 200
                    or _origin(meta.get("requested_url", "")) not in allowed
                    or _origin(meta.get("final_url", "")) != _origin(meta.get("requested_url", ""))):
                raise AnnotationResearchError("Captured source request or origin changed")
            text = _visible_text(raw, meta.get("content_type", ""))
            if _digest(text) != meta.get("text_sha256"):
                raise AnnotationResearchError("Captured source text extraction changed")
            reference_id = "captured-primary-" + _digest([request, meta["bytes_sha256"]])[:24]
            captured.append({"reference_id": reference_id, "kind": "primary_source",
                "issue_ids": request["issue_ids"], "content": {
                    "url": meta["final_url"], "title": meta["final_url"],
                    "captured_text": text, "captured_text_sha256": meta["text_sha256"],
                    "content_type": meta["content_type"], "capture_digest": meta["bytes_sha256"]}})
        except AnnotationResearchError:
            raise
        except Exception as exc:
            raise AnnotationResearchError("Captured primary-source receipt could not be replayed") from exc
    return captured, failures


def validate_research_submission(output: Any, request: Any) -> None:
    """Local worker-workspace gate; exact citation errors can get one correction."""
    if (not isinstance(request, dict) or not isinstance(request.get("issue_ids"), list)
            or not isinstance(request.get("known_reference_input"), dict)):
        raise AnnotationResearchError("Research validation context lacks issue/reference inputs")
    try:
        _validate_research_output(output, request["issue_ids"], request["known_reference_input"],policy_version=_policy_version(request))
    except Exception as exc:
        if isinstance(exc, AnnotationResearchError):
            raise
        raise AnnotationResearchError(f"Research artifact failed local semantic validation: {exc}") from exc


def _resolve_research_output(output: Any, issue_ids: list[str],
                             known_references: dict, *, policy_version: int = 2) -> tuple[list[dict], list[dict], str | None]:
    try:
        facts, unresolved = _validate_research_output(output, issue_ids, known_references,policy_version=policy_version)
        return facts, unresolved, None
    except Exception as exc:
        # Invalid or uncited worker claims are not evidence. Preserve the
        # worker artifact for the independent critic, but expose only gaps to
        # the adjudication wrapper.
        reason = str(exc) or type(exc).__name__
        gaps = [{"issue_id": issue_id, "status": "unresolved", "fact": "",
                 "citations": [],
                 "gap": "Research output could not be bound to supplied authoritative references."}
                for issue_id in issue_ids]
        return [], gaps, reason


def _build_inputs(*, language: str, representation: str, candidate: Any,
                  current_review: dict, prior_history: Any, context: Any,
                  source_text: str | None, deterministic_gate_evidence: dict,
                  initial_adjudication: dict, known_reference_input: dict,
                  normal_review_receipt: dict,
                  policy_version: int | None = None, run_dir: Path | None = None) -> dict:
    if language not in {"zh", "ja", "ko"}:
        raise AnnotationResearchError("Unsupported annotation language")
    if not isinstance(known_reference_input, dict):
        raise AnnotationResearchError("Known references must be a mapping")
    if policy_version is None:
        policy_version = RESEARCH_POLICY_VERSION
    if policy_version not in SUPPORTED_RESEARCH_POLICY_VERSIONS:
        raise AnnotationResearchError("Unsupported research policy version")
    issue_ids = _initial_uncertain_ids(initial_adjudication)
    gate = deterministic_gate_evidence
    if (not isinstance(gate, dict) or gate.get("passed") is not True or gate.get("issues")
            or gate.get("candidate_digest") != _digest(candidate)
            or (source_text is not None and gate.get("source_text_digest") != _digest(source_text))):
        raise AnnotationResearchError("Uncertainty research cannot override deterministic gate failures")
    _ = representation  # the initial adjudication verifier validates supported representations.
    refs = {}
    for identity, row in known_reference_input.items():
        if (not isinstance(identity, str) or not identity or not isinstance(row, dict)
                or row.get("kind") not in LINGUISTIC_REFERENCE_KINDS or "content" not in row):
            continue
        refs[identity] = {"kind": row["kind"], "content": row["content"]}
    transported = {}
    if policy_version >= 7 and isinstance(context, dict):
        from pipeline.annotation_run_lessons import (validate_run_lessons,
            theory_reference_context, REFERENCE_POLICY_FIELD, reference_policy_marker, validate_run_lesson_context_fields)
        validate_run_lesson_context_fields(context)
        nested = context.get('chunk_review_context', context)
        packet = context.get('reviewed_run_lessons', nested.get('reviewed_run_lessons'))
        if packet is not None:
            if run_dir is None:
                raise AnnotationResearchError('Theory transport requires authenticated run root')
            bound = validate_run_lessons(run_dir, packet, candidate=candidate,
                source_text=source_text, language=language, representation=representation,
                context=theory_reference_context(context), current_review=current_review)
            transported = {identity + ':theory-scope-v2': row for identity, row in bound['references'].items()
                           if set(row['issue_ids']) & set(issue_ids)}
            for identity, row in transported.items():
                if identity in refs and refs[identity] != {'kind':row['kind'],'content':row['content']}:
                    raise AnnotationResearchError('Transported theory conflicts with original reference')
                refs[identity] = {'kind':row['kind'],'content':row['content']}
    # Do not let a task manufacture a source by declaring its kind. These rows
    # are coordinator-supplied inputs and must be bound by the initial receipt.
    result = {
        "version": 1, "language": language, "representation": representation,
        "submission_validation_version": SUBMISSION_VALIDATION_VERSION,
        "candidate": candidate, "candidate_digest": _digest(candidate),
        "current_review": current_review, "review_digest": _digest(current_review),
        "prior_history": prior_history, "history_digest": _digest(prior_history),
        "context": context, "context_digest": _digest(context),
        "source_text": source_text, "source_text_digest": _digest(source_text),
        "deterministic_gate_evidence": deterministic_gate_evidence,
        "gate_digest": _digest(deterministic_gate_evidence),
        "initial_adjudication": initial_adjudication,
        "initial_adjudication_digest": _digest(initial_adjudication),
        "normal_review_receipt": normal_review_receipt,
        "normal_review_receipt_digest": _digest(normal_review_receipt),
        "known_reference_input": refs, "known_reference_digest": _digest(refs),
        "issue_ids": issue_ids, "research_policy_digest": _digest(_policies(policy_version)[0]),
        "review_policy_digest": _digest(_policies(policy_version)[1]),
    }
    if policy_version >= 7:
        result['run_lesson_theory_transport'] = {'version':1,'reference_policy':reference_policy_marker() if transported else None,'original_known_reference_digest':_digest(known_reference_input),'transported_references':transported,'transported_references_digest':_digest(transported)}
    if policy_version != 2:
        result["research_policy_version"] = policy_version
    if policy_version >= 4:
        result["uncertainty_tasks"] = _uncertainty_tasks(result)
    return result


def _uncertainty_tasks(inputs: dict) -> dict:
    from pipeline.annotation_review_ledger import resolve_pointer
    from pipeline.annotation_issue_targets import issue_target_paths
    normalized = normalize_review(inputs["language"], inputs["current_review"])
    current = {row["issue_id"]: row["issue"] for row in normalized["issues"]}
    initial = {row["issue_id"]: row for row in inputs["initial_adjudication"]["classifications"]}
    tasks = []
    for identity in inputs["issue_ids"]:
        if identity not in current or identity not in initial:
            raise AnnotationResearchError("Uncertain issue is not bound to the current review")
        classification = initial[identity]
        issue = current[identity]
        canonical_paths = issue_target_paths(issue, inputs["candidate"],
            source_text=inputs["source_text"], representation=inputs["representation"])
        paths = (canonical_paths if canonical_paths is not None
                 else classification.get("candidate_paths", []))
        observations = [{"path": path, "value": resolve_pointer(inputs["candidate"], path)}
                        for path in paths]
        supporting = issue.get("supporting_paths", []) if isinstance(issue, dict) else []
        record_paths = sorted({"/" + "/".join(path.split("/")[1:3]) for path in paths})
        tasks.append({"issue_id": identity, "current_review_issue": issue,
            "candidate_paths": paths, "candidate_observations": observations,
            "initial_classification_candidate_paths": classification.get("candidate_paths", []),
            "target_records": [{"path": path, "value": resolve_pointer(inputs["candidate"], path)}
                               for path in record_paths],
            "supporting_observations": [{"path": path,
                "value": resolve_pointer(inputs["candidate"], path)} for path in supporting],
            "initial_uncertainty_reason": classification.get("reason", ""),
            "initial_diagnosis": classification.get("diagnosis", "")})
    origins = [f"{scheme}://{host}" for scheme, host, port in
               sorted(_primary_origins(inputs["known_reference_input"]))]
    return {"version": 1, "tasks": tasks, "supplied_primary_origins": origins,
            "source_text": inputs["source_text"],
            "candidate_digest": inputs["candidate_digest"],
            "current_review_digest": inputs["review_digest"],
            "initial_adjudication_digest": inputs["initial_adjudication_digest"],
            "other_review_issues_are_context_only": True}


def _policies(version: int) -> tuple[str, str]:
    if version == 2:
        return RESEARCH_POLICY_V2, REVIEW_POLICY_V2
    if version == 3:
        return RESEARCH_POLICY_V3, REVIEW_POLICY_V3
    if version == 4:
        return RESEARCH_POLICY_V4, REVIEW_POLICY_V4
    if version == 5:
        return RESEARCH_POLICY_V5, REVIEW_POLICY_V5
    if version == 6:
        return RESEARCH_POLICY_V6, REVIEW_POLICY_V6
    if version == 7:
        return RESEARCH_POLICY_V6 + "\nAuthenticated transported standalone lessons are supplied theory, never borrowed occurrence approval. Inspect their exact pattern, formation and function before requesting duplicate research; only genuinely missing claims need investigation.", REVIEW_POLICY_V6 + "\nAuthenticate citations to supplied standalone theory and assess current scoped claims independently; a prior lesson approval does not approve this occurrence."
    raise AnnotationResearchError("Unsupported research policy version")


def _policy_version(inputs: dict) -> int:
    # v2 inputs predate this field; its absence is part of their saved digest.
    version = inputs.get("research_policy_version", 2)
    if version not in SUPPORTED_RESEARCH_POLICY_VERSIONS:
        raise AnnotationResearchError("Unsupported research policy version")
    return version


def _research_prompt(inputs: dict) -> str:
    version = _policy_version(inputs)
    policy, _ = _policies(version)
    tail = ("The exact request and supplied references are in the organized annotation_uncertainty_research input. "
            "Use only the unresolved finding IDs and reference IDs already supplied. If captured primary-source pages are present, cite their captured_text values by exact pointer. ")
    if version == 2:
        tail += "Do not issue further source requests during a continuation. Return the research schema.\n"
    elif inputs.get("source_capture"):
        tail += ("This is the single bounded continuation after a captured-page request. Cite relevant captured_text values by exact pointer. "
                 "No further source capture is available, so do not issue additional source requests; state any remaining gap. Return the research schema.\n")
    else:
        tail += ("This is the initial research pass. If an unresolved finding could be answered by a direct page on an already supplied primary-source origin, "
                 "request that exact page in source_requests with the finding ID and reason so it can be captured. Do not stop at a gap when that bounded request is available. "
                 "Return the research schema.\n")
    return policy + "\n\n" + tail


def _review_prompt(inputs: dict) -> str:
    _, policy = _policies(_policy_version(inputs))
    return (policy + "\n\nThe exact research artifact, citations resolved by the host, supplied references, and original issue context are in the organized annotation_uncertainty_research input. "
            "Judge the artifact as submitted; do not rewrite it. Return the generic usage-dictionary-review schema.\n")


def _reference_rows(inputs: dict, facts: list[dict]) -> dict:
    output = {}
    for row in facts:
        identity = "annotation-research-" + _digest([inputs["initial_adjudication_digest"], row["issue_id"]])[:24]
        content = {
            "_annotation_research_fact": True,
            "issue_ids": [row["issue_id"]],
            "fact": row["fact"],
            "citations": row["resolved_citations"],
            "candidate_digest": inputs["candidate_digest"],
            "review_digest": inputs["review_digest"],
            "context_digest": inputs["context_digest"],
            "source_text_digest": inputs["source_text_digest"],
        }
        if _policy_version(inputs)>=7:
            scoped_paths=sorted({path for citation in row['resolved_citations'] for path in citation.get('run_lesson_target_paths',[])})
            if scoped_paths:
                from pipeline.annotation_run_lessons import REFERENCE_POLICY_TEXT
                content['_annotation_run_lesson_scope']={'version':2,'policy_digest':_digest(REFERENCE_POLICY_TEXT),'issue_paths':{row['issue_id']:scoped_paths}}
        output[identity] = {"kind": "approved_lesson", "content": content,
                            "issue_ids": [row["issue_id"]]}
    return output


def _evidence_dir(run_dir: Path, request_digest: str) -> Path:
    from pipeline.worker_paths import checked_directory
    target = run_dir / "annotation-research" / request_digest
    return checked_directory(target, create=True)


def _reviewed_lesson_cache_path(run_dir: Path, scope_digest: str, *, create: bool) -> Path:
    from pipeline.worker_paths import checked_directory
    base = Path(run_dir) / "annotation-research" / "reviewed-lesson-cache"
    if base.is_symlink():
        raise AnnotationResearchError("Reviewed-lesson cache directory is unsafe")
    if create:
        checked_directory(base, create=True)
    elif not base.exists():
        return base / f"{scope_digest}.json"
    else:
        checked_directory(base)
    return base / f"{scope_digest}.json"


def _reviewed_lesson_source_manifest_path(run_dir: Path, *, create: bool) -> Path:
    from pipeline.worker_paths import checked_directory
    base = Path(run_dir) / "annotation-research"
    if base.is_symlink():
        raise AnnotationResearchError("Reviewed-lesson source manifest directory is unsafe")
    if create:
        checked_directory(base, create=True)
    elif not base.exists():
        return base / "reviewed-lesson-sources.json"
    else:
        checked_directory(base)
    return base / "reviewed-lesson-sources.json"


def _scope_call_args(*, language, representation, candidate, current_review,
        prior_history, context, source_text, deterministic_gate_evidence,
        initial_adjudication, known_reference_input, normal_review_receipt):
    return dict(language=language, representation=representation, candidate=candidate,
        current_review=current_review, prior_history=prior_history, context=context,
        source_text=source_text, deterministic_gate_evidence=deterministic_gate_evidence,
        initial_adjudication=initial_adjudication,
        known_reference_input=known_reference_input,
        normal_review_receipt=normal_review_receipt)


def _validate_reviewed_context_scope(expected_context: Any, inputs: dict) -> str:
    """Bind an imported lesson to the exact current uncertain field and source."""
    _validate_registered_lesson_context(expected_context)
    if not isinstance(expected_context, dict):
        raise AnnotationResearchError("Reviewed lesson requires structured source context")
    target = expected_context.get("target_occurrence")
    if not isinstance(target, dict):
        raise AnnotationResearchError("Reviewed lesson context lacks an exact target occurrence")
    target_path = target.get("target_path")
    if not isinstance(target_path, str) or not target_path.startswith("/"):
        raise AnnotationResearchError("Reviewed lesson target lacks an exact candidate path")
    classifications = inputs["initial_adjudication"].get("classifications", [])
    paths = {path for row in classifications if isinstance(row, dict)
             and row.get("issue_id") in inputs["issue_ids"]
             for path in row.get("candidate_paths", []) if isinstance(path, str)}
    if target_path not in paths:
        raise AnnotationResearchError("Reviewed lesson target does not match a current uncertain candidate field")
    try:
        value = resolve_pointer(inputs["candidate"], target_path)
    except (LedgerProtocolError, TypeError, KeyError) as exc:
        raise AnnotationResearchError("Reviewed lesson target path no longer resolves in the candidate") from exc
    if expected_context.get('reviewed_run_lesson_scope_version') == 2:
        from pipeline.annotation_run_lessons import _source_scope, _masked_overlay, _surfaces
        from pipeline.annotation_reference_carry import _position
        base, _ = _source_scope(expected_context)
        context = inputs['context']
        nested = context.get('chunk_review_context', context)
        try:
            position = _position(nested, inputs['source_text'])
        except ValueError as exc:
            raise AnnotationResearchError('Reviewed overlay lesson source position is invalid') from exc
        if (position is None or position['source_start'] != target['source_start']
                or position['parent_text_digest'] != _digest(expected_context['chapter']['text'])
                or target['source_text'] != inputs['source_text']
                or _surfaces(base) != _surfaces(inputs['candidate'])
                or _masked_overlay(base, target_path) != _masked_overlay(inputs['candidate'], target_path)):
            raise AnnotationResearchError('Reviewed overlay lesson differs from current source geometry')
        if not value:
            raise AnnotationResearchError('Reviewed lesson target field is empty')
        return target_path
    path_match = re.match(r"/segments/(\d+)(?:/|$)", target_path)
    segment_index = int(path_match.group(1)) if path_match else target.get("segment_index")
    surface = target.get("surface")
    candidate = inputs["candidate"]
    segments = candidate.get("segments") if isinstance(candidate, dict) else None
    if (type(segment_index) is not int or not isinstance(surface, str) or not surface
            or not isinstance(segments, list) or not 0 <= segment_index < len(segments)):
        raise AnnotationResearchError("Reviewed lesson target lacks an exact source-tap identity")
    segment = segments[segment_index]
    segment_surface = segment.get("text", segment.get("surface")) if isinstance(segment, dict) else None
    if not isinstance(segment_surface, str) or segment_surface != surface:
        raise AnnotationResearchError("Reviewed lesson target surface differs from the current candidate tap")
    occurrence_start = target.get("source_start")
    if type(occurrence_start) is not int or occurrence_start < 0:
        raise AnnotationResearchError("Reviewed lesson target lacks a source-slice offset")
    context = inputs["context"]
    chunk_context = context.get("chunk_review_context", {}) if isinstance(context, dict) else {}
    if not isinstance(chunk_context, dict):
        chunk_context = {}
    chapter_text = chunk_context.get("chapter_text") or (
        context.get("chapter_text") if isinstance(context, dict) else None)
    chunk_start = chunk_context.get("source_start", 0)
    if type(chunk_start) is not int:
        raise AnnotationResearchError("Current adjudication source offset is malformed")
    source = inputs.get("source_text")
    stored_source = target.get("source_text")
    if not isinstance(source, str) or (isinstance(stored_source, str) and stored_source != source):
        raise AnnotationResearchError("Reviewed lesson source slice differs from the current annotation input")
    if chunk_context and occurrence_start != chunk_start:
        raise AnnotationResearchError("Reviewed lesson source-slice offset differs from the current chunk")
    # The stored offset identifies the start of the source slice, not the
    # target word. Bind the word by its exact candidate segment and occurrence
    # order in that source slice; repeated surfaces remain distinct.
    occurrence_rank = sum(1 for row in segments[:segment_index]
        if isinstance(row, dict) and row.get("text", row.get("surface")) == surface)
    cursor = 0
    for _ in range(occurrence_rank + 1):
        local_start = source.find(surface, cursor)
        if local_start < 0:
            raise AnnotationResearchError("Reviewed lesson tap does not occur in the current source slice")
        cursor = local_start + len(surface)
    if isinstance(chapter_text, str) and (
            chapter_text[occurrence_start:occurrence_start + len(source)] != source):
        raise AnnotationResearchError("Reviewed lesson occurrence no longer matches current source text")
    expected_chapter = expected_context.get("chapter", {})
    if not isinstance(expected_chapter, dict):
        raise AnnotationResearchError("Reviewed lesson lacks chapter context")
    expected_chapter_text = expected_chapter.get("text")
    if (not isinstance(expected_chapter_text, str)
            or expected_chapter_text[occurrence_start:occurrence_start + len(source)] != source
            or (isinstance(chapter_text, str) and expected_chapter_text != chapter_text)):
        raise AnnotationResearchError("Reviewed lesson chapter context differs from current source span")
    if not value:
        raise AnnotationResearchError("Reviewed lesson target field is empty")
    return target_path


def _validate_registered_lesson_context(expected_context: Any) -> None:
    if not isinstance(expected_context, dict):
        raise AnnotationResearchError("Reviewed lesson requires structured source context")
    if expected_context.get('reviewed_run_lesson_scope_version') == 2:
        from pipeline.annotation_run_lessons import _source_scope, RunLessonError
        try:
            _source_scope(expected_context)
        except (RunLessonError, KeyError, IndexError, TypeError) as exc:
            raise AnnotationResearchError('Reviewed overlay lesson source scope is invalid') from exc
        return
    target = expected_context.get("target_occurrence")
    chapter = expected_context.get("chapter")
    if not isinstance(target, dict) or not isinstance(chapter, dict):
        raise AnnotationResearchError("Reviewed lesson context lacks chapter and target occurrence")
    path = target.get("target_path")
    match = re.fullmatch(r"/segments/(\d+)(?:/.*)", path) if isinstance(path, str) else None
    index = target.get("segment_index")
    surface = target.get("surface")
    start = target.get("source_start")
    source = target.get("source_text")
    chapter_text = chapter.get("text")
    segments = chapter.get("segments")
    if (not match or type(index) is not int or index != int(match.group(1))
            or not isinstance(surface, str) or not surface
            or type(start) is not int or start < 0
            or not isinstance(source, str) or not source
            or not isinstance(chapter_text, str)
            or not isinstance(segments, list) or not 0 <= index < len(segments)):
        raise AnnotationResearchError("Reviewed lesson lacks an exact source-slice and tap binding")
    if chapter_text[start:start + len(source)] != source:
        raise AnnotationResearchError("Reviewed lesson source slice does not match its chapter offset")
    segment = segments[index]
    segment_surface = segment.get("text", segment.get("surface")) if isinstance(segment, dict) else None
    if segment_surface != surface or surface not in source:
        raise AnnotationResearchError("Reviewed lesson tap differs from its chapter source slice")


def _reviewed_target_issue_ids(inputs: dict, target_path: str) -> list[str]:
    rows = inputs["initial_adjudication"].get("classifications", [])
    issue_ids = [row["issue_id"] for row in rows if isinstance(row, dict)
        and row.get("issue_id") in inputs["issue_ids"]
        and target_path in row.get("candidate_paths", [])]
    if not issue_ids:
        raise AnnotationResearchError("Reviewed lesson target is not bound to an uncertain finding")
    return sorted(set(issue_ids))


def register_reviewed_lesson_source(run_dir: Path, *, source_run_dir: Path,
        writer_job: str, reviewer_job: str, lesson_id: str,
        expected_context: dict) -> dict:
    """Register one explicitly named, independently reviewed lesson source.

    This records a source for later exact-case reuse; it does not create a
    current annotation reference or approve any candidate. No agent folders
    are searched. The source jobs are revalidated every time a matching case
    consumes the descriptor.
    """
    destination = Path(run_dir).resolve(strict=True)
    source_rel = _repo_relative_run_dir(source_run_dir)
    source = _resolve_repo_run_dir(source_rel)
    # Authenticate the named writer and critic before recording the descriptor.
    # The reserved marker is provenance-only and is never exposed as a lesson.
    source_proof = import_reviewed_lesson(destination, writer_job, reviewer_job,
        lesson_id=lesson_id, expected_context=expected_context,
        issue_ids=["_source_descriptor_registration"], source_run_dir=source,
        _persist=False)
    descriptor = {"version": REVIEWED_LESSON_SOURCE_MANIFEST_VERSION,
        "source_run_relpath": source_rel, "writer_job": writer_job,
        "reviewer_job": reviewer_job, "lesson_id": lesson_id,
        "expected_context": expected_context,
        "expected_context_digest": _digest(expected_context),
        "source_pair_evidence_digest": source_proof["evidence"]["evidence_digest"]}
    descriptor["descriptor_digest"] = _digest(descriptor)
    path = _reviewed_lesson_source_manifest_path(destination, create=True)
    if path.exists():
        manifest = _read_json(path)
        if (not isinstance(manifest, dict)
                or manifest.get("version") != REVIEWED_LESSON_SOURCE_MANIFEST_VERSION
                or not isinstance(manifest.get("sources"), list)):
            raise AnnotationResearchError("Reviewed-lesson source manifest is malformed")
    else:
        manifest = {"version": REVIEWED_LESSON_SOURCE_MANIFEST_VERSION, "sources": []}
    sources = manifest["sources"]
    identity = (source_rel, writer_job, reviewer_job, lesson_id)
    replaced = False
    for index, row in enumerate(sources):
        if not isinstance(row, dict):
            raise AnnotationResearchError("Reviewed-lesson source manifest row is malformed")
        if (row.get("source_run_relpath"), row.get("writer_job"),
                row.get("reviewer_job"), row.get("lesson_id")) == identity:
            sources[index] = descriptor
            replaced = True
            break
    if not replaced:
        if len(sources) >= MAX_REVIEWED_LESSON_SOURCES:
            raise AnnotationResearchError("Reviewed-lesson source manifest is full")
        sources.append(descriptor)
    manifest["manifest_digest"] = _digest({
        "version": manifest["version"], "sources": sources})
    _write_json(path, manifest)
    return descriptor


def _matching_reviewed_lesson_source(run_dir: Path, inputs: dict) -> dict | None:
    path = _reviewed_lesson_source_manifest_path(run_dir, create=False)
    if not path.exists():
        return None
    manifest = _read_json(path)
    if (not isinstance(manifest, dict)
            or manifest.get("version") != REVIEWED_LESSON_SOURCE_MANIFEST_VERSION
            or not isinstance(manifest.get("sources"), list)
            or len(manifest["sources"]) > MAX_REVIEWED_LESSON_SOURCES
            or manifest.get("manifest_digest") != _digest({
                "version": manifest.get("version"), "sources": manifest.get("sources")})):
        raise AnnotationResearchError("Reviewed-lesson source manifest failed integrity validation")
    classifications = inputs["initial_adjudication"].get("classifications", [])
    current_paths = {candidate_path for row in classifications if isinstance(row, dict)
        and row.get("issue_id") in inputs["issue_ids"]
        for candidate_path in row.get("candidate_paths", [])
        if isinstance(candidate_path, str)}
    matches = []
    for descriptor in manifest["sources"]:
        if not isinstance(descriptor, dict):
            raise AnnotationResearchError("Reviewed-lesson source descriptor is malformed")
        expected_digest = descriptor.get("descriptor_digest")
        body = {key: value for key, value in descriptor.items() if key != "descriptor_digest"}
        if (descriptor.get("version") != REVIEWED_LESSON_SOURCE_MANIFEST_VERSION
                or expected_digest != _digest(body)
                or descriptor.get("expected_context_digest") != _digest(
                    descriptor.get("expected_context"))):
            raise AnnotationResearchError("Reviewed-lesson source descriptor failed integrity validation")
        expected_context = descriptor.get("expected_context")
        target = expected_context.get("target_occurrence") if isinstance(expected_context, dict) else None
        if not isinstance(target, dict) or target.get("target_path") not in current_paths:
            continue
        target_path = target["target_path"]
        path_match = re.fullmatch(r"/segments/(\d+)(?:/.*)", target_path)
        if not path_match:
            raise AnnotationResearchError("Reviewed-lesson source pointer is unsupported")
        target_index = int(path_match.group(1))
        segments = inputs["candidate"].get("segments", []) if isinstance(inputs["candidate"], dict) else []
        if not isinstance(segments, list) or not 0 <= target_index < len(segments):
            continue
        segment = segments[target_index]
        current_surface = segment.get("text", segment.get("surface")) if isinstance(segment, dict) else None
        if current_surface != target.get("surface"):
            continue
        current_source = inputs.get("source_text")
        if target.get("source_text") != current_source:
            continue
        chunk_context = inputs["context"].get("chunk_review_context", {}) \
            if isinstance(inputs.get("context"), dict) else {}
        if (isinstance(chunk_context, dict)
                and type(chunk_context.get("source_start")) is int
                and target.get("source_start") != chunk_context["source_start"]):
            continue
        chapter_text = chunk_context.get("chapter_text") if isinstance(chunk_context, dict) else None
        if (isinstance(chapter_text, str)
                and expected_context.get("chapter", {}).get("text") != chapter_text):
            continue
        # Exact pointer and source match opts in to strict validation. Once it
        # matches, corrupted writer/critic evidence fails closed before payment.
        target_path = _validate_reviewed_context_scope(expected_context, inputs)
        source_rel = descriptor.get("source_run_relpath")
        source = _resolve_repo_run_dir(source_rel)
        try:
            proof = import_reviewed_lesson(run_dir, descriptor.get("writer_job"),
                descriptor.get("reviewer_job"), lesson_id=descriptor.get("lesson_id"),
                expected_context=expected_context,
                issue_ids=["_source_descriptor_registration"],
                source_run_dir=source, _persist=False)
        except (AnnotationResearchError, OSError, ValueError) as exc:
            raise AnnotationResearchError("Matching reviewed-lesson source jobs no longer verify") from exc
        if proof["evidence"]["evidence_digest"] != descriptor.get("source_pair_evidence_digest"):
            raise AnnotationResearchError("Matching reviewed-lesson writer/reviewer evidence changed")
        matches.append((descriptor, target_path))
    if len(matches) > 1:
        raise AnnotationResearchError("Multiple reviewed-lesson sources match the same annotation target")
    return matches[0][0] if matches else None


def register_reviewed_lesson_cache(run_dir: Path, *, source_run_dir: Path,
        writer_job: str, reviewer_job: str, lesson_id: str,
        expected_context: dict, language: str, representation: str,
        candidate: Any, current_review: dict, prior_history: Any, context: Any,
        source_text: str | None, deterministic_gate_evidence: dict,
        initial_adjudication: dict, known_reference_input: dict,
        normal_review_receipt: dict) -> dict:
    """Explicitly register one reviewed lesson against one exact uncertain case.

    This never searches agent folders. The coordinator names the reviewed
    writer/reviewer jobs and their source run; the imported lesson remains a
    synthetic, issue-scoped reference and is not promoted to a registry.
    """
    args = _scope_call_args(language=language, representation=representation,
        candidate=candidate, current_review=current_review, prior_history=prior_history,
        context=context, source_text=source_text,
        deterministic_gate_evidence=deterministic_gate_evidence,
        initial_adjudication=initial_adjudication,
        known_reference_input=known_reference_input,
        normal_review_receipt=normal_review_receipt)
    inputs = _build_inputs(**args, run_dir=run_dir)
    target_path = _validate_reviewed_context_scope(expected_context, inputs)
    destination = Path(run_dir).resolve(strict=True)
    source_rel = _repo_relative_run_dir(source_run_dir)
    source = _resolve_repo_run_dir(source_rel)
    try:
        verify_adjudication_evidence(destination, initial_adjudication,
            language=language, representation=representation, candidate=candidate,
            current_review=current_review, prior_history=prior_history, context=context,
            known_reference_input=known_reference_input,
            deterministic_gate_evidence=deterministic_gate_evidence,
            normal_review_receipt=normal_review_receipt, source_text=source_text)
    except (AdjudicationError, ValueError, OSError) as exc:
        raise AnnotationResearchError("Reviewed-lesson cache requires replayable uncertain adjudication") from exc
    issue_ids = _reviewed_target_issue_ids(inputs, target_path)
    imported = import_reviewed_lesson(destination, writer_job, reviewer_job,
        lesson_id=lesson_id, expected_context=expected_context, issue_ids=issue_ids,
        source_run_dir=source)
    if set(imported["references"]) & set(inputs["known_reference_input"]):
        raise AnnotationResearchError("Synthetic reviewed-lesson reference collides with supplied references")
    scope_digest = _digest(inputs)
    evidence = {"version": REVIEWED_LESSON_CACHE_VERSION,
        "kind": "reviewed_lesson_cache", "status": "reviewed_lesson_reused",
        "candidate_approved": False, "scope_digest": scope_digest,
        "inputs": inputs, "source_run_relpath": source_rel,
        "imported_expected_context": expected_context,
        "expected_context_digest": _digest(expected_context),
        "imported_lesson_evidence": imported["evidence"],
        "references": imported["references"],
        "references_digest": _digest(imported["references"])}
    evidence["evidence_digest"] = _digest(evidence)
    path = _reviewed_lesson_cache_path(destination, scope_digest, create=True)
    if path.exists():
        if path.is_symlink() or _read_json(path) != evidence:
            raise AnnotationResearchError("A different reviewed-lesson cache receipt already exists for this scope")
    else:
        _write_json(path, evidence)
    return {"status": "reviewed_lesson_reused", "references": imported["references"],
            "evidence": evidence}


def _verify_reviewed_lesson_cache(run_dir: Path, evidence: dict, *, language: str,
        representation: str, candidate: Any, current_review: dict, prior_history: Any,
        context: Any, source_text: str | None, deterministic_gate_evidence: dict,
        initial_adjudication: dict, known_reference_input: dict,
        normal_review_receipt: dict) -> dict:
    if (not isinstance(evidence, dict)
            or evidence.get("version") != REVIEWED_LESSON_CACHE_VERSION
            or evidence.get("kind") != "reviewed_lesson_cache"):
        raise AnnotationResearchError("Unsupported reviewed-lesson cache receipt")
    stored_inputs = evidence.get("inputs") if isinstance(evidence, dict) else None
    policy_version = (stored_inputs.get("research_policy_version", 2)
                      if isinstance(stored_inputs, dict) else 2)
    inputs = _build_inputs(run_dir=run_dir, language=language, representation=representation,
        candidate=candidate, current_review=current_review, prior_history=prior_history,
        context=context, source_text=source_text,
        deterministic_gate_evidence=deterministic_gate_evidence,
        initial_adjudication=initial_adjudication,
        known_reference_input=known_reference_input,
        normal_review_receipt=normal_review_receipt, policy_version=policy_version)
    scope_digest = _digest(inputs)
    if evidence.get("scope_digest") != scope_digest or evidence.get("inputs") != inputs:
        raise AnnotationResearchError("Reviewed-lesson cache scope changed")
    expected_context = evidence.get("imported_expected_context")
    target_path = _validate_reviewed_context_scope(expected_context, inputs)
    try:
        verify_adjudication_evidence(run_dir, initial_adjudication,
            language=language, representation=representation, candidate=candidate,
            current_review=current_review, prior_history=prior_history, context=context,
            known_reference_input=known_reference_input,
            deterministic_gate_evidence=deterministic_gate_evidence,
            normal_review_receipt=normal_review_receipt, source_text=source_text)
    except (AdjudicationError, ValueError, OSError) as exc:
        raise AnnotationResearchError("Reviewed-lesson cache adjudication scope no longer replays") from exc
    source = _resolve_repo_run_dir(evidence.get("source_run_relpath"))
    issue_ids = _reviewed_target_issue_ids(inputs, target_path)
    imported = verify_imported_reviewed_lesson(run_dir,
        evidence.get("imported_lesson_evidence"),
        lesson_id=evidence.get("imported_lesson_evidence", {}).get("lesson_id"),
        expected_context=evidence.get("imported_expected_context", {}),
        issue_ids=issue_ids, source_run_dir=source)
    expected = {"version": REVIEWED_LESSON_CACHE_VERSION,
        "kind": "reviewed_lesson_cache", "status": "reviewed_lesson_reused",
        "candidate_approved": False, "scope_digest": scope_digest,
        "inputs": inputs, "source_run_relpath": evidence["source_run_relpath"],
        "imported_expected_context": evidence.get("imported_expected_context"),
        "expected_context_digest": _digest(evidence.get("imported_expected_context")),
        "imported_lesson_evidence": evidence["imported_lesson_evidence"],
        "references": imported["references"],
        "references_digest": _digest(imported["references"])}
    expected["evidence_digest"] = _digest(expected)
    receipt_path = _reviewed_lesson_cache_path(run_dir, scope_digest, create=False)
    if (evidence != expected or not receipt_path.is_file() or receipt_path.is_symlink()
            or _read_json(receipt_path) != expected):
        raise AnnotationResearchError("Reviewed-lesson cache receipt does not replay exactly")
    return {"status": "reviewed_lesson_reused", "references": imported["references"],
            "evidence": expected}


def _result_shape(evidence: dict) -> dict:
    return {"status": evidence["status"], "references": evidence["references"], "evidence": evidence}


async def research_uncertain_review(runner: Any, run_dir: Path, *, language: str,
        representation: str, candidate: Any, current_review: dict, prior_history: Any,
        context: Any, source_text: str | None, deterministic_gate_evidence: dict,
        initial_adjudication: dict, known_reference_input: dict,
        normal_review_receipt: dict) -> dict:
    """Research only the uncertain findings, then independently review exact facts.

    The returned references are scoped coordinator artifacts, not dictionary
    promotions. Unresolved findings remain unresolved and are never converted
    into candidate edits or new external-source evidence.
    """
    _check_runner_policy(runner)
    inputs = _build_inputs(run_dir=run_dir, language=language, representation=representation,
        candidate=candidate, current_review=current_review, prior_history=prior_history,
        context=context, source_text=source_text,
        deterministic_gate_evidence=deterministic_gate_evidence,
        initial_adjudication=initial_adjudication,
        known_reference_input=known_reference_input,
        normal_review_receipt=normal_review_receipt)
    run_dir = Path(run_dir).resolve()
    try:
        verify_adjudication_evidence(run_dir, initial_adjudication,
            language=language, representation=representation, candidate=candidate,
            current_review=current_review, prior_history=prior_history, context=context,
            known_reference_input=known_reference_input,
            deterministic_gate_evidence=deterministic_gate_evidence,
            normal_review_receipt=normal_review_receipt, source_text=source_text)
    except (AdjudicationError, ValueError, OSError) as exc:
        raise AnnotationResearchError("Initial uncertain adjudication does not replay") from exc

    request_digest = _digest(inputs)
    cache_path = _reviewed_lesson_cache_path(run_dir, request_digest, create=False)
    if cache_path.exists():
        cached_lesson = _read_json(cache_path)
        return _verify_reviewed_lesson_cache(run_dir, cached_lesson,
            language=language, representation=representation, candidate=candidate,
            current_review=current_review, prior_history=prior_history, context=context,
            source_text=source_text, deterministic_gate_evidence=deterministic_gate_evidence,
            initial_adjudication=initial_adjudication,
            known_reference_input=known_reference_input,
            normal_review_receipt=normal_review_receipt)
    descriptor = _matching_reviewed_lesson_source(run_dir, inputs)
    if descriptor is not None:
        return register_reviewed_lesson_cache(run_dir,
            source_run_dir=_resolve_repo_run_dir(descriptor["source_run_relpath"]),
            writer_job=descriptor["writer_job"], reviewer_job=descriptor["reviewer_job"],
            lesson_id=descriptor["lesson_id"],
            expected_context=descriptor["expected_context"],
            language=language, representation=representation, candidate=candidate,
            current_review=current_review, prior_history=prior_history, context=context,
            source_text=source_text,
            deterministic_gate_evidence=deterministic_gate_evidence,
            initial_adjudication=initial_adjudication,
            known_reference_input=known_reference_input,
            normal_review_receipt=normal_review_receipt)
    evidence_dir = _evidence_dir(run_dir, request_digest)
    cached_path = evidence_dir / "evidence.json"
    if cached_path.exists():
        cached = _read_json(cached_path)
        return verify_research_evidence(run_dir, cached, language=language,
            representation=representation, candidate=candidate, current_review=current_review,
            prior_history=prior_history, context=context, source_text=source_text,
            deterministic_gate_evidence=deterministic_gate_evidence,
            initial_adjudication=initial_adjudication,
            known_reference_input=known_reference_input,
            normal_review_receipt=normal_review_receipt)
    _write_json(evidence_dir / "research-input.json", inputs)
    if not inputs["known_reference_input"]:
        evidence = {"version": RESEARCH_EVIDENCE_VERSION, "status": "unresolved", "approved": False,
                    "preflight_reason": "no supplied authoritative references",
                    "request_digest": request_digest, "input_digest": request_digest,
                    "inputs": inputs, "references": {}, "references_digest": _digest({})}
        evidence["evidence_digest"] = _digest(evidence)
        _write_json(evidence_dir / "evidence.json", evidence)
        return _result_shape(evidence)
    research_context = _worker_context("research", inputs)
    research_prompt = _research_prompt(inputs)
    research_schema_path = _schema_path(run_dir, "research", RESEARCH_SCHEMA)
    research_job = f"annotation-uncertainty-research-{request_digest}"
    first_research_output = await runner.call(research_job, research_prompt,
        research_schema_path, "low", tool_profile="workspace",
        workspace_context=research_context)
    first_facts, first_unresolved, first_error = _resolve_research_output(
        first_research_output, inputs["issue_ids"], inputs["known_reference_input"],policy_version=_policy_version(inputs))
    source_requests = []
    if first_error is None:
        source_requests = _validated_source_requests(first_research_output, inputs["issue_ids"],
                                                      inputs["known_reference_input"], first_unresolved)
    captured_references, capture_failures = _persist_captures(
        run_dir, request_digest, source_requests, inputs["known_reference_input"])
    final_research_output = first_research_output
    final_references = dict(inputs["known_reference_input"])
    for row in captured_references:
        final_references[row["reference_id"]] = {"kind": row["kind"], "content": row["content"]}
    continuation_job = None
    continuation_meta = None
    continuation_context = None
    continuation_prompt = None
    if captured_references:
        continuation_inputs = {**inputs,
            "known_reference_input": final_references,
            "known_reference_digest": _digest(final_references),
            "source_capture": {"references": captured_references,
                               "failures": capture_failures,
                               "initial_research_result": first_research_output,
                               "initial_result_digest": _digest(first_research_output)},
            "source_capture_digest": _digest({"references": captured_references,
                                               "failures": capture_failures}),
        }
        continuation_context = _worker_context("research", continuation_inputs)
        continuation_prompt = _research_prompt(continuation_inputs)
        continuation_job = f"annotation-uncertainty-continuation-{_digest([request_digest, _digest(first_research_output), continuation_inputs['source_capture_digest']])}"
        final_research_output = await runner.call(continuation_job, continuation_prompt,
            research_schema_path, "low", tool_profile="workspace",
            workspace_context=continuation_context)
        _write_json(evidence_dir / "continuation-result.json", final_research_output)
        # A continuation is a single bounded pass. It may not open another
        # network round; an additional request remains an unresolved gap.
        if isinstance(final_research_output, dict) and final_research_output.get("source_requests"):
            final_facts, final_unresolved, final_error = [], [
                {"issue_id": issue, "status": "unresolved", "fact": "", "citations": [],
                 "gap": "The bounded source continuation requested another page; further capture is not available."}
                for issue in inputs["issue_ids"]], "Continuation requested an additional source capture"
        else:
            final_facts, final_unresolved, final_error = _resolve_research_output(
                final_research_output, inputs["issue_ids"], final_references,policy_version=_policy_version(inputs))
        continuation_meta, _ = _verify_job(run_dir, job=continuation_job,
            expected_prompt=continuation_prompt, schema_text=research_schema_path.read_text(encoding="utf-8"),
            workspace_context=continuation_context, expected_result=final_research_output)
    else:
        final_facts, final_unresolved, final_error = first_facts, first_unresolved, first_error
    _write_json(evidence_dir / "research-result.json", first_research_output)
    if continuation_job is None:
        _write_json(evidence_dir / "final-research-result.json", final_research_output)
    resolved_research = {"facts": final_facts, "unresolved": final_unresolved,
                         "validation_error": final_error,
                         "capture_failures": capture_failures,
                         "captured_reference_ids": [row["reference_id"] for row in captured_references]}
    review_inputs = {**inputs, "known_reference_input": final_references,
                     "known_reference_digest": _digest(final_references),
                     "research_result": final_research_output,
                     "resolved_research": resolved_research,
                     "research_result_digest": _digest(final_research_output),
                     "resolved_research_digest": _digest(resolved_research),
                     "source_capture": {"requests": source_requests,
                                        "references": captured_references,
                                        "failures": capture_failures,
                                        "first_result_digest": _digest(first_research_output)}}
    _write_json(evidence_dir / "review-input.json", review_inputs)
    review_context = _worker_context("review", review_inputs)
    review_prompt = _review_prompt(review_inputs)
    review_schema_path = Path(__file__).resolve().parent / "schemas" / "usage-dictionary-review.schema.json"
    review_schema = json.loads(review_schema_path.read_text(encoding="utf-8"))
    review_schema_digest = hashlib.sha256(review_schema_path.read_bytes()).hexdigest()
    review_job = f"annotation-uncertainty-review-{_digest([request_digest, _digest(final_research_output), review_schema_digest])}"
    review_output = await runner.call(review_job, review_prompt, review_schema_path,
        "low", tool_profile="workspace", workspace_context=review_context)
    if not isinstance(review_output, dict) or type(review_output.get("approved")) is not bool or not isinstance(review_output.get("issues"), list):
        raise AnnotationResearchError("Independent research review returned malformed result")
    _write_json(evidence_dir / "review-result.json", review_output)
    approved = review_output["approved"] is True and not review_output["issues"]
    references = _reference_rows(inputs, final_facts) if approved and final_facts else {}
    status = "approved" if references else "unresolved"
    research_meta, checked_research = _verify_job(run_dir, job=research_job,
        expected_prompt=research_prompt, schema_text=research_schema_path.read_text(encoding="utf-8"),
        workspace_context=research_context, expected_result=first_research_output)
    review_meta, checked_review = _verify_job(run_dir, job=review_job,
        expected_prompt=review_prompt, schema_text=review_schema_path.read_text(encoding="utf-8"),
        workspace_context=review_context, expected_result=review_output)
    final_meta = continuation_meta or research_meta
    checked_final = final_research_output
    evidence = {
        "version": RESEARCH_EVIDENCE_VERSION, "status": status, "approved": approved,
        "request_digest": request_digest, "input_digest": request_digest,
        "inputs": inputs, "initial_adjudication_digest": inputs["initial_adjudication_digest"],
        "research_job": research_job, "research_fingerprint": research_meta["fingerprint"],
        "research_result_digest": _digest(checked_final),
        "research_workspace_digest": final_meta["workspace_digest"],
        "research_model": final_meta["model"], "research_effort": final_meta["effort"],
        "research_tool_profile": final_meta["tool_profile"],
        "initial_research_job": research_job,
        "initial_research_fingerprint": research_meta["fingerprint"],
        "initial_research_result_digest": _digest(checked_research),
        "continuation_job": continuation_job,
        "continuation_fingerprint": continuation_meta["fingerprint"] if continuation_meta else None,
        "source_requests": source_requests,
        "captured_references": captured_references,
        "capture_failures": capture_failures,
        "resolved_research": resolved_research,
        "review_job": review_job, "review_fingerprint": review_meta["fingerprint"],
        "review_result_digest": _digest(checked_review),
        "review_workspace_digest": review_meta["workspace_digest"],
        "review_model": review_meta["model"], "review_effort": review_meta["effort"],
        "review_tool_profile": review_meta["tool_profile"],
        "review_schema_digest": review_schema_digest,
        "review_output": review_output,
        "references": references, "references_digest": _digest(references),
    }
    evidence["evidence_digest"] = _digest(evidence)
    _write_json(evidence_dir / "evidence.json", evidence)
    return _result_shape(evidence)


def verify_research_evidence(run_dir: Path, evidence: dict, *, language: str,
        representation: str, candidate: Any, current_review: dict, prior_history: Any,
        context: Any, source_text: str | None, deterministic_gate_evidence: dict,
        initial_adjudication: dict, known_reference_input: dict,
        normal_review_receipt: dict) -> dict:
    """Synchronously replay exact research and reviewer artifacts; never calls a model."""
    if isinstance(evidence, dict) and evidence.get("kind") == "reviewed_lesson_cache":
        return _verify_reviewed_lesson_cache(run_dir, evidence, language=language,
            representation=representation, candidate=candidate, current_review=current_review,
            prior_history=prior_history, context=context, source_text=source_text,
            deterministic_gate_evidence=deterministic_gate_evidence,
            initial_adjudication=initial_adjudication,
            known_reference_input=known_reference_input,
            normal_review_receipt=normal_review_receipt)
    if (not isinstance(evidence, dict)
            or evidence.get("version") not in SUPPORTED_RESEARCH_POLICY_VERSIONS):
        raise AnnotationResearchError("Unsupported or missing research evidence version")
    policy_version = evidence["version"]
    inputs = _build_inputs(run_dir=run_dir, language=language, representation=representation,
        candidate=candidate, current_review=current_review, prior_history=prior_history,
        context=context, source_text=source_text,
        deterministic_gate_evidence=deterministic_gate_evidence,
        initial_adjudication=initial_adjudication,
        known_reference_input=known_reference_input,
        normal_review_receipt=normal_review_receipt, policy_version=policy_version)
    run_dir = Path(run_dir).resolve(strict=True)
    try:
        verify_adjudication_evidence(run_dir, initial_adjudication,
            language=language, representation=representation, candidate=candidate,
            current_review=current_review, prior_history=prior_history, context=context,
            known_reference_input=known_reference_input,
            deterministic_gate_evidence=deterministic_gate_evidence,
            normal_review_receipt=normal_review_receipt, source_text=source_text)
    except (AdjudicationError, ValueError, OSError) as exc:
        raise AnnotationResearchError("Initial uncertain adjudication no longer replays") from exc
    request_digest = _digest(inputs)
    evidence_dir = _evidence_dir(run_dir, request_digest)
    stored_inputs = _read_json(evidence_dir / "research-input.json")
    if stored_inputs != inputs or evidence.get("request_digest") != request_digest:
        raise AnnotationResearchError("Research request inputs changed")
    if not inputs["known_reference_input"]:
        expected = {"version": policy_version, "status": "unresolved", "approved": False,
                    "preflight_reason": "no supplied authoritative references",
                    "request_digest": request_digest, "input_digest": request_digest,
                    "inputs": inputs, "references": {}, "references_digest": _digest({})}
        expected["evidence_digest"] = _digest(expected)
        if evidence != expected or _read_json(evidence_dir / "evidence.json") != expected:
            raise AnnotationResearchError("No-reference preflight evidence changed")
        return _result_shape(expected)

    research_context = _worker_context("research", inputs)
    research_prompt = _research_prompt(inputs)
    research_job = f"annotation-uncertainty-research-{request_digest}"
    research_meta, research_output = _verify_job(run_dir, job=research_job,
        expected_prompt=research_prompt,
        schema_text=_schema_path(run_dir, "research", RESEARCH_SCHEMA).read_text(encoding="utf-8"),
        workspace_context=research_context, expected_result=None)
    if _read_json(evidence_dir / "research-result.json") != research_output:
        raise AnnotationResearchError("Persisted initial researcher result differs from the worker result")
    first_facts, first_unresolved, first_error = _resolve_research_output(
        research_output, inputs["issue_ids"], inputs["known_reference_input"],policy_version=_policy_version(inputs))
    source_requests = []
    if first_error is None:
        source_requests = _validated_source_requests(research_output, inputs["issue_ids"],
                                                      inputs["known_reference_input"], first_unresolved)
    if evidence.get("source_requests") != source_requests:
        raise AnnotationResearchError("Primary-source requests changed")
    captured_references, capture_failures = _load_captures(
        run_dir, request_digest, source_requests, inputs["known_reference_input"])
    if (evidence.get("captured_references") != captured_references
            or evidence.get("capture_failures") != capture_failures):
        raise AnnotationResearchError("Captured primary-source evidence changed")
    final_references = dict(inputs["known_reference_input"])
    for row in captured_references:
        final_references[row["reference_id"]] = {"kind": row["kind"], "content": row["content"]}
    continuation_job = None
    continuation_meta = None
    if captured_references:
        continuation_inputs = {**inputs,
            "known_reference_input": final_references,
            "known_reference_digest": _digest(final_references),
            "source_capture": {"references": captured_references,
                               "failures": capture_failures,
                               "initial_research_result": research_output,
                               "initial_result_digest": _digest(research_output)},
            "source_capture_digest": _digest({"references": captured_references,
                                               "failures": capture_failures}),
        }
        continuation_context = _worker_context("research", continuation_inputs)
        continuation_prompt = _research_prompt(continuation_inputs)
        continuation_job = f"annotation-uncertainty-continuation-{_digest([request_digest, _digest(research_output), continuation_inputs['source_capture_digest']])}"
        if evidence.get("continuation_job") != continuation_job:
            raise AnnotationResearchError("Continuation job identity changed")
        continuation_meta, final_research_output = _verify_job(run_dir, job=continuation_job,
            expected_prompt=continuation_prompt,
            schema_text=_schema_path(run_dir, "research", RESEARCH_SCHEMA).read_text(encoding="utf-8"),
            workspace_context=continuation_context, expected_result=None)
        if _read_json(evidence_dir / "continuation-result.json") != final_research_output:
            raise AnnotationResearchError("Continuation researcher result changed")
        if isinstance(final_research_output, dict) and final_research_output.get("source_requests"):
            final_facts, final_unresolved, final_error = [], [
                {"issue_id": issue, "status": "unresolved", "fact": "", "citations": [],
                 "gap": "The bounded source continuation requested another page; further capture is not available."}
                for issue in inputs["issue_ids"]], "Continuation requested an additional source capture"
        else:
            final_facts, final_unresolved, final_error = _resolve_research_output(
                final_research_output, inputs["issue_ids"], final_references,policy_version=_policy_version(inputs))
    else:
        final_research_output = research_output
        final_facts, final_unresolved, final_error = first_facts, first_unresolved, first_error
        if evidence.get("continuation_job") is not None:
            raise AnnotationResearchError("Unexpected continuation worker without captured source pages")
    resolved_research = {"facts": final_facts, "unresolved": final_unresolved,
                         "validation_error": final_error,
                         "capture_failures": capture_failures,
                         "captured_reference_ids": [row["reference_id"] for row in captured_references]}
    review_inputs = {**inputs, "known_reference_input": final_references,
                     "known_reference_digest": _digest(final_references),
                     "research_result": final_research_output,
                     "resolved_research": resolved_research,
                     "research_result_digest": _digest(final_research_output),
                     "resolved_research_digest": _digest(resolved_research),
                     "source_capture": {"requests": source_requests,
                                        "references": captured_references,
                                        "failures": capture_failures,
                                        "first_result_digest": _digest(research_output)}}
    if _read_json(evidence_dir / "review-input.json") != review_inputs:
        raise AnnotationResearchError("Independent reviewer inputs changed")
    review_context = _worker_context("review", review_inputs)
    review_prompt = _review_prompt(review_inputs)
    review_schema_path = Path(__file__).resolve().parent / "schemas" / "usage-dictionary-review.schema.json"
    review_schema = json.loads(review_schema_path.read_text(encoding="utf-8"))
    review_schema_digest = hashlib.sha256(review_schema_path.read_bytes()).hexdigest()
    if evidence.get("review_schema_digest") != review_schema_digest:
        raise AnnotationResearchError("Independent review schema changed")
    review_job = f"annotation-uncertainty-review-{_digest([request_digest, _digest(final_research_output), review_schema_digest])}"
    review_meta, review_output = _verify_job(run_dir, job=review_job,
        expected_prompt=review_prompt, schema_text=review_schema_path.read_text(encoding="utf-8"),
        workspace_context=review_context, expected_result=None)
    if (review_output != _read_json(evidence_dir / "review-result.json")
            or review_output != evidence.get("review_output")):
        raise AnnotationResearchError("Independent review output changed")
    approved = review_output.get("approved") is True and review_output.get("issues") == []
    references = _reference_rows(inputs, final_facts) if approved and final_facts else {}
    status = "approved" if references else "unresolved"
    final_meta = continuation_meta or research_meta
    expected = {
        "version": policy_version, "status": status, "approved": approved,
        "request_digest": request_digest, "input_digest": request_digest,
        "inputs": inputs, "initial_adjudication_digest": inputs["initial_adjudication_digest"],
        "research_job": research_job, "research_fingerprint": research_meta["fingerprint"],
        "research_result_digest": _digest(final_research_output),
        "research_workspace_digest": final_meta["workspace_digest"],
        "research_model": final_meta["model"], "research_effort": final_meta["effort"],
        "research_tool_profile": final_meta["tool_profile"],
        "initial_research_job": research_job,
        "initial_research_fingerprint": research_meta["fingerprint"],
        "initial_research_result_digest": _digest(research_output),
        "continuation_job": continuation_job,
        "continuation_fingerprint": continuation_meta["fingerprint"] if continuation_meta else None,
        "source_requests": source_requests,
        "captured_references": captured_references,
        "capture_failures": capture_failures,
        "resolved_research": resolved_research,
        "review_job": review_job, "review_fingerprint": review_meta["fingerprint"],
        "review_result_digest": _digest(review_output),
        "review_workspace_digest": review_meta["workspace_digest"],
        "review_model": review_meta["model"], "review_effort": review_meta["effort"],
        "review_tool_profile": review_meta["tool_profile"],
        "review_schema_digest": review_schema_digest,
        "review_output": review_output,
        "references": references, "references_digest": _digest(references),
    }
    expected["evidence_digest"] = _digest(expected)
    if evidence != expected or _read_json(evidence_dir / "evidence.json") != expected:
        raise AnnotationResearchError("Research and review receipts do not replay exactly")
    return _result_shape(expected)


def _job_context_inputs(run_dir: Path, job: str) -> tuple[Path, dict, dict, dict]:
    job_dir = _safe_job_dir(run_dir, job)
    meta = _read_json(job_dir / "meta.json")
    from pipeline.agent_harness import CodexRunner
    if (meta.get("return_code") != 0 or meta.get("model") != "gpt-6-luna"
            or meta.get("effort") != "low" or meta.get("tool_profile") != "workspace"
            or not isinstance(meta.get("fingerprint"), str)
            or re.fullmatch(r"[0-9a-f]{64}", meta["fingerprint"]) is None):
        raise AnnotationResearchError("Dictionary evidence lacks a completed low-Luna workspace receipt")
    try:
        CodexRunner._check_tool_profile(job_dir, "workspace", meta)
        inputs = _workspace_inputs(job_dir, meta)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise AnnotationResearchError("Dictionary workspace failed exact manifest/profile verification") from exc
    result = _read_json(job_dir / "result.json")
    return job_dir, meta, inputs, result


def _repository_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _repo_relative_run_dir(path: Path) -> str:
    import os
    root = _repository_root()
    path = Path(os.path.abspath(Path(path)))
    if not path.is_relative_to(root):
        raise AnnotationResearchError("Reviewed source run must be inside this repository")
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise AnnotationResearchError("Reviewed source run path contains a symlink")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AnnotationResearchError("Reviewed source run is unavailable") from exc
    if resolved != path or not resolved.is_dir():
        raise AnnotationResearchError("Reviewed source run path is unsafe")
    return path.relative_to(root).as_posix()


def _resolve_repo_run_dir(relative: str) -> Path:
    root = _repository_root()
    if (not isinstance(relative, str) or not relative or "\\" in relative
            or Path(relative).is_absolute()
            or any(part in {"", ".", ".."} for part in Path(relative).parts)):
        raise AnnotationResearchError("Cached reviewed-source path is unsafe")
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise AnnotationResearchError("Cached reviewed-source path contains a symlink")
    try:
        resolved = current.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AnnotationResearchError("Cached reviewed-source run is unavailable") from exc
    if not resolved.is_relative_to(root) or not resolved.is_dir():
        raise AnnotationResearchError("Cached reviewed-source run escaped repository")
    return resolved


def import_reviewed_lesson(run_dir: Path, writer_job: str, reviewer_job: str, *,
        lesson_id: str, expected_context: dict, issue_ids: list[str],
        source_run_dir: Path | None = None, _persist: bool = True,
        _receipt_version: int = 2) -> dict:
    """Import one existing lesson only when its exact writer output was reviewed.

    ``expected_context`` is a nonempty subset of source/lesson context supplied
    by the coordinator; every key/value must be present in both saved jobs.
    This does not promote the lesson into a dictionary or general registry.
    """
    run_dir = Path(run_dir).resolve(strict=True)
    if writer_job == reviewer_job:
        raise AnnotationResearchError("Dictionary writer and reviewer jobs must be distinct")
    if not isinstance(expected_context, dict) or not expected_context:
        raise AnnotationResearchError("Reviewed lesson import requires coordinator-bound source/context")
    if (not isinstance(issue_ids, list) or not issue_ids
            or any(not isinstance(item, str) or not item for item in issue_ids)
            or len(issue_ids) != len(set(issue_ids))):
        raise AnnotationResearchError("Reviewed lesson import requires exact uncertain issue IDs")
    if _receipt_version not in {1, 2}:
        raise AnnotationResearchError("Unsupported reviewed lesson import version")
    job_root = Path(source_run_dir).resolve(strict=True) if source_run_dir is not None else run_dir
    writer_dir, writer_meta, writer_inputs, writer_result = _job_context_inputs(job_root, writer_job)
    reviewer_dir, reviewer_meta, reviewer_inputs, reviewer_result = _job_context_inputs(job_root, reviewer_job)
    reviewer_context = reviewer_inputs.get("context")
    if not isinstance(reviewer_context, dict):
        raise AnnotationResearchError("Dictionary reviewer did not retain its source/context snapshot")
    for key, expected_value in expected_context.items():
        if (key not in writer_inputs or writer_inputs[key] != expected_value
                or key not in reviewer_context or reviewer_context[key] != expected_value):
            raise AnnotationResearchError("Dictionary writer/reviewer source context does not match current evidence")
    if reviewer_inputs.get("output") != writer_result:
        raise AnnotationResearchError("Reviewer input is not the exact dictionary writer result")
    if reviewer_result != {"approved": True, "issues": []}:
        raise AnnotationResearchError("Dictionary lesson lacks a clean independent approval")
    if not isinstance(writer_result, dict):
        raise AnnotationResearchError("Dictionary writer result is not an entry collection")
    matches = []
    for collection in ("words", "grammar"):
        rows = writer_result.get(collection)
        if not isinstance(rows, list):
            raise AnnotationResearchError("Dictionary writer result has malformed entry collections")
        matches.extend(row for row in rows if isinstance(row, dict) and row.get("id") == lesson_id)
    if len(matches) != 1:
        raise AnnotationResearchError("Reviewed dictionary output does not contain exactly one requested lesson ID")
    lesson = matches[0]
    if _receipt_version == 1:
        # Preserve the original wrapper layout for exact replay of historical
        # receipts. New imports use v2's direct learner-lesson fields below.
        content = {
            "_annotation_research_fact": True,
            "issue_ids": list(issue_ids),
            "lesson_id": lesson_id,
            "lesson": lesson,
            "writer_job": writer_job,
            "reviewer_job": reviewer_job,
            "writer_result_digest": _digest(writer_result),
            "reviewer_result_digest": _digest(reviewer_result),
            "context_digest": _digest(expected_context),
            "source_context": expected_context,
        }
    else:
        content = {**lesson, "_annotation_research_fact": True,
                   "issue_ids": list(issue_ids)}
    reference_id = "reviewed-lesson-" + _digest([writer_job, reviewer_job, lesson_id,
                                                  _digest(writer_result), _digest(expected_context)])[:24]
    reference = {reference_id: {"kind": "approved_lesson", "content": content,
                               "issue_ids": list(issue_ids)}}
    evidence = {
        "version": _receipt_version, "kind": "imported_reviewed_lesson",
        "writer_job": writer_job, "reviewer_job": reviewer_job,
        "writer_meta_digest": _digest(writer_meta), "reviewer_meta_digest": _digest(reviewer_meta),
        "writer_result_digest": _digest(writer_result), "reviewer_result_digest": _digest(reviewer_result),
        "reviewer_input_digest": _digest(reviewer_inputs),
        "expected_context_digest": _digest(expected_context),
        "lesson_id": lesson_id, "issue_ids": list(issue_ids),
        "reference_id": reference_id, "reference_digest": _digest(reference),
        "references": reference,
    }
    evidence["evidence_digest"] = _digest(evidence)
    if _persist:
        evidence_path = run_dir / "annotation-research" / "imports" / f"{evidence['evidence_digest']}.json"
        _write_json(evidence_path, evidence)
    return {"references": reference, "evidence": evidence}


def verify_imported_reviewed_lesson(run_dir: Path, evidence: dict, *,
        lesson_id: str, expected_context: dict, issue_ids: list[str],
        source_run_dir: Path | None = None) -> dict:
    """Replay an imported lesson from actual writer/reviewer jobs and workspaces."""
    if (not isinstance(evidence, dict) or evidence.get("version") not in {1, 2}
            or evidence.get("kind") != "imported_reviewed_lesson"):
        raise AnnotationResearchError("Unsupported reviewed-lesson import receipt")
    result = import_reviewed_lesson(run_dir, evidence.get("writer_job"), evidence.get("reviewer_job"),
        lesson_id=lesson_id, expected_context=expected_context, issue_ids=issue_ids,
        source_run_dir=source_run_dir, _persist=False,
        _receipt_version=evidence["version"])
    if result["evidence"] != evidence:
        raise AnnotationResearchError("Imported lesson provenance changed")
    evidence_path = Path(run_dir) / "annotation-research" / "imports" / f"{evidence['evidence_digest']}.json"
    if _read_json(evidence_path) != evidence:
        raise AnnotationResearchError("Persisted reviewed-lesson import receipt changed")
    return result
