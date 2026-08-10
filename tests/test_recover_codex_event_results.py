import json

from pipeline.recover_codex_event_results import recover_job


SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"text": {"type": "string"}}, "required": ["text"],
}


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def test_recovers_last_schema_valid_agent_message_from_successful_attempt(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    write_json(job / "meta.json", {"return_code": 0})
    write_json(job / "attempts.json", [{
        "return_code": 0, "events": "events.attempt-01.jsonl",
    }])
    events = [
        {"type": "item.completed", "item": {
            "type": "agent_message", "text": json.dumps({"note": "thinking"}),
        }},
        {"type": "item.completed", "item": {
            "type": "agent_message", "text": json.dumps({"text": "恢复文本"}),
        }},
    ]
    (job / "events.attempt-01.jsonl").write_text(
        "\n".join(json.dumps(item) for item in events) + "\n", encoding="utf-8"
    )
    assert recover_job(job, SCHEMA, apply=False)["status"] == "recoverable"
    assert not (job / "result.json").exists()
    assert recover_job(job, SCHEMA, apply=True)["status"] == "recovered"
    assert json.loads((job / "result.json").read_text()) == {"text": "恢复文本"}


def test_recovery_never_overwrites_and_rejects_failed_metadata(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    write_json(job / "result.json", {"text": "existing"})
    assert recover_job(job, SCHEMA, apply=True)["status"] == "existing"
    assert json.loads((job / "result.json").read_text()) == {"text": "existing"}

    (job / "result.json").unlink()
    write_json(job / "meta.json", {"return_code": 1})
    write_json(job / "attempts.json", [])
    assert recover_job(job, SCHEMA, apply=True)["status"] == "ineligible"
    assert not (job / "result.json").exists()
