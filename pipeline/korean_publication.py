"""Verify independent Korean run evidence before promoting content and assets."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile

from jsonschema import validate
from pipeline.agent_harness import CodexRunner

from pipeline import korean_dictionary as dictionary
from pipeline import korean_readability as readability
from pipeline import korean_sentence_breakdowns as sentence_help
from pipeline.korean_agent_harness import POLICY, approved, digest, read, save, normalize_existing
from pipeline import korean_contracts as contracts
from pipeline.korean_sources import EDITION, ROOT, load_unit, sha
from scripts import generate_app_content_json as app_content

CONTENT = ROOT / "content/korean/honggildong"
LEXICON = ROOT / "content/lexicon/korean"


def relevant_entries(chapter: dict, words: dict, grammar: dict) -> dict:
    word_ids = {s["lexical"]["id"] for s in chapter["segments"]
                if s["type"] == "word" and s["lexical"]["kind"] != "grammar"}
    grammar_ids = {link["entry_id"] for link in chapter["grammar_links"]}
    return {"words": [words[key] for key in sorted(word_ids)],
            "grammar": [grammar[key] for key in sorted(grammar_ids)]}


def source_check(chapter: dict) -> None:
    manifest, unit, source = load_unit(chapter["number"])
    alignment = chapter["source_alignment"]
    if alignment["edition"] != EDITION or alignment.get("reviewed") is not True or not alignment["beats"]:
        raise ValueError("Korean source edition/review is missing")
    full = (ROOT / "books/korean/honggildong" / manifest["text_file"]).read_text(encoding="utf-8")
    for beat in alignment["beats"]:
        if (not unit["start"] <= beat["start"] < beat["end"] <= unit["end"]
                or full[beat["start"]:beat["end"]] != beat["quote"]
                or source.count(beat["quote"]) != 1 or not beat["event_en"].strip()):
            raise ValueError("Korean source beat has stale or unsupported span evidence")


def verify_run(run_dir: Path) -> tuple[dict, dict, dict, dict]:
    report = read(run_dir / "report.json")
    if report.get("status") != "complete" or report.get("edition") != EDITION:
        raise ValueError("Korean run is incomplete or uses a different edition")
    if set(report.get("artifacts", {})) != {
            "chapter.json", "dictionary-delta.json", "sentence-breakdowns.json", "source-plan.json", "lexical-plan.json"}:
        raise ValueError("Korean run artifact coverage is incomplete")
    for name, expected in report["artifacts"].items():
        if Path(name).name != name or sha((run_dir / name).read_bytes()) != expected:
            raise ValueError("Korean reviewed run artifact changed")
    required = {"plan", "lexical-plan", "prose", "annotation", "dictionary", "sentence-help"}
    if set(report["stages"]) != required or report["policy_sha256"] != sha(POLICY.read_bytes()):
        raise ValueError("Korean run coverage or policy is stale")
    reviews = {}
    for stage, evidence in report["stages"].items():
        if evidence.get("reused"):
            if stage != "dictionary" or evidence.get("approved") is not True:
                raise ValueError("Only approved dictionary entries may bypass new review")
            reviews[stage] = evidence
            continue
        job = evidence["review_job"]
        if Path(job).name != job:
            raise ValueError("invalid Korean review job")
        review = read(run_dir / "agents" / job / "result.json")
        meta = read(run_dir / "agents" / job / "meta.json")
        if (not approved(review) or digest(review) != evidence["review_digest"]
                or meta.get("return_code") != 0):
            raise ValueError("Korean independent review has no approved completed evidence")
        CodexRunner._check_tool_profile(run_dir / "agents" / job, "offline", meta)
        proposal_job = evidence["proposal_job"]
        if proposal_job:
            if Path(proposal_job).name != proposal_job or proposal_job == job:
                raise ValueError("invalid Korean proposal job")
            proposal_meta = read(run_dir / "agents" / proposal_job / "meta.json")
            if proposal_meta.get("return_code") != 0:
                raise ValueError("Korean proposal process did not complete")
            CodexRunner._check_tool_profile(run_dir / "agents" / proposal_job, "offline", proposal_meta)
            proposal = read(run_dir / "agents" / proposal_job / "result.json")
            if proposal_meta.get("kind") == "annotation_assembly":
                if stage != "annotation":
                    raise ValueError("Only Korean annotation may assemble source chunks")
                values, texts = [], []
                for chunk in proposal_meta["chunks"]:
                    chunk_job = chunk["job"]
                    if Path(chunk_job).name != chunk_job:
                        raise ValueError("invalid Korean chunk job")
                    chunk_value = read(run_dir / "agents" / chunk_job / "result.json")
                    chunk_meta = read(run_dir / "agents" / chunk_job / "meta.json")
                    if digest(chunk_value) != chunk["digest"] or chunk_meta.get("return_code") != 0:
                        raise ValueError("Korean annotation chunk changed after review")
                    CodexRunner._check_tool_profile(run_dir / "agents" / chunk_job, "offline", chunk_meta)
                    validate(chunk_value, contracts.ANNOTATION)
                    values.append(chunk_value)
                    texts.append(chunk["text"])
                if contracts.combine_annotations(values, texts) != proposal:
                    raise ValueError("Korean assembled annotation differs from source chunks")
            if digest(proposal) != evidence["output_digest"]:
                raise ValueError("Korean proposal changed after review")
        reviews[stage] = {**evidence, "review": review, "model": meta["model"],
                          "effort": meta["effort"], "input_fingerprint": meta["fingerprint"]}
    chapter, delta, help_data = [read(run_dir / name) for name in
        ("chapter.json", "dictionary-delta.json", "sentence-breakdowns.json")]
    source_check(chapter)
    if chapter["number"] != report["number"]:
        raise ValueError("Korean run chapter numbering changed")
    manifest, unit, _ = load_unit(chapter["number"])
    if (report["source_sha256"] != manifest["text_sha256"] or report["source_unit"] != unit
            or report.get("source_notes_sha256") != manifest.get("notes_sha256")):
        raise ValueError("Korean run source edition changed")
    _, unit, source = load_unit(chapter["number"])
    outputs = {}
    for name, record in report["stages"].items():
        job = record.get("proposal_job")
        if job:
            outputs[name] = read(run_dir / "agents" / job / "result.json")
            validate(outputs[name], read(contracts.schema_path("breakdowns" if name == "sentence-help" else name)))
    plan = contracts.bind_plan(outputs["plan"], source, unit["start"])
    if read(run_dir / "source-plan.json") != plan:
        raise ValueError("Korean source plan differs from reviewed proposal")
    prose = outputs.get("prose", {"title": chapter["title"].split(". ", 1)[-1], "text": chapter["text"]})
    annotation_job = report["stages"]["annotation"].get("proposal_job")
    if annotation_job:
        assembly = read(run_dir / "agents" / annotation_job / "meta.json")
        if assembly.get("kind") == "annotation_assembly" and [c["text"] for c in assembly["chunks"]] != contracts.annotation_chunks(prose["text"]):
            raise ValueError("Korean annotation chunks do not match reviewed prose")
    words = {**dictionary._registry(dictionary.WORDS), **{e["id"]: e for e in delta["words"]}}
    annotation = outputs.get("annotation")
    if annotation is None:
        annotation = normalize_existing(chapter, words)
    for name, value in (("prose", prose), ("annotation", annotation)):
        if digest(value) != report["stages"][name]["output_digest"]:
            raise ValueError("Korean adopted output differs from independent review")
    focus = read(run_dir / "lexical-plan.json")
    if focus != outputs["lexical-plan"]:
        raise ValueError("Korean lexical plan differs from reviewed proposal")
    if contracts.canonical_annotation(annotation, prose, chapter["number"], EDITION, plan, focus) != chapter:
        raise ValueError("Korean chapter differs from reviewed proposals")
    if "dictionary" in outputs and outputs["dictionary"] != delta:
        raise ValueError("Korean dictionary differs from reviewed proposal")
    if report["stages"]["dictionary"].get("reused") and delta != {"words": [], "grammar": []}:
        raise ValueError("Korean dictionary reuse contains unreviewed additions")
    help_output = outputs.get("sentence-help", {"sentences": help_data["audit"]})
    if (digest(help_output) != report["stages"]["sentence-help"]["output_digest"]
            or contracts.selected_breakdowns(help_output, chapter,
                f"assets/annotations/korean_honggildong_l1_{chapter['number']:03d}.json") != help_data):
        raise ValueError("Korean sentence help differs from reviewed proposal")
    evidence = {"number": chapter["number"], "chapter_digest": digest(chapter),
                "source_sha256": report["source_sha256"], "source_notes_sha256": report.get("source_notes_sha256"), "policy_sha256": report["policy_sha256"],
                "reviews": reviews}
    return chapter, delta, help_data, evidence


def validate_evidence(chapter: dict, evidence: dict, words: dict, grammar: dict,
                      breakdowns: list[dict]) -> None:
    source_check(chapter)
    manifest, _, _ = load_unit(chapter["number"])
    if (evidence.get("chapter_digest") != digest(chapter)
            or evidence.get("source_sha256") != manifest["text_sha256"]
            or evidence.get("source_notes_sha256") != manifest.get("notes_sha256")
            or evidence.get("policy_sha256") != sha(POLICY.read_bytes())
            or evidence.get("dictionary_digest") != digest(relevant_entries(chapter, words, grammar))
            or evidence.get("breakdowns_digest") != digest(breakdowns)):
        raise ValueError("Korean publication evidence is stale")
    if set(evidence["reviews"]) != {"plan", "lexical-plan", "prose", "annotation", "dictionary", "sentence-help"}:
        raise ValueError("Korean independent review coverage is incomplete")
    for name, review in evidence["reviews"].items():
        if review.get("reused"):
            if name != "dictionary" or review["registry_digest"] != evidence["dictionary_digest"]:
                raise ValueError("Korean approved dictionary reuse is stale")
        elif not approved(review.get("review", {})):
            raise ValueError("Korean publication has an unapproved review")


@contextmanager
def staged_paths(content: Path, lexicon: Path, assets: Path):
    changed = [(app_content, "CONTENT_ROOT", content), (app_content, "ASSET_ROOT", assets),
               (dictionary, "WORDS", lexicon / "l1.words.json"),
               (dictionary, "GRAMMAR", lexicon / "l1.grammar.json"),
               (readability, "GRAMMAR", lexicon / "l1.grammar.json"),
               (readability, "EXCEPTIONS", lexicon / "l1.exceptions.json"),
               (sentence_help, "BREAKDOWNS", content / "korean/honggildong/l1.sentence-breakdowns.json")]
    previous = [(module, key, getattr(module, key)) for module, key, _ in changed]
    try:
        for module, key, value in changed:
            setattr(module, key, value)
        yield
    finally:
        for module, key, value in previous:
            setattr(module, key, value)


def publish(run_root: Path, *, promote: bool = True) -> dict:
    runs = sorted(run_root.glob("chapter-*/report.json"))
    if not runs:
        raise ValueError("No accepted Korean chapter runs")
    accepted = [verify_run(report.parent) for report in runs]
    chapters = [item[0] for item in accepted]
    if [c["number"] for c in chapters] != list(range(1, len(chapters) + 1)):
        raise ValueError("Korean publication requires consecutive chapters starting at 1")
    words, grammar = dictionary._registry(dictionary.WORDS), dictionary._registry(dictionary.GRAMMAR)
    for _, delta, _, _ in accepted:
        for registry, entries in ((words, delta["words"]), (grammar, delta["grammar"])):
            for entry in entries:
                if entry["id"] in registry and registry[entry["id"]] != entry:
                    raise ValueError("Korean run overwrites an approved dictionary entry")
                registry[entry["id"]] = entry
    breakdowns = [b for _, _, help_data, _ in accepted for b in help_data["breakdowns"]]
    evidence = []
    for chapter, _, help_data, proof in accepted:
        proof.update({"dictionary_digest": digest(relevant_entries(chapter, words, grammar)),
                      "breakdowns_digest": digest(help_data["breakdowns"])})
        validate_evidence(chapter, proof, words, grammar, help_data["breakdowns"])
        evidence.append(proof)
    exceptions = [{"id": entry["id"], "kind": entry["kind"]} for entry in words.values()
                  if entry["kind"] in ("proper_name", "story_term")]
    with tempfile.TemporaryDirectory(prefix="korean-publication-") as temporary:
        root = Path(temporary)
        book = root / "content/korean/honggildong"
        lexicon, assets = root / "lexicon", root / "assets"
        metadata = {**read(CONTENT / "metadata.json"), "source_edition": "Wikisource/Jikji 30-sheet Gyeongpan edition, revision 460078",
                    "scope": "reviewed Level 1 pipeline chapters", "enabled_levels": ["l1"]}
        save(book / "metadata.json", metadata)
        save(book / "l1.annotations.json", {"schema_version": 1, "language": "korean", "book": "honggildong", "level": "l1", "chapters": chapters})
        save(book / "l1.sentence-breakdowns.json", {"schema_version": 1, "reviewed": True, "breakdowns": breakdowns,
             "audits": {str(c["number"]): help_data["audit"] for c, _, help_data, _ in accepted}})
        save(book / "l1.review.json", {"schema_version": 1, "chapters": evidence})
        for name, entries in (("words", words.values()), ("grammar", grammar.values()), ("exceptions", exceptions)):
            save(lexicon / f"l1.{name}.json", {"reviewed": True, "entries": list(entries)})
        (book / "l1.md").write_text("# 홍길동전\n\n" + "\n\n".join(f"## {c['title']}\n\n{c['text']}" for c in chapters) + "\n", encoding="utf-8")
        with staged_paths(root / "content", lexicon, assets):
            entries = app_content.build_language("korean", {"l1": 1})
            save(assets / "content_ko.json", entries)
        if promote:
            for staged, destination in ((book, CONTENT), (lexicon, LEXICON), (assets, ROOT / "app/assets")):
                for path in staged.rglob("*"):
                    if path.is_file():
                        target = destination / path.relative_to(staged)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        temporary_path = target.with_suffix(target.suffix + ".tmp")
                        temporary_path.write_bytes(path.read_bytes())
                        temporary_path.replace(target)
    return {"status": "published" if promote else "validated", "chapters": len(chapters),
            "word_entries": len(words), "grammar_entries": len(grammar)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    print(json.dumps(publish(args.run_dir, promote=not args.check)))


if __name__ == "__main__":
    main()
