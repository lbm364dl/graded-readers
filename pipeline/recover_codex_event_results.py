#!/usr/bin/env python3
"""Recover missing Codex result.json files from durable successful event logs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
from typing import Any

from jsonschema import Draft202012Validator


def recover_job(job_dir: Path, schema: dict[str, Any], *, apply: bool) -> dict[str, Any]:
    result_path = job_dir / "result.json"
    if result_path.exists():
        return {"status": "existing", "job_dir": str(job_dir)}
    meta_path = job_dir / "meta.json"
    attempts_path = job_dir / "attempts.json"
    if not meta_path.is_file() or not attempts_path.is_file():
        return {"status": "ineligible", "job_dir": str(job_dir), "reason": "missing evidence"}
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    attempts = json.loads(attempts_path.read_text(encoding="utf-8"))
    if meta.get("return_code") != 0 or not isinstance(attempts, list):
        return {"status": "ineligible", "job_dir": str(job_dir), "reason": "unsuccessful metadata"}
    successful = [item for item in attempts if item.get("return_code") == 0]
    if not successful:
        return {"status": "ineligible", "job_dir": str(job_dir), "reason": "no successful attempt"}
    events_name = successful[-1].get("events")
    events_path = job_dir / str(events_name)
    if events_path.parent != job_dir or not events_path.is_file():
        return {"status": "ineligible", "job_dir": str(job_dir), "reason": "missing event log"}
    validator = Draft202012Validator(schema)
    candidates: list[dict[str, Any]] = []
    for line in events_path.read_text(encoding="utf-8", errors="strict").splitlines():
        event = json.loads(line)
        item = event.get("item") if event.get("type") == "item.completed" else None
        if not isinstance(item, dict) or item.get("type") != "agent_message":
            continue
        try:
            value = json.loads(item.get("text", ""))
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and validator.is_valid(value):
            candidates.append(value)
    if not candidates:
        return {"status": "ineligible", "job_dir": str(job_dir), "reason": "no schema-valid message"}
    data = (json.dumps(candidates[-1], ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    if apply:
        with tempfile.NamedTemporaryFile(dir=job_dir, delete=False) as handle:
            handle.write(data)
            temporary = Path(handle.name)
        temporary.replace(result_path)
    return {
        "status": "recovered" if apply else "recoverable",
        "job_dir": str(job_dir), "bytes": len(data),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents-root", required=True)
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = Path(args.agents_root).resolve()
    schema = json.loads(Path(args.schema).read_text(encoding="utf-8"))
    reports = [
        recover_job(path, schema, apply=args.apply)
        for path in sorted(root.glob(f"*/{args.job_name}")) if path.is_dir()
    ]
    counts: dict[str, int] = {}
    for report in reports:
        counts[report["status"]] = counts.get(report["status"], 0) + 1
    print(json.dumps({"counts": counts, "jobs": reports}, ensure_ascii=False, indent=2))
    return 0 if not counts.get("ineligible") else 2


if __name__ == "__main__":
    raise SystemExit(main())
