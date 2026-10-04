"""Small, source-bound chunk receipts used by annotation publishers."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

def digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def child_job_receipt(run_dir: Path, job: str) -> dict[str, Any]:
    """Record a completed child job by its on-disk metadata and result."""
    job_path = Path(job)
    if (not job or not job_path.parts or job != job_path.as_posix() or job_path.is_absolute()
            or any(part in {"", ".", ".."} for part in job_path.parts)):
        raise AnnotationPublicationError("unsafe annotation review job path")
    root = Path(run_dir).resolve(strict=True)
    agents = root / "agents"
    job_dir = agents / job_path
    cursor = agents
    unsafe_parent = agents.is_symlink()
    for part in job_path.parts:
        cursor = cursor / part
        unsafe_parent = unsafe_parent or cursor.is_symlink()
    if unsafe_parent or not job_dir.resolve(strict=True).is_relative_to(
            agents.resolve(strict=True)):
        raise AnnotationPublicationError("annotation review job escaped its run directory")
    meta_path, result_path = job_dir / "meta.json", job_dir / "result.json"
    if meta_path.is_symlink() or result_path.is_symlink():
        raise AnnotationPublicationError("annotation review metadata/result cannot be symlinks")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if not isinstance(meta, dict) or meta.get("return_code") != 0:
        raise AnnotationPublicationError("annotation review job did not complete successfully")
    binding_path = job_dir / "normal-review-binding.json"
    if binding_path.is_symlink() or not binding_path.is_file():
        raise AnnotationPublicationError("coordinator normal-review binding is missing")
    binding_raw = binding_path.read_bytes()
    binding = json.loads(binding_raw)
    if (not isinstance(binding, dict) or binding.get("version") != 1
            or binding.get("job") != job_path.as_posix()
            or binding.get("input_digest") != meta.get("fingerprint")
            or binding.get("result_digest") != digest(result)):
        raise AnnotationPublicationError("coordinator normal-review binding is invalid")
    return {"job": job_path.as_posix(), "input_digest": meta.get("fingerprint"),
            "result_digest": digest(result),
            "binding_sha256": hashlib.sha256(binding_raw).hexdigest()}


def bind_review_job(run_dir: Path, job: str, *, candidate: Any,
                    source_text: str) -> dict[str, Any]:
    """Coordinator-stamp the exact candidate/source after runner cache validation."""
    if isinstance(candidate, dict) and "segments" in candidate and "grammar_overlays" in candidate:
        candidate = {"segments": candidate.get("segments"),
                     "grammar_overlays": candidate.get("grammar_overlays")}
    job_path = Path(job)
    if (not job or not job_path.parts or job != job_path.as_posix()
            or job_path.is_absolute()
            or any(part in {"", ".", ".."} for part in job_path.parts)):
        raise AnnotationPublicationError("unsafe annotation review job path")
    root = Path(run_dir).resolve(strict=True)
    agents = root / "agents"
    job_dir = agents / job_path
    cursor = agents
    unsafe_parent = agents.is_symlink()
    for part in job_path.parts:
        cursor = cursor / part
        unsafe_parent = unsafe_parent or cursor.is_symlink()
    if unsafe_parent or not job_dir.resolve(strict=True).is_relative_to(agents.resolve(strict=True)):
        raise AnnotationPublicationError("annotation review job escaped its run directory")
    meta_path, result_path = job_dir / "meta.json", job_dir / "result.json"
    if meta_path.is_symlink() or result_path.is_symlink():
        raise AnnotationPublicationError("annotation review metadata/result cannot be symlinks")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if not isinstance(meta, dict) or meta.get("return_code") != 0:
        raise AnnotationPublicationError("annotation review child is not a completed job")
    binding = {
        "version": 1, "job": job_path.as_posix(),
        "candidate_digest": digest(candidate), "source_digest": digest(source_text),
        "input_digest": meta.get("fingerprint"), "result_digest": digest(result),
    }
    target = job_dir / "normal-review-binding.json"
    if target.is_symlink():
        raise AnnotationPublicationError("normal-review binding cannot be a symlink")
    encoded = (json.dumps(binding, sort_keys=True, separators=(",", ":")) + "\n").encode()
    with tempfile.NamedTemporaryFile(mode="wb", dir=job_dir, prefix=".review-binding-",
                                     suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    return child_job_receipt(root, job_path.as_posix())


def verify_child_job_receipt(run_dir: Path, receipt: Any) -> dict[str, Any]:
    """Authenticate saved worker result, model policy, profile, and workspace."""
    if not isinstance(receipt, dict):
        raise AnnotationPublicationError("annotation review child receipt is missing")
    actual = child_job_receipt(run_dir, receipt.get("job", ""))
    if actual != {key: receipt.get(key) for key in actual}:
        raise AnnotationPublicationError("annotation review child result changed")
    job_dir = Path(run_dir) / "agents" / actual["job"]
    binding = json.loads((job_dir / "normal-review-binding.json").read_text(encoding="utf-8"))
    if (binding.get("candidate_digest") != receipt.get("candidate_digest")
            or binding.get("source_digest") != receipt.get("source_digest")
            or (receipt.get("candidate_digest") is not None
                and receipt.get("candidate_digest") != binding.get("candidate_digest"))
            or (receipt.get("source_digest") is not None
                and receipt.get("source_digest") != binding.get("source_digest"))):
        raise AnnotationPublicationError("normal-review child saw a different candidate or source")
    meta = json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))
    if meta.get("model") != "gpt-6-luna" or meta.get("effort") != "low":
        raise AnnotationPublicationError("annotation review used an unapproved model policy")
    from pipeline.agent_harness import CodexRunner
    CodexRunner._check_tool_profile(job_dir, meta.get("tool_profile", "offline"), meta)
    return json.loads((job_dir / "result.json").read_text(encoding="utf-8"))


def normal_review_receipt(run_dir: Path, jobs: list[str], review: Any,
                          candidate: Any, source_text: str) -> dict[str, Any]:
    """Bind one/two ordinary reviewers to an exact candidate and source slice."""
    if not jobs or len(set(jobs)) != len(jobs):
        raise AnnotationPublicationError("ordinary review requires distinct child jobs")
    if isinstance(candidate, dict) and "segments" in candidate and "grammar_overlays" in candidate:
        candidate = {"segments": candidate.get("segments"),
                     "grammar_overlays": candidate.get("grammar_overlays")}
    candidate_digest, source_digest = digest(candidate), digest(source_text)
    components = []
    for job in jobs:
        component = child_job_receipt(run_dir, job)
        job_dir = Path(run_dir) / "agents" / component["job"]
        binding = json.loads((job_dir / "normal-review-binding.json").read_text(encoding="utf-8"))
        if (binding.get("candidate_digest") != candidate_digest
                or binding.get("source_digest") != source_digest):
            raise AnnotationPublicationError("normal-review child was bound to different inputs")
        component.update(candidate_digest=candidate_digest, source_digest=source_digest)
        components.append(component)
    return {"kind": "ordinary", "review_digest": digest(review),
            "candidate_digest": candidate_digest, "source_digest": source_digest,
            "components": components}


def verify_normal_review_receipt(run_dir: Path, receipt: Any, *, review: Any,
                                 candidate: Any, source_text: str,
                                 expected_children: int) -> list[dict[str, Any]]:
    if (not isinstance(receipt, dict) or receipt.get("kind") != "ordinary"
            or receipt.get("review_digest") != digest(review)
            or receipt.get("candidate_digest") != digest(candidate)
            or receipt.get("source_digest") != digest(source_text)):
        raise AnnotationPublicationError("ordinary review is not bound to this candidate and source")
    components = receipt.get("components")
    if not isinstance(components, list) or len(components) != expected_children:
        raise AnnotationPublicationError("ordinary review child coverage is incomplete")
    jobs = [component.get("job") for component in components if isinstance(component, dict)]
    if len(jobs) != expected_children or len(set(jobs)) != expected_children:
        raise AnnotationPublicationError("ordinary review child jobs must be distinct")
    if any(component.get("candidate_digest") != receipt.get("candidate_digest")
           or component.get("source_digest") != receipt.get("source_digest")
           for component in components):
        raise AnnotationPublicationError("ordinary review children are bound to different inputs")
    results = [verify_child_job_receipt(run_dir, child) for child in components]
    return results


class AnnotationPublicationError(ValueError):
    pass


def persist_chunk_attempts(run_dir: Path, chunks: list[str],
                           annotated: list[dict[str, Any]], *, surface_key: str
                           ) -> list[dict[str, Any]]:
    """Save accepted per-chunk evidence and return reader-bound file receipts."""
    if len(chunks) != len(annotated):
        raise AnnotationPublicationError("annotation chunk/evidence count mismatch")
    root = Path(run_dir)
    directory = root / "accepted-annotations"
    if directory.is_symlink():
        raise AnnotationPublicationError("accepted-annotations cannot be a symlink")
    directory.mkdir(parents=True, exist_ok=True)
    receipts: list[dict[str, Any]] = []
    offset = 0
    for index, (source_text, item) in enumerate(zip(chunks, annotated)):
        candidate = {"segments": item.get("segments"),
                     "grammar_overlays": item.get("grammar_overlays")}
        if (not isinstance(candidate["segments"], list)
                or not isinstance(candidate["grammar_overlays"], list)):
            raise AnnotationPublicationError("accepted chunk lacks candidate data")
        if "".join(str(row.get(surface_key, "")) for row in candidate["segments"]
                   if isinstance(row, dict)) != source_text:
            raise AnnotationPublicationError("accepted chunk does not reconstruct its source")
        path = directory / f"chunk_{index:04d}.json"
        if path.is_symlink():
            raise AnnotationPublicationError("accepted chunk evidence cannot be a symlink")
        payload = dict(item)
        encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=directory, prefix=f".{index:04d}-", suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
        temporary.replace(path)
        receipts.append({
            "index": index, "source_start": offset,
            "source_end": offset + len(source_text),
            "source_digest": digest(source_text),
            "candidate_digest": digest(candidate),
            "accepted_chunk_sha256": hashlib.sha256(encoded).hexdigest(),
            "review_kind": (item.get("effective_review", {}).get("kind")
                            if isinstance(item.get("effective_review"), dict)
                            else "ordinary"),
        })
        offset += len(source_text)
    return receipts


def verify_chunk_attempt_receipts(run_dir: Path, reader: dict[str, Any],
                                  source_text: str, *, surface_key: str
                                  ) -> list[dict[str, Any]] | None:
    """Match saved chunk attempts to exact source/candidate slices in a reader.

    ``None`` means a legacy reader predating these receipts. A versioned reader
    fails closed on any missing, changed, or misbound chunk evidence.
    """
    audit = reader.get("annotation_audit")
    if not isinstance(audit, dict):
        return None
    version = audit.get("chunk_review_receipts_version")
    has_receipts = "chunk_review_receipts" in audit
    if version is None and not has_receipts:
        return None
    if version != 1 or not has_receipts:
        raise AnnotationPublicationError("unsupported or incomplete chunk review receipt version")
    receipts = audit.get("chunk_review_receipts")
    chunks = audit.get("chunks")
    segments = reader.get("segments")
    overlays = reader.get("grammar_overlays", [])
    if (not isinstance(receipts, list) or not isinstance(chunks, int)
            or len(receipts) != chunks or not isinstance(segments, list)
            or not isinstance(overlays, list)):
        raise AnnotationPublicationError("versioned chunk review receipts are incomplete")
    base = Path(run_dir).resolve(strict=True)
    accepted = base / "accepted-annotations"
    if accepted.is_symlink() or not accepted.is_dir():
        raise AnnotationPublicationError("accepted chunk review evidence is missing")
    result: list[dict[str, Any]] = []
    expected_start = 0
    for index, receipt in enumerate(receipts):
        if not isinstance(receipt, dict) or receipt.get("index") != index:
            raise AnnotationPublicationError("chunk review receipt ordering is invalid")
        start, end = receipt.get("source_start"), receipt.get("source_end")
        if (not isinstance(start, int) or not isinstance(end, int) or start != expected_start
                or end <= start or end > len(source_text)):
            raise AnnotationPublicationError("chunk review receipt source range is invalid")
        source_chunk = source_text[start:end]
        if receipt.get("source_digest") != digest(source_chunk):
            raise AnnotationPublicationError("chunk review receipt is bound to different source text")
        path = accepted / f"chunk_{index:04d}.json"
        if path.is_symlink() or not path.is_file() or not path.resolve(strict=True).is_relative_to(accepted.resolve(strict=True)):
            raise AnnotationPublicationError("accepted chunk review evidence is unsafe or missing")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != receipt.get("accepted_chunk_sha256"):
            raise AnnotationPublicationError("accepted chunk review evidence changed")
        item = json.loads(raw)
        candidate = {"segments": item.get("segments"),
                     "grammar_overlays": item.get("grammar_overlays")}
        if receipt.get("candidate_digest") != digest(candidate):
            raise AnnotationPublicationError("accepted chunk candidate digest does not match")
        expected_segments: list[dict[str, Any]] = []
        cursor = 0
        for row in segments:
            if not isinstance(row, dict):
                raise AnnotationPublicationError("reader contains an invalid segment")
            surface = str(row.get(surface_key, ""))
            row_start, row_end = cursor, cursor + len(surface)
            if row_start < end and row_end > start:
                if row_start < start or row_end > end:
                    raise AnnotationPublicationError("reader segment crosses a reviewed chunk boundary")
                expected_segments.append(row)
            cursor = row_end
        if cursor != len(source_text):
            raise AnnotationPublicationError("reader segments do not cover the source")
        expected_overlays: list[dict[str, Any]] = []
        for overlay in overlays:
            if not isinstance(overlay, dict):
                raise AnnotationPublicationError("reader contains an invalid grammar overlay")
            left, right = overlay.get("start"), overlay.get("end")
            if not isinstance(left, int) or not isinstance(right, int):
                continue
            if left < end and right > start:
                if left < start or right > end:
                    raise AnnotationPublicationError("reader overlay crosses a reviewed chunk boundary")
                expected_overlays.append({**overlay, "start": left - start, "end": right - start})
        if candidate != {"segments": expected_segments, "grammar_overlays": expected_overlays}:
            raise AnnotationPublicationError("accepted chunk differs from published reader slice")
        kind = item.get("effective_review", {}).get("kind") if isinstance(item.get("effective_review"), dict) else "ordinary"
        if receipt.get("review_kind") != kind:
            raise AnnotationPublicationError("chunk review kind differs from its receipt")
        result.append(item)
        expected_start = end
    if expected_start != len(source_text):
        raise AnnotationPublicationError("chunk review receipts do not cover the full source")
    return result
