from pipeline.chinese_boundary_proposals import (
    boundary_proposals, constrained_correction_prompt, generic_gazetteer_spans,
    learner_construction_spans,
)
from pipeline.fixed_boundary_annotation import boundary_correction_prompt


def test_proposals_have_exact_offsets_and_no_selection():
    text = "刘备见夕阳红。"
    value = boundary_proposals(text, {"title": "刘备拜见太守"})
    assert value["selected_boundaries"] is None
    assert value["contract"] == "non_authoritative_boundary_proposals_v1"
    for proposal in value["proposals"].values():
        assert proposal["reconstructs"] is True
        assert "".join(item["text"] for item in proposal["segments"]) == text
        assert all(text[item["start"]:item["end"]] == item["text"] for item in proposal["segments"])
    assert all(text[item["start"]:item["end"]] == item["text"] for item in value["disagreement_regions"])


def test_generic_gazetteer_emits_overlapping_candidates_with_sources():
    text = "刘备拜见河东太守。"
    metadata = {"chapter_title": "刘备拜见河东太守", "scenes": [{"title": "刘备出发"}]}
    spans = generic_gazetteer_spans(text, metadata)
    assert any(item["text"] == "刘备" and item["kind"] == "name_candidate" for item in spans)
    assert any(item["text"].endswith("太守") and item["kind"] == "title_candidate" for item in spans)
    assert all(item["metadata_sources"] for item in spans)


def test_correction_prompt_keeps_proposals_non_authoritative():
    value = boundary_proposals("张飞来了。", {"title": "张飞"})
    prompt = constrained_correction_prompt(value)
    assert "Neither tokenizer is authoritative" in prompt
    assert "selected_boundaries" in prompt
    assert "never rewrite the text" in prompt
    assert "non_authoritative_boundary_proposals_v1" in boundary_correction_prompt(
        "张飞来了。", {"title": "张飞"}
    )


def test_directional_complement_is_exposed_as_non_authoritative_candidate():
    text = "地震和海水泛上来，百姓生活很难。"
    surfaces = [
        "地震", "和", "海水", "泛", "上来", "，", "百姓", "生活", "很", "难", "。",
    ]
    spans = learner_construction_spans(text, surfaces)

    assert spans == [{
        "start": 5,
        "end": 8,
        "text": "泛上来",
        "kind": "verb_directional_complement_candidate",
        "parts": ["泛", "上来"],
    }]
    assert boundary_proposals(text)["learner_construction_spans"] == spans
