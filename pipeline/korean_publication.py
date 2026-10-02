"""Verify independent Korean run evidence before promoting content and assets."""
from __future__ import annotations

import argparse
import shutil
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile

from jsonschema import validate
from pipeline.agent_harness import CodexRunner

from pipeline import korean_dictionary as dictionary
from pipeline import korean_readability as readability
from pipeline import korean_sentence_breakdowns as sentence_help
from pipeline.korean_agent_harness import POLICY, LINGUISTIC_REFERENCE, approved, digest, read, save, normalize_existing
from pipeline import korean_contracts as contracts
from pipeline import korean_curriculum as curriculum
from pipeline.korean_sources import EDITION, ROOT, load_selected_unit, sha
from pipeline.korean_source_context import REFERENCE as SOURCE_CONTEXT_REFERENCE
from scripts import generate_app_content_json as app_content

CONTENT = ROOT / "content/korean/honggildong"
LEXICON = ROOT / "content/lexicon/korean"
POLICY_HISTORY = ROOT / 'pipeline/policy-history/korean'


def policy_digest_matches(expected: str) -> bool:
    if expected == sha(POLICY.read_bytes()):
        return True
    if not isinstance(expected, str) or len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
        return False
    snapshot = POLICY_HISTORY / (expected + '.md')
    return snapshot.is_file() and sha(snapshot.read_bytes()) == expected


def relevant_entries(chapter: dict, words: dict, grammar: dict) -> dict:
    word_ids = {s["lexical"]["id"] for s in chapter["segments"]
                if s["type"] == "word" and s["lexical"]["kind"] != "grammar"}
    grammar_ids = {link["entry_id"] for link in chapter["grammar_links"]}
    return {"words": [words[key] for key in sorted(word_ids)],
            "grammar": [grammar[key] for key in sorted(grammar_ids)]}


def reviewed_word_versions(words: dict):
    """Replay verified editorial history without accepting arbitrary old definitions."""
    current = dictionary._registry(dictionary.WORDS)
    history = read(dictionary.WORDS).get('revision_reviews', [])
    version = dict(words)
    yield version.copy()
    for record in reversed(history):
        before = {entry['id']: entry for entry in record['before_entries']}
        for after in record['proposal']['entries']:
            identity = after['id']
            if identity not in version:
                continue
            if words[identity] != current[identity]:
                raise ValueError('Korean publication changed a reviewed dictionary revision')
            if version[identity] != after:
                raise ValueError('Korean dictionary revision replay is discontinuous')
            version[identity] = before[identity]
        yield version.copy()


def dictionary_digest_matches(chapter: dict, expected: str, words: dict, grammar: dict) -> bool:
    if expected == digest(relevant_entries(chapter, words, grammar)):
        return True
    return any(expected == digest(relevant_entries(chapter, prior, grammar))
               for prior in reviewed_word_versions(words))


def merge_dictionary_delta(words: dict, grammar: dict, delta: dict) -> None:
    for kind, registry in (('words', words), ('grammar', grammar)):
        for entry in delta[kind]:
            if entry['id'] in registry and registry[entry['id']] != entry:
                if kind == 'words' and any(prior.get(entry['id']) == entry
                        for prior in reviewed_word_versions(words)):
                    continue  # Keep the reviewed successor; never restore the old definition.
                raise ValueError('Korean run overwrites an approved dictionary entry')
            registry[entry['id']] = entry


def source_check(chapter: dict) -> None:
    alignment = chapter["source_alignment"]
    if not isinstance(alignment.get('unit'), dict):
        raise ValueError('Korean reviewed source stopping point is missing')
    manifest, unit, source = load_selected_unit(alignment['unit'])
    if unit['number'] != chapter['number']:
        raise ValueError('Korean source scope numbering changed')
    if alignment["edition"] != EDITION or alignment.get("reviewed") is not True or not alignment["beats"]:
        raise ValueError("Korean source edition/review is missing")
    full = (ROOT / "books/korean/honggildong" / manifest["text_file"]).read_text(encoding="utf-8")
    for beat in alignment["beats"]:
        if (not unit["start"] <= beat["start"] < beat["end"] <= unit["end"]
                or full[beat["start"]:beat["end"]] != beat["quote"]
                or source.count(beat["quote"]) != 1 or not beat["event_en"].strip()):
            raise ValueError("Korean source beat has stale or unsupported span evidence")


def verify_run(run_dir: Path) -> tuple[dict, dict, dict, dict]:
    from pipeline.korean_levels import target_level, source_id, LEVEL_POLICY
    report = read(run_dir / "report.json")
    if report.get("status") != "complete" or report.get("edition") != EDITION:
        raise ValueError("Korean run is incomplete or uses a different edition")
    if set(report.get("artifacts", {})) != {
            "chapter.json", "dictionary-delta.json", "sentence-breakdowns.json", "source-plan.json", "lexical-plan.json"}:
        raise ValueError("Korean run artifact coverage is incomplete")
    for name, expected in report["artifacts"].items():
        if Path(name).name != name or sha((run_dir / name).read_bytes()) != expected:
            raise ValueError("Korean reviewed run artifact changed")
    required = {"plan", "lexical-plan", "prose", "annotation", "dictionary", "sentence-help", "curriculum"}
    if (set(report["stages"]) != required or not policy_digest_matches(report["policy_sha256"])
            or report.get('linguistic_reference_sha256') != sha(LINGUISTIC_REFERENCE.read_bytes())):
        raise ValueError("Korean run coverage or policy is stale")
    reviews = {}
    if ('source_context_sha256' in report
            and report['source_context_sha256'] != sha(SOURCE_CONTEXT_REFERENCE.read_bytes())):
        raise ValueError('Korean reviewed source guidance changed')
    for stage, evidence in report["stages"].items():
        if evidence.get("reused"):
            if stage != "dictionary" or evidence.get("approved") is not True:
                raise ValueError("Only approved dictionary entries may bypass new review")
            reviews[stage] = evidence
            continue
        job = evidence["review_job"]
        if 'initial_review_job' in evidence:
            initial_job = evidence['initial_review_job']
            if stage not in ('plan', 'lexical-plan', 'prose') or Path(initial_job).name != initial_job or job != initial_job + '-adjudication':
                raise ValueError('Invalid Korean source-review adjudication lineage')
            initial = read(run_dir / 'agents' / initial_job / 'result.json')
            initial_meta = read(run_dir / 'agents' / initial_job / 'meta.json')
            validate(initial, contracts.REVIEW)
            if initial['approved'] or not initial['issues'] or digest(initial) != evidence['initial_review_digest'] or initial_meta.get('return_code') != 0:
                raise ValueError('Korean initial source review changed after adjudication')
            CodexRunner._check_tool_profile(run_dir / 'agents' / initial_job, 'offline', initial_meta)
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
            if proposal_meta.get('kind') == 'dictionary_assembly':
                if stage != 'dictionary':
                    raise ValueError('Dictionary assembly used for another stage')
                from pipeline.korean_dictionary_jobs import replay
                if replay(run_dir, proposal_meta) != proposal:
                    raise ValueError('Assembled dictionary differs from reviewed workers')
            if proposal_meta.get('kind') == 'curriculum_assembly':
                if stage != 'curriculum':
                    raise ValueError('Curriculum assembly used for another stage')
                from pipeline.korean_curriculum_jobs import replay
                if replay(run_dir, proposal_meta) != proposal:
                    raise ValueError('Assembled curriculum differs from reviewed workers')
            if proposal_meta.get("kind") == "annotation_assembly":
                if stage != "annotation":
                    raise ValueError("Only Korean annotation may assemble source chunks")
                values, texts = [], []
                if 'reuse_plan_job' in proposal_meta:
                    reuse_job = proposal_meta['reuse_plan_job']
                    old_job = proposal_meta['reuse_source_job']
                    if any(Path(job).name != job for job in (reuse_job, old_job)):
                        raise ValueError('invalid Korean annotation reuse job')
                    selection = read(run_dir / 'agents' / reuse_job / 'result.json')
                    reuse_meta = read(run_dir / 'agents' / reuse_job / 'meta.json')
                    if reuse_meta.get('return_code') != 0 or digest(selection) != proposal_meta['reuse_plan_digest']:
                        raise ValueError('Korean annotation reuse plan changed after review')
                    CodexRunner._check_tool_profile(run_dir / 'agents' / reuse_job, 'offline', reuse_meta)
                    validate(selection, contracts.ANNOTATION_REUSE_PLAN)
                    old_meta = read(run_dir / 'agents' / old_job / 'meta.json')
                    reused = contracts.reuse_selection(selection, [c['text'] for c in old_meta['chunks']],
                        [c['text'] for c in proposal_meta['chunks']])
                    if any(proposal_meta['chunks'][new - 1] != old_meta['chunks'][old - 1] for new, old in reused.items()):
                        raise ValueError('Korean retained occurrence differs from reuse plan')
                if 'repair_plan_job' in proposal_meta:
                    repair_job = proposal_meta['repair_plan_job']
                    if Path(repair_job).name != repair_job:
                        raise ValueError('invalid Korean annotation repair job')
                    selection = read(run_dir / 'agents' / repair_job / 'result.json')
                    repair_meta = read(run_dir / 'agents' / repair_job / 'meta.json')
                    if repair_meta.get('return_code') != 0 or digest(selection) != proposal_meta['repair_plan_digest']:
                        raise ValueError('Korean annotation repair plan changed after review')
                    CodexRunner._check_tool_profile(run_dir / 'agents' / repair_job, 'offline', repair_meta)
                    validate(selection, contracts.ANNOTATION_REPAIR_PLAN)
                    contracts.repair_selection(selection, len(proposal_meta['chunks']))
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
                replayed = contracts.combine_annotations(values, texts)
                if 'grammar_binding_job' in proposal_meta:
                    binding_job = proposal_meta['grammar_binding_job']
                    if Path(binding_job).name != binding_job:
                        raise ValueError('invalid Korean grammar binding job')
                    bindings = read(run_dir / 'agents' / binding_job / 'result.json')
                    binding_meta = read(run_dir / 'agents' / binding_job / 'meta.json')
                    if binding_meta.get('return_code') != 0 or digest(bindings) != proposal_meta['grammar_binding_digest']:
                        raise ValueError('Korean grammar bindings changed after annotation review')
                    CodexRunner._check_tool_profile(run_dir / 'agents' / binding_job, 'offline', binding_meta)
                    validate(bindings, contracts.GRAMMAR_BINDINGS)
                    replayed = contracts.bind_grammar_identities(replayed, bindings, set(proposal_meta['approved_grammar_ids']))
                if replayed != proposal:
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
    manifest, unit, _ = load_selected_unit(chapter['source_alignment']['unit'])
    if (report["source_sha256"] != manifest["text_sha256"] or report["source_unit"] != unit
            or report.get("source_notes_sha256") != manifest.get("notes_sha256")):
        raise ValueError("Korean run source edition changed")
    _, unit, source = load_selected_unit(chapter['source_alignment']['unit'])
    outputs = {}
    for name, record in report["stages"].items():
        job = record.get("proposal_job")
        if job:
            outputs[name] = read(run_dir / "agents" / job / "result.json")
            validate(outputs[name], contracts.ANNOTATION if name == 'annotation' else
                read(contracts.schema_path("breakdowns" if name == "sentence-help" else name)))
    plan = contracts.bind_plan(outputs["plan"], source, unit["start"])
    if read(run_dir / "source-plan.json") != plan:
        raise ValueError("Korean source plan differs from reviewed proposal")
    prose = outputs.get("prose", {"title": chapter["title"].split(". ", 1)[-1], "text": chapter["text"]})
    annotation_job = report["stages"]["annotation"].get("proposal_job")
    if annotation_job:
        assembly = read(run_dir / "agents" / annotation_job / "meta.json")
        if assembly.get("kind") == "annotation_assembly" and [c["text"] for c in assembly["chunks"]] != contracts.annotation_chunks(prose["text"], batch_characters=assembly.get('batch_characters', 0)):
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
    level = target_level(chapter)
    if report.get('target_level', 1) != level:
        raise ValueError('Korean run target differs from the reviewed chapter')
    if level > 1 and report.get('level_policy_sha256') != sha(LEVEL_POLICY.read_bytes()):
        raise ValueError('Korean level instructions are stale')
    expected_chapter = contracts.canonical_annotation(annotation, prose, chapter["number"], EDITION, plan, focus, level=level)
    expected_chapter['curriculum'] = {'bindings': outputs['curriculum'],
        'evaluation': curriculum.evaluate_bindings(expected_chapter, outputs['curriculum'], level=level)}
    if expected_chapter != chapter:
        raise ValueError("Korean chapter differs from reviewed proposals")
    if "dictionary" in outputs and outputs["dictionary"] != delta:
        raise ValueError("Korean dictionary differs from reviewed proposal")
    if report["stages"]["dictionary"].get("reused") and delta != {"words": [], "grammar": []}:
        raise ValueError("Korean dictionary reuse contains unreviewed additions")
    help_output = outputs.get("sentence-help", {"sentences": help_data["audit"]})
    if (digest(help_output) != report["stages"]["sentence-help"]["output_digest"]
            or contracts.selected_breakdowns(help_output, chapter,
                source_id(chapter)) != help_data):
        raise ValueError("Korean sentence help differs from reviewed proposal")
    evidence = {"number": chapter["number"], "chapter_digest": digest(chapter),
                "source_sha256": report["source_sha256"], "source_notes_sha256": report.get("source_notes_sha256"), "policy_sha256": report["policy_sha256"],
                "linguistic_reference_sha256": report['linguistic_reference_sha256'],
                "reviews": reviews}
    if 'source_context_sha256' in report:
        evidence['source_context_sha256'] = report['source_context_sha256']
    if level > 1:
        evidence.update(target_level=level, level_policy_sha256=report['level_policy_sha256'])
    return chapter, delta, help_data, evidence


def validate_evidence(chapter: dict, evidence: dict, words: dict, grammar: dict,
                      breakdowns: list[dict]) -> None:
    from pipeline.korean_levels import target_level, LEVEL_POLICY
    level = target_level(chapter)
    if evidence.get('target_level', 1) != level:
        raise ValueError('Korean publication target differs from review evidence')
    if level > 1 and evidence.get('level_policy_sha256') != sha(LEVEL_POLICY.read_bytes()):
        raise ValueError('Korean level instructions are stale')
    source_check(chapter)
    if ('source_context_sha256' in evidence
            and evidence['source_context_sha256'] != sha(SOURCE_CONTEXT_REFERENCE.read_bytes())):
        raise ValueError('Korean published source guidance changed')
    manifest, _, _ = load_selected_unit(chapter['source_alignment']['unit'])
    if (evidence.get("chapter_digest") != digest(chapter)
            or evidence.get("source_sha256") != manifest["text_sha256"]
            or evidence.get("source_notes_sha256") != manifest.get("notes_sha256")
            or not policy_digest_matches(evidence.get("policy_sha256"))
            or evidence.get('linguistic_reference_sha256') != sha(LINGUISTIC_REFERENCE.read_bytes())
            or not dictionary_digest_matches(chapter, evidence.get("dictionary_digest"), words, grammar)
            or evidence.get("breakdowns_digest") != digest(breakdowns)):
        raise ValueError("Korean publication evidence is stale")
    if set(evidence["reviews"]) != {"plan", "lexical-plan", "prose", "annotation", "dictionary", "sentence-help", "curriculum"}:
        raise ValueError("Korean independent review coverage is incomplete")
    if 'curriculum' not in chapter:
        raise ValueError('Korean publication lacks reviewed six-level curriculum evidence')
    grade = curriculum.evaluate_bindings(chapter, chapter['curriculum']['bindings'], level=level)
    if grade != chapter['curriculum']['evaluation'] or not grade['passes']:
        raise ValueError('Korean published curriculum evidence is stale or above level')
    if evidence['reviews']['curriculum']['output_digest'] != digest(chapter['curriculum']['bindings']):
        raise ValueError('Korean curriculum bindings differ from independent review')
    for name, review in evidence["reviews"].items():
        if review.get("reused"):
            if name != "dictionary" or not dictionary_digest_matches(chapter, review["registry_digest"], words, grammar):
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
    if promote:
        policy_bytes = POLICY.read_bytes()
        POLICY_HISTORY.mkdir(parents=True, exist_ok=True)
        snapshot = POLICY_HISTORY / (sha(policy_bytes) + '.md')
        if snapshot.exists() and snapshot.read_bytes() != policy_bytes:
            raise ValueError('Korean policy snapshot changed')
        snapshot.write_bytes(policy_bytes)
    chapters = [item[0] for item in accepted]
    from pipeline.korean_levels import target_level
    level = target_level(chapters[0])
    level_key = f"l{level}"
    if any(target_level(c) != level for c in chapters):
        raise ValueError("A Korean edition publication must have one target level")
    if [c["number"] for c in chapters] != list(range(1, len(chapters) + 1)):
        raise ValueError("Korean publication requires consecutive chapters starting at 1")
    next_start = 0
    for chapter in chapters:
        unit = chapter['source_alignment']['unit']
        if unit['start'] != next_start:
            raise ValueError('Korean chapter source scopes overlap or skip narrative')
        next_start = unit['end'] + 2
    words, grammar = dictionary._registry(dictionary.WORDS), dictionary._registry(dictionary.GRAMMAR)
    for _, delta, _, _ in accepted:
        merge_dictionary_delta(words, grammar, delta)
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
        shutil.copytree(CONTENT, book)
        lexicon, assets = root / "lexicon", root / "assets"
        metadata = {**read(CONTENT / "metadata.json"), "source_edition": "Wikisource/Jikji 30-sheet Gyeongpan edition, revision 460078",
                    "scope": "reviewed TOPIK-aligned curriculum pipeline chapters",
                    "enabled_levels": sorted(set(read(CONTENT / "metadata.json").get("enabled_levels", [])) | {level_key}),
                    "curriculum_source": curriculum.catalog()['source_url'],
                    "curriculum_source_sha256": curriculum.SOURCE_SHA256,
                    "level_system": curriculum.catalog()['level_system']}
        save(book / "metadata.json", metadata)
        save(book / f"{level_key}.annotations.json", {"schema_version": 1, "language": "korean", "book": "honggildong", "level": level_key, "chapters": chapters})
        save(book / f"{level_key}.sentence-breakdowns.json", {"schema_version": 1, "reviewed": True, "breakdowns": breakdowns,
             "audits": {str(c["number"]): help_data["audit"] for c, _, help_data, _ in accepted}})
        save(book / f"{level_key}.review.json", {"schema_version": 1, "chapters": evidence})
        for name, entries in (("words", words.values()), ("grammar", grammar.values()), ("exceptions", exceptions)):
            save(lexicon / f"l1.{name}.json", {**read(LEXICON / f"l1.{name}.json"), "reviewed": True, "entries": list(entries)})
        (book / f"{level_key}.md").write_text("# 홍길동전\n\n" + "\n\n".join(f"## {c['title']}\n\n{c['text']}" for c in chapters) + "\n", encoding="utf-8")
        with staged_paths(root / "content", lexicon, assets):
            entries = app_content.build_language("korean", {f"l{n}": n for n in range(1, 7)})
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
    return {"status": "published" if promote else "validated", "level": level, "chapters": len(chapters),
            "word_entries": len(words), "grammar_entries": len(grammar)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    print(json.dumps(publish(args.run_dir, promote=not args.check)))


if __name__ == "__main__":
    main()
