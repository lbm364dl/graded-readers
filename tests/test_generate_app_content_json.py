import json

import pytest

import scripts.generate_app_content_json as generator


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fixture(tmp_path, *, focus=False):
    book = tmp_path / "content" / "chinese" / "sanguoyanyi"
    _write(book / "metadata.json", json.dumps({
        "id": "sanguoyanyi",
        "title_native": "三国演义",
        "title_en": "Romance of the Three Kingdoms",
    }, ensure_ascii=False))
    _write(book / "hsk1.md", "## 第一回\n\n刘备来了。\n")
    segments = [
        {"text": "刘备", "type": "name", "pinyin": "Liú Bèi",
         "meaning_en": "Liu Bei"},
        {"text": "来了", "type": "word", "pinyin": "lái le",
         "meaning_en": "arrived"},
        {"text": "。", "type": "punctuation", "pinyin": "",
         "meaning_en": ""},
    ]
    if focus:
        for segment in segments:
            punctuation = segment["type"] == "punctuation"
            segment.update({
                "story_term": "",
                "story_term_meaning_en": "",
                "story_term_importance_en": "",
                "target_hsk_level": 1,
                "hsk_status": "not_applicable" if punctuation else "in_level",
                "matched_hsk_level": None if punctuation else 1,
                "hsk_evidence": "punctuation" if punctuation else "listed_hsk1",
                "learning_focus": "not_applicable" if punctuation else (
                    "lookup" if segment["type"] == "name" else "target"
                ),
                "lookup_reason": "proper_name" if segment["type"] == "name" else "",
                "character_hsk_level": None,
                "focus_review_note_en": "",
            })
    annotation = {
        "schema_version": 3 if focus else 1,
        "chapters": [{
            "number": 1,
            "text": "刘备来了。",
            "segments": segments,
            "grammar_overlays": [{
                "start": 2, "end": 4, "text": "来了",
                "grammar_candidate_key": "test.completed_action",
                "pattern": "V了",
                "meaning_en": "completed action",
            }],
            "annotation_audit": {"all_reviewed": True},
            **({"learning_focus_audit": {
                "role_assignment_reviewed": True,
                "semantic_focus_reviewed": True,
            }} if focus else {}),
        }],
    }
    _write(
        book / "hsk1.annotations.json",
        json.dumps(annotation, ensure_ascii=False),
    )
    return book


def test_clean_generator_emits_reviewed_agent_sidecar(tmp_path, monkeypatch):
    _fixture(tmp_path, focus=True)
    monkeypatch.setattr(generator, "CONTENT_ROOT", tmp_path / "content")
    monkeypatch.setattr(generator, "ASSET_ROOT", tmp_path / "assets")

    readers = generator.build_language("chinese", {"hsk1": 1})

    chapter = readers[0]["chapters"][0]
    assert chapter["annotationAsset"] == (
        "assets/annotations/chinese_sanguoyanyi_hsk1_001.json"
    )
    sidecar = json.loads(
        (tmp_path / "assets/annotations/chinese_sanguoyanyi_hsk1_001.json")
        .read_text(encoding="utf-8")
    )
    assert "".join(item["text"] for item in sidecar["segments"]) == sidecar["text"]
    assert sidecar["segments"][0]["meaning_en"] == "Liu Bei"
    assert sidecar["segments"][0]["grammar_candidate_keys"] == []
    assert sidecar["segments"][1]["grammar_candidate_keys"] == [
        "test.completed_action"
    ]


def test_clean_generator_rejects_chinese_without_learning_focus(
    tmp_path, monkeypatch
):
    _fixture(tmp_path)
    monkeypatch.setattr(generator, "CONTENT_ROOT", tmp_path / "content")
    monkeypatch.setattr(generator, "ASSET_ROOT", tmp_path / "assets")

    with pytest.raises(ValueError, match="lack reviewed learning-focus"):
        generator.build_language("chinese", {"hsk1": 1})


def test_clean_generator_preserves_reviewed_learning_focus(tmp_path, monkeypatch):
    _fixture(tmp_path, focus=True)
    monkeypatch.setattr(generator, "CONTENT_ROOT", tmp_path / "content")
    monkeypatch.setattr(generator, "ASSET_ROOT", tmp_path / "assets")

    generator.build_language("chinese", {"hsk1": 1})

    sidecar = json.loads(
        (tmp_path / "assets/annotations/chinese_sanguoyanyi_hsk1_001.json")
        .read_text(encoding="utf-8")
    )
    assert sidecar["segments"][0]["learning_focus"] == "lookup"
    assert sidecar["segments"][1]["learning_focus"] == "target"
    assert sidecar["learning_focus_audit"]["role_assignment_reviewed"] is True


def test_clean_generator_preserves_semantically_reviewed_word_focus(
    tmp_path, monkeypatch
):
    book = _fixture(tmp_path, focus=True)
    path = book / "hsk1.annotations.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["schema_version"] = 3
    chapter = document["chapters"][0]
    chapter["learning_focus_audit"]["semantic_focus_reviewed"] = True
    for segment in chapter["segments"]:
        segment["character_hsk_level"] = (
            1 if len(segment["text"]) == 1 and segment["type"] != "punctuation"
            else None
        )
        segment["focus_review_note_en"] = ""
    _write(path, json.dumps(document, ensure_ascii=False))
    monkeypatch.setattr(generator, "CONTENT_ROOT", tmp_path / "content")
    monkeypatch.setattr(generator, "ASSET_ROOT", tmp_path / "assets")

    generator.build_language("chinese", {"hsk1": 1})

    sidecar = json.loads(
        (tmp_path / "assets/annotations/chinese_sanguoyanyi_hsk1_001.json")
        .read_text(encoding="utf-8")
    )
    assert "character_hsk_level" in sidecar["segments"][0]
    assert sidecar["learning_focus_audit"]["semantic_focus_reviewed"] is True


def test_clean_generator_preserves_reviewed_subsegments(
    tmp_path, monkeypatch
):
    book = _fixture(tmp_path, focus=True)
    path = book / "hsk1.annotations.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["schema_version"] = 5
    chapter = document["chapters"][0]
    chapter["learning_focus_audit"]["semantic_focus_reviewed"] = True
    chapter["subsegment_audit"] = {"all_reviewed": True}
    for segment in chapter["segments"]:
        segment["character_hsk_level"] = None
        segment["focus_review_note_en"] = ""
        segment["composition_en"] = ""
        segment["subsegments"] = []
    chapter["segments"][1]["composition_en"] = (
        "来 is the action, and 了 marks its completion."
    )
    chapter["segments"][1]["subsegments"] = [
        {"start": 0, "end": 1, "text": "来", "meaning_en": "come"},
        {
            "start": 1, "end": 2, "text": "了",
            "meaning_en": "completed action",
        },
    ]
    _write(path, json.dumps(document, ensure_ascii=False))
    monkeypatch.setattr(generator, "CONTENT_ROOT", tmp_path / "content")
    monkeypatch.setattr(generator, "ASSET_ROOT", tmp_path / "assets")

    generator.build_language("chinese", {"hsk1": 1})

    sidecar = json.loads(
        (tmp_path / "assets/annotations/chinese_sanguoyanyi_hsk1_001.json")
        .read_text(encoding="utf-8")
    )
    assert [item["text"] for item in sidecar["segments"][1]["subsegments"]] == [
        "来",
        "了",
    ]
    assert sidecar["subsegment_audit"]["all_reviewed"] is True


def test_clean_generator_strips_retired_chinese_first_help(
    tmp_path, monkeypatch
):
    book = _fixture(tmp_path, focus=True)
    path = book / "hsk1.annotations.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["schema_version"] = 4
    chapter = document["chapters"][0]
    chapter["learning_focus_audit"]["semantic_focus_reviewed"] = True
    chapter["immersion_help_audit"] = {
        "generation_reviewed": True,
        "pedagogy_reviewed": True,
    }
    for segment in chapter["segments"]:
        segment["character_hsk_level"] = None
        segment["focus_review_note_en"] = ""
        segment["explanation_zh"] = ""
        segment["example_zh"] = ""
    _write(path, json.dumps(document, ensure_ascii=False))
    monkeypatch.setattr(generator, "CONTENT_ROOT", tmp_path / "content")
    monkeypatch.setattr(generator, "ASSET_ROOT", tmp_path / "assets")

    generator.build_language("chinese", {"hsk1": 1})

    sidecar = json.loads(
        (tmp_path / "assets/annotations/chinese_sanguoyanyi_hsk1_001.json")
        .read_text(encoding="utf-8")
    )
    assert "explanation_zh" not in sidecar["segments"][0]
    assert "example_zh" not in sidecar["segments"][0]
    assert "immersion_help_audit" not in sidecar


def test_clean_generator_rejects_missing_agent_annotations(tmp_path, monkeypatch):
    book = _fixture(tmp_path)
    (book / "hsk1.annotations.json").unlink()
    monkeypatch.setattr(generator, "CONTENT_ROOT", tmp_path / "content")
    monkeypatch.setattr(generator, "ASSET_ROOT", tmp_path / "assets")

    with pytest.raises(ValueError, match="reviewed agent annotations missing"):
        generator.build_language("chinese", {"hsk1": 1})


def test_partial_preview_omits_unreviewed_legacy_chapters(
    tmp_path, monkeypatch
):
    book = _fixture(tmp_path)
    (book / "hsk1.annotations.json").unlink()
    monkeypatch.setattr(generator, "CONTENT_ROOT", tmp_path / "content")
    monkeypatch.setattr(generator, "ASSET_ROOT", tmp_path / "assets")

    readers = generator.build_language(
        "chinese", {"hsk1": 1}, require_complete_annotations=False
    )

    assert readers == []
    assert not (tmp_path / "assets/annotations").exists()


def test_clean_japanese_generator_preserves_forms_and_uses_lemma_for_level(
    tmp_path, monkeypatch
):
    book = tmp_path / "content" / "japanese" / "wagahai"
    _write(book / "metadata.json", json.dumps({
        "id": "wagahai", "title_native": "吾輩は猫である",
        "title_en": "I Am a Cat",
    }, ensure_ascii=False))
    _write(book / "n5.md", "## 一\n\n猫は食べました。\n")
    segments = [
        {"surface": "猫", "type": "word", "lemma": "猫",
         "surface_kana": "ねこ", "lemma_kana": "ねこ",
         "part_of_speech": "noun", "conjugation_form": "non-inflecting",
         "meaning_en": "cat", "story_role": "story_term",
         "story_importance_en": "The narrator is a cat."},
        {"surface": "は", "type": "particle", "lemma": "は",
         "surface_kana": "は", "lemma_kana": "は",
         "part_of_speech": "particle", "conjugation_form": "non-inflecting",
         "meaning_en": "topic marker", "story_role": "none",
         "story_importance_en": ""},
        {"surface": "食べ", "type": "word", "lemma": "食べる",
         "surface_kana": "たべ", "lemma_kana": "たべる",
         "part_of_speech": "verb", "conjugation_form": "continuative stem",
         "meaning_en": "eat", "story_role": "none", "story_importance_en": ""},
        {"surface": "まし", "type": "auxiliary", "lemma": "ます",
         "surface_kana": "まし", "lemma_kana": "ます",
         "part_of_speech": "auxiliary", "conjugation_form": "continuative",
         "meaning_en": "polite", "story_role": "none", "story_importance_en": ""},
        {"surface": "た", "type": "auxiliary", "lemma": "た",
         "surface_kana": "た", "lemma_kana": "た",
         "part_of_speech": "auxiliary", "conjugation_form": "past",
         "meaning_en": "past", "story_role": "none", "story_importance_en": ""},
        {"surface": "。", "type": "punctuation", "lemma": "",
         "surface_kana": "", "lemma_kana": "", "part_of_speech": "",
         "conjugation_form": "", "meaning_en": "", "story_role": "none",
         "story_importance_en": ""},
    ]
    segments[1]["grammar_candidate_key"] = "topic-wa"
    overlay = {
        "start": 2, "end": 7, "surface": "食べました",
        "grammar_candidate_key": "verb.polite_past", "pattern": "Vました",
        "meaning_en": "ate", "head_lemma": "食べる",
        "head_lemma_kana": "たべる", "form_label": "polite past",
        "explanation_en": "The verb stem takes polite ます and past た.",
        "components": [
            {"start": 0, "end": 2, "surface": "食べ", "lemma": "食べる",
             "lemma_kana": "たべる", "function_en": "verb stem"},
            {"start": 2, "end": 4, "surface": "まし", "lemma": "ます",
             "lemma_kana": "ます", "function_en": "polite auxiliary stem"},
            {"start": 4, "end": 5, "surface": "た", "lemma": "た",
             "lemma_kana": "た", "function_en": "past auxiliary"},
        ],
    }
    _write(book / "n5.annotations.json", json.dumps({
        "schema_version": 1,
        "chapters": [{"number": 1, "text": "猫は食べました。",
                      "segments": segments, "grammar_overlays": [overlay],
                      "annotation_audit": {"all_reviewed": True}}],
    }, ensure_ascii=False))
    monkeypatch.setattr(generator, "CONTENT_ROOT", tmp_path / "content")
    monkeypatch.setattr(generator, "ASSET_ROOT", tmp_path / "assets")
    monkeypatch.setattr(generator, "japanese_vocabulary_levels", lambda: {
        "猫": 1, "ねこ": 1, "食べる": 1, "たべる": 1,
    })

    readers = generator.build_language("japanese", {"n5": 1})
    sidecar = json.loads(
        (tmp_path / "assets/annotations/japanese_wagahai_n5_001.json")
        .read_text(encoding="utf-8")
    )

    assert readers[0]["chapters"][0]["annotationAsset"].endswith(
        "japanese_wagahai_n5_001.json"
    )
    assert sidecar["segments"][2]["lemma"] == "食べる"
    assert sidecar["segments"][2]["curriculum_status"] == "in_level"
    assert sidecar["segments"][2]["grammar_candidate_keys"] == [
        "verb.polite_past"
    ]
    assert sidecar["segments"][1]["grammar_candidate_keys"] == ["topic-wa"]
    assert sidecar["grammar_overlays"][0]["components"][2]["lemma"] == "た"
    assert sidecar["segments"][0]["story_role"] == "none"
    assert sidecar["segments"][0]["learning_focus"] == "target"
    assert sidecar["segments"][0]["story_importance_en"] == ""


def test_clean_japanese_generator_rejects_dictionary_metadata_on_punctuation(
    tmp_path, monkeypatch
):
    book = tmp_path / "content" / "japanese" / "wagahai"
    _write(book / "metadata.json", json.dumps({
        "id": "wagahai", "title_native": "吾輩は猫である",
        "title_en": "I Am a Cat",
    }, ensure_ascii=False))
    _write(book / "n5.md", "## 一\n\n。\n")
    punctuation = {
        "surface": "。", "type": "punctuation", "lemma": "",
        "surface_kana": "", "lemma_kana": "", "part_of_speech": "",
        "conjugation_form": "", "meaning_en": "", "story_role": "none",
        "story_importance_en": "", "grammar_candidate_key": "",
        "dictionary_key": "と", "dictionary_definition_en": "and",
        "form_steps": [],
    }
    _write(book / "n5.annotations.json", json.dumps({
        "schema_version": 1,
        "chapters": [{
            "number": 1, "text": "。", "segments": [punctuation],
            "grammar_overlays": [],
            "annotation_audit": {"all_reviewed": True},
        }],
    }, ensure_ascii=False))
    monkeypatch.setattr(generator, "CONTENT_ROOT", tmp_path / "content")
    monkeypatch.setattr(generator, "ASSET_ROOT", tmp_path / "assets")

    with pytest.raises(ValueError, match="punctuation has Japanese metadata"):
        generator.build_language("japanese", {"n5": 1})
