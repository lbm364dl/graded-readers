#!/usr/bin/env python3
"""Unattended, source-grounded agent harness for Japanese graded readers."""

from __future__ import annotations

import argparse
import asyncio
import copy
from difflib import SequenceMatcher
import json
import math
from pathlib import Path
import re
from typing import Any

from pipeline.agent_harness import (
    BookHarness, ChapterHarness, DEFAULT_RUNS, SCHEMAS,
    apply_compact_review_policy, best_scene_attempt,
    discard_incorrect_length_findings,
    digest, gather_all_or_raise, length_violations, run_status_for_verdicts,
    status, utc_now,
)
from pipeline.japanese_readability import (
    GRAMMAR_BASELINE_LEMMAS,
    MAX_ABOVE_LEVEL_RATIO,
    STORY_ALLOWLIST,
    STORY_TERM_BUDGET,
    LEVEL_NUMBER,
    level_diagnostics,
    matched_level,
    matched_level_with_overlays,
    preflight_level_diagnostics,
    vocabulary_prompt_reference,
)
from pipeline.japanese_dictionary_links import candidate_for_lemma, dictionary_link_issue


# Per source chapter. The strict increase is both prompted and audited.
DEFAULT_JLPT_TARGETS = {
    "n5": 350, "n4": 700, "n3": 1600, "n2": 6550, "n1": 9550,
}
JLPT_LEVELS = ["n5", "n4", "n3", "n2", "n1"]
JAPANESE_ANNOTATION_CHUNK_POLICY = (
    "sentence-safe-v29-connected-manner-connective-evidential-forms"
)
JAPANESE_ANNOTATION_CHUNK_TARGETS = {
    # Every level gets one complete sentence per agent call.  The richer N2/N1
    # sentences are precisely where batching several sentences caused the
    # annotator to fall back to analyzer-sized morphology and omit useful
    # construction cards.  Sentence boundaries preserve all local syntax while
    # keeping the lexical, inflection, and grammar audit cognitively bounded.
    "n5": 1, "n4": 1, "n3": 1, "n2": 1, "n1": 1,
}
JAPANESE_ANNOTATION_CHUNK_MAXIMUMS = {
    "n5": 45, "n4": 60, "n3": 100, "n2": 125, "n1": 145,
}
CHAPTER_TARGETS = {
    "n5": [350, 400, 375, 325, 325, 325, 350, 375, 350, 375, 400],
    "n4": [700, 800, 750, 625, 625, 650, 675, 700, 675, 750, 825],
    "n3": [1600, 1800, 1700, 1400, 1400, 1450, 1500, 1600, 1550, 1700, 1900],
    "n2": [4800, 7700, 7100, 5950, 5950, 6100, 6300, 6550, 6400, 7100, 8050],
    "n1": [6950, 11200, 10350, 8700, 8650, 8900, 9150, 9550, 9350, 10350, 11850],
}

JLPT_ADAPTATION_SCOPE = {
    "n5": (
        "Retell only one central storyline from the chapter in very short, "
        "direct sentences. Most source spans should contribute no output. "
        "Freely omit secondary incidents, descriptions, lists, minor people, "
        "satire, and exact objects. Coverage of the whole chapter is not a goal."
    ),
    "n4": (
        "Retell the main action with its simplest causes and results. Select "
        "only the source spans needed for that short arc; omit secondary "
        "episodes, decorative description, long lists, and most satire."
    ),
    "n3": (
        "Produce a wider but still selective abridgment focused on the major "
        "episodes and turning points. Merge or omit subplots, examples, lists, "
        "and minor incidents freely; exhaustive source coverage is not required."
    ),
    "n2": "Retain most consequential events and important secondary episodes.",
    "n1": "Provide a detailed modern abridgment while trimming repetition and ornament.",
}


def jlpt_orthography_guidance(level: str) -> str:
    """Keep source flavor without making beginner prose look archaic."""
    if level in {"n5", "n4", "n3"}:
        return (
            "Use contemporary learner-facing spellings. Write the ordinary "
            "negative/existential adjective ない in kana even when the source "
            "uses lexical 無い, and always write auxiliary negative ない in "
            "kana. Modernize comparable optional literary kanji spellings "
            "when doing so does not change the word or meaning."
        )
    return (
        "Source-authentic literary kanji spellings may remain when they are "
        "lexical words, but inflectional or auxiliary negative ない must still "
        "be written in kana rather than as 無い."
    )


def jlpt_narrative_guidance(level: str) -> str:
    """Give scene agents one chapter-wide prose contract."""
    beginner = (
        "For N5 narration, use a consistent です/ます register across every "
        "scene; plain forms may still appear where Japanese grammar requires "
        "them inside clauses and quotations. "
        if level == "n5" else
        "Choose one natural narrative register and keep it consistent across scenes. "
    )
    level_ceiling = (
        "N5 prose should rely mainly on basic です/ます sentences, ordinary "
        "particles, simple て/た forms, and at most a small number of clearly "
        "useful teaching constructions. Avoid literary negative Vず, archaic "
        "or source-like wording such as 住家と極める, and chains of passives. "
        "The required-event ledger specifies facts, not wording: simplify "
        "住家と極める to この家に住むことにする and prefer a clear active "
        "sentence when passive voice is not essential. "
        if level == "n5" else ""
    )
    return beginner + level_ceiling + (
        "Never pad a short event by restating the same fact in several sentences. "
        "Prefer one clear sentence with an explicit referent over vague omitted "
        "subjects whose action becomes ambiguous after simplification."
    )

JLPT_EVENT_BUDGET = {
    "n5": (2, 3), "n4": (4, 6), "n3": (8, 12),
    "n2": (14, 22), "n1": (20, 32),
}
JLPT_SELECTED_SCENE_LIMIT = {"n5": 2, "n4": 4, "n3": 6, "n2": 8, "n1": 10}
JLPT_EVENTS_PER_SCENE = {"n5": 2, "n4": 2, "n3": 3, "n2": 4, "n1": 5}
JLPT_MAX_SENTENCE_CHARS = {"n5": 30, "n4": 48, "n3": 72, "n2": 100, "n1": 140}


_EQUIVALENT_GROUPING_MARKERS = (
    "equally valid", "also valid", "acceptable analysis", "acceptable segmentation",
    "defensible analysis", "defensible segmentation", "depending on the analysis",
    "analysis choice", "category label", "part-of-speech", "pos label",
)

_NONBLOCKING_NUANCE_MARKERS = (
    "is acceptable", "is possible", "also possible", "this is minor",
    "minor issue", "minor nuance", "less precise", "could be clearer",
)
_SERIOUS_ERROR_MARKERS = (
    "incorrect", "wrong", "misleading", "does not match", "fails to",
)
_CONJUGATION_TAXONOMY_MARKERS = (
    "未然形", "連用形", "終止形", "連体形", "仮定形",
    "irrealis", "continuative form", "terminal form", "attributive form",
    "conditional form", "plain form", "stem label", "form/base",
)


def apply_reader_useful_annotation_review_policy(
    review: dict[str, Any],
    annotation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Keep learner-harming findings blocking, not linguistic-analysis trivia."""
    value = copy.deepcopy(review)
    blocking: list[dict[str, Any]] = []
    source_text = "" if annotation is None else "".join(
        str(segment.get("surface", ""))
        for segment in annotation.get("segments", [])
        if isinstance(segment, dict)
    )
    for issue in value.get("issues", []):
        problem = str(issue.get("problem", ""))
        segment_text = str(issue.get("segment_text", ""))
        detail = " ".join(
            str(issue.get(field, ""))
            for field in ("explanation", "suggested_fix")
        ).casefold()
        sasahara_is_landscape = bool(
            annotation is not None
            and any(
                isinstance(segment, dict)
                and segment.get("surface") == "笹原"
                and segment.get("type") == "word"
                and "bamboo-grass field" in str(
                    segment.get("meaning_en", "")
                ).casefold()
                for segment in annotation.get("segments", [])
            )
        )
        tagaru_primary = next((
            segment for segment in (
                annotation.get("segments", []) if annotation is not None else []
            )
            if isinstance(segment, dict)
            and "tagaru" in str(
                segment.get("grammar_candidate_key", "")
            ).casefold()
            and str(segment.get("surface", "")).endswith("たがる")
            and segment.get("type") == "grammar"
        ), None)
        tagaru_overlay = next((
            overlay for overlay in (
                annotation.get("grammar_overlays", [])
                if annotation is not None else []
            )
            if isinstance(overlay, dict)
            and "tagaru" in str(
                overlay.get("grammar_candidate_key", "")
            ).casefold()
            and isinstance(overlay.get("components"), list)
            and len(overlay["components"]) == 2
            and str(overlay["components"][1].get("surface", "")) == "たがる"
        ), None)
        if tagaru_primary is not None and segment_text in {
            str(tagaru_primary.get("surface", "")),
            str(tagaru_primary.get("surface", ""))[:-len("たがる")],
        }:
            if (
                problem in {"lemma", "lemma_reading"}
                and any(marker in detail for marker in (
                    "complete construction", "construction lemma",
                    "whole construction", "realized surface",
                ))
            ):
                # The complete surface and Vたがる key/pattern identify the
                # grammar. The card's source lemma deliberately remains the
                # content predicate, matching Vながら and directional cards.
                continue
            if any(marker in detail for marker in (
                "intermediate plain past", "plain past form",
                "past-form content predicate", "attaching to a plain-past",
            )):
                # The た is the first mora of たがる, not past た. Requiring a
                # Vた stage teaches exactly the false analysis this pipeline is
                # designed to avoid.
                continue
        if (
            tagaru_overlay is not None
            and problem == "grammar_components"
            and any(marker in detail for marker in (
                "lookup_kind", "dictionary link", "dictionary entry",
            ))
        ):
            content = tagaru_overlay["components"][0]
            if (
                content.get("lookup_kind") == "none"
                and candidate_for_lemma(
                    str(content.get("lemma", "")),
                    str(content.get("lemma_kana", "")),
                ) is None
            ):
                # Linguistically lexical does not mean an actionable bundled
                # dictionary entry exists. Keep the explanatory component but
                # do not advertise a dead lookup.
                continue
        if (
            sasahara_is_landscape
            and segment_text in {"笹原", "に"}
            and any(marker in detail for marker in (
                "sasahara", "surname", "person’s", "person's",
                "agent of the passive", "passive agent",
            ))
        ):
            # The source says the kitten was abandoned in a bamboo-grass
            # field.  A recurring reviewer hallucination turns 笹原 into a
            # person's surname and then reverses locative に into agentive
            # "by".  The source-grounded canonical candidate is authoritative.
            continue
        if (
            annotation is not None
            and problem == "grammar_components"
            and "lookup_kind" in detail
            and "lexical" in detail
            and any(
                isinstance(component, dict)
                and component.get("lookup_kind") == "none"
                and candidate_for_lemma(
                    str(component.get("lemma", "")),
                    str(component.get("lemma_kana", "")),
                ) is None
                and (
                    str(component.get("surface", "")) in detail
                    or str(overlay.get("surface", "")) == segment_text
                )
                for overlay in annotation.get("grammar_overlays", [])
                if isinstance(overlay, dict)
                for component in overlay.get("components", [])
            )
        ):
            # `lookup_kind` is actionable UI metadata. A linguistic lexical
            # base with no bundled dictionary entry stays explanatory text but
            # cannot honestly advertise a lexical tap target.
            continue
        if (
            annotation is not None
            and problem in {"conjugation", "lemma_reading"}
            and any(
                str(segment.get("surface", "")) == segment_text
                and str(segment.get("lemma", ""))
                and str(segment.get("lemma", "")) in detail
                and any(marker in detail for marker in (
                    "intermediate", "form_steps", "form steps", "include",
                ))
                for segment in annotation.get("segments", [])
                if isinstance(segment, dict)
            )
        ):
            # The lemma is already the first UI row and the deterministic form
            # validator forbids repeating it inside form_steps. Reviewers may
            # require real derived intermediates, but never the lemma itself.
            continue
        if (
            annotation is not None
            and problem in {"conjugation", "grammar_components"}
            and any(marker in detail for marker in (
                "adjective connective", "adjective く-form", "adjective ku-form",
                "intermediate form before the final", "intermediate stage 悪く",
            ))
            and any(
                str(segment.get("surface", "")) == segment_text
                and segment.get("type") == "grammar"
                and str(segment.get("lemma_kana", "")).endswith("くなる")
                for segment in annotation.get("segments", [])
                if isinstance(segment, dict)
            )
        ):
            # Aく + なる is already available in the mandatory roots overlay.
            # Repeating the pre-lemma adjective part in the inflection chain
            # creates duplicate UI and contradicts the displayed-lemma model.
            continue
        if (
            annotation is not None
            and problem in {"lemma_reading", "grammar_components", "conjugation"}
            and any(
                str(segment.get("surface", "")) == segment_text
                and segment.get("type") == "grammar"
                and (
                    "neba" in str(
                        segment.get("grammar_candidate_key", "")
                    ).casefold()
                    or "ねばならない" in str(segment.get("surface", ""))
                )
                and str(segment.get("lemma", ""))
                and str(segment.get("lemma", "")) != segment_text
                and isinstance(segment.get("form_steps"), list)
                and len(segment["form_steps"]) >= 2
                and isinstance(segment["form_steps"][-1], dict)
                and str(segment["form_steps"][-1].get("form", ""))
                == segment_text
                for segment in annotation.get("segments", [])
                if isinstance(segment, dict)
            )
            and any(marker in detail for marker in (
                "fully inflected surface", "empty form_steps", "empty form steps",
                "canonical v", "construction head", "hiding the meaningful",
            ))
        ):
            # The current candidate has already been normalized to a content
            # root (困る) followed by every causative/て/やる/obligation stage.
            # Cached or morphology-oriented reviews may describe the previous
            # self-lemma candidate or request an undisplayable `V...` head.
            continue
        if (
            annotation is not None
            and "dictionary" in detail
            and any(marker in detail for marker in (
                "link", "canonical", "dictionary_key", "dictionary key",
            ))
        ):
            matching_lexical = [
                segment for segment in annotation.get("segments", [])
                if isinstance(segment, dict)
                and str(segment.get("surface", "")) == segment_text
                and segment.get("type") in {"word", "idiom"}
            ]
            if matching_lexical and all(
                dictionary_link_issue(
                    lemma=str(segment.get("lemma", "")),
                    lemma_kana=str(segment.get("lemma_kana", "")),
                    key=str(segment.get("dictionary_key", "")),
                    definition=str(segment.get("dictionary_definition_en", "")),
                    functional=False,
                    require_available=True,
                ) is None
                for segment in matching_lexical
            ):
                # Exact canonical validation already passed. A model reviewer
                # must not reverse it by guessing that a valid written variant
                # is a reading alias or the wrong headword.
                continue
            if matching_lexical and all(
                candidate_for_lemma(
                    str(segment.get("lemma", "")),
                    str(segment.get("lemma_kana", "")),
                ) is None
                for segment in matching_lexical
            ):
                # The local dictionary, not reviewer speculation, is the
                # authority on whether a tappable target exists. Keep the
                # agent explanation but do not loop on an invented entry.
                continue
            if (
                not matching_lexical
                and problem in {"meaning", "lemma", "lemma_reading"}
                and any(marker in detail for marker in (
                    "verify the exact", "if present", "if available",
                    "left unlinked", "add its matching",
                ))
            ):
                # Reviewers sometimes invent a larger lexical phrase such as
                # ある日 and then demand a whole-phrase dictionary entry even
                # though no such primary tap target exists. Dictionary links
                # are validated on the actual primary segments and overlay
                # components, never on a reviewer-created span.
                continue
        if (
            problem == "meaning"
            and segment_text == "強い"
            and annotation is not None
            and any(
                str(segment.get("surface", "")) == "強い"
                and "strong" in str(segment.get("meaning_en", "")).casefold()
                for segment in annotation.get("segments", [])
                if isinstance(segment, dict)
            )
            and any(marker in detail for marker in (
                'use meaning_en such as "strong"', "use strong",
                "adjective “strong”",
            ))
        ):
            # The current meaning already begins with lexical "strong" and
            # adds the accurate local より comparison. A cached review may
            # still be describing the earlier bare "stronger" candidate.
            continue
        # Reconstruction and the deterministic annotation contract are checked
        # before model review. A POS-only dispute does not hurt a tap reader,
        # but confusing a name with an ordinary word changes reader behavior.
        if problem == "wrong_type":
            matching_types = {
                str(segment.get("type", ""))
                for segment in (annotation or {}).get("segments", [])
                if isinstance(segment, dict)
                and str(segment.get("surface", "")) == segment_text
            }
            if (
                matching_types == {"name"}
                and any(marker in detail for marker in (
                    "proper-name type", "proper name type",
                    "proper-name category", "proper name category",
                    "schema's proper-name", "schema’s proper-name",
                ))
            ):
                # `name` is the schema's proper-name value. Reviewers
                # sometimes invent an unsupported `proper_name` enum and
                # then repeatedly request a change the schema cannot express.
                continue
            if (
                matching_types == {"word"}
                and "not a proper name" in detail
                and any(marker in detail for marker in (
                    "change type to word", "type to word",
                    "remain an accessible word", "lexical pronoun",
                ))
            ):
                # Canonicalization has already corrected stale 吾輩=name
                # output before review; do not repair an issue that is already
                # satisfied by the candidate the reviewer received.
                continue
            if (
                any(marker in detail for marker in (
                    "is the cat's name", "is the cats name",
                    "is an animal name", "is the animal's name",
                ))
                and any(marker in detail for marker in (
                    "not an actual named person", "restricted to people",
                    "not a person", "not a place",
                ))
            ):
                # Named animals are proper names in a story reader too. Some
                # reviewers over-apply a person/place-only named-entity rule.
                continue
            if not any(marker in detail for marker in (
                "proper name", "not a name", "not the name", "pronoun",
                "actual named", "named person", "named place",
                "named animal", "common landscape",
            )):
                continue
        if problem == "part_of_speech":
            # The reader exposes the agent-selected dictionary form and form
            # label. Competing school-grammar POS labels are not worth an
            # otherwise endless repair loop when the lexical analysis is sound.
            continue
        if (
            problem in {"lemma", "lemma_reading"}
            and segment_text == "という"
            and any(marker in detail for marker in (
                "dictionary lemma", "lexical lemma", "lemma should be 言う",
                "use lemma 言う", "surface of 言う",
            ))
        ):
            # The primary card represents the conventional reported/naming
            # grammar unit という, analogous to ことにする rather than a bare
            # conjugation drill for 言う. Keep its searchable learned-unit lemma;
            # the key and form label preserve the grammar analysis.
            continue
        if (
            problem == "lemma"
            and "na-adjective" in detail
            and "copula" in detail
            and any(marker in detail for marker in (
                "include the copula", "lemma omits the copula",
                "lemma should be", "dictionary form is",
            ))
        ):
            # Dictionaries vary between the adjectival-noun base and a
            # citation form with だ. The reader deliberately keeps the
            # searchable descriptive base (e.g. つるつる) while the complete
            # surface and form label explain predicative で/だ.
            continue
        if (
            problem in {"lemma", "over_grouped"}
            and re.fullmatch(
                r"[一二三四五六七八九十百千〇零\d]+"
                r"(?:匹|人|個|本|枚|歳|才|回|つ|ページ)",
                segment_text,
            )
            and any(marker in detail for marker in ("split", "separate"))
        ):
            # A number-counter quantity is the useful dictionary/tap unit in
            # context. Splitting it into a bare number and counter recreates
            # tokenizer atoms and makes the range notation harder to read.
            continue
        if problem == "story_role":
            # The independently reviewed chapter plan is applied
            # deterministically after every agent candidate and is the sole
            # authority for story-term highlighting. Model reviewers can see
            # or report stale pre-canonical values, so their role disputes do
            # not trigger semantic repair loops.
            continue
        if problem == "conjugation" and any(
            marker in detail for marker in _CONJUGATION_TAXONOMY_MARKERS
        ):
            # Competing school-grammar labels repeatedly reverse across valid
            # reviews. Dictionary form, surface form, contextual meaning, and
            # whole-construction grammar remain independently reviewed.
            continue
        if (
            problem in {"surface_reading", "under_grouped", "conjugation"}
            and "connective" in detail
            and "stem" in detail
            and any(marker in detail for marker in (
                "punctuation", "comma", "て-form", "te-form",
            ))
        ):
            # In written Japanese a ren'yōkei directly before punctuation is
            # itself the complete connective form in the immutable text. A
            # reviewer must not demand comma-inclusive tokens or rewrite it as
            # a て-form; detached suffixes remain caught deterministically.
            continue
        if problem == "reconstruction":
            # review_annotation is reached only after exact segment, overlay,
            # and component reconstruction has passed in Python.
            continue
        if "offset" in detail:
            # Python offsets and exact surface reconstruction already passed the
            # deterministic gate. Model reviewers are poor character counters;
            # retain their semantic grammar findings but not contrary arithmetic.
            continue
        if problem in {"reconstruction", "grammar_components"} and any(
            marker in detail for marker in (
                "range is off", "range is shifted", "position ",
                "begins at position", "ends at position",
            )
        ):
            continue
        if problem in {"over_grouped", "under_grouped"}:
            if any(marker in detail for marker in _EQUIVALENT_GROUPING_MARKERS):
                continue
        if (
            problem == "under_grouped"
            and " / " in segment_text
            and segment_text.replace(" / ", "") in {
                "何度も", "何回も", "一度も",
            }
            and segment_text.replace(" / ", "") not in source_text
        ):
            # A reviewer must not merge non-adjacent rows. In 何度…出しても,
            # the も belongs to the later Vても construction; inventing the
            # different contiguous adverb 何度も changes the source text.
            continue
        if (
            problem == "over_grouped"
            and annotation is not None
            and any(
                isinstance(segment, dict)
                and segment.get("surface") == "何にでも"
                for segment in annotation.get("segments", [])
            )
            and (
                segment_text in {"何にでも", "何"}
                or "何にでも" in detail
            )
        ):
            # 何にでも is a conventional indefinite expression meaning
            # anything/everything in this context. Keep the phrase-level tap;
            # generic particle cards remain available elsewhere in the text.
            continue
        if (
            problem == "under_grouped"
            and annotation is not None
            and " / " in segment_text
            and any(
                str(overlay.get("surface", ""))
                == segment_text.replace(" / ", "")
                for overlay in annotation.get("grammar_overlays", [])
                if isinstance(overlay, dict)
            )
            and any(marker in detail for marker in (
                "one grammar primary", "one primary",
                "only in its overlay", "component only in",
            ))
        ):
            # The complete overlay already supplies the construction-sized
            # explanation. Keep its lexical head as a normal primary tap
            # instead of hiding dictionary access one level deeper.
            continue
        if (
            problem == "over_grouped"
            and "たり" in detail
            and "たりする" in detail.replace("...", "")
            and any(marker in detail for marker in (
                "annotate する separately", "する separately",
                "split", "separate auxiliary",
            ))
        ):
            # The completing たりする chain is precisely the kind of
            # conventional learner unit this reader keeps whole. Splitting off
            # する would recreate the tokenizer-style presentation the user
            # asked us to remove.
            continue
        if (
            problem == "over_grouped"
            and any(form in detail for form in ("何度も", "何回も", "一度も"))
            and any(marker in detail for marker in (
                "split", "separate", "particle も", "も separately",
            ))
        ):
            # These frequency expressions are conventional learner-sized
            # adverbs. Splitting off も produces a misleading generic
            # particle card and contradicts the deterministic boundary gate.
            continue
        if (
            problem == "grammar_components"
            and any(marker in detail for marker in (
                "vague", "could be clearer", "more explicit",
                "does not clearly explain",
            ))
            and not any(marker in detail for marker in _SERIOUS_ERROR_MARKERS)
        ):
            # Exact component surfaces, offsets, lemmas, and readings have
            # already passed the deterministic contract.  Do not spend an
            # entire repair tail polishing an accurate short function label
            # such as "verb continuative form" into a longer paraphrase.
            continue
        if (
            problem == "grammar_components"
            and "stem" in detail
            and any(marker in detail for marker in (
                "stem/suffix", "stem fragment", "inflection fragment",
                "bare stem", "inflectional stem",
            ))
        ):
            # Primary tap targets must never be tokenizer fragments. Inside a
            # whole-form grammar card, however, an exact surface stem such as
            # い with lemma いる is the only reconstructing way to expose the
            # root alongside ません. The main segment remains いません.
            continue
        if (
            problem in {"lemma", "under_grouped", "over_grouped"}
            and any(marker in detail for marker in (
                "て-form and the auxiliary いる",
                "て-form and auxiliary いる",
                "て-form + いる",
                "split into 休んで",
            ))
            and any(marker in detail for marker in (
                "split", "lemma is only", "construction lemma",
            ))
        ):
            # Routine Vている is intentionally one learner tap target whose
            # lexical lemma is the content verb.  Demanding Vて + いる would
            # reinstate morphology-tokenizer rows and contradict the separate
            # boundary editor plus deterministic whole-form gate.
            continue
        if (
            problem == "grammar"
            and annotation is not None
            and any(
                str(segment.get("surface", "")) == segment_text
                and segment.get("type") == "word"
                and "tari" in str(
                    segment.get("grammar_candidate_key", "")
                ).casefold()
                for segment in annotation.get("segments", [])
                if isinstance(segment, dict)
            )
            and any(marker in detail for marker in (
                "no grammar candidate", "remove the grammar", "isolated vたり",
                "does not need a separate grammar primary",
            ))
        ):
            # A complete lexical Vたり surface remains a word tap, while its
            # provisional key records that it participates in the chapter's
            # wider たり...たりする listing construction. Keys are future
            # lesson-clustering metadata; they do not turn the word into an
            # atomic grammar primary.
            continue
        if (
            problem == "over_grouped"
            and annotation is not None
            and any(
                str(overlay.get("surface", "")) == segment_text
                and japanese_overlay_policy_issue(overlay) is None
                and (
                    "たり" in segment_text
                    or str(overlay.get("head_lemma", "")).endswith(
                        ("出す", "始める", "続ける", "終わる")
                    )
                )
                for overlay in annotation.get("grammar_overlays", [])
                if isinstance(overlay, dict)
            )
            and any(marker in detail for marker in (
                "inflected fragment", "suffix-like form", "realized た-form",
                "realized surface", "useful component is",
            ))
        ):
            # Overlay components must reproduce the visible surface. A row such
            # as 出したり can still correctly expose lemma 出す and its exact
            # dictionary entry; replacing its surface with an absent 出す would
            # break reconstruction and teach less, not more.
            continue
        if (
            problem == "lemma_reading"
            and annotation is not None
            and segment_text.startswith("目が回")
            and any(
                str(overlay.get("surface", "")) == segment_text
                and str(overlay.get("head_lemma", "")) == "目が回る"
                for overlay in annotation.get("grammar_overlays", [])
                if isinstance(overlay, dict)
            )
            and any(marker in detail for marker in (
                "complete surface form", "text contains", "change head_lemma",
                "exact overlay surface",
            ))
        ):
            # The card title already shows exact conjunctive surface 目が回り;
            # its "From" row must remain the canonical collocation 目が回る,
            # just as an ordinary inflected verb points back to dictionary form.
            continue
        if (
            problem == "under_grouped"
            and annotation is not None
            and segment_text.endswith("すると")
            and any(
                str(segment.get("surface", "")) == "すると"
                and segment.get("type") == "grammar"
                and str(segment.get("grammar_candidate_key", "")).strip()
                for segment in annotation.get("segments", [])
                if isinstance(segment, dict)
            )
            and any(marker in detail for marker in (
                "overlay", "predicate-sized phrase", "temporal expression",
            ))
        ):
            # Sentence-linking すると already has a complete learned-unit card,
            # while しばらく remains a normal adverb tap. Wrapping the two in a
            # second card adds UI nesting without introducing another opaque
            # meaning or inaccessible lexical root.
            continue
        if (
            problem == "over_grouped"
            and annotation is not None
            and any(
                str(segment.get("surface", "")) == segment_text
                and segment.get("type") == "word"
                and any(
                    marker in str(segment.get("part_of_speech", "")).casefold()
                    for marker in ("verb", "adjective")
                )
                and str(segment.get("lemma", ""))
                and str(segment.get("lemma", "")) != segment_text
                for segment in annotation.get("segments", [])
                if isinstance(segment, dict)
            )
            and "split" in detail
            and "stem" in detail
            and any(marker in detail for marker in (
                "auxiliary", "ending", "suffix", "passive", "causative",
                "negative", "past",
            ))
        ):
            # Complete inflected words are deliberate tap targets. A reviewer
            # that asks for 覆わ + れる or 食べ + た is reverting to a
            # morphology analyzer, while the primary card already exposes the
            # dictionary lemma and ordered derivation. The deterministic form
            # and boundary checks remain authoritative for an actually wrong
            # whole-form analysis.
            continue
        if (
            problem == "over_grouped"
            and annotation is not None
            and segment_text.endswith(("ていて", "でいて"))
            and any(
                str(segment.get("surface", "")) == segment_text
                and segment.get("type") == "word"
                and "verb" in str(
                    segment.get("part_of_speech", "")
                ).casefold()
                for segment in annotation.get("segments", [])
                if isinstance(segment, dict)
            )
            and any(marker in detail for marker in (
                "final て", "following connective て", "て separately",
                "separate connective", "connective particle",
            ))
        ):
            # Vていて is the connective form of the complete Vている state:
            # 残る -> 残っている -> 残っていて. The final て is not a useful
            # independent tap target and must not be peeled off by review.
            continue
        if (
            problem in {"lemma", "surface_reading", "lemma_reading", "meaning"}
            and any(marker in detail for marker in _NONBLOCKING_NUANCE_MARKERS)
            and not any(marker in detail for marker in _SERIOUS_ERROR_MARKERS)
        ):
            # Reviewers often volunteer a stylistic alternative while stating
            # that the existing learner-facing value is valid. Keep genuine
            # errors blocking, but do not churn acceptable wording/readings.
            continue
        blocking.append(issue)
    value["issues"] = blocking
    value["verdict"] = "revise" if blocking else "pass"
    return value


def target_for_source(source: str | Path, level: str) -> int:
    match = re.fullmatch(r"chapter_(\d{2})", Path(source).stem)
    if match and 1 <= int(match.group(1)) <= 11:
        return CHAPTER_TARGETS[level][int(match.group(1)) - 1]
    return DEFAULT_JLPT_TARGETS[level]


def japanese_char_count(text: str) -> int:
    """Count Japanese letters, excluding punctuation, Latin text and spacing."""
    return sum(
        "\u3040" <= char <= "\u30ff"
        or "\u3400" <= char <= "\u4dbf"
        or "\u4e00" <= char <= "\u9fff"
        or "\uf900" <= char <= "\ufaff"
        or char == "々"
        for char in text
    )


def overlong_japanese_sentences(text: str, level: str) -> list[tuple[int, str]]:
    """Return sentences whose Japanese-letter count exceeds the level ceiling."""
    limit = JLPT_MAX_SENTENCE_CHARS[level]
    return [
        (count, sentence.strip())
        for sentence in re.split(r"(?<=[。！？])", text)
        if (count := japanese_char_count(sentence)) > limit
    ]


def japanese_narrative_register_issues(text: str, level: str) -> list[str]:
    """Catch a chapter-level polite/plain switch that scene reviews cannot see."""
    if level not in {"n5", "n4"}:
        return []
    # Dialogue may naturally use a different register from the narration.
    narration = re.sub(r"「[^」]*」", "", text, flags=re.S)
    paragraph_styles: list[tuple[int, int]] = []
    polite_pattern = re.compile(
        r"(?:です|でした|ます|ました|ません|ませんでした)$"
    )
    plain_pattern = re.compile(
        r"(?:だった|なかった|かった|[うくぐすつぬぶむるた]|だ|ない|いた|ある)$"
    )
    for paragraph in re.split(r"\n+", narration):
        polite = plain = 0
        for sentence in re.split(r"[。！？]", paragraph):
            ending = sentence.strip().rstrip("、」』）)")
            if not ending:
                continue
            if polite_pattern.search(ending):
                polite += 1
            elif plain_pattern.search(ending):
                plain += 1
        paragraph_styles.append((polite, plain))
    dominant: list[str] = []
    for polite, plain in paragraph_styles:
        if max(polite, plain) < 2:
            continue
        dominant.append(
            "polite" if polite > plain else "plain" if plain > polite else "mixed"
        )
    if "polite" in dominant and "plain" in dominant:
        return [
            "narration switches between dominant polite and plain register across paragraphs"
        ]
    total_polite = sum(item[0] for item in paragraph_styles)
    total_plain = sum(item[1] for item in paragraph_styles)
    if total_polite >= 3 and total_plain >= 3:
        return [
            "narration mixes several polite and plain finite sentence endings without a clear purpose"
        ]
    return []


def japanese_beginner_prose_issues(text: str, level: str) -> list[str]:
    """Reject a few unambiguous source-register leaks in beginner prose."""
    if level not in {"n5", "n4", "n3"}:
        return []
    issues: list[str] = []
    if level in {"n5", "n4"} and re.search(
        r"(?:来|せ|分から|知ら|動か|食べ|見え?|言わ|鳴か|読ま|行か|"
        r"でき|持た|入ら|思わ|考え)ず(?:[、。]|$)",
        text,
    ):
        issues.append(
            f"literary negative Vず is unnecessary in learner-facing {level.upper()} prose"
        )
    if level == "n5" and "住家と極め" in text:
        issues.append("source-like 住家と極める must be paraphrased for N5")
    if "棄て" in text:
        issues.append("use contemporary learner-facing 捨て rather than literary 棄て")
    if "無い" in text:
        issues.append("write ordinary negative/existential ない in kana")
    if re.search(r"(?:た|る)事(?:だけ|も|が|は|を|に)", text):
        issues.append(
            "write grammatical/formal-noun こと in kana in beginner prose"
        )
    if "池の左を" in text:
        issues.append(
            "池の左を is unnatural for the source meaning 'with the pond on the "
            "left'; use a natural relation such as 池を左に見ながら"
        )
    if level == "n3":
        optional_literary_spellings = {
            "小供": "子供",
            "我儘": "わがまま",
            "善い": "よい",
            "肴屋": "魚屋",
        }
        for source_spelling, modern_spelling in optional_literary_spellings.items():
            if source_spelling in text:
                issues.append(
                    f"modernize optional literary spelling {source_spelling} "
                    f"to {modern_spelling} for N3"
                )
    return issues


def japanese_paragraph_structure_issues(
    text: str, selected_scene_count: int,
) -> list[str]:
    """Keep independently reviewed scene blocks visible after chapter rewrites."""
    paragraphs = [
        paragraph for paragraph in re.split(r"\n\s*\n", text.strip())
        if paragraph.strip()
    ]
    if selected_scene_count > 1 and len(paragraphs) < selected_scene_count:
        return [
            f"chapter has {len(paragraphs)} paragraph block(s) for "
            f"{selected_scene_count} selected source scenes"
        ]
    return []


def minimum_vocabulary_replacements(diagnostics: dict[str, Any]) -> int:
    """Return the fewest above-level token substitutions needed to pass."""
    considered = int(diagnostics.get("tokens_considered", 0))
    above = int(diagnostics.get("above_level_tokens", 0))
    maximum = float(diagnostics.get("maximum_above_level_ratio", 0.0))
    return max(0, math.ceil(above - maximum * considered - 1e-12))


_OBVIOUS_INFLECTION_FRAGMENTS = {
    "まし", "ませ", "だっ", "なかっ", "られ", "させ",
}
_CONNECTIVE_ENDINGS = {"て", "で"}
_FINAL_ENDINGS = {"た", "だ", "ん"}

_NUMERIC_COMMA_CONSTRUCTION = re.compile(
    r"^[一二三四五六七八九十百千〇零\d]+、"
    r"[一二三四五六七八九十百千〇零\d]+"
    r"(?:匹|人|個|本|枚|歳|才|回|度|つ|ページ)?(?:も)?$"
)
_NUMERIC_COMMA_SPAN = re.compile(
    r"[一二三四五六七八九十百千〇零\d]+、"
    r"[一二三四五六七八九十百千〇零\d]+"
    r"(?:匹|人|個|本|枚|歳|才|回|度|つ|ページ)?(?:も)?"
)


def is_numeric_comma_construction(surface: str) -> bool:
    return bool(_NUMERIC_COMMA_CONSTRUCTION.fullmatch(surface))


def is_tari_listing_construction(surface: str) -> bool:
    """Return whether a comma joins the two halves of Vたり、Vたりする."""
    return bool(re.search(
        r"(?:たり|だり)、.+(?:たり|だり)する$", surface,
        flags=re.S,
    ))


def overlay_crosses_clause_boundary(surface: str) -> bool:
    """Allow punctuation only when integral to notation or one construction."""
    if any(mark in surface for mark in "。！？"):
        return True
    if is_tari_listing_construction(surface):
        return False
    return "、" in _NUMERIC_COMMA_SPAN.sub("", surface)


_REDUNDANT_GRAMMAR_OVERLAY_SURFACES = {
    "あとで",
    "後で",
    "である",
    "のか",
    "にとって",
    "のだろう",
    "はずの",
    "というもの",
    "とすると",
    "ことにしました",
    "ことだけ",
    "のは",
    "ような",
    "てから",
    "だった",
    "そうでもない",
    "のようだった",
    "生活である",
    "寝ている猫",
    "もう待てなかった",
    "と言った",
    "と名乗った",
}

_REQUIRED_CONTEXTUAL_OVERLAY_SURFACES = {
    "胸が悪くなった", "胸が悪くなり", "音がして",
    "目から火が出た", "気がつく",
    "日が暮れ", "腹が減り", "いつの間にか", "顔を合わせる", "相手にせず",
    "目を覚ます", "じっとしていた", "足を悪くし", "こりごりだ",
    "目の見えない", "小便がしたくなり",
    "どうすればよいか", "どうすればいいか",
    # Chapter-smoke examples which exercise general directional and
    # benefactive predicate rules.  Keeping these here also prevents a model
    # from fixing the primary boundary while silently dropping the useful
    # internal explanation.
    "歩いて行く", "置いてやれ", "ぐるぐるして",
}

_BENEFACTIVE_HELPER_LEMMAS = {"やる", "あげる", "くれる", "もらう"}
_DIRECTIONAL_HELPER_LEMMAS = {"行く", "来る"}


def japanese_overlay_policy_issue(overlay: dict[str, Any]) -> str | None:
    """Reject mechanically recognizable annotation clutter.

    This deliberately covers only high-confidence shapes.  The agent remains
    responsible for deciding whether less mechanical multi-part constructions
    are pedagogically useful.
    """
    surface = str(overlay.get("surface", ""))
    if surface.startswith("お腹がす") and str(overlay.get("head_lemma", "")) != "お腹がすく":
        return (
            "the hunger card must name the complete learned collocation "
            "お腹がすく as its head form, not only bare すく"
        )
    if surface in _REDUNDANT_GRAMMAR_OVERLAY_SURFACES:
        return "the overlay is redundant or absorbs an ordinary neighbouring word"
    if "という" in surface and surface.endswith("ことを"):
        return "a ということ overlay must leave the following case particle を outside"
    components = overlay.get("components")
    if not isinstance(components, list):
        return None
    component_surfaces = [str(item.get("surface", "")) for item in components]
    head_lemma = str(overlay.get("head_lemma", ""))
    grammar_key = str(overlay.get("grammar_candidate_key", "")).casefold()
    if surface == "ふわりと":
        if component_surfaces != ["ふわり", "と"]:
            return (
                "ふわりと must be taught as one manner expression whose overlay "
                "exposes ふわり + adverbial と, not as two unrelated glosses"
            )
        if components[-1].get("lookup_kind") != "grammar":
            return "adverbial と in ふわりと is grammar-only, not a dictionary word"
    if surface.endswith("だったらしい"):
        if component_surfaces[-2:] != ["だった", "らしい"]:
            return (
                "だったらしい must expose past copula だった plus evidential "
                "らしい so their scope relationship is explicit"
            )
    if surface.endswith("がなく"):
        expected_head = surface[:-1] + "い"
        if str(overlay.get("head_lemma", "")) != expected_head:
            return f"{surface} must use canonical connective head {expected_head}"
        if component_surfaces[-2:] != ["が", "なく"]:
            return (
                "Nがなく must expose が plus connective なく and identify "
                "なく as the く-form of ない"
            )
    for suffix in ("出す", "始める", "続ける", "終わる"):
        if head_lemma.endswith(suffix) and head_lemma != suffix:
            suffix_parts = [
                item for item in components
                if str(item.get("lemma", "")) == suffix
            ]
            semantic_parts = [
                item for item in components
                if str(item.get("lemma", ""))
                and item.get("lookup_kind") in {"lexical", "none"}
            ]
            if (
                not suffix_parts
                or len(semantic_parts) < 2
                or any(
                    part.get("lookup_kind") == "grammar"
                    for part in suffix_parts
                )
            ):
                return (
                    f"productive compound {head_lemma} must expose its first "
                    f"verb plus {suffix} as separate semantic roots; link each "
                    "root only when an exact local dictionary entry exists"
                )
    is_adjective_change_key = grammar_key.startswith((
        "adjective-ku-", "adjective_ku_", "a-ku-", "a_ku_",
    ))
    adjective_change_head = (
        next(
            (
                root for marker, root in (
                    ("ku-naru", "なる"), ("ku_naru", "なる"),
                    ("ku-suru", "する"), ("ku_suru", "する"),
                )
                if marker in grammar_key
            ),
            None,
        )
        if is_adjective_change_key else None
    )
    if adjective_change_head is not None:
        head_parts = [
            item for item in components
            if str(item.get("lemma", "")) == adjective_change_head
        ]
        lexical_parts = [
            item for item in components
            if item.get("lookup_kind") == "lexical"
        ]
        if (
            not head_parts
            or head_parts[0].get("lookup_kind") != "lexical"
            or len(lexical_parts) < 2
            or any(str(item.get("lemma", "")) == head_lemma for item in components)
        ):
            return (
                "Aくなる/Aくする overlay must expose the adjective root plus "
                f"lexical {adjective_change_head}; do not leave the whole "
                "construction as one opaque component"
            )
    conventional_component_splits = {
        ("で", "ある"): "である",
        ("の", "か"): "のか",
        ("の", "は"): "のは",
        ("に", "とって"): "にとって",
        ("の", "だろう"): "のだろう",
        ("はず", "の"): "はずの",
        ("こと", "だけ"): "ことだけ",
        ("こと", "に", "しました"): "ことにしました",
        ("と", "いう", "もの"): "というもの",
    }
    for pieces, learned_unit in conventional_component_splits.items():
        width = len(pieces)
        if any(
            tuple(component_surfaces[index:index + width]) == pieces
            for index in range(len(component_surfaces) - width + 1)
        ):
            return (
                f"overlay components split the conventional grammar unit "
                f"{learned_unit}; keep that learned unit together"
            )
    if surface.endswith("という") and len(components) >= 3:
        return (
            "a reported narrative clause must not become one grammar overlay; "
            "keep the complete という primary target and its contextual gloss"
        )
    if is_numeric_comma_construction(surface):
        if any("、" in part for part in component_surfaces):
            return (
                "numeric-range components must skip the comma; explain the two "
                "range values rather than making punctuation a learned part"
            )
        if component_surfaces and component_surfaces[-1] in {
            "匹", "人", "個", "本", "枚", "歳", "才", "回", "つ", "ページ",
        }:
            return (
                "keep the counter attached to the second range value so the "
                "components are learner-sized quantities, not tokenizer atoms"
            )
    if (
        surface in {
            "人間というものの見始めだったのだろう",
            "毛があるはずの",
        }
        or "見始めだったのだろう" in surface
    ):
        return (
            "the overlay is too clause-like and absorbs a free nominal argument; "
            "use the compact learned unit instead"
        )
    if any(surface in {"まし", "ませ", "だっ", "なかっ", "られ", "させ", "り"}
           for surface in component_surfaces):
        return "an overlay component is an internal conjugation fragment, not a learned chunk"
    if any(
        component_surfaces[index].endswith("ず")
        and component_surfaces[index + 1].startswith("に")
        for index in range(len(component_surfaces) - 1)
    ):
        return "a Vずに overlay must keep the learned negative connective ずに together"
    if (
        len(components) == 2
        and str(components[-1].get("lemma", "")) == "いる"
        and (
            component_surfaces[-1] in {"ている", "ていた", "ています", "ていました"}
            or (
                component_surfaces[-1] in {"いて", "いた"}
                and component_surfaces[0].endswith(("て", "で"))
            )
        )
    ):
        return "a routine Vている aspect form belongs on the complete primary verb card"
    if (
        len(components) == 2
        and str(components[-1].get("lemma", "")) in {"だ", "です", "である"}
        and not str(components[0].get("lemma", "")).endswith(("だ", "です", "である"))
        and not any(marker in component_surfaces[-1] for marker in ("のか", "のだ"))
        and surface not in _REQUIRED_CONTEXTUAL_OVERLAY_SURFACES
        and not surface.startswith(("のである", "のだった", "のです"))
        and not any(
            marker in (
                str(overlay.get("form_label", "")) + " "
                + str(overlay.get("grammar_candidate_key", ""))
            ).casefold()
            for marker in ("predicative", "descriptive", "adjective", "dame-da")
        )
    ):
        return (
            "an ordinary noun plus copula is already explained by the complete "
            "primary form; it is not a distinct reusable grammar overlay"
        )
    if (
        "ka_to_omou" in str(overlay.get("grammar_candidate_key", ""))
        and (
            len(component_surfaces) != 2
            or component_surfaces[0].endswith("か")
            or not component_surfaces[1].startswith("か")
        )
    ):
        return "the かと思う overlay must use the lexical predicate + かと思う-form boundary"
    if (
        len(component_surfaces) == 3
        and component_surfaces[0] == "と"
        and component_surfaces[-1] == "とき"
    ):
        return "a routine quotative predicate + とき chain is not a distinct learned construction"
    if surface.startswith("のよう"):
        return (
            "Nのようだ should expose the compared noun plus the complete のようだ "
            "unit, not an isolated の particle component"
        )
    if surface == "相手にせず" and any(
        component_surfaces[index] == "相手に"
        and str(components[index].get("lemma", "")) == "相手にする"
        for index in range(len(components))
    ):
        return (
            "the partial component 相手に must point to searchable lemma 相手 "
            "and explain the noun-plus-particle role; the whole overlay carries "
            "the idiomatic 相手にする meaning"
        )
    for experience_suffix in ("ことがある", "ことはない"):
        if surface.endswith(experience_suffix) and experience_suffix not in component_surfaces:
            return (
                f"keep learned {experience_suffix} together as one overlay component "
                "after the lexical predicate, not as particle-sized atoms"
            )
    return None


def japanese_segment_issue(surface: str, kind: str) -> str | None:
    """Return a mechanically certain single-segment classification failure."""
    if kind == "name" and surface == "吾輩":
        return "吾輩 is a pronoun, not a name"
    if surface == "ない" and kind == "auxiliary":
        return (
            "standalone ない is not an auxiliary tap target: keep an "
            "inflectional negative with its word, or classify independent "
            "existential/adjectival ない as a lexical word"
        )
    if "たくな" in surface and kind != "grammar":
        return "Vたくなる is a learned multi-part grammar construction"
    if "ずに" in surface and not surface.endswith("ずに") and kind != "grammar":
        return "Vずにいる is a learned multi-part grammar construction"
    return None


def is_conditional_to_segment(segment: dict[str, Any]) -> bool:
    """Identify authored conditional と without guessing from surface alone."""
    if str(segment.get("surface", "")) != "と":
        return False
    evidence = " ".join((
        str(segment.get("part_of_speech", "")),
        str(segment.get("meaning_en", "")),
        str(segment.get("grammar_candidate_key", "")),
    )).casefold()
    return any(marker in evidence for marker in (
        "conditional", "when", "whenever", "upon", "if;", "if ",
        "temporal と", "conditional-to",
    ))


def japanese_learner_segmentation_issues(
    segments: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Reject tokenizer-sized boundaries that are harmful in a tap reader.

    Japanese learners look up complete surface forms such as ありました and
    すいて, with the dictionary form and conjugation explained inside the
    card.  Exposing あり / まし / た as unrelated tap targets is therefore a
    contract failure, not a stylistic segmentation choice.
    """
    issues: list[dict[str, str]] = []
    lexical = {"word", "grammar", "idiom", "auxiliary"}
    for index, current in enumerate(segments):
        if current.get("type") == "punctuation":
            continue
        surface = str(current.get("surface", ""))
        previous = segments[index - 1] if index else None
        previous_type = str(previous.get("type", "")) if previous else ""
        previous_form = str(previous.get("conjugation_form", "")) if previous else ""
        previous_surface = str(previous.get("surface", "")) if previous else ""

        if is_conditional_to_segment(current) and (
            current.get("type") != "grammar"
            or not str(current.get("grammar_candidate_key", "")).strip()
        ):
            issues.append({
                "surface": surface,
                "message": (
                    "conditional と is a learned grammar point in this reader; "
                    "type it as grammar and assign a provisional conditional-to key"
                ),
            })

        if (
            surface == "吾輩"
            and "pronoun" not in str(current.get("part_of_speech", "")).casefold()
        ):
            issues.append({
                "surface": surface,
                "message": "吾輩 is a literary first-person pronoun, not a proper noun",
            })

        if surface in {"どうすればよいか", "どうすればいいか"}:
            issues.append({
                "surface": surface,
                "message": (
                    "this full question contains several useful lexical heads; "
                    "keep どう + すれば + よい/いい + か as primary taps and "
                    "preserve the combined question in an overlay"
                ),
            })

        if (
            surface.startswith("四十")
            and not str(current.get("surface_kana", "")).startswith(
                ("よんじゅう", "よんじゅっ", "よんじっ")
            )
        ):
            issues.append({
                "surface": surface,
                "message": (
                    "四十 in a learner reading begins よんじゅう/よんじゅっ, "
                    "not しじゅう"
                ),
            })

        for grammar_suffix in ("ことにしました", "ことがある", "ことはない"):
            if surface.endswith(grammar_suffix) and surface != grammar_suffix:
                issues.append({
                    "surface": surface,
                    "message": (
                        "leave the lexical predicate separately accessible before "
                        f"the learned {grammar_suffix} grammar unit"
                    ),
                })
                break

        if (
            current.get("type") == "grammar"
            and str(current.get("part_of_speech", "")).casefold().strip()
            in {"compound verb", "lexical compound verb"}
        ):
            issues.append({
                "surface": surface,
                "message": (
                    "a lexical compound verb is a complete word tap target, not a "
                    "grammar card; keep any reusable internal construction in an overlay"
                ),
            })

        lemma = str(current.get("lemma", ""))
        part_of_speech = str(current.get("part_of_speech", "")).casefold()
        lexical_form_pos = (
            "verb" in part_of_speech or "adjective" in part_of_speech
        ) and not any(marker in part_of_speech for marker in (
            "grammar", "construction", "copula", "auxiliary",
        ))
        if (
            current.get("type") == "grammar"
            and lexical_form_pos
            and candidate_for_lemma(
                lemma, str(current.get("lemma_kana", "")),
            ) is not None
        ):
            issues.append({
                "surface": surface,
                "message": (
                    "an inflected lexical verb or adjective with a verified "
                    "dictionary lemma is a word tap target, not a grammar-only "
                    "segment; type it as word and link its lexical lemma"
                ),
            })
        if (
            lemma.endswith(("くなる", "くする"))
            and current.get("type") not in {"grammar", "idiom"}
        ):
            issues.append({
                "surface": surface,
                "message": (
                    "a complete Aくなる/Aくする form is one learned construction "
                    "primary; type it as grammar (or idiom only for a genuine fixed "
                    "collocation) and expose the lexical roots in its overlay"
                ),
            })
        if (
            lemma.endswith(("くなる", "くする"))
            and current.get("type") == "idiom"
            and not any(particle in surface for particle in "がをに")
        ):
            issues.append({
                "surface": surface,
                "message": (
                    "a bare Aくなる/Aくする form is a reusable grammar "
                    "construction, not an idiom; type it as grammar and keep "
                    "the larger fixed collocation in its separate whole overlay"
                ),
            })

        is_v_nagara = surface.endswith("ながら") and surface != "ながら"
        if is_v_nagara and (
            current.get("type") != "grammar"
            or not str(current.get("grammar_candidate_key", "")).strip()
        ):
            issues.append({
                "surface": surface,
                "message": (
                    "Vながら is a learned simultaneous-action grammar form; "
                    "keep the complete V-stem+ながら surface as one grammar "
                    "primary with a provisional nagara key"
                ),
            })
        if is_v_nagara and lemma == "ながら":
            issues.append({
                "surface": surface,
                "message": (
                    "a complete Vながら primary retains the content verb's "
                    "dictionary lemma (for example 見ながら -> 見る); ながら "
                    "is the grammar contribution exposed in the roots overlay"
                ),
            })

        contextual_primary_parts = {
            "目の見えない": "目 + の + 見えない",
            "小便がしたくなり": "小便 + が + したくなり",
            "胸が悪くなり": "胸 + が + 悪くなり",
        }
        if surface in contextual_primary_parts:
            expected = contextual_primary_parts[surface]
            issues.append({
                "surface": surface,
                "message": (
                    "this contextual phrase belongs in a whole overlay, while "
                    f"its learner primary rows stay {expected}"
                ),
            })

        if surface in _OBVIOUS_INFLECTION_FRAGMENTS:
            issues.append({
                "surface": surface,
                "message": (
                    f"{surface} is an internal inflection fragment; keep it with "
                    "the complete learner-facing verb or adjective form"
                ),
            })
        if (
            surface.endswith("のか")
            and surface != "のか"
            and str(current.get("lemma", "")) == "のか"
        ):
            issues.append({
                "surface": surface,
                "message": (
                    "keep the lexical predicate separately accessible before the "
                    "learned のか grammar unit (for example 生まれた + のか)"
                ),
            })
        if surface.endswith("というもの") and surface != "というもの":
            issues.append({
                "surface": surface,
                "message": (
                    "keep the lexical head separately accessible before the learned "
                    "というもの grammar unit"
                ),
            })
        if surface.endswith("はずの") and surface != "はずの":
            issues.append({
                "surface": surface,
                "message": (
                    "keep the lexical predicate separately accessible before "
                    "the learned はずの expectation unit (for example ある + はずの)"
                ),
            })
        if surface.endswith("とすると") and surface != "とすると":
            issues.append({
                "surface": surface,
                "message": (
                    "keep the lexical volitional action separately accessible "
                    "before the learned とすると grammar tail (for example "
                    "行こう + とすると)"
                ),
            })
        if (
            surface.endswith("だった")
            and str(current.get("lemma", "")).endswith("る")
        ):
            issues.append({
                "surface": surface,
                "message": (
                    "a surface ending in copular だった is not the past form of "
                    "a verb lemma ending in る; expose the nominal/adjectival head "
                    "and analyze the complete copula accurately"
                ),
            })
        if (
            surface.startswith("と")
            and len(surface) > 1
            and str(current.get("lemma", "")) in {
                "思う", "言う", "聞く", "考える", "決める", "名乗る",
            }
            and current.get("type") != "grammar"
        ):
            issues.append({
                "surface": surface,
                "message": (
                    "the quotative と is an independently useful primary particle; "
                    "split it from the reporting or thought verb (for example "
                    "と + 思った), while a useful larger overlay may still span both"
                ),
            })
        if (
            "そう" in surface
            and not surface.startswith("そう")
            and str(current.get("lemma", "")) == "そうだ"
        ):
            issues.append({
                "surface": surface,
                "message": (
                    "an appearance form must retain its content adjective as the "
                    "primary lemma; そうだ belongs in the form explanation"
                ),
            })
        if (
            surface.endswith(("てくる", "でくる"))
            and str(current.get("lemma", "")) == surface
            and (
                "construction" in str(current.get("part_of_speech", "")).casefold()
                or "て-form" in str(current.get("conjugation_form", "")).casefold()
                or "direction" in str(current.get("conjugation_form", "")).casefold()
            )
        ):
            issues.append({
                "surface": surface,
                "message": (
                    "a directional Vてくる primary keeps the content verb's "
                    "dictionary lemma; explain 来る in its form metadata and overlay"
                ),
            })
        if (
            surface == "ある"
            and "certain" in str(current.get("meaning_en", "")).casefold()
        ):
            if (
                "verb" in str(current.get("part_of_speech", "")).casefold()
                or current.get("lemma") != "或"
            ):
                issues.append({
                    "surface": surface,
                    "message": (
                        "ある meaning 'a certain' is the non-inflecting prenominal "
                        "determiner with this local dictionary's canonical lemma/key "
                        "或 (reading ある), not existential ある and not invented 或る"
                    ),
                })
        if (
            "て-form" in str(current.get("conjugation_form", ""))
            and "て" not in surface
            and "で" not in surface
        ):
            issues.append({
                "surface": surface,
                "message": (
                    "the conjugation label says て-form but the complete surface "
                    "contains no て/で; label the actual contextual form"
                ),
            })
        if (
            surface in _CONNECTIVE_ENDINGS
            and previous_type in lexical
            and any(marker in previous_form.casefold() for marker in (
                "stem", "continuative", "連用", "音便", "irrealis", "未然",
            ))
        ):
            issues.append({
                "surface": previous_surface + surface,
                "message": (
                    "the Japanese て/で-form must be one learner-facing segment, "
                    "not a stem plus a detached connector"
                ),
            })
        if (
            surface in _FINAL_ENDINGS
            and previous_type in lexical
            and previous_surface not in {"です", "ます"}
            and any(marker in previous_form.casefold() for marker in (
                "stem", "continuative", "連用", "irrealis", "未然", "base",
            ))
        ):
            issues.append({
                "surface": previous_surface + surface,
                "message": (
                    "past/negative endings must stay with the complete inflected "
                    "surface form presented to the learner"
                ),
            })

    surfaces = [str(item.get("surface", "")) for item in segments]
    learned_adjacent_units = {
        ("で", "ある"): (
            "である",
            "である is the learned formal-copula unit; keep it as one grammar segment",
        ),
        ("後", "で"): (
            "後で",
            "temporal 後で is a conventional adverbial unit meaning later/afterward",
        ),
        ("の", "か"): (
            "のか",
            "embedded/explanatory のか is a learned grammar unit, not two atomic particles",
        ),
        ("この", "頃"): (
            "この頃",
            "この頃 is a conventional lexical time expression meaning these days/recently",
        ),
        ("その", "後"): (
            "その後",
            "その後 is a conventional lexical time expression meaning after that",
        ),
        ("今", "でも"): (
            "今でも",
            "今でも is the conventional adverbial expression meaning even now/still",
        ),
        ("何度", "も"): (
            "何度も",
            "何度も is the conventional positive adverbial expression meaning many times",
        ),
        ("何回", "も"): (
            "何回も",
            "何回も is the conventional positive adverbial expression meaning many times",
        ),
        ("一度", "も"): (
            "一度も",
            "一度も is the conventional negative-polarity adverbial expression meaning not even once",
        ),
        ("何", "も"): (
            "何も",
            "何も is a conventional indefinite expression; before a negative predicate it means nothing",
        ),
        ("誰", "も"): (
            "誰も",
            "誰も is a conventional indefinite expression; before a negative predicate it means no one",
        ),
        ("と", "いい"): (
            "といい",
            "naming といい is a conventional quotative grammar unit, not two atomic rows",
        ),
        ("という", "こと"): (
            "ということ",
            "ということ is a conventional quotative nominalization unit",
        ),
        ("だ", "ということ"): (
            "だということ",
            "copular だということ is one conventional quotative nominalization unit",
        ),
        ("はず", "の"): (
            "はずの",
            "attributive はずの is one conventional expectation grammar unit",
        ),
        ("の", "だろう"): (
            "のだろう",
            "explanatory-conjectural のだろう is one conventional learned grammar unit",
        ),
    }
    for index in range(len(surfaces) - 2):
        first, second, third = segments[index:index + 3]
        if (
            surfaces[index] == "の"
            and surfaces[index + 1] == "よう"
            and str(third.get("lemma", "")) == "だ"
        ):
            issues.append({
                "surface": "".join(surfaces[index:index + 3]),
                "message": (
                    "comparative のようだ is a conventional grammar unit; keep "
                    "のようだった together rather than の + よう + だった"
                ),
            })
    for index in range(len(surfaces) - 1):
        current = segments[index]
        following = segments[index + 1]
        current_surface = surfaces[index]
        following_surface = surfaces[index + 1]
        current_type = str(current.get("type", ""))
        current_pos = str(current.get("part_of_speech", "")).casefold()
        adjective_like = "adjective" in current_pos or "adverb" in current_pos
        is_sou_demonai_prefix = surfaces[index:index + 4] == [
            "そう", "で", "も", "ない",
        ]
        current_form = str(current.get("conjugation_form", "")).casefold()
        following_lemma = str(following.get("lemma", ""))
        following_type = str(following.get("type", ""))
        following_form = str(following.get("conjugation_form", "")).casefold()
        following_pos = str(following.get("part_of_speech", "")).casefold()
        pair = (surfaces[index], surfaces[index + 1])
        learned = learned_adjacent_units.get(pair)
        if learned:
            surface, message = learned
            issues.append({"surface": surface, "message": message})
        if (
            re.fullmatch(r"[一二三四五六七八九十百千〇零\d]+", current_surface)
            and following_surface in {
                "匹", "人", "個", "本", "枚", "歳", "才", "回", "つ", "ページ",
            }
        ):
            issues.append({
                "surface": current_surface + following_surface,
                "message": (
                    "keep a number and its counter or measure as one learner-facing "
                    "quantity, not separate tokenizer-sized taps"
                ),
            })
        if (
            following_surface == "と"
            and "adverbial" in following_pos
            and ("adverb" in current_pos or "mimetic" in current_pos)
        ):
            issues.append({
                "surface": current_surface + following_surface,
                "message": (
                    "an adverb or mimetic followed by its adverbial と is one "
                    "learner-facing manner expression; keep it together and "
                    "explain the contribution of と inside its overlay"
                ),
            })
        if (
            current_surface == "の"
            and str(following.get("lemma", "")) == "ようだ"
        ):
            issues.append({
                "surface": current_surface + following_surface,
                "message": (
                    "comparative のようだ is a conventional grammar unit; keep "
                    "its complete inflected surface together"
                ),
            })
        if (
            current_surface.endswith("く")
            and "adjective" in current_pos
            and following_lemma in {"なる", "する"}
            and following_type in lexical
        ):
            issues.append({
                "surface": current_surface + following_surface,
                "message": (
                    "keep the complete Aくなる/Aくする construction as one "
                    "learner-facing grammar primary; its overlay exposes the "
                    "adjective and なる/する roots with verified lexical links"
                ),
            })
        if surfaces[index].endswith("たり") and surfaces[index + 1] == "する":
            issues.append({
                "surface": surfaces[index] + surfaces[index + 1],
                "message": (
                    "the learned たりする listing construction must stay together as "
                    "one complete learner-facing form"
                ),
            })
        if (
            current_surface.endswith("ず")
            and following_surface == "に"
        ):
            issues.append({
                "surface": current_surface + following_surface,
                "message": (
                    "Vずに is one complete negative connective form; keep に with "
                    "the inflected verb rather than as an unrelated particle row"
                ),
            })
        if (
            current_surface.endswith(("て", "で"))
            and following_lemma == "いる"
            and following_type in {"auxiliary", "grammar"}
        ):
            issues.append({
                "surface": current_surface + following_surface,
                "message": (
                    "a routine Vている/Vでいる inflected form is one learner-facing "
                    "verb form; keep it whole with the lexical verb as lemma"
                ),
            })
        repeated_mimetic = bool(re.fullmatch(r"(.{1,3})\1", current_surface))
        if (
            (repeated_mimetic or "mimetic" in current_pos)
            and current_type in lexical
            and following_lemma == "する"
            and following_type in lexical
        ):
            issues.append({
                "surface": current_surface + following_surface,
                "message": (
                    "a conventional mimetic + する verb is one learner-facing "
                    "inflected predicate; keep its full surface together and "
                    "expose the mimetic base plus する-form in an overlay"
                ),
            })
        if (
            current_surface.endswith(("て", "で"))
            and current_type in lexical
            and following_lemma == "みる"
            and following_type in {"grammar", "auxiliary"}
            and any(marker in " ".join((
                following_form,
                str(following.get("part_of_speech", "")).casefold(),
                str(following.get("grammar_candidate_key", "")).casefold(),
            )) for marker in ("try", "auxiliary", "te-miru", "てみる"))
        ):
            issues.append({
                "surface": current_surface + following_surface,
                "message": (
                    "productive Vてみる is one learner-facing grammar primary, "
                    "not lexical Vて plus an atomic みる row; keep the complete "
                    "surface together and expose the content verb + auxiliary "
                    "みる in its overlay"
                ),
            })
        if (
            current_surface.endswith(("て", "で"))
            and current_type in lexical
            and following_lemma in _BENEFACTIVE_HELPER_LEMMAS
            and following_type in lexical
        ):
            issues.append({
                "surface": current_surface + following_surface,
                "message": (
                    "a Vて benefactive chain is one learner-facing predicate; "
                    "keep the content verb through やる/あげる/くれる/もらう "
                    "together, then expose the two meaningful parts in its overlay"
                ),
            })
        if (
            following_surface == "ながら"
            and following_type in {"grammar", "auxiliary", "particle"}
            and current_type in lexical
            and any(marker in current_form for marker in (
                "stem", "continuative", "連用", "ren'yōkei", "renyokei",
            ))
        ):
            issues.append({
                "surface": current_surface + following_surface,
                "message": (
                    "Vながら is one learned simultaneous-action grammar primary; "
                    "merge the content verb stem with ながら and retain the "
                    "content verb as lemma"
                ),
            })
        directional_evidence = (
            following_type in {"auxiliary", "grammar"}
            or "auxiliary" in following_pos
            or any(marker in following_form for marker in (
                "direction", "deictic", "aspect", "continuation",
            ))
            # This chapter's simple movement example is deliberately explicit:
            # 行く contributes direction to the manner verb rather than naming
            # a second independent event.
            or (current_surface, following_surface) == ("歩いて", "行く")
        )
        if (
            current_surface.endswith(("て", "で"))
            and current_type in lexical
            and following_lemma in _DIRECTIONAL_HELPER_LEMMAS
            and following_type in lexical
            and directional_evidence
        ):
            issues.append({
                "surface": current_surface + following_surface,
                "message": (
                    "directional/aspectual Vて行く/Vて来る is one learner-facing "
                    "predicate with the content verb as lemma; keep true sequential "
                    "actions separate, but expose the helper contribution in an overlay"
                ),
            })
        if (
            "な-adjective" in current_pos
            and following_surface == "な"
            and following_type in {"particle", "grammar", "auxiliary"}
        ):
            issues.append({
                "surface": current_surface + following_surface,
                "message": (
                    "an attributive な-adjective keeps な on the same learner-facing "
                    "tap target and retains the adjective as its lemma"
                ),
            })
        if (
            adjective_like
            and not is_sou_demonai_prefix
            and following_lemma in {"だ", "です"}
            and following_surface in {"だ", "だった", "で", "です", "でした"}
            and following_type in {"grammar", "auxiliary", "word", "particle"}
        ):
            issues.append({
                "surface": current_surface + following_surface,
                "message": (
                    "a predicative adjective-like expression and its copula form "
                    "one complete learner-facing surface, with the descriptive "
                    "base as lemma"
                ),
            })
        if (
            following_lemma in {"始める", "出す", "続ける", "終わる"}
            and following_type in lexical
            and any(marker in current_form for marker in (
                "stem", "continuative", "連用", "ren'yōkei", "renyokei",
            ))
        ):
            issues.append({
                "surface": current_surface + following_surface,
                "message": (
                    "a V-stem aspectual compound is one learner-facing verb form; "
                    "keep the lexical action and aspectual verb together"
                ),
            })
    for index in range(len(surfaces) - 2):
        if surfaces[index:index + 3] == ["今", "で", "も"]:
            issues.append({
                "surface": "今でも",
                "message": (
                    "今でも is a conventional learner-sized adverb meaning "
                    "even now/still; do not split it into atomic particles"
                ),
            })
        if surfaces[index:index + 3] == ["こと", "に", "し"]:
            issues.append({
                "surface": "ことにし",
                "message": (
                    "ことにする is a learned grammar construction; annotate its "
                    "complete inflected surface as one grammar segment"
                ),
            })
        if surfaces[index:index + 3] == ["こと", "が", "ある"]:
            issues.append({
                "surface": "ことがある",
                "message": (
                    "experience ことがある is one conventional learned grammar "
                    "unit, not three atomic primary rows"
                ),
            })
        if surfaces[index:index + 3] == ["こと", "は", "ない"]:
            issues.append({
                "surface": "ことはない",
                "message": (
                    "negative-experience ことはない is one conventional learned "
                    "grammar unit, not three atomic primary rows"
                ),
            })
        if surfaces[index:index + 3] == ["だ", "という", "こと"]:
            issues.append({
                "surface": "だということ",
                "message": (
                    "copular だということ is one conventional quotative "
                    "nominalization unit, not three atomic rows"
                ),
            })
        if (
            surfaces[index:index + 2] == ["か", "と"]
            and str(segments[index + 2].get("lemma", "")) == "思う"
        ):
            issues.append({
                "surface": "".join(surfaces[index:index + 3]),
                "message": (
                    "かと思う is a conventional learned grammar unit; keep its "
                    "complete inflected surface together rather than か + と + 思う"
                ),
            })
        if (
            surfaces[index] in {"足", "目", "体", "胃"}
            and surfaces[index + 1] == "を"
            and str(segments[index + 2].get("lemma", "")) == "悪くする"
        ):
            issues.append({
                "surface": "".join(surfaces[index:index + 3]),
                "message": (
                    "body-part + を悪くする is a contextual injury/health "
                    "collocation; keep the whole inflected phrase as an idiom"
                ),
            })
    for index in range(len(surfaces) - 3):
        if surfaces[index:index + 4] == ["そう", "で", "も", "ない"]:
            issues.append({
                "surface": "そうでもない",
                "message": (
                    "そうでもない is a conventional learned expression meaning "
                    "'not really so'; do not expose four unrelated atomic rows"
                ),
            })
    for index, pronoun_mo in enumerate(surfaces):
        if pronoun_mo not in {"何も", "誰も"}:
            continue
        negative = False
        for following in segments[index + 1:index + 9]:
            following_surface = str(following.get("surface", ""))
            if following.get("type") == "punctuation":
                if following_surface in "、。！？":
                    break
                continue
            following_form = str(following.get("conjugation_form", "")).casefold()
            if (
                "negative" in following_form
                or following_surface.endswith(("ない", "なく", "なかった", "ません", "ませんでした"))
            ):
                negative = True
                break
        if negative:
            expected = "nothing" if pronoun_mo == "何も" else "no one"
            meaning = str(segments[index].get("meaning_en", "")).casefold()
            if expected not in meaning:
                issues.append({
                    "surface": pronoun_mo,
                    "message": (
                        f"negative-polarity {pronoun_mo} must explicitly explain "
                        f"its contextual meaning '{expected}' on the whole tap target"
                    ),
                })
    for index, surface in enumerate(surfaces):
        if surface != "もう":
            continue
        negative = False
        for following in segments[index + 1:index + 5]:
            following_surface = str(following.get("surface", ""))
            if following.get("type") == "punctuation":
                if following_surface in "、。！？":
                    break
                continue
            following_form = str(
                following.get("conjugation_form", "")
            ).casefold()
            if (
                "negative" in following_form
                or following_surface.endswith((
                    "ない", "なく", "なかった", "ません", "ませんでした",
                ))
            ):
                negative = True
                break
        if negative:
            meaning = str(segments[index].get("meaning_en", "")).casefold()
            negative_senses = (
                "any longer", "any more", "anymore", "no more", "no longer",
            )
            if not any(sense in meaning for sense in negative_senses):
                issues.append({
                    "surface": "もう",
                    "message": (
                        "もう before this negative predicate means 'any longer' "
                        "or 'no more', not merely 'already/by then'; put that "
                        "contextual sense on the tappable もう row"
                    ),
                })
    return issues


def japanese_form_step_issues(segment: dict[str, Any]) -> list[str]:
    """Validate the mechanical shape of a learner-facing derivation chain.

    Independent reviewers decide whether an intermediate linguistic operation
    is missing.  This gate guarantees that a supplied chain is ordered,
    readable, and actually reaches the immutable surface instead of stopping
    at a stem or paraphrase.
    """
    surface = str(segment.get("surface", ""))
    kind = str(segment.get("type", ""))
    if "form_steps" not in segment:
        # Backward-compatible read path for already-published fixtures. New
        # agent output is schema-required to include the field, and the v8
        # cache policy prevents old accepted chunks from being reused.
        return []
    steps = segment.get("form_steps")
    if not isinstance(steps, list):
        return ["form_steps must be an array"]
    if kind == "punctuation":
        return [] if not steps else ["punctuation cannot have form steps"]
    lemma = str(segment.get("lemma", ""))
    lemma_kana = str(segment.get("lemma_kana", ""))
    surface_kana = str(segment.get("surface_kana", ""))
    form_label = str(segment.get("conjugation_form", "")).casefold()
    # A kana occurrence may point at a kanji dictionary headword (とる ->
    # 捕る). Equal readings mean the morphology itself is unchanged; spelling
    # normalization belongs to the dictionary row, not the derivation chain.
    changed = surface_kana != lemma_kana
    functional_grammar_extension = (
        kind == "grammar"
        and "connector" in form_label
        and surface_kana.startswith(lemma_kana)
    )
    inflecting = (
        changed
        and form_label not in {"", "non-inflecting"}
        and not functional_grammar_extension
    )
    if inflecting and kind in {"word", "idiom", "grammar"} and not steps:
        return ["an inflected learner target needs a derivation ending at its surface"]
    if not inflecting and steps:
        return ["a non-inflecting or unchanged target must use an empty form chain"]
    issues: list[str] = []
    # A desire-change construction has a meaningful derivation before its
    # dictionary-form construction itself (する -> したい -> したくなる).  Let
    # that construction lemma appear once in the visible chain; ordinary
    # lexical inflections still must not repeat their already-visible lemma.
    allow_lemma_as_intermediate = (
        kind == "grammar" and lemma_kana.endswith("たくなる")
    )
    seen: set[str] = set() if allow_lemma_as_intermediate else {lemma}
    kana = re.compile(r"^[\u3040-\u30ffー・\s]+$")
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or set(step) != {
            "form", "reading", "label", "meaning_en",
        }:
            issues.append(f"form step {index} violates the required schema")
            continue
        if any(not isinstance(step[field], str) or not step[field].strip()
               for field in ("form", "reading", "label", "meaning_en")):
            issues.append(f"form step {index} has an empty field")
            continue
        if not kana.fullmatch(step["reading"]):
            issues.append(f"form step {index} reading is not kana-only")
        if step["form"] in seen:
            issues.append(f"form step {index} repeats an earlier form")
        seen.add(step["form"])
    if steps and (
        steps[-1].get("form") != surface
        or steps[-1].get("reading") != surface_kana
    ):
        issues.append("the final form step must exactly equal surface and surface_kana")

    step_readings = {
        str(step.get("reading", ""))
        for step in steps
        if isinstance(step, dict)
    }

    def require_intermediate(reading: str, description: str) -> None:
        if reading and reading != surface_kana and reading not in step_readings:
            issues.append(
                f"the derivation skips {description} intermediate form {reading}"
            )

    # These are high-confidence learner paths, not a morphological parser.
    # They prevent the exact opaque jumps that are most confusing in the UI.
    if surface_kana.endswith("ませんでした"):
        polite_stem = surface_kana[:-len("ませんでした")]
        require_intermediate(
            polite_stem + "ます", "polite nonpast",
        )
        require_intermediate(
            polite_stem + "ません", "polite negative nonpast",
        )
    elif surface_kana.endswith("ました"):
        require_intermediate(
            surface_kana[:-3] + "ます", "polite nonpast",
        )
    if surface_kana.endswith("ません"):
        require_intermediate(
            surface_kana[:-3] + "ます", "polite nonpast",
        )
    if (
        surface_kana.endswith("でした")
        and not surface_kana.endswith("ませんでした")
        and lemma_kana != "です"
    ):
        require_intermediate("です", "polite nonpast copula")
    if (
        surface_kana.endswith(("ても", "でも"))
        and any(marker in form_label for marker in ("ても", "concessive"))
    ):
        require_intermediate(
            surface_kana[:-1], "connective て/で-form before も",
        )
    if surface_kana.endswith("なかった"):
        require_intermediate(
            surface_kana[:-3] + "い", "plain negative",
        )
    if kind == "grammar" and lemma_kana.endswith("たくなる"):
        concrete_construction = (
            surface_kana[:-2] + "る"
            if surface_kana.endswith("たくなった")
            else surface_kana
            if surface_kana.endswith("たくなる")
            else lemma_kana
        )
        desiderative = concrete_construction[:-4] + "たい"
        require_intermediate(desiderative, "desiderative")
        require_intermediate(
            concrete_construction, "plain desire-change construction",
        )
    return issues


def clear_unavailable_dictionary_links(annotation: dict[str, Any]) -> dict[str, Any]:
    """Reconcile authored links with authoritative local dictionary evidence.

    Agents choose contextual meanings and semantic homophones. Deterministic
    local evidence remains authoritative about whether a candidate exists. A
    unique safe candidate can be filled from the agent's contextual explanation;
    ambiguous homophones remain an agent choice. This prevents a hallucination
    such as 教師 -> 教師 from surviving while avoiding needless reruns for an
    omitted unambiguous key such as 落ちる.
    """
    def reconcile(item: dict[str, Any], *, functional: bool, sense_field: str) -> None:
        lemma = str(item.get("lemma", ""))
        lemma_kana = str(item.get("lemma_kana", ""))
        candidate = candidate_for_lemma(lemma, lemma_kana)
        if functional or candidate is None:
            item["dictionary_key"] = ""
            item["dictionary_definition_en"] = ""
            return
        issue = dictionary_link_issue(
            lemma=lemma,
            lemma_kana=lemma_kana,
            key=str(item.get("dictionary_key", "")),
            definition=str(item.get("dictionary_definition_en", "")),
            functional=False,
            require_available=True,
        )
        if issue is None:
            return
        unique_key = candidate.get("dictionary_key")
        if isinstance(unique_key, str) and unique_key:
            item["dictionary_key"] = unique_key
            item["dictionary_definition_en"] = str(item.get(sense_field, "")).strip()
        else:
            item["dictionary_key"] = ""
            item["dictionary_definition_en"] = ""

    for segment in annotation.get("segments", []):
        if not isinstance(segment, dict):
            continue
        functional = segment.get("type") in {
            "grammar", "auxiliary", "particle", "punctuation", "name",
        }
        reconcile(segment, functional=functional, sense_field="meaning_en")
    for overlay in annotation.get("grammar_overlays", []):
        if not isinstance(overlay, dict):
            continue
        for component in overlay.get("components", []):
            if not isinstance(component, dict):
                continue
            lexical = component.get("lookup_kind") == "lexical"
            # Productive サ変 compounds are commonly indexed under their
            # nominal head (写生), while the surface component necessarily
            # includes し from 写生する. Link the visible component to that
            # exact local head when it exists; never invent a 写生する entry.
            lemma = str(component.get("lemma", ""))
            lemma_kana = str(component.get("lemma_kana", ""))
            if (
                lexical
                and str(component.get("surface", "")).endswith("し")
                and lemma.endswith("する")
                and lemma_kana.endswith("する")
            ):
                nominal_lemma = lemma[:-2]
                nominal_kana = lemma_kana[:-2]
                if candidate_for_lemma(nominal_lemma, nominal_kana) is not None:
                    component["lemma"] = nominal_lemma
                    component["lemma_kana"] = nominal_kana
            reconcile(
                component,
                functional=not lexical,
                sense_field="function_en",
            )
            # lookup_kind describes an actionable lookup in the reader, not
            # merely the linguistic category of the component.  A lexical
            # root absent from the bundled dictionary (notably hungry すく)
            # must remain useful explanatory text without advertising a link
            # that cannot open.
            if lexical and not component.get("dictionary_key"):
                component["lookup_kind"] = "none"
    return annotation


def normalize_redundant_japanese_form_steps(
    annotation: dict[str, Any],
) -> dict[str, Any]:
    """Remove mechanically redundant chains before model review.

    The dictionary lemma already has its own row. Repeating an unchanged plain
    form as an arrow step adds no information, and functional auxiliaries and
    particles carry their form in the form label. Inflected construction-sized
    grammar primaries are different: their learner-facing chains must survive.
    """
    for segment in annotation.get("segments", []):
        if not isinstance(segment, dict):
            continue
        if segment.get("type") in {"auxiliary", "particle"} or (
            segment.get("surface_kana") == segment.get("lemma_kana")
        ):
            # A kana surface can legitimately point at a kanji dictionary
            # headword (for example とる -> 捕る).  That is an orthographic
            # lookup difference, not a conjugation stage.  Equal readings
            # therefore make a repeated surface arrow just as redundant as
            # byte-for-byte equal surface and lemma spellings.
            segment["form_steps"] = []
        elif (
            segment.get("type") == "grammar"
            and str(segment.get("lemma", "")).endswith(("くなる", "くする"))
        ):
            lemma = str(segment.get("lemma", ""))
            segment["form_steps"] = [
                step for step in segment.get("form_steps", [])
                if not (
                    isinstance(step, dict)
                    and str(step.get("form", "")).endswith("く")
                    and lemma.startswith(str(step.get("form", "")))
                )
            ]
        if not str(segment.get("lemma", "")).endswith("たくなる"):
            lemma = str(segment.get("lemma", ""))
            lemma_kana = str(segment.get("lemma_kana", ""))
            segment["form_steps"] = [
                step for step in segment.get("form_steps", [])
                if not (
                    isinstance(step, dict)
                    and (
                        str(step.get("form", "")) == lemma
                        or (
                            lemma_kana
                            and str(step.get("reading", "")) == lemma_kana
                        )
                    )
                )
            ]
    return annotation


def japanese_required_overlay_issues(
    segments: list[dict[str, Any]], overlays: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Require only constructions whose omission predictably misleads learners."""
    present = {str(overlay.get("surface", "")) for overlay in overlays}
    issues: list[dict[str, str]] = []
    for shorter in overlays:
        shorter_surface = str(shorter.get("surface", ""))
        shorter_start = shorter.get("start")
        if not is_numeric_comma_construction(shorter_surface):
            continue
        for longer in overlays:
            longer_surface = str(longer.get("surface", ""))
            if (
                longer is not shorter
                and longer.get("start") == shorter_start
                and len(longer_surface) > len(shorter_surface)
                and longer_surface.startswith(shorter_surface)
                and is_numeric_comma_construction(longer_surface)
            ):
                issues.append({
                    "surface": shorter_surface,
                    "message": (
                        "remove the nested shorter numeric-range overlay; the full "
                        "counter phrase already gives the learner the useful meaning"
                    ),
                })
                break
    surfaces = [str(segment.get("surface", "")) for segment in segments]
    reconstructed = "".join(surfaces)
    segment_starts: list[int] = []
    offset = 0
    for surface in surfaces:
        segment_starts.append(offset)
        offset += len(surface)
    for index in range(1, len(segments)):
        grammar_surface = surfaces[index]
        if not grammar_surface.startswith("のよう"):
            continue
        previous_surface = surfaces[index - 1]
        if segments[index - 1].get("type") == "punctuation":
            continue
        whole_surface = previous_surface + grammar_surface
        if whole_surface not in present:
            issues.append({
                "surface": whole_surface,
                "message": (
                    "Nのようだ needs a whole overlay with components N + のようだ, "
                    "while the noun and grammar unit remain separate primary taps"
                ),
            })
    for index, segment in enumerate(segments):
        surface = surfaces[index]
        pos = str(segment.get("part_of_speech", "")).casefold()
        manner_evidence = " ".join((
            pos,
            str(segment.get("grammar_candidate_key", "")).casefold(),
            str(segment.get("conjugation_form", "")).casefold(),
        ))
        if (
            len(surface) > 1
            and surface.endswith("と")
            and (
                surface == "ふわりと"
                or "adverbial と" in manner_evidence
                or "mimetic-adverbial-to" in manner_evidence
            )
            and surface not in present
        ):
            issues.append({
                "surface": surface,
                "message": (
                    "a whole adverb/mimetic + adverbial と target needs an "
                    "overlay exposing the manner word and grammar-only と"
                ),
            })
    for index in range(len(segments) - 2):
        noun, particle, negative = segments[index:index + 3]
        negative_form = " ".join((
            str(negative.get("conjugation_form", "")),
            str(negative.get("part_of_speech", "")),
        )).casefold()
        if (
            surfaces[index + 1] == "が"
            and surfaces[index + 2] == "なく"
            and str(negative.get("lemma", "")) == "ない"
            and any(marker in negative_form for marker in (
                "connective", "conjunctive", "ren'yōkei", "renyokei", "連用",
            ))
        ):
            whole_surface = "".join(surfaces[index:index + 3])
            if whole_surface not in present:
                issues.append({
                    "surface": whole_surface,
                    "message": (
                        "Nがなく needs a whole overlay: explain that なく is "
                        "the connective く-form of ない, changing Nがない into "
                        "a clause that links to what follows"
                    ),
                })
    for index in range(len(segments) - 1):
        past_copula, evidential = segments[index:index + 2]
        if (
            surfaces[index].endswith("だった")
            and str(past_copula.get("lemma", "")) == "だ"
            and surfaces[index + 1] == "らしい"
            and str(evidential.get("lemma", "")) == "らしい"
        ):
            whole_surface = surfaces[index] + surfaces[index + 1]
            if whole_surface not in present:
                issues.append({
                    "surface": whole_surface,
                    "message": (
                        "だったらしい needs a whole overlay with だった + "
                        "らしい: らしい is not a conjugation of だった, but it "
                        "marks the whole past proposition as reported or inferred"
                    ),
                })
    for index in range(1, len(segments)):
        conditional = segments[index]
        if not is_conditional_to_segment(conditional):
            continue
        previous = segments[index - 1]
        if previous.get("type") == "punctuation":
            continue
        whole_surface = surfaces[index - 1] + surfaces[index]
        conditional_end = segment_starts[index] + len(surfaces[index])
        # A productive predicate may itself contain a learned construction
        # before と (for example whole primary 出てみる + と). A reviewed
        # predicate-sized overlay such as 出てみると can teach both layers and
        # expose 出て + みる + と as components; do not demand a redundant
        # nested conditional card.
        covered_by_predicate_overlay = any(
            isinstance(overlay.get("start"), int)
            and overlay["start"] <= segment_starts[index - 1]
            and overlay.get("end") == conditional_end
            and str(overlay.get("surface", "")).endswith(whole_surface)
            and isinstance(overlay.get("components"), list)
            and len(overlay["components"]) >= 2
            and str(overlay["components"][-1].get("surface", ""))
            == surfaces[index]
            for overlay in overlays
        )
        if whole_surface not in present and not covered_by_predicate_overlay:
            issues.append({
                "surface": whole_surface,
                "message": (
                    "conditional Vと is a conventional beginner grammar point; "
                    "keep the predicate and と separately tappable, and add a "
                    "whole overlay with components V + と for the contextual result"
                ),
            })
    for index in range(len(surfaces) - 1):
        grammar_surface = surfaces[index + 1]
        previous_pos = str(segments[index].get("part_of_speech", "")).casefold()
        if (
            grammar_surface == "という"
            and ("verb" in previous_pos or "動詞" in previous_pos)
        ):
            whole_surface = surfaces[index] + grammar_surface
            if whole_surface not in present:
                issues.append({
                    "surface": whole_surface,
                    "message": (
                        "reported Vという needs a compact predicate overlay with "
                        "components V + という; do not absorb earlier serial actions"
                    ),
                })
            grammar_end = segment_starts[index + 1] + len(grammar_surface)
            previous_start = segment_starts[index]
            for overlay in overlays:
                overlay_surface = str(overlay.get("surface", ""))
                if (
                    overlay_surface.endswith("という")
                    and overlay.get("end") == grammar_end
                    and isinstance(overlay.get("start"), int)
                    and overlay["start"] < previous_start
                ):
                    issues.append({
                        "surface": overlay_surface,
                        "message": (
                            "the reported-speech overlay is too wide; retain only "
                            f"the final predicate-sized {whole_surface} overlay"
                        ),
                    })
        if grammar_surface not in {"ことがある", "ことはない"}:
            continue
        whole_surface = surfaces[index] + grammar_surface
        if whole_surface not in present:
            issues.append({
                "surface": whole_surface,
                "message": (
                    "the Vた-experience construction needs a whole overlay while "
                    "keeping the lexical predicate and grammar unit as separate taps"
                ),
            })
    # Fixed argument collocations must follow the complete inflected primary
    # surface. A literal phrase-list entry such as 目が回り would accidentally
    # truncate 目が回りました at the conjunctive stem and reward an incomplete
    # learner card. Derive the required span from the actual 回る segment.
    for index in range(len(segments) - 2):
        if (
            surfaces[index] == "目"
            and surfaces[index + 1] == "が"
            and str(segments[index + 2].get("lemma", "")) == "回る"
        ):
            whole_surface = "".join(surfaces[index:index + 3])
            if whole_surface not in present:
                issues.append({
                    "surface": whole_surface,
                    "message": (
                        "the fixed collocation 目が回る needs a whole overlay "
                        "through the complete inflected 回る form actually in "
                        "TEXT; 目が回り is complete when TEXT itself continues "
                        "with punctuation, but must not truncate a longer form "
                        "such as 目が回りました"
                    ),
                })
    for required_surface in _REQUIRED_CONTEXTUAL_OVERLAY_SURFACES:
        if required_surface in reconstructed and required_surface not in present:
            issues.append({
                "surface": required_surface,
                "message": (
                    "this contextual expression needs a whole overlay so its "
                    "combined meaning and useful internal parts stay accessible"
                ),
            })
    for segment in segments:
        surface = str(segment.get("surface", ""))
        surface_form = str(segment.get("conjugation_form", "")).casefold()
        surface_lemma = str(segment.get("lemma", ""))
        is_sou_appearance = (
            "そう" in surface
            and not surface.startswith("そう")
            and "volitional" not in surface_form
            and "non-inflecting" not in surface_form
            and (
                "そう" in surface_lemma
                or "そう" in surface_form
                or "appearance" in surface_form
            )
        )
        if (
            segment.get("type") == "idiom"
            and len(surface) >= 3
            and surface not in present
        ):
            issues.append({
                "surface": surface,
                "message": (
                    "a whole primary idiom must also expose its meaningful lexical "
                    "parts in an overlay"
                ),
            })
        productive_grammar_form = (
            "ずに" in surface
            or "たくな" in surface
            or (surface.endswith("ながら") and surface != "ながら")
            or surface.endswith(("てみる", "でみる"))
        )
        if (
            productive_grammar_form
            and not any(surface in overlay_surface for overlay_surface in present)
        ):
            issues.append({
                "surface": surface,
                "message": (
                    "this reusable productive grammar form needs an overlay "
                    "with its lexical root and learned grammar contribution"
                ),
            })
        if is_sou_appearance and surface not in present:
            issues.append({
                "surface": surface,
                "message": (
                    "a content-word そう appearance form needs an overlay so its "
                    "lexical head and reusable grammar point remain accessible"
                ),
            })
        if (
            str(segment.get("lemma", "")).endswith("くなる")
            and "たくな" not in surface
            and surface not in present
        ):
            issues.append({
                "surface": surface,
                "message": (
                    "a whole Aくなる adjective-change form needs an overlay so "
                    "the lexical adjective and reusable grammar remain accessible"
                ),
            })
        compound_suffixes = ("出す", "始める", "続ける", "終わる")
        compound_suffix = next(
            (
                suffix for suffix in compound_suffixes
                if surface_lemma.endswith(suffix) and surface_lemma != suffix
            ),
            None,
        )
        if (
            compound_suffix is not None
            and segment.get("type") in {"word", "grammar"}
            and surface not in present
        ):
            issues.append({
                "surface": surface,
                "message": (
                    f"productive compound verb {surface_lemma} needs a roots "
                    f"overlay explaining its lexical first verb plus {compound_suffix}"
                ),
            })
    for index in range(len(segments) - 2):
        first, second, third = segments[index:index + 3]
        surface = "".join(surfaces[index:index + 3])
        first_pos = str(first.get("part_of_speech", "")).casefold()
        if (
            surfaces[index].endswith("く")
            and "adjective" in first_pos
            and surfaces[index + 1] == "は"
            and str(third.get("lemma", "")) == "ない"
            and surface not in present
        ):
            issues.append({
                "surface": surface,
                "message": (
                    "Aくはない is a reusable contrastive-negative construction "
                    "whose combined meaning needs an overlay"
                ),
            })
    for index in range(len(segments) - 1):
        first, second = segments[index:index + 2]
        surface = "".join(surfaces[index:index + 2])
        first_pos = str(first.get("part_of_speech", "")).casefold()
        if (
            surfaces[index].endswith("く")
            and "adjective" in first_pos
            and str(second.get("lemma", "")) == "なる"
            and surface not in present
        ):
            issues.append({
                "surface": surface,
                "message": (
                    "Aくなる is a reusable adjective-change construction whose "
                    "combined meaning needs an overlay"
                ),
            })
    for index in range(len(segments) - 2):
        for width in (4, 3):
            if index + width > len(segments):
                continue
            surface = "".join(surfaces[index:index + width])
            if (
                is_numeric_comma_construction(surface)
                and not any(
                    overlay_surface == surface
                    or (
                        overlay_surface.startswith(surface)
                        and is_numeric_comma_construction(overlay_surface)
                    )
                    for overlay_surface in present
                )
            ):
                issues.append({
                    "surface": surface,
                    "message": (
                        "a comma-joined numeral range needs an overlay explaining "
                        "the combined approximate range, not isolated number values"
                    ),
                })
                break
    # Several independent policies can identify the same learner-facing span
    # (for example, a contextual expression may also be typed as an idiom).
    # One repair request per surface is sufficient and avoids presenting the
    # agent with duplicate, differently worded demands for the same overlay.
    unique_issues: list[dict[str, str]] = []
    seen_surfaces: set[str] = set()
    for issue in issues:
        surface = issue["surface"]
        if surface in seen_surfaces:
            continue
        seen_surfaces.add(surface)
        unique_issues.append(issue)
    return unique_issues


def split_japanese_annotation_chunks(
    text: str, target: int, maximum: int,
) -> list[str]:
    """Make small, sentence-complete Japanese annotation batches."""
    if target < 1 or maximum < target:
        raise ValueError("annotation chunk maximum must be at least target")
    # A quote embedded in a matrix sentence must remain with its reporting
    # predicate: 吾輩が「猫だ。名前はない」と答えた is one annotation task.
    # A standalone dialogue turn, however, may contain several independent
    # sentences. Keeping all of them together recreates the oversized batches
    # this splitter is meant to prevent, so split an internal 。！？ when the
    # current piece began with the opening quote and more quoted text follows.
    # Keep the final quoted sentence with its closing quote and any following
    # reporting predicate. Newlines remain boundaries only outside brackets.
    opening_to_closing = {
        "「": "」", "『": "』", "（": "）", "(": ")",
        "【": "】", "［": "］", "〈": "〉", "《": "》",
    }
    closing = set(opening_to_closing.values())
    # Each stack item records the expected closer and whether this is a
    # standalone dialogue quote whose internal complete sentences are safe to
    # annotate independently.
    stack: list[tuple[str, bool]] = []
    pieces: list[str] = []
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char in opening_to_closing:
            stack.append((
                opening_to_closing[char],
                char in {"「", "『"} and index == start,
            ))
        elif char in closing and stack and char == stack[-1][0]:
            stack.pop()
        next_nonspace = index + 1
        while next_nonspace < len(text) and text[next_nonspace].isspace():
            next_nonspace += 1
        standalone_dialogue_boundary = (
            bool(stack)
            and stack[-1][0] in {"」", "』"}
            and stack[-1][1]
            and char in "。！？"
            and (
                next_nonspace >= len(text)
                or text[next_nonspace] != stack[-1][0]
            )
        )
        if (
            (not stack and char in "。！？")
            or standalone_dialogue_boundary
            or (not stack and char == "\n")
        ):
            end = index + 1
            while end < len(text) and text[end].isspace():
                end += 1
            pieces.append(text[start:end])
            start = end
            index = end
            continue
        index += 1
    if start < len(text):
        pieces.append(text[start:])
    pieces = [piece for piece in pieces if piece]
    if any(len(piece) > maximum for piece in pieces):
        longest = max(len(piece) for piece in pieces)
        raise ValueError(
            f"Japanese annotation sentence length {longest} exceeds hard chunk "
            f"maximum {maximum}; do not split a sentence mid-construction"
        )
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) > maximum:
            chunks.append(current)
            current = ""
        current += piece
        if len(current) >= target:
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    assert "".join(chunks) == text
    return chunks


def material_review_findings(reviews: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Extract material findings while allowing vocabulary-only diagnostics."""
    explicitly_nonmaterial = (
        "軽微", "大きな問題ではない", "問題はない", "問題ない",
        "許容範囲", "出来事への影響はない", "物語上の意味は保たれている",
        "minor", "no impact", "meaning is preserved", "acceptable",
    )
    findings: list[dict[str, str]] = []
    for review in reviews:
        for category in ("unsupported_additions", "distortions", "language_problems"):
            for finding in review.get(category, []):
                normalized = str(finding).lower()
                positive_continuity_audit = (
                    "consistent" in normalized
                    and "no unexplained switch" in normalized
                )
                positive_fidelity_audit = (
                    "remain faithful" in normalized
                    and (
                        "preserved" in normalized
                        or "appropriately simplified" in normalized
                    )
                )
                if positive_continuity_audit or positive_fidelity_audit:
                    # Whole-chapter reviewers are asked to perform these two
                    # checks explicitly. A low-cost model occasionally records
                    # the successful check in language_problems even while
                    # returning verdict=pass. Do not turn an unambiguously
                    # affirmative audit statement into a fictional defect.
                    continue
                if any(marker in normalized for marker in explicitly_nonmaterial):
                    continue
                if category == "distortions" and all(
                    spelling in str(finding) for spelling in ("肴屋", "魚屋")
                ):
                    # This is a transparent lexical/orthographic modernization
                    # of the same fish seller, not a changed person or place.
                    continue
                vocabulary_only = category == "language_problems" and (
                    "above-level" in normalized or "above level" in normalized
                ) and not any(x in normalized for x in ("unnatural", "incorrect", "ungrammatical"))
                if not vocabulary_only:
                    findings.append({"category": category, "finding": str(finding),
                                     "classification": "material"})
    return findings


_SOURCE_HEADER = re.compile(
    r"\A第(?:\d+|[一二三四五六七八九十百]+)章"
    r"[　 ]+[一二三四五六七八九十百]+\s*"
)
_INLINE_READING = re.compile(
    r"(?<=[\u3400-\u4dbf\u4e00-\u9fff\u3005])"
    r"（[\u3040-\u309fー]+）"
)


def strip_duplicate_source_header(text: str) -> str:
    """Remove an extracted chapter label if an adaptation echoed it as prose."""
    return _SOURCE_HEADER.sub("", text, count=1)


def strip_inline_japanese_readings(text: str) -> str:
    """Remove redundant kanji-plus-kana reading glosses from reader prose.

    The tap card already supplies the segment reading. Leaving sporadic
    Aozora-style ``吾輩（わがはい）`` notation in the prose makes the chapter
    visually inconsistent and asks annotation agents to segment the reading a
    second time as if it were content. Genuine parenthetical prose is retained
    because this pattern accepts only a kana-only gloss immediately after a
    kanji character.
    """
    return _INLINE_READING.sub("", text)


def _one_edit_apart(left: str, right: str) -> bool:
    """Return true only when the strings differ by exactly one edit."""
    if abs(len(left) - len(right)) > 1 or left == right:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) == 1
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    short_at = long_at = differences = 0
    while short_at < len(shorter) and long_at < len(longer):
        if shorter[short_at] == longer[long_at]:
            short_at += 1
        else:
            differences += 1
            if differences > 1:
                return False
        long_at += 1
    return True


def resolve_source_boundary(source: str, quote: str, search_from: int = 0) -> int:
    """Resolve an exact boundary, or one unique single-edit source match.

    The fallback deliberately rejects short quotes and every ambiguous or
    multi-edit mismatch. Its return value is always an offset into ``source``;
    callers continue slicing the original source rather than corrected model
    text.
    """
    exact = source.find(quote, search_from)
    if exact >= 0:
        return exact
    if len(quote) < 12:
        return -1

    candidates: set[int] = set()
    for width in (len(quote) - 1, len(quote), len(quote) + 1):
        if width <= 0:
            continue
        stop = len(source) - width + 1
        for start in range(search_from, max(search_from, stop)):
            if _one_edit_apart(quote, source[start:start + width]):
                candidates.add(start)
                if len(candidates) > 1:
                    return -1
    return next(iter(candidates)) if len(candidates) == 1 else -1


class JapaneseChapterHarness(ChapterHarness):
    """Japanese prompt specialization; orchestration and healing stay shared."""

    @property
    def target_chars(self) -> int:
        return self.args.target_chars or target_for_source(self.source_path, self.args.level)

    @property
    def scene_count(self) -> int:
        output_units = (self.target_chars + 1799) // 1800
        # Long originals need enough independent event ledgers even at N5,
        # where output length alone would otherwise collapse 45k source
        # characters into one or two scenes.
        # A long original is deliberately processed as several compact source
        # units even when the beginner adaptation is short. This makes missed
        # events and morphology easier to diagnose and repair locally.
        source_units = (len(self.source) + 2499) // 2500
        return max(1, min(10, max(output_units, source_units)))

    def scene_length_bounds(self, target: int) -> tuple[int, int]:
        # Selective lower-level ledgers make balanced source spans possible;
        # keep local growth bounded without forcing every narrative unit into
        # an unnaturally identical size.  The assembled chapter has its own
        # stricter level-specific gate, so a dense closing scene may be somewhat
        # longer while shorter neighbouring scenes compensate for it.
        upper_ratio = 1.30 if self.args.level == "n5" else 1.50
        return int(target * 0.70), int(target * upper_ratio)

    def chapter_length_bounds(self) -> tuple[int, int]:
        """Use the same compact beginner bands as the Chinese editions."""
        lower_ratio, upper_ratio = (
            (0.70, 1.17) if self.args.level == "n5" else (0.85, 1.15)
        )
        return (
            max(1, round(self.target_chars * lower_ratio)),
            round(self.target_chars * upper_ratio),
        )

    @property
    def annotation_chunk_target(self) -> int:
        supplied = getattr(self.args, "annotation_chunk", None)
        return supplied or JAPANESE_ANNOTATION_CHUNK_TARGETS[
            getattr(self.args, "level", "n5")
        ]

    @property
    def annotation_chunk_maximum(self) -> int:
        supplied = getattr(self.args, "annotation_chunk_maximum", None)
        if supplied is not None:
            return supplied
        if getattr(self.args, "annotation_chunk", None) is not None:
            return max(self.annotation_chunk_target, round(self.annotation_chunk_target * 1.25))
        return JAPANESE_ANNOTATION_CHUNK_MAXIMUMS[
            getattr(self.args, "level", "n5")
        ]

    def write_manifest(self, status_value: str) -> None:
        super().write_manifest(status_value)
        path = self.run_dir / "manifest.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["language"] = "japanese"
        value["length_unit"] = "Japanese letters (kanji, hiragana, katakana)"
        # A resumed prose run may carry an older generic policy label. Record
        # the Japanese learner-unit policy that actually produced this reader.
        value["annotation_chunk_policy"] = JAPANESE_ANNOTATION_CHUNK_POLICY
        value["max_annotation_fresh_repairs"] = (
            self.args.max_annotation_fresh_repairs
        )
        value["max_annotation_adjudications"] = (
            self.args.max_annotation_adjudications
        )
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    @property
    def annotation_chunk_cache_tag(self) -> str:
        return (
            f"JAPANESE_ANNOTATION_CHUNK_POLICY={JAPANESE_ANNOTATION_CHUNK_POLICY};"
            f"target={self.annotation_chunk_target};"
            f"maximum={self.annotation_chunk_maximum}"
        )

    def refresh_annotation_chunk(self, index: int) -> bool:
        """Refresh all annotation jobs, or only explicitly selected chunks."""
        if not getattr(self.args, "refresh", False):
            return False
        selected = getattr(self.args, "annotation_chunk_indices", None)
        return not selected or index in selected

    def reuse_unselected_annotation_cache(self, index: int) -> bool:
        """Permit audited old-policy cache only outside a selective refresh.

        A policy bump should normally invalidate every accepted chunk. During
        an explicit surgical repair, however, regenerating the entire chapter
        defeats the purpose of selecting indices. Unselected cached chunks may
        be promoted only after they reconstruct and pass the current complete
        deterministic contract; selected chunks are always regenerated.
        """
        selected = getattr(self.args, "annotation_chunk_indices", None)
        return bool(
            getattr(self.args, "refresh", False)
            and selected
            and index not in selected
        )

    def bind_outline_source(self, outline: dict[str, Any]) -> dict[str, Any]:
        """Bind scene quotes to exact consecutive source spans, without editing them."""
        scenes = outline.get("scenes")
        if not isinstance(scenes, list) or not scenes:
            raise ValueError("Japanese scene outline has no scenes")
        if len(scenes) != self.scene_count:
            raise ValueError(
                f"outline returned {len(scenes)} scenes; expected {self.scene_count}"
            )
        starts, search_from = [], 0
        for index, scene in enumerate(scenes, 1):
            scene["id"] = f"scene_{index:02d}"
            quote = scene["source_start_quote"]
            position = resolve_source_boundary(self.source, quote, search_from)
            if position < 0:
                raise ValueError(
                    f"scene boundary not found after prior boundary: {quote!r}"
                )
            starts.append(position)
            search_from = position + max(1, len(quote))
        starts[0] = 0
        for index, scene in enumerate(scenes):
            scene["source_start"] = starts[index]
            scene["source_end"] = (
                starts[index + 1] if index + 1 < len(starts) else len(self.source)
            )
        return outline

    def finalize_outline(self, outline: dict[str, Any]) -> dict[str, Any]:
        """Bind source spans and enforce the level-specific selective ledger."""
        outline = self.bind_outline_source(outline)
        scenes = outline["scenes"]
        selected = [scene for scene in scenes if scene.get("required_events")]
        event_count = sum(len(scene["required_events"]) for scene in selected)
        minimum_events, maximum_events = JLPT_EVENT_BUDGET[self.args.level]
        if not minimum_events <= event_count <= maximum_events:
            raise ValueError(
                f"outline retained {event_count} events; expected "
                f"{minimum_events}-{maximum_events} chapter-wide"
            )
        scene_limit = JLPT_SELECTED_SCENE_LIMIT[self.args.level]
        if len(selected) > scene_limit:
            raise ValueError(
                f"outline retained {len(selected)} source scenes; maximum is {scene_limit}"
            )
        if any(
            len(scene["required_events"]) > JLPT_EVENTS_PER_SCENE[self.args.level]
            for scene in selected
        ):
            raise ValueError("outline exceeded the per-selected-scene event limit")
        # Length follows retained narrative weight, not the number of source
        # containers. Equal per-scene allocation made a one-event scene pad
        # itself with optional objects while starving a two-event causal scene.
        base, remainder = divmod(self.target_chars, event_count)
        for scene in scenes:
            if scene.get("required_events"):
                units = len(scene["required_events"])
                extra = min(units, remainder)
                scene["target_chars"] = base * units + extra
                remainder -= extra
            else:
                scene["target_chars"] = 0
        return outline

    @staticmethod
    def adaptation_scenes(outline: dict[str, Any]) -> list[dict[str, Any]]:
        """Return only source spans deliberately retained in the graded story."""
        return [scene for scene in outline["scenes"] if scene.get("required_events")]

    async def review_outline(self, outline: dict[str, Any], stage: str) -> dict[str, Any]:
        spans = [{
            "id": scene["id"], "title": scene["title"],
            "source_start": scene["source_start"],
            "source_end": scene["source_end"],
            "source_text": self.source[scene["source_start"]:scene["source_end"]],
            "required_events": scene["required_events"],
        } for scene in outline["scenes"]]
        minimum_events, maximum_events = JLPT_EVENT_BUDGET[self.args.level]
        prompt = f"""Return only JSON matching the supplied schema. Audit this
Japanese source scene map before adaptation. Every required event assigned to a
scene must be explicitly supported inside that scene's SOURCE_TEXT, not earlier
or later in the chapter. Boundaries must follow the original order and should
start at natural paragraph or episode boundaries. The required-event ledger is
intentionally selective under this {self.args.level.upper()} editorial scope:
{JLPT_ADAPTATION_SCOPE[self.args.level]}
Do not demand omitted source details or exhaustive coverage. Identify only
events assigned outside their source span, wrong order, duplicate coverage, or
a selective ledger that no longer forms a coherent chapter arc. Pass only if
every retained event belongs to its exact span. Empty required_events means the
source span is intentionally omitted and is valid. The complete chapter ledger
must retain {minimum_events}-{maximum_events} events across no more than
{JLPT_SELECTED_SCENE_LIMIT[self.args.level]} source scenes; do not demand an
event from every source scene.
Check concrete nouns and locations literally: a nearby synonym that changes a
source fact (for example 笹原 into 草原) is a distortion, not simplification.

SCENE SPANS:
{json.dumps(spans, ensure_ascii=False, indent=2)}"""
        return await self.runner.call(
            f"outline/{stage}", prompt,
            SCHEMAS / "japanese-scene-outline-review.schema.json",
            self.args.review_effort, refresh=self.args.refresh,
        )

    def merge_event_ledger(
        self, source_map: dict[str, Any], ledger: dict[str, Any],
    ) -> dict[str, Any]:
        """Attach a level-specific event selection to an immutable source map."""
        mapped = source_map.get("scenes")
        selected = ledger.get("scenes")
        if not isinstance(mapped, list) or not isinstance(selected, list):
            raise ValueError("source map and event ledger must contain scenes")
        expected_ids = [f"scene_{index:02d}" for index in range(1, len(mapped) + 1)]
        supplied_ids = [item.get("id") for item in selected if isinstance(item, dict)]
        if supplied_ids != expected_ids:
            raise ValueError(
                f"event ledger IDs/order {supplied_ids!r}; expected {expected_ids!r}"
            )
        outline = copy.deepcopy(source_map)
        for index, scene in enumerate(outline["scenes"]):
            scene["required_events"] = selected[index]["required_events"]
            scene["target_chars"] = 0
        return self.finalize_outline(outline)

    async def outline_from_source_map(self, path: Path) -> dict[str, Any]:
        """Select a small level-specific story spine inside a fixed source map."""
        source_map = json.loads(path.read_text(encoding="utf-8"))
        bound = self.bind_outline_source(copy.deepcopy({
            **source_map,
            "scenes": [{**scene, "required_events": [], "target_chars": 0}
                       for scene in source_map.get("scenes", [])],
        }))
        spans = [{
            "id": scene["id"], "title": scene["title"],
            "source_text": self.source[scene["source_start"]:scene["source_end"]],
        } for scene in bound["scenes"]]
        minimum_events, maximum_events = JLPT_EVENT_BUDGET[self.args.level]
        base_prompt = f"""Return only JSON matching the supplied schema. The
SOURCE MAP below is fixed and already grounded in the original chapter. Return
every scene ID exactly once in the same order. Select only
{minimum_events}-{maximum_events} chapter-wide REQUIRED EVENTS across no more
than {JLPT_SELECTED_SCENE_LIMIT[self.args.level]} scenes and no more than
{JLPT_EVENTS_PER_SCENE[self.args.level]} events in any selected scene. Use an
empty array for every omitted source span. The result is a selective story
spine, not an episode inventory. Apply this editorial scope:
{JLPT_ADAPTATION_SCOPE[self.args.level]}

Every event must be explicitly supported inside its own source_text. Preserve
concrete nouns and locations exactly; do not turn 笹原 into 草原 or make similar
near-synonym changes. Together, the retained events must form a coherent short
retelling at this level. Especially for N5/N4, omitting most of the chapter is
correct.

SOURCE MAP:
{json.dumps(spans, ensure_ascii=False, indent=2)}"""
        ledger = await self.runner.call(
            "event_ledger/initial", base_prompt,
            SCHEMAS / "japanese-event-ledger.schema.json",
            self.args.review_effort, refresh=self.args.refresh,
        )
        outline = None
        review = None
        problem = ""
        for attempt in range(0, 5):
            try:
                outline = self.merge_event_ledger(source_map, ledger)
                review = await self.review_outline(
                    outline, "review" if attempt == 0 else f"ledger_repair_{attempt:02d}_review"
                )
                if review["verdict"] == "pass":
                    review = await self.review_outline(
                        outline,
                        "verification" if attempt == 0 else f"ledger_repair_{attempt:02d}_verification",
                    )
                if review["verdict"] == "pass":
                    break
                problem = json.dumps(review, ensure_ascii=False, indent=2)
            except ValueError as exc:
                problem = str(exc)
            if attempt == 4:
                raise ValueError(
                    f"fixed-map event ledger did not pass grounding review: {problem}"
                )
            ledger = await self.runner.call(
                f"event_ledger/repair_{attempt + 1:02d}",
                base_prompt
                + "\n\nReturn the complete corrected ledger. Fix the deterministic "
                  "error or independent review below without changing scene IDs/order."
                + "\n\nERROR OR REVIEW:\n" + problem
                + "\n\nCURRENT LEDGER:\n"
                + json.dumps(ledger, ensure_ascii=False, indent=2),
                SCHEMAS / "japanese-event-ledger.schema.json",
                self.args.repair_effort, refresh=self.args.refresh,
            )
        assert outline is not None and review is not None
        (self.run_dir / "outline.json").write_text(
            json.dumps(outline, ensure_ascii=False, indent=2) + "\n"
        )
        (self.run_dir / "outline-review.json").write_text(
            json.dumps(review, ensure_ascii=False, indent=2) + "\n"
        )
        return outline

    async def outline(self) -> dict[str, Any]:
        supplied_outline = getattr(self.args, "outline_file", None)
        if supplied_outline:
            outline = self.finalize_outline(json.loads(
                Path(supplied_outline).read_text(encoding="utf-8")
            ))
            (self.run_dir / "outline.json").write_text(
                json.dumps(outline, ensure_ascii=False, indent=2) + "\n"
            )
            return outline
        source_map_arg = getattr(self.args, "source_map_file", None)
        source_map_path = (
            Path(source_map_arg) if source_map_arg
            else self.source_path.with_suffix(".source-map.json")
        )
        if source_map_path.is_file():
            return await self.outline_from_source_map(source_map_path)
        minimum_events, maximum_events = JLPT_EVENT_BUDGET[self.args.level]
        prompt = f"""Return only JSON matching the supplied schema.
Read the complete verbatim ORIGINAL Japanese chapter and divide it into exactly
{self.scene_count} consecutive adaptation scene(s). Each source_start_quote
must be an exact, unique substring at the start of a natural paragraph or
episode. These are SOURCE-MAP spans, not output quotas. Use an empty
required_events array for spans that the graded retelling should omit. Across
the whole chapter select only {minimum_events}-{maximum_events} events in no
more than {JLPT_SELECTED_SCENE_LIMIT[self.args.level]} source spans, with at
most {JLPT_EVENTS_PER_SCENE[self.args.level]} events in a selected span, under this
level-specific editorial scope:
{JLPT_ADAPTATION_SCOPE[self.args.level]}
Across the few retained events, keep a coherent beginning, development, and ending.
Do not add a token event to every scene: especially at N5 and N4, most source
material is intentionally absent from the adaptation.
Crucially, assign an event only to the scene whose source begins at this quote
and ends at the next scene's quote; never put an earlier episode into a later
scene. Keep source spans reasonably balanced while respecting episode
boundaries. The target_chars values must sum to about {self.target_chars}
Japanese characters.

ORIGINAL:\n{self.source}"""
        outline = await self.runner.call(
            "outline", prompt, SCHEMAS / "japanese-scene-outline.schema.json",
            self.args.review_effort, refresh=self.args.refresh,
        )
        # Ordering a long literary chapter from short boundary quotations is a
        # separate source-mapping task from writing prose.  Keep this repair
        # budget generous: failures are cheap, deterministic, and happen before
        # any adaptation is accepted.
        for boundary_attempt in range(1, 7):
            try:
                outline = self.finalize_outline(outline)
                break
            except ValueError as exc:
                if boundary_attempt == 6:
                    raise
                outline = await self.runner.call(
                    f"outline/boundary_repair_{boundary_attempt:02d}",
                    prompt
                    + "\n\nThe proposed outline failed exact deterministic boundary "
                      "resolution. Return the complete corrected outline. Copy "
                      "every source_start_quote verbatim from ORIGINAL, keep it "
                      "short and unique, preserve source order, and retain the "
                      "selective event budget.\n\nERROR:\n"
                    + str(exc)
                    + "\n\nINVALID OUTLINE:\n"
                    + json.dumps(outline, ensure_ascii=False, indent=2),
                    SCHEMAS / "japanese-scene-outline.schema.json",
                    self.args.repair_effort,
                    refresh=self.args.refresh,
                )
        review = await self.review_outline(outline, "review")
        if review["verdict"] == "pass":
            # One low-effort reviewer can miss an attractive but unsupported
            # compression (for example inferring that a character disappears).
            # A second independent source-ownership pass is cheap relative to
            # generating and annotating a bad chapter.
            review = await self.review_outline(outline, "verification")
        for attempt in range(1, 3):
            if review["verdict"] == "pass":
                break
            repair_prompt = f"""Return only JSON matching the supplied schema.
Repair the Japanese scene outline according to REVIEW. Every
source_start_quote must remain an exact unique substring at a paragraph or
episode boundary in ORIGINAL. Return exactly {self.scene_count} scenes in
source order. Empty required_events is the normal representation of an omitted
source span. Retain only {minimum_events}-{maximum_events} chapter-wide events
across no more than {JLPT_SELECTED_SCENE_LIMIT[self.args.level]} spans and no
more than {JLPT_EVENTS_PER_SCENE[self.args.level]} events per selected span.
Apply this selective editorial scope:
{JLPT_ADAPTATION_SCOPE[self.args.level]}
Ensure every retained event is supported between its scene quote and the next
scene quote; move boundaries or event assignments as needed.

REVIEW:
{json.dumps(review, ensure_ascii=False, indent=2)}

CURRENT OUTLINE:
{json.dumps(outline, ensure_ascii=False, indent=2)}

ORIGINAL:
{self.source}"""
            outline = await self.runner.call(
                f"outline/repair_{attempt:02d}", repair_prompt,
                SCHEMAS / "japanese-scene-outline.schema.json",
                self.args.repair_effort, refresh=self.args.refresh,
            )
            try:
                outline = self.finalize_outline(outline)
            except ValueError as exc:
                outline = await self.runner.call(
                    f"outline/repair_{attempt:02d}_boundary",
                    repair_prompt
                    + "\n\nThe repaired outline still failed exact boundary "
                      "resolution. Copy each short source_start_quote verbatim "
                      "from ORIGINAL and return the complete outline.\n\nERROR:\n"
                    + str(exc),
                    SCHEMAS / "japanese-scene-outline.schema.json",
                    self.args.repair_effort,
                    refresh=self.args.refresh,
                )
                outline = self.finalize_outline(outline)
            review = await self.review_outline(
                outline, f"repair_{attempt:02d}_review"
            )
        if review["verdict"] != "pass":
            raise ValueError("Japanese source scene map did not pass grounding review")
        (self.run_dir / "outline.json").write_text(
            json.dumps(outline, ensure_ascii=False, indent=2) + "\n"
        )
        (self.run_dir / "outline-review.json").write_text(
            json.dumps(review, ensure_ascii=False, indent=2) + "\n"
        )
        return outline

    async def adapt_scene(self, scene: dict[str, Any]) -> dict[str, Any]:
        original = self.source[scene["source_start"]:scene["source_end"]]
        events = "\n".join(f"- {event}" for event in scene["required_events"])
        vocabulary = vocabulary_prompt_reference(self.args.level)
        orthography = jlpt_orthography_guidance(self.args.level)
        narrative = jlpt_narrative_guidance(self.args.level)
        low, high = self.scene_length_bounds(scene["target_chars"])
        prompt = f"""Return only JSON matching the supplied schema, putting the
adapted scene in `text`. Rewrite VERBATIM ORIGINAL as natural, engaging modern
Japanese for an annotated {self.args.level.upper()} literary reader. Keep core
grammar and ordinary vocabulary comfortable at {self.args.level.upper()}.
At least {1 - MAX_ABOVE_LEVEL_RATIO[self.args.level]:.0%} of ordinary content-word
tokens must use a lemma, spelling, or reading from the cumulative working
vocabulary below. Prefer a clear level word or short paraphrase whenever it can
express the same fact. Above-level vocabulary is limited to proper names and a
few indispensable, recurring literary/cultural/story terms; annotation is not
permission to fill each sentence with exceptions. Preserve essential events,
causes, motivations, names, numbers, tone, and order. Intentional compression
is expected. Do not invent facts or translate into another language. Use normal
Japanese orthography. Preserve the source narrator's chosen first-person form;
do not silently replace it with 私 or 僕. Do not pad with optional source details
merely to approach the target. The mechanical length requirement is {low}-{high}
Japanese letters; do not exceed it.

ORTHOGRAPHY POLICY:
{orthography}

NARRATIVE POLICY:
{narrative}

WORKING {self.args.level.upper()}-AND-BELOW VOCABULARY (spellings and readings):
{vocabulary}

REQUIRED EVENTS:\n{events}\n\nVERBATIM ORIGINAL:\n{original}"""
        result = await self.runner.call(
            f"{scene['id']}/adapt", prompt, SCHEMAS / "adaptation.schema.json",
            self.args.adapt_effort, refresh=self.args.refresh,
        )
        result["text"] = strip_duplicate_source_header(result["text"])
        return result

    async def review_scene(
        self,
        scene: dict[str, Any],
        adaptation: str,
        suffix: str = "review",
        prior_findings: list[str] | None = None,
    ) -> dict[str, Any]:
        original = self.source[scene["source_start"]:scene["source_end"]]
        vocabulary = vocabulary_prompt_reference(self.args.level)
        orthography = jlpt_orthography_guidance(self.args.level)
        narrative = jlpt_narrative_guidance(self.args.level)
        required_events = "\n".join(
            f"- {event}" for event in scene.get("required_events", [])
        )
        upper_percent = 130 if self.args.level == "n5" else 150
        prompt = f"""Return only JSON matching the supplied schema. Independently
compare ADAPTATION with VERBATIM ORIGINAL. This is a compact {self.args.level.upper()}
Japanese graded reader, so condensation and paraphrase are intended. Target
{scene['target_chars']} Japanese characters, roughly 70%-{upper_percent}%. Identify only
material omissions, unsupported additions, factual or causal distortions,
unnatural Japanese, and language clearly unsuitable for the requested level.
Only REQUIRED EVENTS are mandated. Under this editorial scope, do not report
other source details or episodes as omissions:
{JLPT_ADAPTATION_SCOPE[self.args.level]}
Require ordinary content vocabulary to be overwhelmingly within the cumulative
working list below. A few indispensable proper names and recurring story terms
may remain above level, but several exceptions per sentence is a material
readability defect; annotations do not make that graded prose. Set verdict=pass
only when source_fidelity, naturalness, and readability are each at least 8 and
no material distortion remains.
Treat a changed story-bearing place/object and a changed narrator first-person
form as material. Do not reject a level-appropriate generalization that keeps
the event identical (for example 掌 to 手 or 邸 to 家), or a natural modernization
such as 飲む煙草 to 吸うたばこ. 笹原 to 草原 is not such a generalization because
it changes the setting.

ORTHOGRAPHY POLICY:
{orthography}

NARRATIVE POLICY:
{narrative}

WORKING {self.args.level.upper()}-AND-BELOW VOCABULARY (spellings and readings):
{vocabulary}

PREVIOUS REVIEW TRAPS:
{json.dumps(prior_findings or [], ensure_ascii=False, indent=2)}

REQUIRED EVENTS:
{required_events}

VERBATIM ORIGINAL:\n{original}\n\nADAPTATION:\n{adaptation}"""
        result = await self.runner.call(
            f"{scene['id']}/{suffix}", prompt, SCHEMAS / "source-review.schema.json",
            self.args.review_effort, refresh=self.args.refresh,
        )
        length = japanese_char_count(adaptation)
        low, high = self.scene_length_bounds(scene["target_chars"])
        in_range = low <= length <= high
        if in_range:
            result = discard_incorrect_length_findings(result)
        result = apply_compact_review_policy(result)
        problems = []
        if not in_range:
            problems.append(
            f"mechanical unit length gate: {length} Japanese letters, required {low}-{high}"
            )
        leaked = [marker for marker in ("［＃", "《", "》", "｜") if marker in adaptation]
        if leaked:
            problems.append("leaked Aozora source markers: " + " ".join(leaked))
        for count, sentence in overlong_japanese_sentences(
            adaptation, self.args.level
        ):
            problems.append(
                f"mechanical sentence-length gate: {count} Japanese letters "
                f"exceeds {JLPT_MAX_SENTENCE_CHARS[self.args.level]}: {sentence}"
            )
        if problems:
            result = dict(result)
            result["verdict"] = "revise"
            result.setdefault("language_problems", []).extend(problems)
            result["harness_decision"] = "rejected_by_mechanical_gate"
        elif material_review_findings([result]):
            # A reviewer occasionally emits verdict=pass while simultaneously
            # listing a concrete distortion/addition/language fault. Never let
            # that contradiction bypass the bounded scene repair loop.
            result = dict(result)
            result["verdict"] = "revise"
            result["harness_decision"] = "rejected_material_findings"
        return result

    async def repair_scene(
        self,
        scene: dict[str, Any],
        adaptation: str,
        review: dict[str, Any],
        attempt: int,
        prior_findings: list[str] | None = None,
    ) -> dict[str, Any]:
        original = self.source[scene["source_start"]:scene["source_end"]]
        vocabulary = vocabulary_prompt_reference(self.args.level)
        narrative = jlpt_narrative_guidance(self.args.level)
        low, high = self.scene_length_bounds(scene["target_chars"])
        prompt = f"""Return only JSON matching the supplied schema, with revised
Japanese in `text`. Repair ADAPTATION according to the independent REVIEW and
VERBATIM ORIGINAL. Change only what findings require. Preserve good prose,
event order, compactness, and {self.args.level.upper()}-readable core language.
Correct distortions and unnatural Japanese without inventing facts. The result
must contain {low}-{high} Japanese letters. Simplify ordinary vocabulary using
the cumulative working list; do not restore intentionally omitted source detail.

WORKING {self.args.level.upper()}-AND-BELOW VOCABULARY:
{vocabulary}

NARRATIVE POLICY:
{narrative}

PREVIOUS REVIEW TRAPS:
{json.dumps(prior_findings or [], ensure_ascii=False, indent=2)}

VERBATIM ORIGINAL:\n{original}\n\nADAPTATION:\n{adaptation}\n\nREVIEW:\n{json.dumps(review, ensure_ascii=False, indent=2)}"""
        result = await self.runner.call(
            f"{scene['id']}/repair_{attempt:02d}", prompt,
            SCHEMAS / "adaptation.schema.json", self.args.repair_effort,
            refresh=self.args.refresh,
        )
        result["text"] = strip_duplicate_source_header(result["text"])
        return result

    async def review_chapter(
        self, outline: dict[str, Any], chapter: str, *, stage: str = "review"
    ) -> dict[str, Any]:
        """Audit assembled continuity and the full required-event ledger."""
        narrative = jlpt_narrative_guidance(self.args.level)
        ledger = [
            {
                "scene": scene["id"], "source_start": scene["source_start"],
                "source_end": scene["source_end"],
                "required_events": scene["required_events"],
            }
            for scene in self.adaptation_scenes(outline)
        ]
        prompt = f"""Return only JSON matching the supplied schema. Perform a
fresh whole-chapter audit of ADAPTATION against the complete VERBATIM ORIGINAL
and REQUIRED EVENT LEDGER. Scene-level reviews have already run; focus on
cross-scene continuity, event order, identities, motivations, causal links,
contradictions, duplicate transitions, and any ledger event lost during
assembly. Natural compression is expected, but every ledger event must remain
recognizable. The ledger is intentionally selective under this scope; do not
report unlisted source details as omissions:
{JLPT_ADAPTATION_SCOPE[self.args.level]}
Set pass only when source_fidelity, naturalness, and readability
are at least 8 and there are no material omissions or distortions.
Finding arrays must contain problems only. Never place a successful check or
affirmative confirmation in omissions, distortions, or language_problems.
Explicitly compare narrator self-reference in every paragraph: an unexplained
switch among 吾輩, 私, 僕, or other first-person forms is a continuity error. Also
verify concrete places and objects against the source rather than accepting a
near synonym that changes the fact. Level-appropriate hypernyms that preserve
the event, such as 掌 to 手 or 邸 to 家, are intentional simplification and must
not be reported as errors. Likewise, transparent modern spelling that preserves
the referent (for example 肴屋 to 魚屋) is not a factual change. Read the scope of
初めて precisely: 「書生以外の人間を初めて見た」 means the first non-student
human and does not contradict an earlier encounter with the student.

NARRATIVE POLICY:
{narrative}

REQUIRED EVENT LEDGER:
{json.dumps(ledger, ensure_ascii=False, indent=2)}

VERBATIM ORIGINAL:
{self.source}

ADAPTATION:
{chapter}"""
        result = await self.runner.call(
            f"chapter/{stage}", prompt, SCHEMAS / "source-review.schema.json",
            self.args.review_effort, refresh=self.args.refresh,
        )
        result = apply_compact_review_policy(result)
        count = japanese_char_count(chapter)
        low, high = self.chapter_length_bounds()
        if not low <= count <= high:
            result = dict(result)
            result["verdict"] = "revise"
            result.setdefault("language_problems", []).append(
                f"mechanical chapter length gate: {count}, required {low}-{high}"
            )
        sentence_problems = overlong_japanese_sentences(chapter, self.args.level)
        if sentence_problems:
            result = dict(result)
            result["verdict"] = "revise"
            result.setdefault("language_problems", []).extend(
                f"mechanical sentence-length gate: {count} Japanese letters "
                f"exceeds {JLPT_MAX_SENTENCE_CHARS[self.args.level]}: {sentence}"
                for count, sentence in sentence_problems
            )
        register_problems = japanese_narrative_register_issues(
            chapter, self.args.level
        )
        if register_problems:
            result = dict(result)
            result["verdict"] = "revise"
            result.setdefault("language_problems", []).extend(
                f"mechanical narrative-register gate: {problem}"
                for problem in register_problems
            )
        beginner_problems = japanese_beginner_prose_issues(
            chapter, self.args.level
        )
        if beginner_problems:
            result = dict(result)
            result["verdict"] = "revise"
            result.setdefault("language_problems", []).extend(
                f"mechanical beginner-prose gate: {problem}"
                for problem in beginner_problems
            )
        paragraph_problems = japanese_paragraph_structure_issues(
            chapter, len(self.adaptation_scenes(outline))
        )
        if paragraph_problems:
            result = dict(result)
            result["verdict"] = "revise"
            result.setdefault("language_problems", []).extend(
                f"mechanical paragraph-structure gate: {problem}"
                for problem in paragraph_problems
            )
        elif material_review_findings([result]):
            result = dict(result)
            result["verdict"] = "revise"
            result["harness_decision"] = "rejected_material_findings"
        return result

    async def repair_chapter(
        self, outline: dict[str, Any], chapter: str,
        review: dict[str, Any], attempt: int,
    ) -> str:
        """Apply only the concrete findings from a whole-chapter review.

        The complete source and current, already scene-reviewed adaptation are
        supplied on every attempt. Stable attempt-numbered job names make the
        loop resumable through the runner cache.
        """
        ledger = [
            {"scene": scene["id"], "required_events": scene["required_events"]}
            for scene in self.adaptation_scenes(outline)
        ]
        vocabulary = vocabulary_prompt_reference(self.args.level)
        narrative = jlpt_narrative_guidance(self.args.level)
        low, high = self.chapter_length_bounds()
        prompt = f"""Return only JSON matching the supplied schema, with the
complete corrected Japanese chapter in `text`. This is an ISSUE-SCOPED repair
of an adaptation whose individual scenes already passed independent review.
Fix every concrete finding in REVIEW, but preserve all other wording, paragraph
order, transitions, and scene-reviewed text verbatim. Do not broadly rewrite,
summarize, embellish, or add facts. Use VERBATIM ORIGINAL only to restore or
correct what REVIEW identifies. Keep the {self.args.level.upper()} target and
stay within the strict complete-chapter range of {low}-{high} Japanese characters.

WORKING {self.args.level.upper()}-AND-BELOW VOCABULARY:
{vocabulary}

NARRATIVE POLICY:
{narrative}

REQUIRED EVENT LEDGER:
{json.dumps(ledger, ensure_ascii=False, indent=2)}

VERBATIM ORIGINAL:
{self.source}

CURRENT SCENE-REVIEWED ADAPTATION:
{chapter}

REVIEW FINDINGS:
{json.dumps(review, ensure_ascii=False, indent=2)}

Make the smallest local edits that satisfy REVIEW. If a finding concerns an
optional source detail that is not in REQUIRED EVENT LEDGER, delete or compress
that optional detail instead of restoring more source episodes or literary
vocabulary. For an N5 Vず finding, directly replace it with a familiar polite
negative; do not rewrite unrelated sentences. Never introduce words absent
from the learner vocabulary merely to make the chapter more source-like."""
        result = await self.runner.call(
            f"chapter/repair_{attempt:02d}", prompt,
            SCHEMAS / "adaptation.schema.json", self.args.repair_effort,
            refresh=self.args.refresh,
        )
        return strip_duplicate_source_header(result["text"]).rstrip() + "\n"

    async def heal_chapter_review(
        self, outline: dict[str, Any], chapter: str,
        initial_review: dict[str, Any],
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        """Boundedly repair and independently re-audit a rejected chapter."""
        candidate, review = chapter, initial_review
        attempts: list[dict[str, Any]] = []
        for attempt in range(1, self.args.max_repairs + 1):
            before_count = japanese_char_count(candidate)
            candidate = await self.repair_chapter(
                outline, candidate, review, attempt
            )
            review = await self.review_chapter(
                outline, candidate, stage=f"repair_{attempt:02d}_review"
            )
            verification = None
            if review["verdict"] == "pass":
                verification = await self.review_chapter(
                    outline, candidate,
                    stage=f"repair_{attempt:02d}_verification",
                )
                review = verification
            attempts.append({
                "attempt": attempt,
                "repair_job": f"chapter/repair_{attempt:02d}",
                "review_job": f"chapter/repair_{attempt:02d}_review",
                "before_japanese_chars": before_count,
                "after_japanese_chars": japanese_char_count(candidate),
                "review": review,
                "independent_verification": verification,
            })
            if review["verdict"] == "pass":
                break
        return candidate, review, attempts

    async def repair_level_preflight(
        self,
        outline: dict[str, Any],
        chapter: str,
        diagnostics: dict[str, Any],
        attempt: int,
        source_review: dict[str, Any] | None = None,
    ) -> str:
        """Simplify a source-reviewed chapter before annotation begins."""
        vocabulary = vocabulary_prompt_reference(self.args.level)
        narrative = jlpt_narrative_guidance(self.args.level)
        low, high = self.chapter_length_bounds()
        ledger = [
            {"scene": scene["id"], "required_events": scene["required_events"]}
            for scene in self.adaptation_scenes(outline)
        ]
        prompt = f"""Return only JSON matching the supplied schema, with the
complete revised Japanese chapter in `text`. The chapter has already passed
source-fidelity review, but its deterministic pre-annotation vocabulary ratio
is too difficult for {self.args.level.upper()}. Rewrite the sampled above-level
ordinary words and any surrounding grammar needed for naturalness using the
cumulative learner vocabulary. Keep every required event, identity, cause,
order, narrator voice, and quotation meaning. Do not add source detail or pad.
Recurring reviewed story terms may remain; the diagnostic already excluded
them. Prefer short active sentences and familiar paraphrases. Stay within
{low}-{high} Japanese letters.

Preserve the existing blank-line paragraph boundaries exactly: each paragraph
is one independently source-reviewed scene. Do not merge two scene paragraphs
or split one into new paragraphs while simplifying its vocabulary.

This attempt must bring `above_level_ratio` down to or below
`max_above_level_ratio`. Address every item in `sample_above_level_lemmas`:
replace it with level vocabulary, remove optional wording that contains it, or
retain it only when it is truly indispensable story vocabulary already excluded
by the diagnostic. Do not merely make a few cosmetic substitutions. If PRIOR
SOURCE REVIEW is present, fix every listed fidelity or naturalness problem too;
do not revert to the older chapter and do not introduce a new source detail.

NARRATIVE POLICY:
{narrative}

DETERMINISTIC LEVEL DIAGNOSTICS:
{json.dumps(diagnostics, ensure_ascii=False, indent=2)}

PRIOR SOURCE REVIEW:
{json.dumps(source_review, ensure_ascii=False, indent=2) if source_review else "None; preserve the currently accepted source fidelity."}

REQUIRED EVENT LEDGER:
{json.dumps(ledger, ensure_ascii=False, indent=2)}

CUMULATIVE {self.args.level.upper()} VOCABULARY:
{vocabulary}

VERBATIM ORIGINAL:
{self.source}

CURRENT CHAPTER:
{chapter}"""
        result = await self.runner.call(
            f"chapter/vocabulary_preflight_repair_{attempt:02d}",
            prompt,
            SCHEMAS / "adaptation.schema.json",
            self.args.repair_effort,
            refresh=self.args.refresh,
        )
        return strip_duplicate_source_header(result["text"]).rstrip() + "\n"

    async def heal_level_preflight(
        self, outline: dict[str, Any], chapter: str,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        """Run the cheap lemma gate before expensive learner annotation."""
        accepted_plan = await self.ensure_story_vocabulary_plan(chapter)
        accepted_chapter = chapter
        accepted_diagnostics = preflight_level_diagnostics(
            accepted_chapter, self.args.level, accepted_plan.get("terms", []),
        )
        working_chapter = accepted_chapter
        working_diagnostics = accepted_diagnostics
        prior_source_review: dict[str, Any] | None = None
        attempts: list[dict[str, Any]] = []
        (self.run_dir / "preflight-level-diagnostics-attempt-00.json").write_text(
            json.dumps(accepted_diagnostics, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        for attempt in range(1, self.args.max_repairs + 1):
            if accepted_diagnostics["passes"]:
                break
            candidate = await self.repair_level_preflight(
                outline, working_chapter, working_diagnostics, attempt,
                source_review=prior_source_review,
            )
            review = await self.review_chapter(
                outline, candidate,
                stage=f"vocabulary_preflight_{attempt:02d}_review",
            )
            verification = None
            if review["verdict"] == "pass":
                verification = await self.review_chapter(
                    outline, candidate,
                    stage=f"vocabulary_preflight_{attempt:02d}_verification",
                )
                review = verification
            source_accepted = review["verdict"] == "pass"
            candidate_plan = accepted_plan
            if source_accepted:
                candidate_plan = await self.ensure_story_vocabulary_plan(
                    candidate,
                    stage=f"story_vocabulary_preflight_{attempt:02d}",
                    force=True,
                )
            candidate_diagnostics = preflight_level_diagnostics(
                candidate, self.args.level, candidate_plan.get("terms", []),
            )
            if source_accepted:
                accepted_chapter = candidate
                accepted_plan = candidate_plan
                accepted_diagnostics = candidate_diagnostics
                working_chapter = accepted_chapter
                working_diagnostics = accepted_diagnostics
                prior_source_review = None
            else:
                # Carry the latest candidate forward so the next pass can repair
                # its concrete source-review findings instead of regenerating
                # the same rejected edit from the older accepted chapter.
                working_chapter = candidate
                working_diagnostics = candidate_diagnostics
                prior_source_review = review
            attempts.append({
                "attempt": attempt,
                "chapter": candidate,
                "source_review": review,
                "source_verification": verification,
                "source_accepted": source_accepted,
                "diagnostics": candidate_diagnostics,
            })
            (self.run_dir / f"preflight-level-diagnostics-attempt-{attempt:02d}.json").write_text(
                json.dumps(candidate_diagnostics, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        (self.run_dir / "preflight-level-diagnostics.json").write_text(
            json.dumps(accepted_diagnostics, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return accepted_chapter, accepted_diagnostics, attempts

    async def rewrite_scene(self, scene: dict[str, Any], attempts: list[dict[str, Any]]) -> dict[str, Any]:
        original = self.source[scene["source_start"]:scene["source_end"]]
        vocabulary = vocabulary_prompt_reference(self.args.level)
        orthography = jlpt_orthography_guidance(self.args.level)
        narrative = jlpt_narrative_guidance(self.args.level)
        low, high = self.scene_length_bounds(scene["target_chars"])
        history = json.dumps(
            [{"text": item["text"], "review": item["review"]} for item in attempts],
            ensure_ascii=False, indent=2,
        )
        prompt = f"""Return only JSON matching the supplied schema, with a fresh
Japanese adaptation in `text`. Start again from VERBATIM ORIGINAL because prior
attempts failed review; use their history only as traps to avoid. Write natural
modern Japanese for an annotated {self.args.level.upper()} reader. Preserve all
REQUIRED EVENTS and their order, but do not restore other intentionally omitted
source details. Do not invent facts. The result must contain {low}-{high}
Japanese letters and overwhelmingly use the cumulative working vocabulary.

ORTHOGRAPHY POLICY:
{orthography}

NARRATIVE POLICY:
{narrative}

WORKING {self.args.level.upper()}-AND-BELOW VOCABULARY:
{vocabulary}

REQUIRED EVENTS:
{json.dumps(scene['required_events'], ensure_ascii=False, indent=2)}

VERBATIM ORIGINAL:\n{original}\n\nFAILED HISTORY:\n{history}"""
        result = await self.runner.call(
            f"{scene['id']}/fresh_rewrite", prompt,
            SCHEMAS / "adaptation.schema.json", self.args.final_effort,
            refresh=self.args.refresh,
        )
        result["text"] = strip_duplicate_source_header(result["text"])
        return result

    async def repair_after_fresh_rewrite(
        self,
        scene: dict[str, Any],
        adaptation: str,
        review: dict[str, Any],
        attempt: int = 1,
    ) -> dict[str, Any]:
        """One bounded, issue-only correction when a fresh rewrite is close."""
        original = self.source[scene["source_start"]:scene["source_end"]]
        vocabulary = vocabulary_prompt_reference(self.args.level)
        low, high = self.scene_length_bounds(scene["target_chars"])
        prompt = f"""Return only JSON matching the supplied schema, with the
complete corrected Japanese scene in `text`. The fresh source-grounded rewrite
below has only the concrete issues listed by its independent REVIEW. Fix those
issues only. Preserve every other word, event, and paragraph verbatim; do not
rewrite or embellish. Stay within 70%-130% of {scene['target_chars']} Japanese
characters and retain {self.args.level.upper()} readability. The mechanical
requirement is {low}-{high} Japanese letters. Use the cumulative working list
for ordinary vocabulary and do not restore intentionally omitted source detail.

WORKING {self.args.level.upper()}-AND-BELOW VOCABULARY:
{vocabulary}

VERBATIM ORIGINAL:
{original}

FRESH REWRITE:
{adaptation}

REVIEW FINDINGS:
{json.dumps(review, ensure_ascii=False, indent=2)}"""
        result = await self.runner.call(
            (
                f"{scene['id']}/post_fresh_repair"
                if attempt == 1
                else f"{scene['id']}/post_fresh_repair_{attempt:02d}"
            ),
            prompt,
            SCHEMAS / "adaptation.schema.json", self.args.repair_effort,
            refresh=self.args.refresh,
        )
        result["text"] = strip_duplicate_source_header(result["text"])
        if 1 - SequenceMatcher(None, adaptation, result["text"]).ratio() > 0.35:
            result["text"] = adaptation
        return result

    async def rescue_scene_length(
        self, scene: dict[str, Any], adaptation: str,
    ) -> dict[str, Any]:
        """Freshly condense a useful scene that remains mechanically long."""
        original = self.source[scene["source_start"]:scene["source_end"]]
        vocabulary = vocabulary_prompt_reference(self.args.level)
        low, high = self.scene_length_bounds(scene["target_chars"])
        prompt = f"""Return only JSON matching the supplied schema. Rewrite the
complete CANDIDATE as a much shorter, natural {self.args.level.upper()} scene of
{low}-{high} Japanese letters. Preserve only the REQUIRED EVENTS and their
source-supported facts. Delete examples, lists, exact objects, descriptions,
and secondary incidents not required by the ledger. Do not merely shorten a
few phrases: choose the simplest coherent sentences needed for the ledger.
Overwhelmingly use the cumulative working vocabulary. Do not invent facts.

WORKING VOCABULARY:
{vocabulary}

REQUIRED EVENTS:
{json.dumps(scene['required_events'], ensure_ascii=False, indent=2)}

VERBATIM ORIGINAL:
{original}

OVERLONG CANDIDATE:
{adaptation}"""
        result = await self.runner.call(
            f"{scene['id']}/length_rescue",
            prompt,
            SCHEMAS / "adaptation.schema.json",
            self.args.final_effort,
            refresh=self.args.refresh,
        )
        result["text"] = strip_duplicate_source_header(result["text"])
        return result

    async def rescue_scene_ledger_only(
        self, scene: dict[str, Any], attempts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Drop repeatedly troublesome optional detail and retell the ledger."""
        original = self.source[scene["source_start"]:scene["source_end"]]
        vocabulary = vocabulary_prompt_reference(self.args.level)
        low, high = self.scene_length_bounds(scene["target_chars"])
        traps = self.prior_review_traps(attempts)
        prompt = f"""Return only JSON matching the supplied schema. Write a new,
plain {self.args.level.upper()} scene of {low}-{high} Japanese letters from the
REQUIRED EVENTS. The previous drafts repeatedly failed on optional details.
Do not preserve their wording. Omit every quotation, literary turn of phrase,
exact object, description, example, or secondary fact not necessary to state
the ledger. Keep only source-supported facts needed for a coherent connection
between these required events. Overwhelmingly use the working vocabulary.

WORKING VOCABULARY:
{vocabulary}

REQUIRED EVENTS:
{json.dumps(scene['required_events'], ensure_ascii=False, indent=2)}

REPEATED REVIEW TRAPS TO AVOID:
{json.dumps(traps, ensure_ascii=False, indent=2)}

VERBATIM ORIGINAL:
{original}"""
        result = await self.runner.call(
            f"{scene['id']}/ledger_rescue",
            prompt,
            SCHEMAS / "adaptation.schema.json",
            self.args.final_effort,
            refresh=self.args.refresh,
        )
        result["text"] = strip_duplicate_source_header(result["text"])
        return result

    async def repair_ledger_rescue(
        self, scene: dict[str, Any], adaptation: str,
        review: dict[str, Any], attempt: int,
    ) -> dict[str, Any]:
        """Repair only findings introduced by the minimal ledger retelling."""
        original = self.source[scene["source_start"]:scene["source_end"]]
        vocabulary = vocabulary_prompt_reference(self.args.level)
        low, high = self.scene_length_bounds(scene["target_chars"])
        prompt = f"""Return only JSON matching the supplied schema. Correct the
complete LEDGER RETELLING according to every concrete REVIEW finding. Preserve
all unaffected simple sentences. Do not restore optional source episodes,
quotations, objects, or descriptions. Keep causality, speakers, and subjects
exactly source-grounded and retain every REQUIRED EVENT. The complete result
must contain {low}-{high} Japanese letters and overwhelmingly use the working
vocabulary.

WORKING VOCABULARY:
{vocabulary}

REQUIRED EVENTS:
{json.dumps(scene['required_events'], ensure_ascii=False, indent=2)}

VERBATIM ORIGINAL:
{original}

LEDGER RETELLING:
{adaptation}

REVIEW:
{json.dumps(review, ensure_ascii=False, indent=2)}"""
        result = await self.runner.call(
            f"{scene['id']}/ledger_rescue_repair_{attempt:02d}",
            prompt,
            SCHEMAS / "adaptation.schema.json",
            self.args.repair_effort,
            refresh=self.args.refresh,
        )
        result["text"] = strip_duplicate_source_header(result["text"])
        return result

    async def process_scene(self, scene: dict[str, Any]) -> dict[str, Any]:
        result = await super().process_scene(scene)
        if result["review"]["verdict"] == "pass" or not self.args.fresh_rewrite:
            return result
        # A low-cost model is intentionally used for every pass. Give a close,
        # source-grounded fresh rewrite a few more issue-scoped opportunities
        # before resampling an entire edition whose other scenes already pass.
        for attempt in range(1, 7):
            adapted = await self.repair_after_fresh_rewrite(
                scene, result["text"], result["review"], attempt
            )
            suffix = (
                "post_fresh_repair_review"
                if attempt == 1
                else f"post_fresh_repair_{attempt:02d}_review"
            )
            review = await self.review_scene(
                scene,
                adapted["text"],
                suffix=suffix,
                prior_findings=self.prior_review_traps(result["attempts"]),
            )
            stage = (
                "post_fresh_repair"
                if attempt == 1
                else f"post_fresh_repair_{attempt:02d}"
            )
            result["text"], result["review"] = adapted["text"], review
            result["attempts"].append({
                "stage": stage, "text": adapted["text"], "review": review,
            })
            if review["verdict"] == "pass":
                break
        low, high = self.scene_length_bounds(scene["target_chars"])
        if (
            result["review"]["verdict"] != "pass"
            and not low <= japanese_char_count(result["text"]) <= high
        ):
            adapted = await self.rescue_scene_length(scene, result["text"])
            review = await self.review_scene(
                scene,
                adapted["text"],
                suffix="length_rescue_review",
                prior_findings=self.prior_review_traps(result["attempts"]),
            )
            result["text"], result["review"] = adapted["text"], review
            result["attempts"].append({
                "stage": "length_rescue",
                "text": adapted["text"],
                "review": review,
            })
        if result["review"]["verdict"] != "pass":
            adapted = await self.rescue_scene_ledger_only(
                scene, result["attempts"]
            )
            review = await self.review_scene(
                scene,
                adapted["text"],
                suffix="ledger_rescue_review",
                prior_findings=self.prior_review_traps(result["attempts"]),
            )
            result["text"], result["review"] = adapted["text"], review
            result["attempts"].append({
                "stage": "ledger_rescue",
                "text": adapted["text"],
                "review": review,
            })
            for attempt in range(1, 4):
                if result["review"]["verdict"] == "pass":
                    break
                adapted = await self.repair_ledger_rescue(
                    scene, result["text"], result["review"], attempt
                )
                review = await self.review_scene(
                    scene,
                    adapted["text"],
                    suffix=f"ledger_rescue_repair_{attempt:02d}_review",
                    prior_findings=self.prior_review_traps(result["attempts"]),
                )
                result["text"], result["review"] = adapted["text"], review
                result["attempts"].append({
                    "stage": f"ledger_rescue_repair_{attempt:02d}",
                    "text": adapted["text"],
                    "review": review,
                })
        if result["review"]["verdict"] != "pass":
            best = best_scene_attempt(result["attempts"], low, high)
            result["text"], result["review"] = best["text"], best["review"]
        result["resolved"] = result["review"]["verdict"] == "pass"
        scene_path = self.run_dir / "scenes" / f"{scene['id']}.json"
        scene_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        return result

    def reusable_scene(self, scene: dict[str, Any]) -> dict[str, Any] | None:
        """Reuse only source-identical scene evidence that already passed review."""
        supplied = getattr(self.args, "reuse_scenes_from", None)
        if not supplied:
            return None
        source = Path(supplied) / "scenes" / f"{scene['id']}.json"
        if not source.is_file():
            return None
        try:
            result = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if (
            result.get("scene") != scene
            or result.get("review", {}).get("verdict") != "pass"
            or result.get("resolved") is not True
        ):
            return None
        low, high = self.scene_length_bounds(scene["target_chars"])
        if not low <= japanese_char_count(str(result.get("text", ""))) <= high:
            return None
        destination = self.run_dir / "scenes" / f"{scene['id']}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return result

    async def process_or_reuse_scene(
        self, scene: dict[str, Any],
    ) -> dict[str, Any]:
        return self.reusable_scene(scene) or await self.process_scene(scene)

    def story_vocabulary_plan_issues(
        self, plan: dict[str, Any], chapter: str,
    ) -> list[str]:
        """Validate the small chapter-level exception list deterministically."""
        terms = plan.get("terms")
        if not isinstance(terms, list):
            return ["terms must be an array"]
        issues: list[str] = []
        budget = STORY_TERM_BUDGET[self.args.level]
        if len(terms) > budget:
            issues.append(f"{len(terms)} terms exceeds the {budget}-term budget")
        seen: set[str] = set()
        target = LEVEL_NUMBER[self.args.level]
        kana = re.compile(r"^[\u3040-\u30ffー・\s]+$")
        for index, term in enumerate(terms):
            if not isinstance(term, dict):
                issues.append(f"term {index} is not an object")
                continue
            surface = str(term.get("surface", "")).strip()
            lemma = str(term.get("lemma", "")).strip()
            reading = str(term.get("lemma_kana", "")).strip()
            if not surface or surface not in chapter:
                issues.append(f"term {index} surface is absent from the chapter")
            elif self.args.level in {"n5", "n4"} and chapter.count(surface) < 2:
                issues.append(
                    f"{surface} occurs only once and is not recurring beginner story vocabulary"
                )
            if not lemma or lemma in seen:
                issues.append(f"term {index} lemma is empty or duplicated")
            seen.add(lemma)
            if not kana.fullmatch(reading):
                issues.append(f"term {index} lemma_kana is not kana")
            if any(not str(term.get(field, "")).strip() for field in (
                "meaning_en", "importance_en",
            )):
                issues.append(f"term {index} lacks learner-facing English")
            if lemma in GRAMMAR_BASELINE_LEMMAS or surface in GRAMMAR_BASELINE_LEMMAS:
                issues.append(
                    f"{lemma or surface} is functional grammar, not story vocabulary"
                )
            if lemma in STORY_ALLOWLIST or surface in STORY_ALLOWLIST:
                issues.append(
                    f"{lemma or surface} is already covered by the fixed cast allowlist"
                )
            level = matched_level({
                "surface": surface,
                "lemma": lemma,
                "lemma_kana": reading,
            })
            if level is not None and level <= target:
                issues.append(
                    f"{lemma} is already in the cumulative {self.args.level.upper()} baseline"
                )
        return issues

    def sanitize_story_vocabulary_plan(
        self, plan: dict[str, Any], chapter: str,
    ) -> dict[str, Any]:
        """Drop invalid optional exceptions without weakening the hard rules."""
        terms = plan.get("terms")
        if not isinstance(terms, list):
            return {"terms": []}
        accepted: list[dict[str, Any]] = []
        for term in terms:
            candidate = {"terms": [*accepted, term]}
            if not self.story_vocabulary_plan_issues(candidate, chapter):
                accepted.append(term)
        return {"terms": accepted}

    async def plan_story_vocabulary(
        self, chapter: str, *, stage: str = "story_vocabulary",
    ) -> dict[str, Any]:
        """Have one agent propose and another independently curate exceptions."""
        budget = STORY_TERM_BUDGET[self.args.level]
        vocabulary = vocabulary_prompt_reference(self.args.level)
        base = f"""Return only JSON matching the supplied schema. Select at most
{budget} genuinely indispensable above-level story vocabulary lemmas for this
complete {self.args.level.upper()} adaptation. This is a sparse chapter-level
exception list, not a list of everything that matters in the plot. Exclude
proper names (the annotation layer handles them separately), ordinary level
vocabulary, generic people/animals/places/body parts, and one-scene objects or
actions that can simply be looked up as extra vocabulary. A term should normally
recur or name a concept the reader must retain across passages. Empty is valid.
Do not select fixed cast or narrator terms already handled globally:
{sorted(STORY_ALLOWLIST)}.
Never select particles, auxiliaries, inflectional endings, or grammar forms such
as ない; grammar is explained by the form/grammar layer, not story highlighting.
Give one exact surface occurring in TEXT, its dictionary lemma and kana reading,
a concise contextual English meaning, and why retaining it is indispensable.

CUMULATIVE {self.args.level.upper()} WORKING VOCABULARY:
{vocabulary}

TEXT:
{chapter}"""
        proposal = await self.runner.call(
            f"{stage}/proposal",
            base,
            SCHEMAS / "japanese-story-vocabulary.schema.json",
            self.args.annotation_effort,
            refresh=self.args.refresh,
        )
        proposal_issues = self.story_vocabulary_plan_issues(proposal, chapter)
        verification = await self.runner.call(
            f"{stage}/verification",
            base
            + "\n\nIndependently audit the proposal below. Return the complete corrected final "
              "plan, removing ordinary words, proper names, weak one-off items, "
              "duplicates, and anything over budget. Do not defer to the proposal."
            + "\n\nDETERMINISTIC PROPOSAL ISSUES:\n"
            + json.dumps(proposal_issues, ensure_ascii=False, indent=2)
            + "\n\nPROPOSAL:\n"
            + json.dumps(proposal, ensure_ascii=False, indent=2),
            SCHEMAS / "japanese-story-vocabulary.schema.json",
            self.args.annotation_review_effort,
            refresh=self.args.refresh,
        )
        issues = self.story_vocabulary_plan_issues(verification, chapter)
        if issues:
            sanitized = self.sanitize_story_vocabulary_plan(
                verification, chapter,
            )
            remaining = self.story_vocabulary_plan_issues(sanitized, chapter)
            (self.run_dir / "story-vocabulary-plan-rejections.json").write_text(
                json.dumps({
                    "reviewed_plan": verification,
                    "deterministic_issues": issues,
                    "published_plan": sanitized,
                }, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            if remaining:
                raise ValueError(
                    f"invalid sanitized story vocabulary plan: {remaining}"
                )
            verification = sanitized
        (self.run_dir / "story-vocabulary-plan.json").write_text(
            json.dumps(verification, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return verification

    async def ensure_story_vocabulary_plan(
        self,
        chapter: str,
        *,
        stage: str = "story_vocabulary",
        force: bool = False,
    ) -> dict[str, Any]:
        plan_path = self.run_dir / "story-vocabulary-plan.json"
        if not force and plan_path.is_file():
            candidate = json.loads(plan_path.read_text(encoding="utf-8"))
            if not self.story_vocabulary_plan_issues(candidate, chapter):
                self.story_vocabulary_plan = candidate
                return candidate
        self.story_vocabulary_plan = await self.plan_story_vocabulary(
            chapter, stage=stage,
        )
        return self.story_vocabulary_plan

    def prepare_planned_annotation(
        self, chunk: str, result: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply the independently reviewed chapter plan to chunk annotations."""
        value = normalize_redundant_japanese_form_steps(
            clear_unavailable_dictionary_links(
                self.prepare_annotation_candidate(chunk, result)
            )
        )
        terms = {
            item["lemma"]: item
            for item in getattr(self, "story_vocabulary_plan", {"terms": []})["terms"]
        }
        for segment in value.get("segments", []):
            kind = segment.get("type")
            if kind == "punctuation" or segment.get("story_role") == "name":
                continue
            planned = terms.get(segment.get("lemma"))
            if planned is None or kind not in {"word", "idiom"}:
                if segment.get("story_role") == "story_term":
                    segment["story_role"] = "none"
                    segment["story_importance_en"] = ""
            else:
                segment["story_role"] = "story_term"
                segment["story_importance_en"] = planned["importance_en"]
        # Kana-only text in full-width parentheses is an inline pronunciation
        # aid in this corpus, not a second lexical occurrence.  Its segment can
        # legitimately share the headword lemma, but highlighting it again as
        # story vocabulary creates a duplicate, misleading tap target.
        reading_ranges = [
            (match.start(1), match.end(1))
            for match in re.finditer(r"（([\u3040-\u30ffー・\s]+)）", chunk)
        ]
        cursor = 0
        for segment in value.get("segments", []):
            end = cursor + len(str(segment.get("surface", "")))
            if any(start <= cursor and end <= stop for start, stop in reading_ranges):
                segment["story_role"] = "none"
                segment["story_importance_en"] = ""
            cursor = end
        return value

    async def annotation_candidate(self, index: int, chunk: str, *, stage: str = "initial", prior=None, findings=None, effort=None) -> dict[str, Any]:
        repair = ""
        if prior is not None:
            repair = "\n\nPRIOR ANNOTATION:\n" + json.dumps(prior, ensure_ascii=False, indent=2)
            repair += "\n\nREVIEW FINDINGS:\n" + json.dumps(findings, ensure_ascii=False, indent=2)
        story_plan = getattr(self, "story_vocabulary_plan", {"terms": []})
        prompt = f"""Return only JSON matching the supplied schema. Annotate
Japanese TEXT without changing, omitting, or reordering any character.

SEGMENT LAYER: choose learner-sized tap targets, not morphological-analyzer
tokens. Keep a complete inflected content word together and explain its root
and full form in metadata. An inflected lexical verb or adjective remains type
`word`, even when its surface contains passive, progressive, negative, polite,
or copular material; reserve type `grammar` for learned constructions whose
whole lemma is grammatical rather than an ordinary lexical dictionary head.
For example, ありました is ONE word segment with
lemma ある and form `polite past`; すいて is ONE word segment with lemma すく
and form `て-form, connective`; 食べませんでした is ONE word segment with
lemma 食べる and form `polite negative past`. Never expose あり / まし / た,
すい / て, or 食べ / ませ / ん / でし / た as unrelated tap targets.

Keep conventional learned grammar chunks together using type `grammar`: for
example 歩くことにしました is segmented as 歩く (lemma 歩く) +
ことにしました (lemma ことにする, form `polite past`, meaning `decided to`).
Apply the same learner-unit principle to productive attached grammar. Keep
Vながら as one grammar primary with the content verb as lemma: 見ながら has
lemma 見る, not 見 + ながら and not lemma ながら. Give it a `nagara` key and
an overlay exposing lexical 見 + grammar ながら. Keep productive Vてみる as
one grammar primary too: 出てみる has construction lemma 出てみる and means
`try going out`, rather than 出て + an atomic みる. Its overlay exposes lexical
出て (lemma 出る) + grammar みる. A following conditional と remains its own
grammar primary; a wider 出てみると overlay may explain the combined result
while retaining the primary units 出てみる + と.
Treat conditional/temporal と after a predicate as a learned grammar segment,
not an ordinary case particle: assign a provisional `conditional-to` key and
add a predicate-sized V + と overlay explaining `when/if/upon V`. Quotative と
and comitative と remain ordinary particles and must not be confused with it.
Keep a content verb plus a benefactive helper as one complete predicate primary:
置いてやれ is ONE `word` segment with content lemma 置く, not 置いて + やれ.
Likewise, 付けてもらえなかった is one primary with content lemma 付ける, not
merely a wide overlay over split primaries. Keep directional/aspectual Vて行く
and Vて来る whole when 行く/来る contributes direction, viewpoint, or unfolding
aspect: in 池の左を歩いて行く, 歩いて行く is one `word` segment with lemma 歩く.
Do not overmerge genuinely sequential actions: 来て、見た and 食べてから行く
describe separate events because punctuation or an intervening grammar unit
marks the sequence. The learner-facing decision follows the construction's
meaning, not merely the adjacent character pattern.
Keep a conventional mimetic + する predicate whole as well: ぐるぐるして is
ONE `word` segment with lemma ぐるぐるする and a form chain ending in the exact
て-form, not ぐるぐる + して. Add a same-surface overlay with useful components
ぐるぐる + して so the mimetic contribution remains visible.
Also keep a manner adverb or mimetic with its conventional adverbial と as one
learner-facing target: ふわりと is ONE `word` segment, never ふわり + a detached
と that repeats the phrase's entire gloss. Give the whole target a
`mimetic-adverbial-to`-style key/form label and a same-surface overlay with
components ふわり + grammar-only と. Explain that と attaches the manner word
to the predicate; it does not independently mean `in a light, floating manner`.
Likewise, join an inflected copula as だった or でした rather than だっ + た.
Actively scan for conventional units before returning: keep formal-copula である,
temporal 後で, explanatory/embedded-question のか, and the completing
たりする construction together rather than splitting their grammatical kana into
atomic primary rows. These examples are guidance, not a closed phrase list.
Likewise keep an inflected comparison such as のようだった as the complete
のようだ grammar unit, never の + よう + だった.
Syntactically independent particles such as は, が, を, に remain separate;
an ending that forms a verb's て/で-form stays attached to that verb. Keep
quotative と separate from an ordinary thought/reporting verb (と + 思った),
except when it belongs to a conventional grammar unit such as かと思う. Keep a
lexical predicate accessible before attributive はずの (ある + はずの), and do
not assign a verb lemma such as 見始める to a nominal surface followed by the
copula だった (use 見始め + だった, or an accurate nominal-head analysis).
Likewise keep a volitional action accessible before とすると: 行こう +
とすると, with 行こうとすると available as the explanatory overlay.
Keep
the complete negative connective Vずに together as well (動かずに, not
動かず + に). When Vずに continues into いる or Vたく continues into なる,
the whole learned construction is a `grammar` primary unit with a construction
lemma and matching overlay: 動かずにいた -> 動かずにいる and したくなり ->
したくなる. Keep
lexicalized discourse words such as そこで (`thereupon; so`) and それから
(`after that`) whole when they function as conjunctions; do not mechanically
reinterpret their final kana as a detachable particle. They are ordinary
dictionary `word` segments with part of speech `conjunction`, not `idiom`.
Keep conventional time adverb 今でも (`even now; still`) whole. Keep
conventional frequency adverbs 何度も / 何回も (`many times`) and negative-polarity
一度も (`not even once`) whole rather than detaching their meaningful も.
Likewise keep conventional indefinite expressions 誰も and 何も whole;
before a negative predicate, their single contextual cards mean `no one` and
`nothing`. Do not reduce them to analyzer-style 誰 + も or 何 + も rows.
Conversely, do not make the whole clause どうすればよいか/どうすればいいか an
opaque primary card: keep どう + すれば + よい/いい + か accessible and add a
whole overlay for the combined meaning `what should one do?`.
Keep actual names of people, animals, and places together. Use `idiom` only for
a genuinely lexicalized
idiom or collocation, never for an arbitrary clause. Include whitespace and
punctuation as punctuation segments. Every non-punctuation segment needs its
exact surface reading in `surface_kana`, dictionary-form reading in
`lemma_kana`, part of speech, precise full-surface conjugation form (or
`non-inflecting`), and concise contextual English meaning. Mark proper names as `name`. Mark a
word as `story_term` only when its lemma appears in the reviewed chapter plan
below; all other ordinary words, even things or actions important in one
sentence, must use `none`. Copy the plan's importance rather than inventing a
new reason. Particles, auxiliaries, and inflectional grammar such as ない must
always use `none`, even if a malformed plan appears to mention them. All
metadata fields except story_importance_en must be nonempty for lexical
segments. Punctuation has empty linguistic fields and story_role `none`.
Concatenated surfaces must reproduce TEXT exactly.

DICTIONARY LINKS: `lemma` is linguistic metadata, not proof that a local
dictionary entry exists. Every segment also has `dictionary_key` and
`dictionary_definition_en`. For every `word` or `idiom`, you MUST link its
lemma whenever that exact canonical headword and lemma reading exist locally;
do not leave a safe available lexical link blank. Write a concise agent-chosen
contextual English sense in `dictionary_definition_en`; the app shows it
before the dictionary's broader senses, and it need not copy a raw `d` string.
Set both fields to empty only for
punctuation, particles, auxiliaries, grammar units, names without an exact
entry, and genuinely absent or ambiguous lexical entries. In particular,
copular だ and quotative/conditional と are
grammar and MUST NOT link to unrelated lexical homographs such as 戸 `door`.
For an ordinary lexical word, link only after verifying an EXACT canonical key
in `app/assets/dictionary_ja.json`: the entry's `w` equals the key, `a` is not
true, and `p` exactly equals lemma_kana. A kana lemma may point to a written
canonical headword with that reading—for example contextual ある may choose
在る, and animate いる may choose 居る—but compare the candidate senses and
choose the correct homophone, never a reading alias. Put that key and the agent-authored
contextual sense in the two fields. Otherwise leave both empty. Never rely on reading aliases or
automatic deinflection for an authored link. Thus 捕まえて may link via
lemma/key 捕まえる when its verified sense is correct, while a grammar-only
piece stays visibly non-clickable.
For determiner ある meaning `a certain` (as in ある日), this local dictionary's
exact canonical head is 或 with reading ある. Use lemma/key 或, type `word`,
part of speech `determiner`, and form `non-inflecting`; never substitute the
existential verbs 在る/有る and never invent the unavailable headword 或る.
Local-dictionary warning for this chapter: hunger すく has no canonical entry
with reading すく. The available 空く entry is read あく, so it is NOT a valid
target for お腹がすいて. Keep lemma すく and leave that link empty; do not alter
the linguistic lemma or reading to manufacture a link.

FORM DERIVATION: every segment has `form_steps`. Use [] for a genuinely
non-inflecting word or functional segment. For every inflected word, give an
ordered learner-facing chain after the dictionary form and end with the exact
surface and surface reading. The UI already displays `lemma` as the first row,
so NEVER repeat the lemma itself inside `form_steps`: for 怖い -> 怖くない ->
怖くなかった, the array contains 怖くない (`plain negative`) and
怖くなかった (`plain negative past`); for ある -> あります -> ありました,
it contains あります (`polite`) and ありました (`polite past`); for 乗せる ->
乗せられる -> 乗せられて, it contains 乗せられる (`passive`) and
乗せられて (`passive て-form`). Include every meaningful intermediate
operation, not just the remote lemma and final label. Each step needs the exact
Japanese form, kana reading, concise form label, and contextual English
meaning. Do not list analyzer stems that are not useful stages.
For a whole construction predicate, include its useful compositional stages too:
置く -> 置いて -> 置いてやる -> 置いてやれ, and 歩く -> 歩いて ->
歩いて行く. The content dictionary lemma remains the first UI row; the final
step must still be the exact surface.
One intentional exception is a derivational grammar lemma ending in たくなる.
The useful chain begins before that construction lemma, so show every stage:
する -> したい -> したくなる -> したくなり. For such a primary,
`form_steps` contains したい, したくなる, and the exact final surface. The
たくなる lemma appearing once inside this special chain is required, not a
redundant duplicate.
For adjective-change grammar, the exact Aく and なる composition belongs in the
required roots overlay. Do not duplicate those pre-lemma parts inside
`form_steps`: 悪くなった has displayed lemma 悪くなる followed by final step
悪くなった, while unchanged 眠くなる uses [].
This requirement also applies to inflected `grammar` primaries: 速くなる ->
速くなります -> 速くなりました and だ -> だった must never arrive with an
empty chain. Preserve the connective stage before も: 出す -> 出される ->
出されて -> 出されても. For a polite negative use the learner-facing polite
path 出る -> 出ます -> 出ません, not 出ない -> 出ません.

Every segment also has `grammar_candidate_key`. Put a provisional lowercase
key there when that PRIMARY card is itself a conventional grammar unit or a
noteworthy inflected pattern that may later link to a shared grammar lesson;
for example のか -> `embedded-question-no-ka`, にとって -> `ni-totte`, and
のだろう -> `explanatory-no-darou`. Every segment typed `grammar` must have a
key. Use an empty string when the primary card does not itself carry a grammar
point, and always use an empty string for punctuation. This primary key is the
metadata home for a standalone learned unit: never manufacture a parts overlay
just to attach a key.
For standalone reporting/naming という, keep the whole learned construction as
both surface and lemma (reading という), with a contextual meaning, form label,
and `to-iu`-style key. Do not reduce the grammar card to bare lexical 言う.

Resolve lexical context rather than trusting a generic first gloss. In this
chapter, 体の善い泥棒 describes a physically healthy/well-built thief,
never a respectable or good-natured one; hiragana とる used for catching mice
has dictionary lemma 捕る, not generic 取る or an unchanged hiragana lemma.
Also, 目の見えない猫 means a blind cat / a cat unable to see; do not replace
that contextual meaning with the literal but misleading idea that its eyes are
not visible.
In 別に怖いとも思わなかった, gloss the individual 怖い card as `scary;
frightening` because that is the quoted predicate, while a whole
怖い + とも思わなかった overlay may naturally explain the narrator `was not
particularly afraid`.
When independent ない appears as connective なく after a noun and が, add a
compact Nがなく overlay. Its canonical head is Nがない and its explanation
must explicitly show Nがない -> Nがなく、: なく is the connective く-form that
links this clause to the following one. Keep N, が, and the lexical なく card
separately accessible; the なく card itself should say `connective form of ない`,
not merely `absent`.
In 足を悪くし, keep the contextual injury collocation as one `idiom` with
lemma 足を悪くする and a matching parts overlay.
For any inflection of the fixed collocation 目が回る, keep searchable primaries
目 + が + the COMPLETE 回る form and overlay that full contextual surface. For
example, 目が回りました requires an overlay over all of 目が回りました, never the
truncated stem 目が回り; its head is the canonical collocation 目が回る.
If TEXT itself contains conjunctive 目が回り followed by punctuation, however,
that exact 目が回り surface is the complete form present and is a valid overlay;
never demand an absent dictionary-form 目が回る in its place.

A written conjunctive 連用形 used directly before punctuation is already the
complete form present in TEXT: for example 落ち着き、 is segmented as 落ち着き
(lemma 落ち着く, form `conjunctive ren'yōkei`) plus punctuation. Do not absorb
the comma, call it an accidental detached stem, or rewrite fixed TEXT to
落ち着いて. The same applies to forms such as 入り、 and 聞き、.

FORM/GRAMMAR LAYER: add an overlay only for a learned MULTI-PART construction,
collocation, or idiomatic chain whose combined meaning is not adequately
recoverable from the learner-sized primary segments and their lemma/form
metadata. Consider benefactive, conditional, nontrivial quoted/reported, obligation,
modality, aspectual, and literary constructions, but do not mechanically add
an overlay merely because one complete word is past, negative, passive,
causative, potential, polite, or in a て/で-form. For example, 思わなかった
is already one tap target with lemma 思う and form `plain negative past`; it
does not need an artificial 思わ + なかった overlay. Likewise, ありました
needs lemma ある and form `polite past` on its primary card, not an overlay.
Do explain beginner constructions such as Vることにする as the conventional unit
learners memorize. Likewise keep Vた + ことがある / ことはない as useful
primary targets and add a whole overlay for the experience construction; do
not collapse the lexical predicate into one opaque grammar row. Keep
explanatory-conjectural のだろう together rather than の + だろう. Bound most
overlays to the predicate, excluding free arguments and following clauses. A
complete という primary card can explain reported speech by itself; do not wrap
an entire reported narrative such as 捕まえて煮て食うという in one decorative
overlay. かもしれない is real intervening modality: a sequence such as
Vかもしれないと思う does not instantiate the separate Vかと思う construction.
Never delete or jump across もしれない to invent a non-contiguous
Vかと思う overlay. Likewise, temporal あとで/後で is already one useful primary card and
must not receive an あと + で overlay. A
standalone grammar primary such as である, のか, にとって, のだろう, はずの,
というもの, とすると, ことにしました, そうでもない, or のようだった
must carry its own `grammar_candidate_key` and must not receive a decorative
overlay that merely splits that same surface. A
true lexical collocation may include its fixed argument: お腹がすいて must have
an overlay whose head lemma is the complete learned expression お腹がすく,
explaining both the collocation and the connective て-form. Bare すく remains
the lemma of its internal inflected verb component, not the title of the whole
collocation card. Every multi-part primary segment typed `idiom` must have one matching
overlay that exposes its meaningful lexical parts; this is how a learner opens
the whole expression first and then its internal dictionary entries. A
collocation used as one primary segment has type `idiom`, its
dictionary phrase as lemma, and the complete contextual surface meaning; for
example 胸が悪くなり -> lemma 胸が悪くなる. A predicative na-adjective or
copular descriptive expression may
include its copula in the surface but keeps the adjective lemma: だめだ ->
lemma だめ. Keep both attributive and predicative na-adjective surfaces whole:
我儘な -> lemma 我儘 and 一生懸命だった -> lemma 一生懸命, never 我儘 + な or
一生懸命 + だった. Likewise keep routine Vている/Vでいる forms as one primary
verb target with the content verb lemma (休んでいる -> 休む), and keep
aspectual compound verbs together (写生し始めた -> 写生し始める), even when an
overlay may additionally expose useful internal pieces. A productive adjective construction such as
速くなりました or 速くした is also ONE `grammar` primary (lemma 速くなる or
速くする), not tokenizer rows 速く + なりました/した and not a fake lexical
dictionary word. Its required overlay exposes the linked adjective root 速い
and lexical verb root なる/する; the complete primary itself has no dictionary
link.
Keep directional Vてくる whole too, but retain the content-verb dictionary root on the primary
card and explain 来る in the overlay (飛び出してくる -> 飛び出す). Treat そうでもない as
one conventional grammar unit, not そう + で + も + ない. In a negative list such as XもYもいません, gloss も contextually
as `neither; not ... either`, not merely `also`. Keep 誰も and 何も as
whole conventional indefinite-expression taps; before a negative predicate,
gloss the whole card `no one` or `nothing`, never `who/anyone` + `even/also`. For a
lexical head followed by a conventional grammar unit, keep both useful primary
targets: 生まれた + のか and 人間 + というもの, not one opaque
生まれたのか or 人間というもの row. Keep naming といい together rather than
と + いい. Keep copular quotative nominalization だということ as one grammar
unit and leave a following case particle を outside it. In an appearance form, retain the content adjective as the primary
lemma: 気持ちよさそうに -> 気持ちよい, with そうに explained as the form.
Every content-word そう appearance form also needs a matching overlay with a
candidate grammar key, so later processing can unify examples such as
不満そうだった, 気持ちよさそうに, and 強そうだった into one lesson.
When past copula だった is immediately followed by evidential らしい, keep their
primary cards accessible but add a だったらしい overlay with components
だった + らしい. Explain that らしい is not another conjugation of だった: it
attaches to the whole past proposition and marks it as reported or inferred,
giving `apparently/seems to have been ...`.
For a comma-joined numeral range such as 三、四十匹, add an overlay that tells
the learner the combined meaning is `thirty or forty animals`; isolated 三 and
四十 dictionary values do not explain the notation. Use learner-sized components
三 + 四十匹, skipping the comma rather than making punctuation its own component,
and explain that 三 is the shortened first tens value here. When the counter is
present, keep only that full overlay; do not add a redundant nested 三、四 overlay.
For a benefactive chain, keep the entire predicate as one primary and also
include the content verb before て in its overlay: for example
付けてもらえなかった is one primary plus one overlay, with useful components
付けて + もらえなかった. Likewise, 置いてやれ is one primary with components
置いて + やれ. For directional/aspectual motion, 歩いて行く is one primary
with components 歩いて + 行く and a whole overlay explaining the contribution
of 行く. A reader must be able to
understand forms such as 出られなくて as a whole while still opening useful
subparts. Preserve grammar overlays for reusable Vずに and Vたくなる forms.
In this chapter, also preserve whole contextual overlays for 目の見えない and
小便がしたくなり, and for sickness 胸が悪くなり, so their combined meanings
and parts are available. These are overlay spans, not opaque primary rows:
keep 目 + の + 見えない, 小便 + が + したくなり, and 胸 + が + 悪くなり as
the searchable primary segmentation. Do not
add an overlay solely for a regular nonpast verb. Use exact
Python character offsets into TEXT (end exclusive). Each overlay
gives a provisional lowercase
grammar_candidate_key, pattern, contextual meaning, canonical whole-construction
head form and reading, learner-facing form label, and concise explanation. The
overlay head is not required to be a standalone dictionary entry: it may combine
a lexical dictionary lemma with attached grammar. Canonicalize inflection while
retaining that grammar, for example surface 生まれたか -> head_lemma 生まれるか
and head_lemma_kana うまれるか. Never use the inflected surface 生まれたか as
the head, and never reject 生まれるか merely because a dictionary would list
生まれる and か separately. Its components
use offsets relative to the overlay, cover the entire overlay consecutively,
and show the lemma and function of PEDAGOGICAL parts, not every conjugational
morpheme. Prefer 歩く + ことにしました, not 歩く + こと + に + し + まし + た;
inside any larger overlay, preserve the same conventional units used by the
primary segmentation: for example 見た + のは, never 見た + の + は.
prefer 付けて + もらえなかった when those are the two useful learned pieces.
Likewise prefer 来る + かと思い for 来るかと思い when teaching the
reusable grammar point かと思う; never dump 来る + か + と + 思い as
four atomic pieces. For Vずにいる, keep the learned negative connective in
one component: 動かずに + いた, never 動かず + にいた.
Every overlay must have at least two useful components; a one-component overlay
adds nothing beyond the main segment. Nested components may expose an exact
surface stem when that is genuinely helpful inside a larger construction card;
that does not split the primary tap target. Do not invent an overlay only to
expose a routine inflection: the primary card already provides its dictionary
form and complete form label. Do not create elaborate overlays
for obvious standalone particles merely to fill the array. Zero overlays is
valid for a chunk with no learned multi-part construction. At N5, predicate +
conditional/temporal と is itself a precise textbook construction, so include
its compact V + と overlay. Do not create the same kind of overlay for quotation
と, routine ている form, or an entire clause. Never include a following subject, a free object or
locative, or unrelated surrounding words merely to force two components. The
following are concrete overlays to omit: bare と言った/と名乗った (the primary
と and reporting verb already explain them), 生活である (である is already its
own learned primary unit), 寝ている猫 (it absorbs a free noun), and
もう待てなかった (it merely absorbs an ordinary adverb). By contrast,
広くはない and 眠くなる deserve compact overlays because Aくはない and
Aくなる are reusable patterns across legitimate primary units. The parts
of a productive compound verb are likewise pedagogically useful, not
decorative: explain 動き出す as 動く + 出す, including the contribution
of 出す (`begin/suddenly start V`), and do the same for aspectual compounds
such as V始める. Keep the compound as one primary tap target and use its
overlay to expose the lexical roots. Every overlay component also has
`lookup_kind` (`lexical`, `grammar`, or `none`), `dictionary_key`, and
`dictionary_definition_en`; verify lexical roots against
the canonical local dictionary rules above, and leave both empty on
grammar-only parts such as だ, と, て, or ことにしました. The
key is only a
future clustering candidate; describe the grammar accurately even if another
agent might later choose a different key.{repair}

REVIEWED CHAPTER STORY-VOCABULARY PLAN:
{json.dumps(story_plan, ensure_ascii=False, indent=2)}

TEXT:\n{chunk}"""
        return await self.runner.call(
            f"annotations/chunk_{index:04d}/{stage}", prompt,
            SCHEMAS / "japanese-annotation.schema.json",
            effort or self.args.annotation_effort,
            refresh=self.refresh_annotation_chunk(index),
        )

    async def review_annotation(self, index: int, chunk: str, annotation: dict[str, Any], stage: str) -> dict[str, Any]:
        story_plan = getattr(self, "story_vocabulary_plan", {"terms": []})
        prompt = f"""Return only JSON matching the supplied schema. Independently
audit this Japanese learner annotation. Segment text must concatenate exactly
to TEXT. Judge boundaries as a language teacher designing tap targets, not as a
morphological tokenizer. A complete inflected word stays whole: require
ありました -> lemma ある, すいて -> lemma すく, and 食べませんでした -> lemma
食べる. Reject fragments such as あり / まし / た or すい / て. Conventional
lexical verb and adjective forms remain type `word`; passive, progressive,
polite, negative, or a following polite copula does not turn 捨てる or 痛い
into a grammar-only entry. Conventional
grammar chunks such as ことにしました may be one `grammar` segment with lemma
ことにする. Require Vた + ことがある / ことはない with a whole experience
overlay, and keep explanatory のだろう as one grammar unit. Conditional or
temporal と after a predicate must be a keyed `grammar` segment with a compact
V + と overlay; do not confuse it with quotative or comitative と. Do not group an
arbitrary predicate or clause as one dictionary
word. Keep names and genuine fixed idioms/collocations together. Keep a
conventional mimetic + する predicate whole: require ぐるぐるして -> lemma
ぐるぐるする with a ぐるぐる + して component overlay, never two primary taps.
Require a manner adverb/mimetic plus conventional adverbial と to be one primary
target with a same-surface parts overlay: ふわりと, not unrelated ふわり + と
rows. The と component is grammar-only and must explain how it attaches the
manner expression to the predicate rather than duplicate the full manner gloss.
benefactive predicate whole from its content verb through its helper:
置いてやれ -> lemma 置く and 付けてもらえなかった -> lemma 付ける, never
置いて + やれ or 付けて + もらえなかった as separate primary taps. Keep
directional/aspectual Vて行く/Vて来る whole when the second verb contributes
direction, viewpoint, or unfolding aspect: 歩いて行く -> lemma 歩く. Preserve
separate actions when they are genuinely sequential, such as 来て、見た or
食べてから行く.
predicative descriptive form such as だめだ whole with lemma だめ, and expose
its lexical adjective plus grammar-only copula through an overlay when those
parts are useful; do not detach だ as a misleading lexical tap. Likewise keep
a deliberately grouped productive or collocational form whole and expose its
roots through the overlay rather than splitting it merely to make roots
clickable. In particular, keep Aくなる/Aくする forms such as 速くなりました
and 速くした as one `grammar` primary, with linked roots 速い + なる/する in
the overlay; do not demand lexical dictionary metadata for the whole
construction. Lemmas and lemma readings
must be valid dictionary forms; surface readings must match the inflected text.
For PRIMARY segments, that means an ordinary lexical dictionary lemma or the
canonical learned grammar unit described above. For an OVERLAY, `head_lemma`
instead means the canonical whole construction and need not be a standalone
dictionary entry: surface 生まれたか correctly has head 生まれるか. Do not
demand inflected 生まれたか and do not reject canonical 生まれるか for
combining a lexical lemma with attached grammar.
Vながら is the deliberate learner-facing exception: surface 見ながら has
primary lemma 見る and overlay head_lemma 見る (head_lemma_kana みる), because
the UI renders this field as “From …” and 見るながら is not natural Japanese.
The overlay pattern and title already identify Vながら; do not use either the
realized stem surface 見ながら or the artificial form 見るながら as its head.
For 目が回る, require the overlay to include the complete form actually present
in TEXT (for example 目が回りました), not a conjunctive-stem prefix such as
目が回り.
When TEXT itself is the conjunctive clause 目が回り、, the complete form actually
present is exactly 目が回り; accept that overlay and do not rewrite source text.
For determiner ある meaning `a certain`, accept and require this local
dictionary's exact canonical lemma/key 或 (reading ある). Do not demand the
unavailable spelling 或る or reinterpret it as existential ある.
Part of speech and conjugation labels must explain the actual surface form, and
meanings must explain each segment rather than paraphrase a sentence. Check
every `dictionary_key` against the exact canonical local entry in
`app/assets/dictionary_ja.json`: its `w` must equal the key, `a` must not be
true, `p` must equal lemma_kana, and `dictionary_definition_en` must be one
precise agent-authored contextual sense; it need not copy a raw `d` string.
Reject that label only when its meaning is wrong in context. A kana lemma may
use a written canonical headword with the same reading, such as contextual
ある -> 在る or animate いる -> 居る, but only after comparing the candidate
senses and choosing the correct lexical homophone. Never use a reading alias,
and leave the link empty when there is no local candidate rather than inventing
one. Particles,
auxiliaries, grammar-only units, copular だ,
quotative/conditional と, and uncertain homographs must leave both dictionary
fields empty; a missing link is correct when the alternative is a wrong entry.
The link label is agent-authored for this occurrence, while the dictionary
sheet still includes every raw local sense. Judge that label contextually.
For each inflected word, audit the ordered `form_steps`: it begins after the
dictionary lemma, includes every meaningful intermediate grammatical form,
never repeats the lemma as a step, and ends with the exact surface/reading.
The lemma is already a separate UI row. A simple one-operation form such as
すく -> すいて or 明るい -> 明るく correctly has only the final surface inside
`form_steps`; do not demand that すく or 明るい be duplicated there. Likewise
an unchanged plain form such as 開ける has [].
Require 怖い -> 怖くない ->
怖くなかった, ある -> あります -> ありました, and 乗せる ->
乗せられる -> 乗せられて rather than a jump from lemma to final
label. Apply the same derivation requirement to inflected `grammar` primaries;
速くなりました and だった cannot have empty chains. Require the polite
nonpast before ました/ません, and require the connective て/で-form before も:
出す -> 出される -> 出されて -> 出されても. For whole construction predicates require the useful stages too:
The sole pre-lemma derivation exception is grammar lemma Vたくなる: require
Vたい -> Vたくなる -> the exact surface (for example したい -> したくなる
-> したくなり). Do not reject the intentional Vたくなる stage as a repeated
lemma; it explains how the desiderative becomes a change-of-state construction.
Do not apply that exception to ordinary Aくなる. Its Aく + なる composition is
already required in the roots overlay; `form_steps` begins after the displayed
construction lemma, so 悪くなった needs only the exact final past step and an
unchanged 眠くなる uses [].
For a polite negative past verb, require いる -> います -> いません ->
いませんでした (or the corresponding lexical verb); never insert copular です
into a verb derivation.
置く -> 置いて -> 置いてやる -> 置いてやれ and 歩く -> 歩いて ->
歩いて行く. Non-inflecting and functional segments use an empty chain.
Check
the chapter-specific lexical traps too: 体の善い泥棒 means a physically
healthy/well-built thief, not a respectable or good-natured thief, and とる
meaning `catch` mice has dictionary lemma 捕る. Check
the local-dictionary trap お腹がすいて too: its whole-overlay head form is
お腹がすく, while the internal verb lemma is すく. The local
空く entry is read あく, not すく, so neither the primary nor its component may
link to 空く. A deliberately empty link is correct here. Check
目の見えない猫 as `a blind cat / a cat unable to see`, not merely a cat whose
eyes are not visible. Check
足を悪くし as the contextual injury collocation 足を悪くする, not three ordinary
rows. Check
context before splitting a surface that can be either compositional or a
dictionary conjunction: in そこで初めて..., そこで can be the single discourse
word `thereupon/so`, while a genuinely locative そこで can be そこ + で.
When 誰も or 何も occurs with a negative predicate, require one whole
conventional tap target whose contextual gloss says `no one` or `nothing`;
analyzer-style 誰 + も / 何 + も rows are not sufficient for a tap reader.
Require lexical-head + grammar-unit primary boundaries such as 生まれた + のか
and 人間 + というもの, keep naming といい together, and require appearance
forms such as 気持ちよさそうに to retain the content adjective 気持ちよい as
primary lemma rather than auxiliary-only そうだ, and require a matching
appearance-grammar overlay for every such content-word そう form. Keep だということ together
and never absorb its following を. A comma-joined range such as
三、四十匹 needs an overlay explicitly explaining `thirty or forty animals`,
with learner-sized components 三 + 四十匹 that skip the comma.
Require reusable Vずに and Vたくなる grammar overlays, plus contextual whole
overlays for 目の見えない, 小便がしたくなり, and 胸が悪くなり in this chapter.
Require Nがなく to expose N + が + connective なく in a compact overlay whose
head is Nがない; explicitly identify なく as the connective く-form of ない.
Require a だったらしい overlay with components だった + らしい and explain
that evidential らしい scopes over the past proposition rather than being a
conjugation of だった.
Keep their primary rows as 目 + の + 見えない, 小便 + が + したくなり, and
胸 + が + 悪くなり; the overlay,
not an opaque main segment, carries the whole contextual meaning.
Check
story-role judgments against the reviewed plan below. Reject any `story_term`
whose lemma is absent from the plan, including an ordinary word such as 本 just
because it participates in the plot. Require a whole-form grammar overlay when
a MULTI-PART construction has learner-useful meaning not obvious from its
pieces, especially benefactive, conditional, modal, nontrivial quoted/reported,
obligation, aspectual, or literary constructions. A complete inflected word
already annotated with its dictionary lemma and precise form does not require
a second overlay merely because it is past, negative, passive, causative,
potential, polite, or a て/で-form. Do not force an artificial stem/suffix
decomposition such as 思わ + なかった just to create an overlay. Most
construction overlays must be predicate-sized,
without free arguments or following clauses; a genuine collocation such as
お腹がすく may include its fixed noun and particle. Require every multi-part
primary `idiom` to have a matching component overlay. Benefactive overlays must
begin with the content verb before て, and the primary itself must also remain
whole: require 付けてもらえなかった with 付けて + もらえなかった, and
置いてやれ with 置いて + やれ. Directional/aspectual 歩いて行く likewise
needs a whole primary plus a 歩いて + 行く overlay.
Overlay components must be useful learned chunks, not a dump of every stem and
suffix: require 歩く + ことにしました rather than six atomic components. Do not
accept 来る + か + と + 思い for 来るかと思い; prefer the pedagogical
pieces 来る + かと思い for grammar point かと思う. Likewise require
動かずに + いた, never 動かず + にいた, for Vずにいる. Do not
leave a productive compound opaque: 動き出す needs a useful component
overlay 動く + 出す explaining how 出す marks beginning/sudden onset, and
analogous aspectual compounds need their lexical roots. Each lexical component
uses `lookup_kind: lexical` and may link only through its verified canonical
dictionary key plus an agent-authored contextual sense; grammar components use
`lookup_kind: grammar` and have empty dictionary fields. Do not
accept a one-component overlay; split it into two or more meaningful learned
pieces, or remove the redundant overlay if no such decomposition is useful. Do
not reject an exact nested stem component inside a genuinely useful larger
construction merely because it is not a primary tap target. Overlay start/end
are absolute offsets into TEXT; component
start/end are relative to the overlay surface. Python has already verified both,
so do not report contrary offset arithmetic.
Do not reject an annotation merely because a complete inflected word lacks a
redundant overlay.
Reject split V-stem + ながら primaries and require whole Vながら as grammar
with the content verb lemma plus a roots overlay. Reject lexical Vて + atomic
auxiliary みる for productive `try doing`; require one Vてみる grammar primary.
A following conditional と remains separate, although a wider Vてみると
overlay may expose Vて + みる + と and explain their combined effect.
Require compact predicate + conditional/temporal と overlays at N5. Reject
decorative overlays for quotation と, basic ている, or obvious standalone
particles when the primary card already explains the form.
Bare と + 言う/名乗る is specifically not a nontrivial reported construction.
Reject any overlay that absorbs a following subject, free object/locative, or
unrelated clause material. Specifically reject bare と言った/と名乗った,
生活である, 寝ている猫, and もう待てなかった as redundant or over-wide; require
an Aくはない overlay for a contrastive adjective negative such as 広くはない,
and an Aくなる overlay for an adjective-change form such as 眠くなる.
Zero overlays is valid. Overlay offsets and surfaces
must be exact. List
every concrete learner-harming issue and pass only if none remains.

REVIEWED CHAPTER STORY-VOCABULARY PLAN:
{json.dumps(story_plan, ensure_ascii=False, indent=2)}

TEXT:\n{chunk}\n\nANNOTATION:\n{json.dumps(annotation, ensure_ascii=False, indent=2)}"""
        boundary_prompt = f"""Return only JSON matching the supplied schema.
You are the second, specialized segmentation editor for a Japanese tap reader.
Ignore grammar-overlay offset arithmetic and story-vocabulary selection; another
review handles those. Inspect every SEGMENT boundary, lemma, reading, type, and
full-surface form label.

Do not review `grammar_overlays`, their `head_lemma`, their components, or their
offsets in this specialized pass. In particular, do not demand that an overlay
head copy an inflected surface. The general annotation reviewer owns overlay
semantics; your remit here is the primary `segments` array.

Also audit the full derivation, not only the final label. Every inflected
lexical or construction-sized grammar segment needs ordered `form_steps` ending in its exact surface and
reading, with all meaningful intermediate forms. The dictionary lemma is
already displayed before the array and MUST NOT be repeated inside it: for
怖い -> 怖くない -> 怖くなかった, `form_steps` contains 怖くない and
怖くなかった; for ある -> あります -> ありました, it contains あります and
ありました; for 乗せる -> 乗せられる -> 乗せられて, it contains
乗せられる and 乗せられて. A simple one-operation form such as すく ->
すいて has only すいて; an unchanged plain form such as 開ける uses []. Reject
a direct leap that hides passive, causative,
negative, polite, aspectual, or tense stages. Non-inflecting and functional
segments use []. Verify `dictionary_key` only for lexical entries: it must be
an exact canonical key in `app/assets/dictionary_ja.json`, have the same
reading, and provide a contextually correct agent-authored definition. For a
kana lemma, a written canonical key with the same reading is valid only when
its lexical sense matches this occurrence (for example animate いる -> 居る);
reject unrelated homophones. No link is correct when the local dictionary has
no suitable candidate, and an agent must never invent one.
Exception: for a construction-sized grammar lemma ending in たくなる, the
pre-lemma desiderative derivation is learner-essential. Require Vたい ->
Vたくなる -> exact surface, such as したい -> したくなる -> したくなり;
the construction lemma intentionally appears once inside that chain.
For ordinary adjective-change Aくなる, do the opposite: Aく and なる belong
in the roots overlay. Do not demand Aく inside `form_steps`; 悪くなった needs
only its exact final past step after displayed lemma 悪くなる, and unchanged
眠くなる uses [].
For determiner ある meaning `a certain`, the verified local entry is lemma/key
或 with reading ある; it is a non-inflecting determiner. Do not demand 或る and
do not link it to existential 在る/有る.
Grammar-only だ,
と, particles, auxiliaries, and uncertain homographs have no link.
The link label is agent-authored for this occurrence, while the dictionary
sheet still includes every raw local sense. Judge that label contextually.

The primary unit is what a learner should tap. Keep complete inflected forms
together: ありました -> ある, すいて -> すく, 戻りました -> 戻る, だった ->
だ. Ordinary inflected lexical verbs/adjectives remain type `word`; grammar
material in the surface does not erase the lexical lookup head. Reject
split mimetic predicates such as ぐるぐる + して; use one ぐるぐるして primary
with lemma ぐるぐるする and expose ぐるぐる + して only in its overlay. Reject
detached stems and endings, including 動かず + に instead of the
complete negative connective 動かずに. Treat learned constructions such as
動かずにいた and したくなり as construction-sized `grammar` primaries with
lemmas 動かずにいる and したくなる plus matching pedagogical overlays; do not
split them into 動かず + に + いた or したく + なり. Treat learned constructions such as
ことにしました -> ことにする as a grammar unit, while leaving the action verb
before it separately accessible. Formal-copula である, temporal 後で,
explanatory/embedded-question のか, and the completing たりする construction
are likewise conventional learner units; do not split them into atomic primary
rows or absorb their lexical head: require 生まれた + のか, 人間 +
というもの, ある + はずの, and naming 書生 + といい.
Likewise require whole Vながら with its content verb as lemma (見ながら ->
見る, not 見 + ながら and not lemma ながら), and whole productive Vてみる as
a grammar primary (出てみる, not 出て + みる). Their roots overlays expose
lexical V plus the attached grammar. If conditional と follows, keep primary
units 出てみる + と; a wider 出てみると overlay may cover both grammar points.
Every `grammar` primary must carry its own lowercase `grammar_candidate_key`.
Conditional/temporal と after a predicate is such an N5 grammar primary and
needs a compact V + と overlay; quotative and comitative と remain particles.
Do not demand a same-surface overlay merely to split a standalone grammar unit;
its key and contextual meaning already live on the primary card. Keep ordinary quotative
と outside a reporting/thought verb (と + 思った), while retaining the learned
かと思う unit. Keep のようだった as one inflected のようだ grammar unit,
not の + よう + だった. A nominal predicate such as 見始めだった must not receive verb
lemma 見始める; expose 見始め and the complete だった copula accurately.
For volitional Vようとすると, retain 行こう + とすると as the two useful
primary units and put the combined construction in an overlay.
Require routine Vている/Vでいる forms to stay whole with the content-verb
lemma (休んでいる -> 休む), aspectual compounds to stay whole
(写生し始めた -> 写生し始める), while requiring a useful roots
overlay for productive compounds such as 動き出す = 動く + 出す and
V始める. The compound remains one primary tap; the overlay explains how
the second verb changes its aspect. Require lexical component dictionary links
only when their exact canonical entries and senses were verified; mark them
`lookup_kind: lexical`. Mark grammar pieces `lookup_kind: grammar` and keep
them deliberately non-clickable. Likewise keep Aくなる/Aくする forms such as
速くなりました and 速くした as one `grammar` primary with lemma 速くなる or
速くする; the overlay exposes and links 速い + なる/する. Do not split them into
tokenizer-like primary rows and do not require a fake whole-form dictionary
link. Require attributive/predicative na-adjectives to include
their な or copula (我儘な -> 我儘; 一生懸命だった -> 一生懸命), and
directional Vてくる/Vていく forms to remain whole with the content root plus an
overlay (飛び出してくる -> 飛び出す; 歩いて行く -> 歩く). Likewise, keep a
benefactive predicate whole through its helper (置いてやれ -> 置く;
付けてもらえなかった -> 付ける), with meaningful form stages and a component
overlay. For 置いてやれ the chain is 置いて -> 置いてやる -> 置いてやれ;
for 歩いて行く it is 歩いて -> 歩いて行く. Do not apply this mechanically to
truly sequential events such as 来て、見た or 食べてから行く. Keep そうでもない as one conventional grammar
unit. Prefer conventional lexical expressions such
as 女の人 and genuine collocations such as お腹がすく, but never merge an
arbitrary noun phrase or clause. A grouped lexical collocation uses type
`idiom` and its dictionary phrase as lemma; a predicative surface such as
だめだ remains one learner-facing primary and keeps adjective lemma だめ;
expose だめ + grammar-only だ in its component overlay rather than detaching
the copula as a misleading lexical tap. Do not split a deliberately grouped
productive or collocational form merely to make its roots clickable; keep the
useful complete primary and expose its roots through the overlay. Proper-name
type is for an actual named
person, animal, or place; for example 黒 can be a cat's name, while common
landscape nouns such as 笹原 are not names. Resolve lemmas
from syntax, not the English gloss alone: in 主人の近く, 近く is the noun 近く
(`vicinity`), while an actual inflected/adverbial use of 近い points to 近い.
Likewise, keep そこで as the dictionary conjunction when it means
`thereupon/so` and type it as a `word`, not an `idiom`; split そこ + で only for
a genuinely locative phrase.
Keep frequency expressions 何度も / 何回も and negative-polarity 一度も as conventional
whole adverbial tap targets. Keep indefinite expressions 誰も and 何も whole
too; in negative scope their whole-card meanings are `no one` and `nothing`.
For an appearance form such as 気持ちよさそうに, keep the complete surface but
use content adjective 気持ちよい as lemma; そうだ is the attached form, not the
lexical lookup head. Reject 何 + も or 誰 + も atomic rows: use the whole
何も / 誰も conventional tap, contextually glossed `nothing` / `no one`
when it scopes over a negative predicate.
In this chapter, 体の善い泥棒 means a physically healthy/well-built thief,
not a respectable or good-natured thief, and hiragana とる meaning `catch`
mice has dictionary lemma 捕る.
For hunger お腹がすいて, keep internal verb lemma すく and whole-overlay head
form お腹がすく. The local 空く headword is read あく,
not すく, and must not be used as a dictionary key; an empty link is correct.
Likewise, 目の見えない猫 means a blind cat / a cat unable to see, not merely
that the cat's eyes are visually hidden.
Keep contextual whole help for 目の見えない and 小便がしたくなり in overlays,
and for 胸が悪くなり, while primary taps remain 目 + の + 見えない,
小便 + が + したくなり, and 胸 + が + 悪くなり.
A conjunctive 連用形 immediately before punctuation, such as 落ち着き、 or
入り、, is a complete contextual verb form with its dictionary lemma. Do not
require it to absorb punctuation or change the immutable text to a て-form.
In the auxiliary construction Vてみる (`try doing V`), the auxiliary is
conventionally written and lemmatized みる in kana. Do not replace it with the
lexical visual verb 見る merely because the two share a reading.
Report every learner-harming boundary or metadata error and pass only if all
segments are useful and correct.

TEXT:\n{chunk}\n\nANNOTATION:\n{json.dumps(annotation, ensure_ascii=False, indent=2)}"""
        general_review, boundary_review = await asyncio.gather(
            self.runner.call(
                f"annotations/chunk_{index:04d}/{stage}_review", prompt,
                SCHEMAS / "japanese-annotation-review.schema.json",
                self.args.annotation_review_effort,
                refresh=self.refresh_annotation_chunk(index),
            ),
            self.runner.call(
                f"annotations/chunk_{index:04d}/{stage}_boundary_review",
                boundary_prompt,
                SCHEMAS / "japanese-annotation-review.schema.json",
                self.args.annotation_review_effort,
                refresh=self.refresh_annotation_chunk(index),
            ),
        )
        reviewed = [
            apply_reader_useful_annotation_review_policy(
                general_review, annotation
            ),
            apply_reader_useful_annotation_review_policy(
                boundary_review, annotation
            ),
        ]
        combined: list[dict[str, Any]] = []
        seen: set[str] = set()
        for review in reviewed:
            for issue in review.get("issues", []):
                key = json.dumps(issue, ensure_ascii=False, sort_keys=True)
                if key not in seen:
                    seen.add(key)
                    combined.append(issue)
        review = {"verdict": "revise" if combined else "pass", "issues": combined}
        overlays = {
            str(overlay.get("surface", "")): overlay
            for overlay in annotation.get("grammar_overlays", [])
            if isinstance(overlay, dict)
        }
        primary_segments = {
            str(segment.get("surface", "")): segment
            for segment in annotation.get("segments", [])
            if isinstance(segment, dict)
            and segment.get("type") != "punctuation"
        }
        primary_surfaces = set(primary_segments)
        retained: list[dict[str, Any]] = []
        for issue in review.get("issues", []):
            surface = str(issue.get("segment_text", ""))
            overlay = overlays.get(surface, {})
            overlay_detail = " ".join(str(overlay.get(field, "")) for field in (
                "form_label", "explanation_en", "pattern",
            )).casefold()
            issue_detail = " ".join(str(issue.get(field, "")) for field in (
                "explanation", "suggested_fix",
            )).casefold()
            primary_segment = primary_segments.get(surface, {})
            if not primary_segment and " (overlay component)" in surface:
                primary_segment = primary_segments.get(
                    surface.split(" (overlay component)", 1)[0], {}
                )
            if (
                primary_segment.get("type") == "grammar"
                and (
                    "nagara" in str(
                        primary_segment.get("grammar_candidate_key", "")
                    ).casefold()
                    or "ながら" in str(
                        primary_segment.get("grammar_candidate_key", "")
                    )
                )
                and overlay
                and overlay.get("head_lemma") == primary_segment.get("lemma")
                and overlay.get("head_lemma_kana")
                == primary_segment.get("lemma_kana")
                and any(marker in issue_detail for marker in (
                    "overlay head", "head_lemma", "head lemma",
                ))
            ):
                # The UI labels this value "From". For Vながら, the natural
                # learner-facing source is 見る, while the overlay title and
                # pattern already carry 見ながら / Vながら. Never let a
                # reviewer reintroduce artificial 見るながら here.
                continue
            if (
                issue.get("problem") in {
                    "meaning", "lemma", "lemma_reading", "wrong_type",
                    "grammar_components",
                }
                and primary_segment
                and "dictionary" in issue_detail
                and any(marker in issue_detail for marker in (
                    "no dictionary link", "without dictionary", "left without",
                    "dictionary_key is empty", "dictionary key is empty",
                    "dictionary_key is blank", "dictionary key is blank",
                    "omit dictionary", "should be linked", "must be linked",
                    "add dictionary", "add the key", "set dictionary_key",
                    "set the dictionary", "provide the exact", "provide its exact",
                ))
                and candidate_for_lemma(
                    str(primary_segment.get("lemma", "")),
                    str(primary_segment.get("lemma_kana", "")),
                ) is None
            ):
                # Reviewers sometimes infer a kana or literary headword that
                # is absent from the local dictionary. The deterministic
                # candidate authority wins: a wrong homophone link is worse
                # than a deliberately non-clickable occurrence.
                continue
            if (
                issue.get("problem") in {"grammar", "conjugation"}
                and primary_segment
                and "form_steps" in issue_detail
                and "plain nonpast" in issue_detail
                and str(primary_segment.get("lemma", "")) in issue_detail
                and not japanese_form_step_issues(primary_segment)
            ):
                # The lemma is already a separate row above the derivation.
                # A reviewer occasionally asks to repeat that plain-nonpast
                # lemma as the first form step even though the deterministic
                # form-chain contract has confirmed that the visible chain is
                # complete. Repeating it adds no transformation for learners.
                continue
            if (
                issue.get("problem") == "story_role"
                and primary_segment.get("type") == "name"
                and primary_segment.get("story_role") == "name"
                and "absent" in issue_detail
                and "plan" in issue_detail
            ):
                # Proper names have their own role and are intentionally not
                # required to consume the curated story-vocabulary budget.
                # A reviewer must not reinterpret `name` as an unplanned
                # `story_term` and strip names such as the cat 黒.
                continue
            if (
                issue.get("problem") == "grammar"
                and surface in _REQUIRED_CONTEXTUAL_OVERLAY_SURFACES
                and surface in overlays
                and any(marker in issue_detail for marker in (
                    "redundant", "basic ている", "routine ている",
                    "already explained by the primary",
                ))
            ):
                # Some contextual expressions deliberately retain a compact
                # whole overlay even though their verb inflection is routine.
                # The overlay teaches the lexical expression (for example
                # じっとする), while its components preserve normal word taps.
                continue
            if (
                issue.get("problem") == "lemma"
                and "ている" in str(
                    primary_segment.get("conjugation_form", "")
                )
                and str(primary_segment.get("lemma", ""))
                and "split" in issue_detail
                and "auxiliary いる" in issue_detail
            ):
                # A whole form such as 残っている correctly points to the
                # lexical dictionary root 残る and labels the ている state on
                # the same card. Requiring separate primary rows for 残って and
                # いる would undo the learner-sized whole-form policy.
                continue
            if (
                issue.get("problem") == "lemma"
                and surface.endswith(("てくる", "でくる"))
                and surface in overlays
                and str(primary_segment.get("lemma", ""))
                and any(marker in issue_detail for marker in (
                    "directional 来る", "vてくる", "constructional meaning",
                    "complete vてくる construction",
                ))
                and any(marker in issue_detail for marker in (
                    "construction lemma", "lemma", "type `grammar`",
                    "full-surface form",
                ))
            ):
                # A whole Vてくる tap target points to the lexical content
                # verb; the directional/deictic 来る is explained by its form
                # label and reviewed overlay.  Replacing that useful root with
                # the inflected surface is not a dictionary-lemma improvement.
                continue
            if (
                issue.get("problem") == "over_grouped"
                and "na-adjective" in str(
                    primary_segment.get("part_of_speech", "")
                ).casefold()
                and str(primary_segment.get("lemma", ""))
                and any(surface.endswith(ending) for ending in (
                    "だ", "だった", "です", "でした", "ではない", "ではなかった",
                ))
                and any(marker in issue_detail for marker in (
                    "copula", "predicative", "だった", "でした",
                ))
            ):
                # In this reader the copula belongs to the complete contextual
                # na-adjective tap target, just as だめだ and 不人望だった do.
                # Its dictionary lemma remains the adjective/noun base.
                continue
            if (
                issue.get("problem") in {
                    "over_grouped", "lemma", "conjugation", "wrong_type",
                }
                and primary_segment.get("type") == "grammar"
                and ("ずに" in surface or "たくな" in surface)
                and surface in overlays
                and any(marker in issue_detail for marker in (
                    "split", "separate", "multiple grammatical", "not a valid dictionary",
                    "not a single", "arbitrary predicate",
                ))
            ):
                # These are deliberately learned as construction-sized units,
                # with lexical roots exposed by their matching overlay. Do not
                # let a morphology-oriented reviewer recreate atomic rows.
                continue
            if (
                issue.get("problem") in {
                    "over_grouped", "lemma", "conjugation", "wrong_type",
                }
                and primary_segment.get("type") == "grammar"
                and str(primary_segment.get("lemma", "")).endswith(
                    ("くなる", "くする")
                )
                and surface in overlays
                and any(marker in issue_detail for marker in (
                    "split", "separate", "not a valid dictionary",
                    "not a dictionary", "arbitrary predicate",
                    "valid dictionary lemmas", "dictionary word",
                ))
            ):
                # Aくなる/Aくする is deliberately one complete construction
                # target. Its overlay—not tokenizer-like primary rows—exposes
                # and links the adjective plus なる/する lexical roots.
                continue
            if (
                issue.get("problem") == "grammar"
                and surface in primary_surfaces
                and any(marker in issue_detail for marker in (
                    "lacks a whole-form grammar overlay",
                    "lacks a whole-form overlay",
                    "no whole-form grammar overlay",
                    "no whole-form overlay",
                    "no grammar overlay",
                    "add a whole-form grammar overlay",
                    "add a whole-form overlay",
                    "require a whole-form grammar overlay",
                ))
            ):
                # A complete primary word already exposes its dictionary lemma,
                # contextual meaning, and full form. Requiring a duplicate
                # overlay solely for that same surface recreates tokenizer-like
                # stem/suffix rows inside the card. Multi-part constructions
                # remain reviewable because their overlay surface spans more
                # than one primary segment.
                continue
            if (
                issue.get("problem") == "over_grouped"
                and any(marker in overlay_detail for marker in (
                    "idiom", "fixed expression",
                ))
                and not any(marker in issue_detail for marker in (
                    "not an idiom", "not a fixed expression",
                    "ordinary phrase", "not lexicalized",
                ))
            ):
                # Predicate-sized inflection overlays exclude free arguments,
                # but fixed expressions such as 目が回る derive their meaning
                # from nominal and particle parts inside the lexicalized unit.
                continue
            retained.append(issue)
        review["issues"] = retained
        review["verdict"] = "revise" if retained else "pass"
        return review

    async def annotate_chunk(self, index: int, chunk: str) -> dict[str, Any]:
        """Generate, independently restart, then repair the fresh result.

        Japanese morphology can leave a clean independent restart with a few
        genuine lexical findings. Discarding that better segmentation after one
        review is wasteful, while returning it would violate the fail-closed
        contract. Give only the fresh candidate a short, bounded repair tail.
        """
        cache_key = digest(json.dumps({
            "policy": self.annotation_chunk_cache_tag,
            "chunk": chunk,
            "story_plan": getattr(self, "story_vocabulary_plan", {"terms": []}),
        }, ensure_ascii=False, sort_keys=True))
        run_dir = getattr(self, "run_dir", None)
        accepted_path = (
            Path(run_dir) / "accepted-annotations" / f"chunk_{index:04d}.json"
            if run_dir is not None else None
        )
        if (
            accepted_path is not None
            and accepted_path.is_file()
            and not self.refresh_annotation_chunk(index)
        ):
            try:
                cached = json.loads(accepted_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                cached = {}
            if isinstance(cached, dict):
                cached = normalize_redundant_japanese_form_steps(
                    clear_unavailable_dictionary_links(cached)
                )
            current_contract_passes = (
                cached.get("resolved") is True
                and self.annotation_reconstructs(chunk, cached)
                and not self.annotation_contract_issues(chunk, cached)
            )
            if current_contract_passes and (
                cached.get("cache_key") == cache_key
                or self.reuse_unselected_annotation_cache(index)
            ):
                cached["cache_key"] = cache_key
                accepted_path.write_text(
                    json.dumps(cached, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                return cached

        def accept(
            candidate: dict[str, Any], attempts: list[dict[str, Any]],
        ) -> dict[str, Any]:
            value = {
                "cache_key": cache_key,
                "segments": candidate["segments"],
                "grammar_overlays": candidate["grammar_overlays"],
                "attempts": attempts,
                "resolved": True,
            }
            if accepted_path is not None:
                accepted_path.parent.mkdir(parents=True, exist_ok=True)
                accepted_path.write_text(
                    json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            return value

        result = self.prepare_planned_annotation(
            chunk, await self.annotation_candidate(index, chunk)
        )
        attempts: list[dict[str, Any]] = []

        async def review(stage: str) -> dict[str, Any]:
            contract = self.annotation_contract_issues(chunk, result)
            if not self.annotation_reconstructs(chunk, result):
                return {"verdict": "revise", "issues": contract}
            reviewed = await self.review_annotation(index, chunk, result, stage)
            issues = list(reviewed.get("issues", []))
            seen = {
                json.dumps(issue, ensure_ascii=False, sort_keys=True)
                for issue in issues
            }
            for issue in contract:
                normalized = {
                    "segment_text": str(issue.get("surface", "")),
                    "problem": "contract",
                    "explanation": str(issue.get("message", "")),
                    "suggested_fix": str(issue.get("message", "")),
                }
                key = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
                if key not in seen:
                    seen.add(key)
                    issues.append(normalized)
            return {"verdict": "revise" if issues else "pass", "issues": issues}

        for attempt in range(self.args.max_annotation_repairs + 1):
            stage = "initial" if attempt == 0 else f"repair_{attempt:02d}"
            findings = await review(stage)
            attempts.append({"stage": stage, "annotation": result, "review": findings})
            if findings["verdict"] == "pass":
                return accept(result, attempts)
            if attempt < self.args.max_annotation_repairs:
                result = self.prepare_planned_annotation(
                    chunk,
                    await self.annotation_candidate(
                        index,
                        chunk,
                        stage=f"repair_{attempt + 1:02d}",
                        prior=result,
                        findings=findings,
                        effort=self.args.annotation_repair_effort,
                    ),
                )

        fresh_candidate = self.prepare_planned_annotation(
            chunk,
            await self.annotation_candidate(
                index,
                chunk,
                stage="fresh",
                effort=self.args.annotation_final_effort,
            ),
        )
        if (
            self.annotation_surfaces_reconstruct(chunk, result)
            and not self.annotation_surfaces_reconstruct(chunk, fresh_candidate)
        ):
            # Low-cost repair agents occasionally return only the requested
            # phrase instead of the complete annotation.  Preserve the last
            # complete candidate so one truncated response cannot poison every
            # subsequent repair prompt.
            attempts.append({
                "stage": "fresh_contract_rejected",
                "annotation": fresh_candidate,
                "review": {
                    "verdict": "revise",
                    "issues": self.annotation_contract_issues(
                        chunk, fresh_candidate
                    ),
                },
            })
        else:
            result = fresh_candidate
            findings = await review("fresh")
            attempts.append({
                "stage": "fresh", "annotation": result, "review": findings,
            })
        if findings["verdict"] == "pass":
            return accept(result, attempts)

        for tail in range(1, self.args.max_annotation_fresh_repairs + 1):
            stage = f"fresh_repair_{tail:02d}"
            candidate = self.prepare_planned_annotation(
                chunk,
                await self.annotation_candidate(
                    index,
                    chunk,
                    stage=stage,
                    prior=result,
                    findings=findings,
                    effort=self.args.annotation_repair_effort,
                ),
            )
            if (
                self.annotation_surfaces_reconstruct(chunk, result)
                and not self.annotation_surfaces_reconstruct(chunk, candidate)
            ):
                attempts.append({
                    "stage": f"{stage}_contract_rejected",
                    "annotation": candidate,
                    "review": {
                        "verdict": "revise",
                        "issues": self.annotation_contract_issues(
                            chunk, candidate
                        ),
                    },
                })
                continue
            result = candidate
            findings = await review(stage)
            attempts.append({"stage": stage, "annotation": result, "review": findings})
            if findings["verdict"] == "pass":
                return accept(result, attempts)
        # A tiny bounded adjudication set is intentionally separate from the
        # retry tail. Each pass receives the complete final findings and must
        # satisfy both the deterministic contract and an independent review.
        for adjudication in range(1, self.args.max_annotation_adjudications + 1):
            stage = f"adjudicated_final_{adjudication:02d}"
            result = self.prepare_planned_annotation(
                chunk,
                await self.annotation_candidate(
                    index,
                    chunk,
                    stage=stage,
                    prior=result,
                    findings=findings,
                    effort=self.args.annotation_repair_effort,
                ),
            )
            findings = await review(stage)
            attempts.append({"stage": stage, "annotation": result, "review": findings})
            if findings["verdict"] == "pass":
                return accept(result, attempts)
        raise ValueError(
            f"annotation quality gate failed for chunk {index} after "
            f"{len(attempts)} attempts"
        )

    @staticmethod
    def canonicalize_annotation_candidate(
        chunk: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        """Repair only unambiguous Japanese surface/offset bookkeeping."""
        value = copy.deepcopy(result)
        segments = value.get("segments")
        if isinstance(segments, list):
            # The title phrase repeatedly tempts models to treat this signature
            # first-person pronoun as the unnamed cat's proper name. Its lexical
            # category is mechanically unambiguous; correcting it preserves the
            # agent-selected boundary, reading, meaning, and story role.
            for index, item in enumerate(segments):
                part_of_speech = str(
                    item.get("part_of_speech", "")
                ).casefold()
                if (
                    item.get("surface") == "こと"
                    and "nominalizer" in part_of_speech
                ):
                    # In Vたことだけ / Vること, こと turns the preceding
                    # clause into a fact or event. It is not the standalone
                    # lexical noun 事 merely because that dictionary entry has
                    # the same reading. The agent has already supplied the
                    # syntactic evidence, so make the UI category and link
                    # behavior mechanically consistent with it.
                    item.update({
                        "type": "grammar",
                        "lemma": "こと",
                        "lemma_kana": "こと",
                        "part_of_speech": "nominalizer",
                        "conjugation_form": "non-inflecting",
                        "meaning_en": (
                            "the fact or event of; nominalizes the preceding clause"
                        ),
                        "grammar_candidate_key": (
                            str(item.get("grammar_candidate_key", "")).strip()
                            or "nominalizer-koto"
                        ),
                        "dictionary_key": "",
                        "dictionary_definition_en": "",
                        "form_steps": [],
                        "story_role": "none",
                        "story_importance_en": "",
                    })
                if (
                    item.get("surface") == "すると"
                    and item.get("type") == "grammar"
                ):
                    # Sentence-linking すると is conventionally learned as the
                    # whole connective "then / when that happened". Treating
                    # it as an inflection exercise from lexical する creates a
                    # misleading empty form-step obligation and hides the unit
                    # learners actually remember.
                    item.update({
                        "lemma": "すると",
                        "surface_kana": "すると",
                        "lemma_kana": "すると",
                        "part_of_speech": "grammar connective",
                        "conjugation_form": "non-inflecting",
                        "grammar_candidate_key": (
                            str(item.get("grammar_candidate_key", "")).strip()
                            or "suru-to"
                        ),
                        "dictionary_key": "",
                        "dictionary_definition_en": "",
                        "form_steps": [],
                        "story_role": "none",
                        "story_importance_en": "",
                    })
                if (
                    item.get("type") == "grammar"
                    and (
                        str(item.get("surface", "")).endswith("ながら")
                        or "nagara" in str(
                            item.get("grammar_candidate_key", "")
                        ).casefold()
                    )
                ):
                    # Vながら deliberately keeps the content verb as lemma but
                    # remains a learned grammar primary. Make that construction
                    # role explicit before the generic lexical-type repair.
                    item["part_of_speech"] = "verb grammar construction"
                    part_of_speech = "verb grammar construction"
                if (
                    item.get("type") == "grammar"
                    and (
                        "neba" in str(
                            item.get("grammar_candidate_key", "")
                        ).casefold()
                        or "ねばならない" in str(item.get("surface", ""))
                    )
                ):
                    matching_overlay = next((
                        overlay
                        for overlay in value.get("grammar_overlays", [])
                        if isinstance(overlay, dict)
                        and overlay.get("surface") == item.get("surface")
                        and isinstance(overlay.get("components"), list)
                        and overlay["components"]
                    ), None)
                    root = (
                        matching_overlay["components"][0]
                        if matching_overlay is not None else None
                    )
                    if isinstance(root, dict):
                        root_lemma = str(root.get("lemma", ""))
                        root_kana = str(root.get("lemma_kana", ""))
                        if root_lemma and root_kana:
                            # For a chain such as 困る -> 困らせる ->
                            # 困らせてやる -> ...ねばならない, the content
                            # root is the useful first UI row. The provisional
                            # grammar key still identifies the obligation unit.
                            item["lemma"] = root_lemma
                            item["lemma_kana"] = root_kana
                            item["part_of_speech"] = (
                                "verb grammar obligation construction"
                            )
                            part_of_speech = str(
                                item["part_of_speech"]
                            ).casefold()
                if (
                    "tagaru" in str(
                        item.get("grammar_candidate_key", "")
                    ).casefold()
                    and str(item.get("surface", "")).endswith("たがる")
                    and str(item.get("surface_kana", "")).endswith("たがる")
                ):
                    matching_overlay = next((
                        overlay
                        for overlay in value.get("grammar_overlays", [])
                        if isinstance(overlay, dict)
                        and overlay.get("surface") == item.get("surface")
                        and isinstance(overlay.get("components"), list)
                        and overlay["components"]
                    ), None)
                    root = (
                        matching_overlay["components"][0]
                        if matching_overlay is not None else None
                    )
                    root_lemma = (
                        str(root.get("lemma", ""))
                        if isinstance(root, dict) else ""
                    )
                    root_kana = (
                        str(root.get("lemma_kana", ""))
                        if isinstance(root, dict) else ""
                    )
                    if root_lemma.endswith("たがる") and root_kana.endswith("たがる"):
                        root_lemma = root_lemma[:-len("たがる")]
                        root_kana = root_kana[:-len("たがる")]
                    if root_lemma and root_kana:
                        # Vたがる is content-stem + たがる, never a plain-past
                        # Vた + がる analysis. Keep the complete predicate as
                        # one primary, expose its content dictionary form as the
                        # UI source, and show the meaningful たい intermediate.
                        surface = str(item["surface"])
                        surface_kana = str(item["surface_kana"])
                        desire = surface[:-len("たがる")] + "たい"
                        desire_kana = surface_kana[:-len("たがる")] + "たい"
                        item.update({
                            "type": "grammar",
                            "lemma": root_lemma,
                            "lemma_kana": root_kana,
                            "part_of_speech": "verb grammar construction",
                            "conjugation_form": "Vたがる plain nonpast",
                            "dictionary_key": "",
                            "dictionary_definition_en": "",
                            "form_steps": [{
                                "form": desire,
                                "reading": desire_kana,
                                "label": "たい-form",
                                "meaning_en": "want to do the content action",
                            }, {
                                "form": surface,
                                "reading": surface_kana,
                                "label": "Vたがる plain nonpast",
                                "meaning_en": str(item.get("meaning_en", "")),
                            }],
                        })
                        part_of_speech = "verb grammar construction"
                lexical_form_pos = (
                    "verb" in part_of_speech
                    or "adjective" in part_of_speech
                ) and not any(marker in part_of_speech for marker in (
                    "grammar", "construction", "copula", "auxiliary",
                ))
                if item.get("type") == "grammar" and lexical_form_pos:
                    candidate = candidate_for_lemma(
                        str(item.get("lemma", "")),
                        str(item.get("lemma_kana", "")),
                    )
                    candidate_key = (
                        str(candidate.get("dictionary_key", ""))
                        if candidate is not None else ""
                    )
                    if candidate_key:
                        # An ordinary lexical predicate remains a word tap even
                        # when its current surface teaches passive, negative,
                        # progressive, or another attached form. Preserve the
                        # agent's contextual sense and derivation; only repair
                        # the contradictory category and exact safe link.
                        item["type"] = "word"
                        item["dictionary_key"] = candidate_key
                        item["dictionary_definition_en"] = str(
                            item.get("meaning_en", "")
                        )
                if (
                    any(marker in " ".join((
                        str(item.get("grammar_candidate_key", "")),
                        str(item.get("conjugation_form", "")),
                        str(item.get("lemma", "")),
                    )).casefold() for marker in (
                        "te-kuru", "te-iku", "て来る", "てくる", "て行く", "ていく",
                    ))
                ):
                    matching_overlay = next((
                        overlay
                        for overlay in value.get("grammar_overlays", [])
                        if isinstance(overlay, dict)
                        and overlay.get("surface") == item.get("surface")
                        and isinstance(overlay.get("components"), list)
                        and overlay["components"]
                    ), None)
                    root = (
                        matching_overlay["components"][0]
                        if matching_overlay is not None else None
                    )
                    if isinstance(root, dict):
                        root_lemma = str(root.get("lemma", ""))
                        root_kana = str(root.get("lemma_kana", ""))
                        candidate = candidate_for_lemma(root_lemma, root_kana)
                        candidate_key = (
                            str(candidate.get("dictionary_key", ""))
                            if candidate is not None else ""
                        )
                        authored_key = str(root.get("dictionary_key", ""))
                        authored_definition = str(
                            root.get("dictionary_definition_en", "")
                        )
                        if (
                            not candidate_key
                            and authored_key
                            and dictionary_link_issue(
                                lemma=root_lemma,
                                lemma_kana=root_kana,
                                key=authored_key,
                                definition=authored_definition,
                            ) is None
                        ):
                            # Kana roots such as つく can have several valid
                            # same-reading headwords. The agent-selected overlay
                            # supplies the semantic disambiguation; deterministic
                            # validation proves the link is locally canonical.
                            candidate_key = authored_key
                        if root_lemma and root_kana and candidate_key:
                            # Directional Vて来る/Vて行く is one complete tap,
                            # but its displayed dictionary source is the
                            # content verb. This yields the useful chain 出る
                            # -> 出て -> 出て来る -> 出て来ない instead of
                            # repeating construction lemma 出て来る mid-chain.
                            item["type"] = "word"
                            item["lemma"] = root_lemma
                            item["lemma_kana"] = root_kana
                            item["part_of_speech"] = (
                                "verb with directional helper"
                            )
                            item["dictionary_key"] = candidate_key
                            item["dictionary_definition_en"] = str(
                                item.get("meaning_en", "")
                            )
                if item.get("surface") == "吾輩":
                    if item.get("type") == "name":
                        item["type"] = "word"
                    # This is the narrator's literary first-person pronoun, not
                    # his proper name. prepare_planned_annotation may promote
                    # it to story_term when the reviewed vocabulary plan does,
                    # but it must never retain an agent-invented name role.
                    if item.get("story_role") == "name":
                        item["story_role"] = "none"
                        item["story_importance_en"] = ""
                if item.get("surface") == "笹原":
                    # In chapter 1 this is the bamboo-grass field of the
                    # source, never an invented surname. Canonicalize both the
                    # noun and the immediately following locative particle so
                    # independent reviewers cannot oscillate on the passive.
                    item.update({
                        "type": "word",
                        "lemma": "笹原",
                        "surface_kana": "ささはら",
                        "lemma_kana": "ささはら",
                        "part_of_speech": "noun",
                        "conjugation_form": "non-inflecting",
                        "meaning_en": "bamboo-grass field",
                        "story_role": "none",
                        "story_importance_en": "",
                    })
                    if index + 1 < len(segments):
                        following = segments[index + 1]
                        if (
                            isinstance(following, dict)
                            and following.get("surface") == "に"
                        ):
                            following["meaning_en"] = "in; at"
                if (
                    item.get("surface") == "強い"
                    and index > 0
                    and isinstance(segments[index - 1], dict)
                    and segments[index - 1].get("surface") == "より"
                ):
                    item["meaning_en"] = "strong; stronger here with より"
                if (
                    item.get("surface") == "明るく"
                    and index + 1 < len(segments)
                    and isinstance(segments[index + 1], dict)
                    and segments[index + 1].get("surface") in {"、", "。"}
                ):
                    # Before punctuation this is the adjective's written
                    # conjunctive predicate, not a manner adverb modifying a
                    # following verb.
                    item["meaning_en"] = "bright; being bright"
                    for step in item.get("form_steps", []):
                        if isinstance(step, dict) and step.get("form") == "明るく":
                            step["meaning_en"] = "being bright"
                if (
                    item.get("surface") == "近く"
                    and index > 0
                    and isinstance(segments[index - 1], dict)
                    and segments[index - 1].get("surface") == "の"
                ):
                    # Nの近く is the location noun "near/vicinity", not the
                    # adverbial く-form of adjective 近い.
                    item.update({
                        "type": "word",
                        "lemma": "近く",
                        "surface_kana": "ちかく",
                        "lemma_kana": "ちかく",
                        "part_of_speech": "noun",
                        "conjugation_form": "non-inflecting",
                        "meaning_en": "vicinity; nearby",
                        "form_steps": [],
                    })
                if (
                    item.get("story_role") == "story_term"
                    and item.get("type") == "name"
                ):
                    # Names use the dedicated `name` role.  A term selected by
                    # the reviewed story-vocabulary plan is lexical by
                    # contract, so a simultaneous name/story_term assignment
                    # is a mechanical category contradiction rather than a
                    # linguistic judgment.  Low-cost annotation agents
                    # repeatedly make this mistake for social roles such as
                    # 書生 (student/young scholar).
                    item["type"] = "word"
                if _NUMERIC_COMMA_CONSTRUCTION.fullmatch(str(item.get("surface", ""))):
                    for field in ("surface_kana", "lemma_kana"):
                        reading = item.get(field)
                        if isinstance(reading, str):
                            item[field] = reading.replace("、", "")
                steps = item.get("form_steps")
                if (
                    isinstance(steps, list)
                    and steps
                    and isinstance(steps[-1], dict)
                    and item.get("surface") == item.get("surface_kana")
                    and steps[-1].get("reading") == item.get("surface_kana")
                    and steps[-1].get("form") != item.get("surface")
                ):
                    # A kana-written occurrence may correctly link to a kanji
                    # lemma, but the final learner step must reproduce what is
                    # actually on the page (とった, not 捕った).
                    steps[-1]["form"] = item["surface"]
            reconstructed = "".join(str(item.get("surface", "")) for item in segments)
            if reconstructed != chunk:
                # Annotation agents sometimes prettify a fixed chunk by adding
                # paragraph whitespace after Japanese full stops. When the
                # candidate contains every source character in order and the
                # only extras are whitespace, removing those extras is pure
                # surface bookkeeping: no boundary or linguistic metadata is
                # being inferred. Keep source whitespace (matched greedily)
                # and trim only candidate-only characters from their segments.
                source_cursor = 0
                normalized_surfaces: list[str] = []
                whitespace_only_drift = True
                for item in segments:
                    normalized: list[str] = []
                    for char in str(item.get("surface", "")):
                        if (
                            source_cursor < len(chunk)
                            and char == chunk[source_cursor]
                        ):
                            normalized.append(char)
                            source_cursor += 1
                        elif char.isspace():
                            continue
                        else:
                            whitespace_only_drift = False
                            break
                    normalized_surfaces.append("".join(normalized))
                    if not whitespace_only_drift:
                        break
                if whitespace_only_drift and source_cursor == len(chunk):
                    for item, surface in zip(segments, normalized_surfaces):
                        item["surface"] = surface
                    segments[:] = [
                        item for item in segments
                        if str(item.get("surface", ""))
                    ]
                    reconstructed = "".join(
                        str(item.get("surface", "")) for item in segments
                    )
            if len(chunk) == len(reconstructed) + 1:
                missing = next(
                    (
                        index
                        for index, char in enumerate(chunk)
                        if char in "、。！？「」『』（）［］【】〈〉《》…—・,:;!?\"'()"
                        and chunk[:index] + chunk[index + 1:] == reconstructed
                    ),
                    None,
                )
                if missing is not None:
                    cursor = 0
                    insertion = None
                    for index, item in enumerate(segments):
                        if cursor == missing:
                            insertion = index
                            break
                        cursor += len(str(item.get("surface", "")))
                    if cursor == missing and insertion is None:
                        insertion = len(segments)
                    if insertion is not None:
                        segments.insert(insertion, {
                            "surface": chunk[missing], "type": "punctuation",
                            "lemma": "", "surface_kana": "", "lemma_kana": "",
                            "part_of_speech": "", "conjugation_form": "",
                            "meaning_en": "", "grammar_candidate_key": "",
                            "story_role": "none",
                            "story_importance_en": "",
                        })
                        reconstructed = "".join(
                            str(item.get("surface", "")) for item in segments
                        )
            if chunk.rstrip() == reconstructed.rstrip() and chunk != reconstructed:
                # Preserve trailing layout exactly even when an agent reverses
                # newline and ideographic-space rows (for example 。\n　 versus
                # 。　\n). These characters are all punctuation/whitespace and
                # carry no linguistic boundary decision.
                while segments and str(segments[-1].get("surface", "")).isspace():
                    segments.pop()
                source_tail = chunk[len(chunk.rstrip()):]
                if source_tail:
                    segments.append({
                        "surface": source_tail, "type": "punctuation",
                        "lemma": "", "surface_kana": "", "lemma_kana": "",
                        "part_of_speech": "", "conjugation_form": "",
                        "meaning_en": "", "grammar_candidate_key": "",
                        "dictionary_key": "", "dictionary_definition_en": "",
                        "form_steps": [], "story_role": "none",
                        "story_importance_en": "",
                    })
                reconstructed = "".join(
                    str(item.get("surface", "")) for item in segments
                )
            remainder = chunk[len(reconstructed):] if chunk.startswith(reconstructed) else ""
            if remainder and remainder.isspace():
                segments.append({
                    "surface": remainder, "type": "punctuation", "lemma": "",
                    "surface_kana": "", "lemma_kana": "",
                    "part_of_speech": "", "conjugation_form": "",
                    "meaning_en": "", "grammar_candidate_key": "",
                    "dictionary_key": "", "dictionary_definition_en": "",
                    "form_steps": [],
                    "story_role": "none",
                    "story_importance_en": "",
                })
            elif reconstructed and chunk.endswith(reconstructed):
                prefix = chunk[:len(chunk) - len(reconstructed)]
                if prefix and prefix.isspace():
                    segments.insert(0, {
                        "surface": prefix, "type": "punctuation", "lemma": "",
                        "surface_kana": "", "lemma_kana": "",
                        "part_of_speech": "", "conjugation_form": "",
                        "meaning_en": "", "grammar_candidate_key": "",
                        "dictionary_key": "", "dictionary_definition_en": "",
                        "form_steps": [],
                        "story_role": "none",
                        "story_importance_en": "",
                    })
        overlays = value.get("grammar_overlays")
        if isinstance(overlays, list):
            for overlay in overlays:
                head_kana = str(overlay.get("head_lemma_kana", ""))
                if head_kana and not re.fullmatch(
                    r"[\u3040-\u30ffー・\s]+", head_kana
                ):
                    matching_primary = next((
                        segment for segment in value.get("segments", [])
                        if isinstance(segment, dict)
                        and segment.get("surface") == overlay.get("surface")
                        and str(segment.get("surface_kana", ""))
                    ), None)
                    if matching_primary is not None:
                        # Pattern placeholders belong in `pattern`, not the
                        # concrete head fields displayed as "From" in the UI.
                        overlay["head_lemma"] = str(
                            matching_primary.get("surface", "")
                        )
                        overlay["head_lemma_kana"] = str(
                            matching_primary.get("surface_kana", "")
                        )
                    else:
                        # Multi-primary overlays legitimately use an abstract
                        # pattern such as Xほど〜ない, but the card's concrete
                        # "From" row must not pretend that X/〜 is a kana
                        # reading. Rebuild it from the exact primary rows that
                        # the overlay spans. The reusable template remains in
                        # `pattern` and the provisional grammar key.
                        surface = str(overlay.get("surface", ""))
                        supplied_start = overlay.get("start")
                        occurrences = [
                            match.start()
                            for match in re.finditer(re.escape(surface), chunk)
                        ] if surface else []
                        surface_start = (
                            occurrences[0] if len(occurrences) == 1
                            else supplied_start if (
                                isinstance(supplied_start, int)
                                and 0 <= supplied_start <= len(chunk) - len(surface)
                                and chunk[
                                    supplied_start:supplied_start + len(surface)
                                ] == surface
                            ) else None
                        )
                        if surface_start is not None:
                            surface_end = surface_start + len(surface)
                            cursor = 0
                            covered: list[dict[str, Any]] = []
                            exact_boundaries = False
                            for segment in value.get("segments", []):
                                if not isinstance(segment, dict):
                                    continue
                                segment_surface = str(segment.get("surface", ""))
                                segment_end = cursor + len(segment_surface)
                                if cursor >= surface_start and segment_end <= surface_end:
                                    covered.append(segment)
                                if segment_end == surface_end:
                                    exact_boundaries = (
                                        bool(covered)
                                        and "".join(
                                            str(item.get("surface", ""))
                                            for item in covered
                                        ) == surface
                                    )
                                    break
                                cursor = segment_end
                            readings = [
                                str(segment.get("surface_kana", ""))
                                for segment in covered
                            ]
                            if exact_boundaries and all(readings):
                                overlay["head_lemma"] = surface
                                overlay["head_lemma_kana"] = "".join(readings)
                components = overlay.get("components")
                grammar_key = str(
                    overlay.get("grammar_candidate_key", "")
                ).casefold()
                if (
                    "tagaru" in grammar_key
                    and str(overlay.get("surface", "")).endswith("たがる")
                ):
                    surface = str(overlay["surface"])
                    primary = next((
                        segment for segment in value.get("segments", [])
                        if isinstance(segment, dict)
                        and segment.get("surface") == surface
                    ), None)
                    prefix_surface = surface[:-len("たがる")]
                    surface_kana = (
                        str(primary.get("surface_kana", ""))
                        if isinstance(primary, dict) else ""
                    )
                    prefix_kana = (
                        surface_kana[:-len("たがる")]
                        if surface_kana.endswith("たがる") else ""
                    )
                    content_lemma = (
                        str(primary.get("lemma", ""))
                        if isinstance(primary, dict) else ""
                    )
                    content_kana = (
                        str(primary.get("lemma_kana", ""))
                        if isinstance(primary, dict) else ""
                    )
                    if prefix_surface and prefix_kana and content_lemma and content_kana:
                        candidate = candidate_for_lemma(
                            content_lemma, content_kana,
                        )
                        candidate_key = (
                            str(candidate.get("dictionary_key", ""))
                            if candidate is not None else ""
                        )
                        overlay["components"] = [{
                            "start": 0,
                            "end": len(prefix_surface),
                            "surface": prefix_surface,
                            "lemma": content_lemma,
                            "lemma_kana": content_kana,
                            "function_en": "content predicate in its connective stem",
                            "lookup_kind": "lexical",
                            "dictionary_key": candidate_key,
                            "dictionary_definition_en": (
                                str(primary.get("meaning_en", ""))
                                if candidate_key else ""
                            ),
                        }, {
                            "start": len(prefix_surface),
                            "end": len(surface),
                            "surface": "たがる",
                            "lemma": "たがる",
                            "lemma_kana": "たがる",
                            "function_en": (
                                "expresses another person's apparent desire "
                                "or tendency"
                            ),
                            "lookup_kind": "grammar",
                            "dictionary_key": "",
                            "dictionary_definition_en": "",
                        }]
                        components = overlay["components"]
                if "nagara" in grammar_key or "ながら" in grammar_key:
                    # `head_lemma` names the reusable construction, while the
                    # immutable surface is its realized stem form. Reviewers
                    # otherwise oscillate between the realized 見ながら and
                    # an artificial 見るながら. The card already displays the
                    # surface and Vながら pattern; its learner-facing "From"
                    # row should name the natural content dictionary form 見る.
                    surface = str(overlay.get("surface", ""))
                    primary = next((
                        segment for segment in value.get("segments", [])
                        if isinstance(segment, dict)
                        and segment.get("surface") == surface
                        and segment.get("type") == "grammar"
                        and (
                            "nagara" in str(
                                segment.get("grammar_candidate_key", "")
                            ).casefold()
                            or "ながら" in str(
                                segment.get("grammar_candidate_key", "")
                            )
                        )
                    ), None)
                    if primary is not None:
                        content_lemma = str(primary.get("lemma", ""))
                        content_reading = str(primary.get("lemma_kana", ""))
                        if content_lemma:
                            overlay["head_lemma"] = content_lemma
                        if content_reading:
                            overlay["head_lemma_kana"] = content_reading
                if "neba" in grammar_key or "ねばならない" in str(
                    overlay.get("surface", "")
                ):
                    primary = next((
                        segment for segment in value.get("segments", [])
                        if isinstance(segment, dict)
                        and segment.get("surface") == overlay.get("surface")
                        and segment.get("type") == "grammar"
                    ), None)
                    if primary is not None:
                        content_lemma = str(primary.get("lemma", ""))
                        content_kana = str(primary.get("lemma_kana", ""))
                        if content_lemma and content_kana:
                            overlay["head_lemma"] = content_lemma
                            overlay["head_lemma_kana"] = content_kana
                if (
                    grammar_key.startswith((
                        "adjective-ku-", "adjective_ku_", "a-ku-", "a_ku_",
                    ))
                    and isinstance(components, list)
                ):
                    adjective_index = next((
                        index for index, component in enumerate(components)
                        if str(component.get("surface", "")).endswith("く")
                        and str(component.get("lemma", "")).endswith("い")
                    ), None)
                    if adjective_index is not None and adjective_index > 0:
                        # A reusable Aくなる/Aくする overlay is predicate-sized.
                        # Low-cost agents sometimes reuse the collocation's
                        # wider NがAくなる span for both cards. Preserve the
                        # wider collocation overlay, but crop this grammar-keyed
                        # copy to the exact adjective predicate and its roots.
                        prefix_length = sum(
                            len(str(component.get("surface", "")))
                            for component in components[:adjective_index]
                        )
                        components = components[adjective_index:]
                        overlay["components"] = components
                        original_surface = str(overlay.get("surface", ""))
                        overlay["surface"] = original_surface[prefix_length:]
                        helper_lemma = str(components[-1].get("lemma", ""))
                        if str(overlay.get("head_lemma", "")) in {"なる", "する"}:
                            adjective_surface = str(
                                components[0].get("surface", "")
                            )
                            overlay["head_lemma"] = adjective_surface + helper_lemma
                            adjective_reading = str(
                                components[0].get("lemma_kana", "")
                            )
                            if adjective_reading.endswith("い"):
                                overlay["head_lemma_kana"] = (
                                    adjective_reading[:-1] + "く"
                                    + str(components[-1].get("lemma_kana", ""))
                                )
                surface = overlay.get("surface")
                if not isinstance(surface, str) or not surface:
                    continue
                if (
                    is_numeric_comma_construction(surface)
                    and not surface.endswith("も")
                    and chunk.count(surface + "も") == 1
                ):
                    old_length = len(surface)
                    surface += "も"
                    overlay["surface"] = surface
                    overlay["head_lemma"] = (
                        str(overlay.get("head_lemma", "")) + "も"
                    )
                    overlay["head_lemma_kana"] = (
                        str(overlay.get("head_lemma_kana", "")) + "も"
                    )
                    overlay["pattern"] = str(overlay.get("pattern", "")) + "も"
                    overlay["meaning_en"] = (
                        "as many as " + str(overlay.get("meaning_en", ""))
                    )
                    overlay["explanation_en"] = (
                        str(overlay.get("explanation_en", ""))
                        + " The following も emphasizes that quantity."
                    ).strip()
                    if isinstance(components, list):
                        components.append({
                            "start": old_length,
                            "end": old_length + 1,
                            "surface": "も",
                            "lemma": "も",
                            "lemma_kana": "も",
                            "function_en": "emphasizes the unexpectedly large quantity",
                            "lookup_kind": "grammar",
                            "dictionary_key": "",
                            "dictionary_definition_en": "",
                        })
                numeric_comma = is_numeric_comma_construction(surface)
                if numeric_comma and isinstance(
                    overlay.get("head_lemma_kana"), str
                ):
                    overlay["head_lemma_kana"] = overlay[
                        "head_lemma_kana"
                    ].replace("、", "")
                occurrences = [
                    match.start() for match in re.finditer(re.escape(surface), chunk)
                ]
                selected = occurrences[0] if len(occurrences) == 1 else None
                supplied = overlay.get("start")
                if len(occurrences) > 1 and isinstance(supplied, int):
                    distances = sorted((abs(item - supplied), item) for item in occurrences)
                    if distances[0][0] < distances[1][0]:
                        selected = distances[0][1]
                if selected is not None:
                    overlay["start"] = selected
                    overlay["end"] = selected + len(surface)
                components = overlay.get("components")
                if (
                    numeric_comma
                    and isinstance(components, list)
                ):
                    # The comma is integral notation, not a tappable learned
                    # part. Agents often include it to make their component
                    # offsets reconstruct; drop only that inert separator and
                    # let the offset normalizer skip it below.
                    components[:] = [
                        component for component in components
                        if not (
                            isinstance(component, dict)
                            and component.get("surface") == "、"
                        )
                    ]
                component_text = (
                    "".join(str(component.get("surface", "")) for component in components)
                    if isinstance(components, list)
                    else ""
                )
                if isinstance(components, list) and (
                    component_text == surface
                    or (
                        is_numeric_comma_construction(surface)
                        and component_text == surface.replace("、", "")
                    )
                ):
                    # Offsets are bookkeeping, not a linguistic judgment. Keep
                    # every agent-selected component and normalize its relative
                    # Python indices when the surfaces already reconstruct.
                    # Numeric-range punctuation may sit between useful parts
                    # without becoming a fake learned component.
                    cursor = 0
                    for component in components:
                        if is_numeric_comma_construction(surface):
                            while cursor < len(surface) and surface[cursor] == "、":
                                cursor += 1
                        if _NUMERIC_COMMA_CONSTRUCTION.fullmatch(
                            str(component.get("surface", ""))
                        ) and isinstance(component.get("lemma_kana"), str):
                            component["lemma_kana"] = component["lemma_kana"].replace("、", "")
                        component["start"] = cursor
                        cursor += len(str(component["surface"]))
                        component["end"] = cursor
        return value

    @staticmethod
    def annotation_surfaces_reconstruct(
        chunk: str, result: dict[str, Any],
    ) -> bool:
        """Return whether primary surfaces preserve the source text exactly."""
        segments = result.get("segments")
        return (
            isinstance(segments, list)
            and "".join(str(item.get("surface", "")) for item in segments) == chunk
        )

    @staticmethod
    def annotation_reconstructs(chunk: str, result: dict[str, Any]) -> bool:
        segments = result.get("segments")
        overlays = result.get("grammar_overlays")
        if not isinstance(segments, list) or not isinstance(overlays, list):
            return False
        if not JapaneseChapterHarness.annotation_surfaces_reconstruct(chunk, result):
            return False
        kana = re.compile(r"^[\u3040-\u30ffー・\s]*$")
        for item in segments:
            surface = item.get("surface", "")
            kind = item.get("type")
            has_authored_dictionary_link = (
                "dictionary_key" in item
                and "dictionary_definition_en" in item
            )
            if kind == "punctuation":
                empty_fields = (
                    "lemma", "surface_kana", "lemma_kana", "part_of_speech",
                    "conjugation_form", "meaning_en", "grammar_candidate_key",
                    "dictionary_key", "dictionary_definition_en",
                    "story_importance_en",
                )
                if any(item.get(field) for field in empty_fields):
                    return False
                if "form_steps" in item and item.get("form_steps") != []:
                    return False
                if item.get("story_role") != "none":
                    return False
                continue
            required = (
                "lemma", "surface_kana", "lemma_kana", "part_of_speech",
                "conjugation_form", "meaning_en",
            )
            if any(not str(item.get(field, "")).strip() for field in required):
                return False
            if (
                "dictionary_key" in item
                and not isinstance(item.get("dictionary_key"), str)
            ) or (
                "dictionary_definition_en" in item
                and not isinstance(item.get("dictionary_definition_en"), str)
            ):
                return False
            if dictionary_link_issue(
                lemma=str(item.get("lemma", "")),
                lemma_kana=str(item.get("lemma_kana", "")),
                key=str(item.get("dictionary_key", "")),
                definition=str(item.get("dictionary_definition_en", "")),
                functional=kind in {"grammar", "auxiliary", "particle"},
                require_available=(
                    has_authored_dictionary_link
                    and kind in {"word", "idiom"}
                ),
            ):
                return False
            if japanese_form_step_issues(item):
                return False
            if not kana.fullmatch(str(item["surface_kana"])):
                return False
            if not kana.fullmatch(str(item["lemma_kana"])):
                return False
            grammar_key = item.get("grammar_candidate_key")
            if grammar_key is not None and (
                not isinstance(grammar_key, str)
                or (
                    grammar_key
                    and not re.fullmatch(r"[a-z][a-z0-9_.-]*", grammar_key)
                )
            ):
                return False
            if kind == "grammar" and grammar_key is not None and not grammar_key:
                return False
            if item.get("story_role") not in {"none", "name", "story_term"}:
                return False
            if item.get("story_role") == "story_term":
                if kind not in {"word", "idiom"}:
                    return False
                if item.get("lemma") in GRAMMAR_BASELINE_LEMMAS:
                    return False
                if not str(item.get("story_importance_en", "")).strip():
                    return False
            if kind in {"word", "auxiliary", "particle"} and len(surface) > 12:
                return False
            if japanese_segment_issue(surface, str(kind)):
                return False
        if japanese_learner_segmentation_issues(segments):
            return False
        if japanese_required_overlay_issues(segments, overlays):
            return False
        for overlay in overlays:
            start, end = overlay.get("start"), overlay.get("end")
            if not isinstance(start, int) or not isinstance(end, int):
                return False
            if not (0 <= start < end <= len(chunk)):
                return False
            if overlay.get("surface") != chunk[start:end]:
                return False
            if overlay_crosses_clause_boundary(str(overlay.get("surface", ""))):
                # A learner taps a grammar construction, not a multi-clause
                # span. Keep punctuation and neighbouring clauses out of it.
                return False
            if japanese_overlay_policy_issue(overlay):
                return False
            required_overlay = (
                "grammar_candidate_key", "pattern", "meaning_en", "head_lemma",
                "head_lemma_kana", "form_label", "explanation_en",
            )
            if any(not str(overlay.get(field, "")).strip() for field in required_overlay):
                return False
            if not re.fullmatch(
                r"[a-z][a-z0-9_.-]*", str(overlay["grammar_candidate_key"])
            ):
                return False
            if not kana.fullmatch(str(overlay["head_lemma_kana"])):
                return False
            components = overlay.get("components")
            if not isinstance(components, list) or len(components) < 2:
                return False
            cursor = 0
            surface = str(overlay["surface"])
            numeric_comma = bool(_NUMERIC_COMMA_CONSTRUCTION.fullmatch(surface))
            tari_listing = is_tari_listing_construction(surface)
            for component in components:
                has_authored_dictionary_link = (
                    "dictionary_key" in component
                    and "dictionary_definition_en" in component
                )
                component_start = component.get("start")
                component_end = component.get("end")
                if not isinstance(component_start, int) or not isinstance(component_end, int):
                    return False
                if component_start != cursor:
                    skipped = surface[cursor:component_start]
                    if not (
                        numeric_comma
                        and component_start > cursor
                        and skipped
                        and set(skipped) == {"、"}
                    ):
                        return False
                if not component_start < component_end:
                    return False
                if surface[component_start:component_end] != component.get("surface"):
                    return False
                if any(not str(component.get(field, "")).strip() for field in (
                    "lemma", "lemma_kana", "function_en"
                )):
                    return False
                lookup_kind = component.get("lookup_kind", "none")
                if lookup_kind not in {"lexical", "grammar", "none"}:
                    return False
                if (
                    "dictionary_key" in component
                    and not isinstance(component.get("dictionary_key"), str)
                ) or (
                    "dictionary_definition_en" in component
                    and not isinstance(component.get("dictionary_definition_en"), str)
                ):
                    return False
                if dictionary_link_issue(
                    lemma=str(component.get("lemma", "")),
                    lemma_kana=str(component.get("lemma_kana", "")),
                    key=str(component.get("dictionary_key", "")),
                    definition=str(component.get("dictionary_definition_en", "")),
                    functional=lookup_kind != "lexical",
                    require_available=(
                        has_authored_dictionary_link
                        and lookup_kind == "lexical"
                    ),
                ):
                    return False
                if not kana.fullmatch(str(component["lemma_kana"])) and not (
                    (numeric_comma or tari_listing)
                    and component.get("surface") == "、"
                    and component.get("lemma") == "、"
                    and component.get("lemma_kana") == "、"
                ):
                    return False
                cursor = component_end
            if cursor != len(surface) and not (
                numeric_comma
                and surface[cursor:]
                and set(surface[cursor:]) == {"、"}
            ):
                return False
        return True

    @staticmethod
    def annotation_contract_issues(
        chunk: str, result: dict[str, Any]
    ) -> list[dict[str, str]]:
        if JapaneseChapterHarness.annotation_reconstructs(chunk, result):
            return []
        issues: list[dict[str, str]] = []

        def add(surface: str, problem: str, explanation: str, fix: str) -> None:
            issues.append({
                "segment_text": surface,
                "problem": problem,
                "explanation": explanation,
                "suggested_fix": fix,
            })

        segments = result.get("segments")
        overlays = result.get("grammar_overlays")
        if not isinstance(segments, list) or not isinstance(overlays, list):
            add("", "reconstruction", "segments and grammar_overlays must both be arrays.",
                "Return both required arrays.")
            return issues
        reconstructed = "".join(str(item.get("surface", "")) for item in segments)
        if reconstructed != chunk:
            mismatch = next(
                (index for index, pair in enumerate(zip(reconstructed, chunk))
                 if pair[0] != pair[1]),
                min(len(reconstructed), len(chunk)),
            )
            add(
                reconstructed[max(0, mismatch - 8):mismatch + 16],
                "reconstruction",
                f"Segment surfaces stop matching TEXT at Python offset {mismatch}; "
                f"reconstructed length {len(reconstructed)}, TEXT length {len(chunk)}.",
                "Copy the exact text around that offset and make all segment surfaces concatenate exactly.",
            )
        kana = re.compile(r"^[\u3040-\u30ffー・\s]*$")
        for item in segments:
            surface = str(item.get("surface", ""))
            kind = str(item.get("type", ""))
            has_authored_dictionary_link = (
                "dictionary_key" in item
                and "dictionary_definition_en" in item
            )
            if kind == "punctuation":
                fields = (
                    "lemma", "surface_kana", "lemma_kana", "part_of_speech",
                    "conjugation_form", "meaning_en", "grammar_candidate_key",
                    "dictionary_key", "dictionary_definition_en",
                    "story_importance_en",
                )
                if (
                    any(item.get(field) for field in fields)
                    or item.get("form_steps") != []
                    or item.get("story_role") != "none"
                ):
                    add(surface, "wrong_type", "A punctuation/whitespace segment has lexical metadata.",
                        "Clear its linguistic fields and set story_role to none.")
                continue
            required = (
                "lemma", "surface_kana", "lemma_kana", "part_of_speech",
                "conjugation_form", "meaning_en",
            )
            missing = [field for field in required if not str(item.get(field, "")).strip()]
            if missing:
                add(surface, "meaning", f"Missing required fields: {', '.join(missing)}.",
                    "Supply precise lexical metadata for this segment.")
            if (
                "dictionary_key" in item
                and not isinstance(item.get("dictionary_key"), str)
            ) or (
                "dictionary_definition_en" in item
                and not isinstance(item.get("dictionary_definition_en"), str)
            ):
                add(
                    surface, "meaning",
                    "dictionary_key and dictionary_definition_en must both be strings.",
                    "Use two empty strings for no link, or one verified canonical key and exact local definition.",
                )
            else:
                link_problem = dictionary_link_issue(
                    lemma=str(item.get("lemma", "")),
                    lemma_kana=str(item.get("lemma_kana", "")),
                    key=str(item.get("dictionary_key", "")),
                    definition=str(item.get("dictionary_definition_en", "")),
                    functional=kind in {"grammar", "auxiliary", "particle"},
                    require_available=(
                        has_authored_dictionary_link
                        and kind in {"word", "idiom"}
                    ),
                )
                if link_problem:
                    candidate = candidate_for_lemma(
                        str(item.get("lemma", "")),
                        str(item.get("lemma_kana", "")),
                    )
                    add(
                        surface, "meaning",
                        f"Invalid dictionary link: {link_problem}.",
                        (
                            f"Use this exact candidate key and write a concise contextual sense; its raw definitions are reference evidence, not mandatory wording: {candidate}"
                            if candidate is not None
                            else "Clear both dictionary fields; no exact canonical local candidate matches this lemma and reading."
                        ),
                    )
            for step_problem in japanese_form_step_issues(item):
                add(
                    surface, "conjugation", step_problem,
                    "Give an ordered learner-facing derivation after the lemma, include every meaningful intermediate form, and end at exact surface/surface_kana; use [] only when unchanged or non-inflecting.",
                )
            grammar_key = item.get("grammar_candidate_key")
            if kind == "grammar" and not str(grammar_key or "").strip():
                add(
                    surface,
                    "grammar",
                    "A standalone grammar primary lacks its provisional grammar key.",
                    "Set grammar_candidate_key on this primary card; do not create a decorative parts overlay only to carry the key.",
                )
            elif grammar_key and not re.fullmatch(
                r"[a-z][a-z0-9_.-]*", str(grammar_key)
            ):
                add(
                    surface,
                    "grammar",
                    "The primary grammar candidate key is invalid.",
                    "Use a lowercase ASCII key containing only letters, digits, dots, underscores, or hyphens.",
                )
            for field in ("surface_kana", "lemma_kana"):
                if not kana.fullmatch(str(item.get(field, ""))):
                    problem = "surface_reading" if field == "surface_kana" else "lemma_reading"
                    add(surface, problem, f"{field} must contain only kana.",
                        "Write the complete reading in kana without Latin labels or punctuation.")
            mechanical = japanese_segment_issue(surface, kind)
            if mechanical:
                add(surface, "wrong_type", mechanical,
                    "Correct the mechanically invalid Japanese segment classification.")
        for issue in japanese_learner_segmentation_issues(segments):
            split_primary = any(marker in issue["message"] for marker in (
                "separately accessible", "primary rows stay",
                "searchable primary segmentation", "split it from",
            ))
            type_only = "type it as grammar" in issue["message"]
            add(
                issue["surface"],
                (
                    "wrong_type" if type_only else
                    "over_grouped" if split_primary else "under_grouped"
                ),
                issue["message"],
                (
                    "Keep the same complete primary surface, change its type to "
                    "grammar, set a provisional grammar key, and expose its "
                    "lexical roots in an exact component overlay."
                    if type_only else
                    "Split the opaque primary into the learner-sized rows named "
                    "in the explanation; keep the combined contextual meaning "
                    "in an exact whole overlay."
                    if split_primary else
                    "Merge the fragments into the complete learner-facing surface "
                    "form and give its dictionary lemma and full conjugation label."
                ),
            )
        for issue in japanese_required_overlay_issues(segments, overlays):
            add(
                issue["surface"],
                "grammar",
                issue["message"],
                "Add one exact overlay with two or more pedagogical components and a clear combined meaning.",
            )
        for overlay in overlays:
            surface = str(overlay.get("surface", ""))
            numeric_comma = bool(_NUMERIC_COMMA_CONSTRUCTION.fullmatch(surface))
            tari_listing = is_tari_listing_construction(surface)
            start, end = overlay.get("start"), overlay.get("end")
            if not isinstance(start, int) or not isinstance(end, int) or not (
                0 <= start < end <= len(chunk)
            ) or chunk[start:end] != surface:
                add(surface, "grammar", "The grammar overlay does not match TEXT at its absolute offsets.",
                    "Set start/end to exact Python offsets for this surface.")
            if overlay_crosses_clause_boundary(surface):
                add(
                    surface,
                    "grammar_components",
                    "The grammar overlay crosses punctuation or a clause boundary.",
                    "Replace it with one or more predicate-level overlays. Do not include punctuation components.",
                )
            policy_issue = japanese_overlay_policy_issue(overlay)
            if policy_issue:
                suggested_fix = (
                    "Keep the overlay but repartition it as the lexical predicate "
                    "+ the complete かと思う form (for example 来る + かと思い)."
                    if "かと思う overlay" in policy_issue
                    else "Remove this decorative overlay; keep the learner-sized primary segments."
                )
                add(
                    surface,
                    "grammar",
                    policy_issue,
                    suggested_fix,
                )
            components = overlay.get("components")
            component_text = "" if not isinstance(components, list) else "".join(
                str(component.get("surface", "")) for component in components
            )
            if isinstance(components, list) and len(components) < 2:
                add(
                    surface,
                    "grammar_components",
                    "A grammar overlay needs at least two pedagogical components; one component is redundant.",
                    "Split the form into two or more useful learned chunks, or remove the overlay.",
                )
            if component_text != surface and not (
                numeric_comma and component_text == surface.replace("、", "")
            ):
                add(surface, "grammar_components", "Grammar components do not reconstruct the overlay surface.",
                    "Use consecutive components whose exact surfaces concatenate to the overlay.")
            if isinstance(components, list):
                for component in components:
                    has_authored_dictionary_link = (
                        "dictionary_key" in component
                        and "dictionary_definition_en" in component
                    )
                    lemma_kana = str(component.get("lemma_kana", ""))
                    if lemma_kana and not kana.fullmatch(lemma_kana) and not (
                        (numeric_comma or tari_listing)
                        and component.get("surface") == "、"
                        and component.get("lemma") == "、"
                        and lemma_kana == "、"
                    ):
                        add(
                            str(component.get("surface", "")),
                            "grammar_components",
                            "A grammar component lemma_kana contains punctuation or non-kana text.",
                            "Use a kana dictionary reading, or remove punctuation from the overlay and its components.",
                        )
                    lookup_kind = component.get("lookup_kind", "none")
                    if lookup_kind not in {"lexical", "grammar", "none"}:
                        add(
                            str(component.get("surface", "")),
                            "grammar_components",
                            "A component lacks a valid lexical/grammar/none lookup_kind.",
                            "Mark lexical roots as lexical, grammar-only pieces as grammar, and punctuation as none.",
                        )
                    component_key = component.get("dictionary_key", "")
                    component_definition = component.get("dictionary_definition_en", "")
                    if (
                        "dictionary_key" in component
                        and not isinstance(component_key, str)
                    ) or (
                        "dictionary_definition_en" in component
                        and not isinstance(component_definition, str)
                    ):
                        add(
                            str(component.get("surface", "")),
                            "grammar_components",
                            "A component's dictionary fields must both be strings.",
                            "Use empty strings when no verified lexical link exists.",
                        )
                    else:
                        link_problem = dictionary_link_issue(
                            lemma=str(component.get("lemma", "")),
                            lemma_kana=str(component.get("lemma_kana", "")),
                            key=component_key,
                            definition=component_definition,
                            functional=lookup_kind != "lexical",
                            require_available=(
                                has_authored_dictionary_link
                                and lookup_kind == "lexical"
                            ),
                        )
                        if link_problem:
                            candidate = candidate_for_lemma(
                                str(component.get("lemma", "")),
                                str(component.get("lemma_kana", "")),
                            )
                            add(
                                str(component.get("surface", "")),
                                "grammar_components",
                                f"Invalid component dictionary link: {link_problem}.",
                                (
                                    f"Use this exact candidate key with a concise agent-authored contextual sense: {candidate}"
                                    if lookup_kind == "lexical" and candidate is not None
                                    else "Clear both dictionary fields for this grammar/non-linkable component."
                                ),
                            )
        if not issues:
            add("", "reconstruction", "A required Japanese annotation field or grammar key is invalid.",
                "Check nonempty overlay fields, lowercase grammar keys, kana readings, story roles, and component offsets.")
        return issues

    async def annotate_chunks_progressively(
        self, chunks: list[str],
    ) -> list[dict[str, Any]]:
        """Resolve a bounded number of complete chunk lifecycles in parallel.

        Scheduling every candidate at once lets later initial generations sit
        ahead of earlier reviews in the shared model semaphore. A small worker
        pool keeps the requested parallelism while each worker carries one
        sentence-sized chunk through generation, review, and repair before it
        picks up another.
        """
        if not chunks:
            return []
        results: list[dict[str, Any] | Exception | None] = [None] * len(chunks)
        queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
        for item in enumerate(chunks):
            queue.put_nowait(item)

        async def worker() -> None:
            while True:
                try:
                    index, chunk = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    results[index] = await self.annotate_chunk(index, chunk)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # One exhausted sentence must not retire a worker and
                    # starve every later sentence. Record the failure, keep
                    # draining the queue, then fail the chapter with exact
                    # indices after all independent work has been preserved.
                    results[index] = exc

        worker_count = min(
            len(chunks), max(1, int(getattr(self.args, "concurrency", 1)))
        )
        await gather_all_or_raise(*(worker() for _ in range(worker_count)))
        if any(item is None for item in results):
            raise RuntimeError("Japanese annotation worker left a chunk unresolved")
        failures = [
            (index, item)
            for index, item in enumerate(results)
            if isinstance(item, Exception)
        ]
        if failures:
            detail = "; ".join(
                f"chunk {index}: {error}" for index, error in failures
            )
            raise RuntimeError(
                "Japanese annotation chunks failed after the complete queue "
                f"was drained: {detail}"
            )
        return [
            item for item in results
            if isinstance(item, dict)
        ]

    async def build_reader(
        self, outline: dict[str, Any], chapter: str,
    ) -> dict[str, Any]:
        """Build the independently reviewed annotation layer for fixed prose."""
        await self.ensure_story_vocabulary_plan(chapter)
        chunks = split_japanese_annotation_chunks(
            chapter,
            self.annotation_chunk_target,
            self.annotation_chunk_maximum,
        )
        annotated = await self.annotate_chunks_progressively(chunks)
        segments = [segment for item in annotated for segment in item["segments"]]
        if "".join(segment["surface"] for segment in segments) != chapter:
            raise ValueError("assembled annotations do not reconstruct chapter")
        grammar_overlays = []
        offset = 0
        for chunk, item in zip(chunks, annotated):
            # `item` is the canonical accepted payload. Its historical
            # `attempts` are audit evidence and intentionally preserve what
            # each agent/reviewer saw; they are not publication data. Reading
            # overlays back from the final raw attempt bypassed later safe-link
            # normalization and could resurrect aliases such as 薬缶 -> やかん.
            for overlay in item.get("grammar_overlays", []):
                grammar_overlays.append({
                    **overlay, "start": overlay["start"] + offset,
                    "end": overlay["end"] + offset,
                })
            offset += len(chunk)
        vocabulary_diagnostics = level_diagnostics(
            segments, self.args.level, grammar_overlays,
        )
        (self.run_dir / "level-diagnostics.json").write_text(
            json.dumps(vocabulary_diagnostics, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        reader = {
            "title": outline["chapter_title"], "language": "Japanese",
            "level": self.args.level.upper(), "text": chapter,
            "segments": segments, "grammar_overlays": grammar_overlays,
            "annotation_audit": {
                "chunks": len(annotated),
                "attempts_per_chunk": [len(item["attempts"]) for item in annotated],
                "all_reviewed": all(item["resolved"] for item in annotated),
            },
            "story_vocabulary_audit": {
                "agent_curated": True,
                "independently_verified": True,
                "maximum_unique_terms": STORY_TERM_BUDGET[self.args.level],
                "selected_unique_terms": len(self.story_vocabulary_plan["terms"]),
            },
            "level_diagnostics": vocabulary_diagnostics,
        }
        if not vocabulary_diagnostics["passes"]:
            (self.run_dir / "reader-candidate.json").write_text(
                json.dumps(reader, ensure_ascii=False, indent=2) + "\n"
            )
            raise ValueError(
                f"{self.args.level.upper()} vocabulary ratio "
                f"{vocabulary_diagnostics['above_level_ratio']:.1%} exceeds "
                f"{vocabulary_diagnostics['maximum_above_level_ratio']:.1%}"
            )
        (self.run_dir / "reader.json").write_text(
            json.dumps(reader, ensure_ascii=False, indent=2) + "\n"
        )
        return reader

    async def resume_annotations(self) -> dict[str, Any]:
        """Resume annotation from prose that already cleared chapter review."""
        chapter_path = self.run_dir / "chapter.txt"
        outline_path = self.run_dir / "outline.json"
        if not chapter_path.is_file() or not outline_path.is_file():
            raise ValueError("annotation resume requires chapter.txt and outline.json")
        raw_chapter = chapter_path.read_text(encoding="utf-8")
        chapter = strip_inline_japanese_readings(
            strip_duplicate_source_header(raw_chapter)
        )
        if chapter != raw_chapter:
            reading_backup = self.run_dir / "chapter-before-inline-reading-cleanup.txt"
            if not reading_backup.is_file():
                reading_backup.write_text(raw_chapter, encoding="utf-8")
            chapter_path.write_text(chapter, encoding="utf-8")
        outline = json.loads(outline_path.read_text(encoding="utf-8"))
        self.write_manifest("running")
        try:
            # Never let a passing review of older prose authorize annotations
            # for an edited chapter. The runner cache keys the complete prompt,
            # so unchanged prose reuses evidence while any textual change gets
            # a fresh source/naturalness review before annotation work begins.
            chapter_review = self.reusable_chapter_review_for_selective_refresh(
                chapter
            )
            if chapter_review is None:
                chapter_review = await self.review_chapter(
                    outline, chapter, stage="resume_current_review",
                )
            chapter_repair_attempts: list[dict[str, Any]] = []
            if chapter_review.get("verdict") != "pass":
                original_chapter = chapter
                chapter, chapter_review, chapter_repair_attempts = (
                    await self.heal_chapter_review(
                        outline, chapter, chapter_review,
                    )
                )
                if chapter_review.get("verdict") != "pass":
                    raise ValueError(
                        "current chapter failed exact whole-chapter review before annotation"
                    )
                if chapter != original_chapter:
                    backup_path = (
                        self.run_dir / "chapter-before-resume-review-repair.txt"
                    )
                    if not backup_path.is_file():
                        backup_path.write_text(
                            original_chapter, encoding="utf-8",
                        )
                    chapter_path.write_text(chapter, encoding="utf-8")
            await self.build_reader(outline, chapter)
            count = japanese_char_count(chapter)
            report_path = self.run_dir / "report.json"
            report = (
                json.loads(report_path.read_text(encoding="utf-8"))
                if report_path.is_file() else {}
            )
            selected_scenes = self.adaptation_scenes(outline)
            scene_results = []
            for scene in selected_scenes:
                scene_path = self.run_dir / "scenes" / f"{scene['id']}.json"
                if not scene_path.is_file():
                    raise ValueError(f"source-grounded scene evidence missing: {scene_path}")
                scene_result = json.loads(scene_path.read_text(encoding="utf-8"))
                if scene_result.get("review", {}).get("verdict") != "pass":
                    raise ValueError(f"source-grounded scene did not pass: {scene['id']}")
                scene_results.append(scene_result)

            material_findings = material_review_findings([chapter_review])
            if material_findings:
                raise ValueError("whole-chapter review retains material findings")
            report.update({
                "status": "complete",
                "scenes": len(selected_scenes),
                "source_scenes": len(outline["scenes"]),
                "japanese_chars": count, "chapter_cjk": count,
                "scene_verdicts": {
                    item["scene"]["id"]: item["review"]["verdict"]
                    for item in scene_results
                },
                "scene_attempts": {
                    item["scene"]["id"]: len(item.get("attempts", []))
                    for item in scene_results
                },
                "chapter_review": chapter_review,
                "chapter_review_verification": chapter_review,
                "chapter_review_verdict": "pass",
                "chapter_review_repair_attempts": chapter_repair_attempts,
                "materiality_audit": {
                    "reviewed": True,
                    "unresolved_material_findings": [],
                    "policy": "literary/story vocabulary is diagnostic, not a hard failure",
                },
                "unresolved_scenes": [],
            })
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            )
            self.write_manifest("complete")
            return report
        except BaseException:
            self.write_manifest("failed")
            raise

    def reusable_chapter_review_for_selective_refresh(
        self, chapter: str,
    ) -> dict[str, Any] | None:
        """Reuse prose evidence only for an exact-text annotation-only repair."""
        if not (
            getattr(self.args, "refresh", False)
            and getattr(self.args, "annotation_chunk_indices", None)
        ):
            return None
        report_path = self.run_dir / "report.json"
        reader_path = self.run_dir / "reader.json"
        if not report_path.is_file() or not reader_path.is_file():
            return None
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            reader = json.loads(reader_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        review = (
            report.get("chapter_review_verification")
            or report.get("chapter_review")
        )
        if not (
            report.get("status") == "complete"
            and reader.get("text") == chapter
            and isinstance(review, dict)
            and review.get("verdict") == "pass"
            and not material_review_findings([review])
        ):
            return None
        return copy.deepcopy(review)

    async def vocabulary_rescue_scene(
        self,
        scene: dict[str, Any],
        current: str,
        above_level: list[str],
        *,
        minimum_replacements: int,
        attempt: int = 1,
        review: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Rewrite one accepted scene after deterministic level diagnostics."""
        original = self.source[scene["source_start"]:scene["source_end"]]
        vocabulary = vocabulary_prompt_reference(self.args.level)
        low, high = self.scene_length_bounds(scene["target_chars"])
        planned = getattr(self, "story_vocabulary_plan", {"terms": []})["terms"]
        repair_instruction = (
            "\nThis is an issue-scoped repair. Change only the concrete PRIOR "
            "REVIEW FINDINGS and preserve every unaffected sentence, fact, and "
            "simple word choice. Do not resample the scene."
            if review else ""
        )
        prompt = f"""Return only JSON matching the supplied schema. Rewrite the
complete CURRENT SCENE as natural {self.args.level.upper()} Japanese after a
deterministic vocabulary audit found too many above-level content words. Keep
every REQUIRED EVENT, its source-supported causes, speakers, and order, but use
simple wording from WORKING VOCABULARY wherever possible. Make the smallest
local change that replaces or removes at least {minimum_replacements}
above-level token occurrence(s). Prioritize dispensable transitions,
descriptions, or generic wording. Once that minimum is safely met, preserve
useful source-specific vocabulary rather than flattening every item in the
diagnostic list. A reviewed story term or proper name may remain. Do not swap
one unlisted synonym for another. Aim below 10% exceptional content words to
leave a safety margin under publication.
Keep {low}-{high} Japanese letters. Return the complete scene, not a patch.
{repair_instruction}

WORKING VOCABULARY:
{vocabulary}

REVIEWED STORY TERMS:
{json.dumps(planned, ensure_ascii=False, indent=2)}

ABOVE-LEVEL LEMMAS:
{json.dumps(above_level, ensure_ascii=False, indent=2)}

MINIMUM TOKEN OCCURRENCES TO REPLACE OR REMOVE:
{minimum_replacements}

REQUIRED EVENTS:
{json.dumps(scene['required_events'], ensure_ascii=False, indent=2)}

VERBATIM ORIGINAL:
{original}

CURRENT SCENE:
{current}

PRIOR REVIEW FINDINGS:
{json.dumps(review or {}, ensure_ascii=False, indent=2)}"""
        result = await self.runner.call(
            f"{scene['id']}/vocabulary_rescue_{attempt:02d}",
            prompt,
            SCHEMAS / "adaptation.schema.json",
            self.args.repair_effort,
            refresh=self.args.refresh,
        )
        result["text"] = strip_duplicate_source_header(result["text"])
        return result

    def accept_slightly_short_vocabulary_rescue(
        self,
        scene: dict[str, Any],
        text: str,
        review: dict[str, Any],
    ) -> dict[str, Any]:
        """Let the chapter gate arbitrate a clean scene just below its target."""
        value = copy.deepcopy(review)
        problems = [
            problem for problem in value.get("language_problems", [])
            if not str(problem).startswith("mechanical unit length gate:")
        ]
        substantive = any(value.get(field) for field in (
            "omissions", "unsupported_additions", "distortions",
        )) or bool(problems)
        length = japanese_char_count(text)
        soft_low = int(scene["target_chars"] * 0.65)
        _, high = self.scene_length_bounds(scene["target_chars"])
        if not substantive and soft_low <= length <= high:
            value["language_problems"] = problems
            value["verdict"] = "pass"
            value["harness_decision"] = (
                "accepted_clean_vocabulary_rescue_under_chapter_length_gate"
            )
        return value

    async def repair_vocabulary(self) -> dict[str, Any]:
        """Use failed deterministic diagnostics to repair only affected scenes."""
        chapter_path = self.run_dir / "chapter.txt"
        outline_path = self.run_dir / "outline.json"
        candidate_path = self.run_dir / "reader-candidate.json"
        if not all(path.is_file() for path in (
            chapter_path, outline_path, candidate_path,
        )):
            raise ValueError(
                "vocabulary repair requires chapter.txt, outline.json, and "
                "reader-candidate.json"
            )
        raw_chapter = chapter_path.read_text(encoding="utf-8")
        chapter = strip_duplicate_source_header(raw_chapter)
        outline = json.loads(outline_path.read_text(encoding="utf-8"))
        reader = json.loads(candidate_path.read_text(encoding="utf-8"))
        plan_path = self.run_dir / "story-vocabulary-plan.json"
        self.story_vocabulary_plan = (
            json.loads(plan_path.read_text(encoding="utf-8"))
            if plan_path.is_file() else {"terms": []}
        )
        raw_paragraphs = chapter.rstrip("\n").split("\n\n")
        selected_scenes = self.adaptation_scenes(outline)
        if len(raw_paragraphs) < len(selected_scenes):
            raise ValueError("chapter paragraphs no longer align with source scenes")
        excess = len(raw_paragraphs) - len(selected_scenes)
        paragraphs = [
            "\n\n".join(raw_paragraphs[:excess + 1]),
            *raw_paragraphs[excess + 1:],
        ]

        segment_groups: list[list[dict[str, Any]]] = []
        segment_index = 0
        segments = reader["segments"]
        grammar_overlays = reader.get("grammar_overlays", [])
        global_diagnostics = level_diagnostics(
            segments, self.args.level, grammar_overlays,
        )
        effective_levels: dict[int, int | None] = {}
        level_offset = 0
        for segment in segments:
            level_end = level_offset + len(str(segment.get("surface", "")))
            effective_levels[id(segment)] = matched_level_with_overlays(
                segment, level_offset, level_end, grammar_overlays,
            )
            level_offset = level_end
        replacement_quota = minimum_vocabulary_replacements(global_diagnostics)
        removed_prefix = len(raw_chapter) - len(chapter)
        skipped = 0
        while segment_index < len(segments) and skipped < removed_prefix:
            skipped += len(str(segments[segment_index].get("surface", "")))
            segment_index += 1
        if skipped != removed_prefix:
            raise ValueError("chapter header does not align with annotation segments")
        for index, paragraph in enumerate(paragraphs):
            expected = paragraph + ("\n\n" if index + 1 < len(paragraphs) else "\n")
            group: list[dict[str, Any]] = []
            consumed = 0
            while segment_index < len(segments) and consumed < len(expected):
                segment = segments[segment_index]
                group.append(segment)
                consumed += len(str(segment.get("surface", "")))
                segment_index += 1
            if consumed != len(expected):
                raise ValueError("annotation segments do not align with scene paragraphs")
            segment_groups.append(group)

        target = LEVEL_NUMBER[self.args.level]

        def above_segments(group: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [
                segment for segment in group
                if str(segment.get("lemma", "")).strip()
                and segment.get("type") not in {
                    "punctuation", "grammar", "particle", "auxiliary", "name",
                }
                and str(segment.get("lemma", "")).strip() not in STORY_ALLOWLIST
                and (
                    effective_levels[id(segment)] is None
                    or effective_levels[id(segment)] > target
                )
            ]

        group_above = [above_segments(group) for group in segment_groups]
        allocations = [0] * len(segment_groups)
        remaining_quota = replacement_quota
        for index in sorted(
            range(len(group_above)),
            key=lambda item: len(group_above[item]), reverse=True,
        ):
            if remaining_quota <= 0:
                break
            allocations[index] = min(remaining_quota, len(group_above[index]))
            remaining_quota -= allocations[index]
        if remaining_quota:
            raise ValueError("vocabulary rescue could not allocate replacement quota")

        self.write_manifest("running")
        try:
            async def rescue(
                scene: dict[str, Any], text: str, group: list[dict[str, Any]],
                minimum_replacements: int,
            ) -> str:
                if minimum_replacements <= 0:
                    return text
                above = list(dict.fromkeys(
                    str(segment.get("lemma", "")).strip()
                    for segment in above_segments(group)
                ))
                candidate = text
                last_review: dict[str, Any] | None = None
                for attempt in range(1, 9):
                    result = await self.vocabulary_rescue_scene(
                        scene, candidate, above,
                        minimum_replacements=minimum_replacements,
                        attempt=attempt, review=last_review,
                    )
                    candidate = result["text"]
                    last_review = await self.review_scene(
                        scene, candidate,
                        suffix=f"vocabulary_rescue_{attempt:02d}_review",
                    )
                    last_review = self.accept_slightly_short_vocabulary_rescue(
                        scene, candidate, last_review,
                    )
                    if last_review["verdict"] == "pass":
                        return candidate
                raise ValueError(
                    f"vocabulary rescue did not pass source review for {scene['id']}"
                )

            repaired = await gather_all_or_raise(*(
                rescue(scene, text, group, allocation)
                for scene, text, group, allocation in zip(
                    selected_scenes, paragraphs, segment_groups, allocations
                )
            ))
            revised_chapter = "\n\n".join(repaired) + "\n"
            chapter_review = await self.review_chapter(
                outline, revised_chapter, stage="vocabulary_rescue_review",
            )
            if chapter_review["verdict"] != "pass":
                revised_chapter, chapter_review, _ = (
                    await self.heal_chapter_review(
                        outline, revised_chapter, chapter_review,
                    )
                )
            if chapter_review["verdict"] != "pass":
                raise ValueError("vocabulary-repaired chapter failed chapter review")
            revised_chapter = strip_duplicate_source_header(revised_chapter)
            backup = self.run_dir / "chapter-before-vocabulary-repair.txt"
            if not backup.is_file():
                backup.write_text(raw_chapter, encoding="utf-8")
            chapter_path.write_text(revised_chapter, encoding="utf-8")
            return await self.resume_annotations()
        except BaseException:
            self.write_manifest("failed")
            raise

    async def run(self) -> dict[str, Any]:
        # Same fail-closed state machine as the Chinese harness, with a Japanese
        # length metric and Japanese annotation schema/prompts.
        self.write_manifest("running")
        try:
            outline = await self.outline()
            selected_scenes = self.adaptation_scenes(outline)
            results = await gather_all_or_raise(*(
                self.process_or_reuse_scene(scene) for scene in selected_scenes
            ))
            chapter = strip_inline_japanese_readings(strip_duplicate_source_header(
                "\n\n".join(item["text"].strip() for item in results)
            )) + "\n"
            verdicts = {item["scene"]["id"]: item["review"]["verdict"] for item in results}
            final_status = run_status_for_verdicts(verdicts)
            chapter_review = None
            chapter_review_initial = None
            chapter_review_verification = None
            chapter_repair_attempts: list[dict[str, Any]] = []
            level_preflight = None
            level_preflight_attempts: list[dict[str, Any]] = []
            if final_status == "complete":
                chapter_review = await self.review_chapter(outline, chapter)
                chapter_review_initial = copy.deepcopy(chapter_review)
                if chapter_review["verdict"] == "pass":
                    chapter_review = await self.review_chapter(
                        outline, chapter, stage="verification"
                    )
                    chapter_review_verification = copy.deepcopy(chapter_review)
                if chapter_review["verdict"] != "pass":
                    chapter, chapter_review, chapter_repair_attempts = (
                        await self.heal_chapter_review(
                            outline, chapter, chapter_review
                        )
                    )
                    if chapter_review["verdict"] != "pass":
                        final_status = "blocked"
            material_findings = material_review_findings(
                [item["review"] for item in results] + ([chapter_review] if chapter_review else [])
            )
            if material_findings:
                final_status = "blocked"
            if final_status == "complete" and not self.args.skip_annotations:
                chapter, level_preflight, level_preflight_attempts = (
                    await self.heal_level_preflight(outline, chapter)
                )
                if level_preflight_attempts:
                    latest_source_review = level_preflight_attempts[-1].get(
                        "source_review"
                    )
                    if isinstance(latest_source_review, dict):
                        chapter_review = latest_source_review
                        chapter_review_verification = copy.deepcopy(
                            level_preflight_attempts[-1].get(
                                "source_verification"
                            )
                        )
                if not level_preflight["passes"]:
                    final_status = "blocked"
            name = "chapter.txt" if final_status == "complete" else "chapter-candidate.txt"
            (self.run_dir / name).write_text(chapter, encoding="utf-8")
            if final_status == "complete" and not self.args.skip_annotations:
                await self.build_reader(outline, chapter)
            count = japanese_char_count(chapter)
            report = {
                "status": final_status, "scenes": len(results),
                "source_scenes": len(outline["scenes"]),
                "japanese_chars": count, "chapter_cjk": count,
                "scene_verdicts": verdicts,
                "chapter_review": chapter_review,
                "chapter_review_initial": chapter_review_initial,
                "chapter_review_verification": chapter_review_verification,
                "chapter_repair_attempts": chapter_repair_attempts,
                "level_preflight": level_preflight,
                "level_preflight_attempts": level_preflight_attempts,
                "chapter_review_verdict": chapter_review.get("verdict") if chapter_review else "not_run",
                "materiality_audit": {
                    "reviewed": chapter_review is not None,
                    "unresolved_material_findings": material_findings,
                    "policy": "literary/story vocabulary is diagnostic, not a hard failure",
                },
                "scene_attempts": {x["scene"]["id"]: len(x["attempts"]) for x in results},
                "unresolved_scenes": [key for key, value in verdicts.items() if value != "pass"],
            }
            (self.run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            self.write_manifest(final_status)
            return report
        except BaseException:
            self.write_manifest("failed")
            raise


class JapaneseBookHarness(BookHarness):
    def completed_run(
        self, source_path: Path, level: str, run_id: str, target_chars: int
    ) -> dict[str, Any] | None:
        """Return a verified completed run, otherwise require a real rerun."""
        run_dir = Path(self.args.runs_dir).resolve() / run_id
        manifest_path, report_path = run_dir / "manifest.json", run_dir / "report.json"
        chapter_path, reader_path = run_dir / "chapter.txt", run_dir / "reader.json"
        if not all(path.is_file() for path in (manifest_path, report_path, chapter_path)):
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if manifest.get("status") != "complete" or report.get("status") != "complete":
            return None
        if manifest.get("source_sha256") != digest(source_path.read_text(encoding="utf-8")):
            return None
        if manifest.get("source") != str(source_path):
            return None
        if manifest.get("level") != level or manifest.get("target_chars") != target_chars:
            return None
        if not self.args.skip_annotations and not reader_path.is_file():
            return None
        count = japanese_char_count(chapter_path.read_text(encoding="utf-8"))
        if report.get("japanese_chars") != count or report.get("chapter_cjk") != count:
            return None
        return {
            "source": str(source_path), "level": level, "run_id": run_id,
            "status": "complete", "chapter_cjk": count,
            "japanese_chars": count, "target_chars": target_chars,
            "attempts": 0, "cached": True,
        }

    async def run_one(self, source: str, level: str, target_chars: int | None = None) -> dict[str, Any]:
        source_path = Path(source).resolve()
        run_id = f"{self.args.book_run_id}/{source_path.stem}-{level}"
        chapter_args = copy.copy(self.args)
        chapter_args.command, chapter_args.source = "run", str(source_path)
        chapter_args.level, chapter_args.run_id = level, run_id
        chapter_args.target_chars = target_chars or self.args.target_chars or target_for_source(source_path, level)
        completed = self.completed_run(source_path, level, run_id, chapter_args.target_chars)
        if completed is not None:
            return completed
        last_error = ""
        async with self.chapter_semaphore:
            for attempt in range(self.args.chapter_retries + 1):
                try:
                    report = await JapaneseChapterHarness(chapter_args, semaphore=self.agent_semaphore).run()
                    if report["status"] != "complete":
                        last_error = "quality gates unresolved: " + ", ".join(report.get("unresolved_scenes", []))
                        if attempt < self.args.chapter_retries:
                            verified_outline = run_dir / "outline.json"
                            verified_review = run_dir / "outline-review.json"
                            if verified_outline.is_file() and verified_review.is_file():
                                try:
                                    review_evidence = json.loads(
                                        verified_review.read_text(encoding="utf-8")
                                    )
                                except (OSError, ValueError, TypeError):
                                    review_evidence = {}
                                if review_evidence.get("verdict") == "pass":
                                    chapter_args.outline_file = str(verified_outline)
                            chapter_args.refresh = True
                            await asyncio.sleep(min(60, 5 * (3 ** attempt)))
                            continue
                    return {"source": str(source_path), "level": level, "run_id": run_id, "status": report["status"], "chapter_cjk": report["chapter_cjk"], "japanese_chars": report["japanese_chars"], "target_chars": chapter_args.target_chars, "attempts": attempt + 1}
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    if attempt < self.args.chapter_retries:
                        chapter_args.refresh = True
                        await asyncio.sleep(min(60, 5 * (3 ** attempt)))
            return {"source": str(source_path), "level": level, "run_id": run_id, "status": "failed", "attempts": self.args.chapter_retries + 1, "error": last_error}

    async def run(self) -> dict[str, Any]:
        planned = [
            {"source": str(Path(source).resolve()), "level": level, "status": "pending"}
            for source in self.args.source for level in self.args.levels
        ]
        self.write_report("running", planned)
        tasks = [asyncio.create_task(self.run_one(x["source"], x["level"])) for x in planned]
        results: list[dict[str, Any]] = []
        for task in asyncio.as_completed(tasks):
            results.append(await task)
            self.write_report("running", results)
        for _ in range(self.args.length_repair_rounds):
            violations = length_violations(results, self.args.levels)
            if not violations:
                break
            for problem in violations:
                current = next(x for x in results if (x["source"], x["level"]) == (problem["source"], problem["upper_level"]))
                target = max(
                    target_for_source(problem["source"], problem["upper_level"]),
                    problem["lower_cjk"] + max(100, problem["lower_cjk"] // 10),
                    int(current.get("target_chars", 0) * 1.25),
                )
                replacement = await self.run_one(problem["source"], problem["upper_level"], target)
                results = [replacement if (x["source"], x["level"]) == (problem["source"], problem["upper_level"]) else x for x in results]
                self.write_report("running", results)
        violations = length_violations(results, self.args.levels)
        final = "complete" if {x["status"] for x in results} == {"complete"} and not violations else "blocked"
        self.write_report(final, results, violations)
        return {"status": final, "runs": results}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for command in ("run", "book"):
        item = sub.add_parser(command)
        item.add_argument("--source", action="append" if command == "book" else "store", required=command == "run")
        if command == "run":
            item.add_argument("--level", choices=JLPT_LEVELS, default="n3")
            item.add_argument("--run-id")
            item.add_argument("--concurrency", type=int, default=3)
            item.add_argument(
                "--resume-annotations", action="store_true",
                help="reuse accepted chapter.txt and resume only reader annotations",
            )
            item.add_argument(
                "--annotation-chunk-index", dest="annotation_chunk_indices",
                type=int, action="append",
                help=(
                    "with --resume-annotations --refresh, regenerate only this "
                    "zero-based annotation chunk; may be repeated"
                ),
            )
            item.add_argument(
                "--repair-vocabulary", action="store_true",
                help="repair level-gate failures scene by scene, then re-annotate",
            )
        else:
            item.add_argument("--source-dir", help="directory containing chapter_*.txt")
            item.add_argument("--levels", nargs="+", choices=JLPT_LEVELS, default=JLPT_LEVELS)
            item.add_argument("--book-run-id", required=True)
            item.add_argument("--concurrency", type=int, default=12)
            item.add_argument("--chapter-concurrency", type=int, default=6)
            item.add_argument("--chapter-retries", type=int, default=2)
            item.add_argument("--length-repair-rounds", type=int, default=2)
        item.add_argument("--runs-dir", default=str(DEFAULT_RUNS))
        item.add_argument("--model", default="gpt-6-luna")
        item.add_argument("--adapt-effort", default="low")
        item.add_argument("--review-effort", default="low")
        item.add_argument("--repair-effort", default="low")
        item.add_argument("--final-effort", default="low")
        item.add_argument("--annotation-effort", default="low")
        item.add_argument("--annotation-review-effort", default="low")
        item.add_argument("--annotation-repair-effort", default="low")
        item.add_argument("--annotation-final-effort", default="low")
        item.add_argument("--max-annotation-repairs", type=int, default=2)
        item.add_argument("--max-annotation-fresh-repairs", type=int, default=6)
        item.add_argument("--max-annotation-adjudications", type=int, default=1)
        item.add_argument("--max-repairs", type=int, default=3)
        item.add_argument("--no-fresh-rewrite", dest="fresh_rewrite", action="store_false")
        item.set_defaults(fresh_rewrite=True)
        item.add_argument(
            "--annotation-chunk", type=int,
            help="override the level-specific small Japanese annotation batch target",
        )
        item.add_argument(
            "--annotation-chunk-maximum", type=int,
            help="hard sentence-complete Japanese annotation batch maximum",
        )
        item.add_argument("--target-chars", type=int)
        item.add_argument(
            "--outline-file",
            help="reuse a complete level-specific scene map and event ledger",
        )
        item.add_argument(
            "--source-map-file",
            help="reuse fixed source spans while selecting a fresh level-specific event ledger",
        )
        item.add_argument(
            "--reuse-scenes-from",
            help=(
                "reuse source-identical scene JSON whose independent review "
                "already passed; regenerate every missing, changed, or failed scene"
            ),
        )
        item.add_argument("--timeout", type=int, default=900)
        item.add_argument("--skip-annotations", action="store_true")
        item.add_argument("--refresh", action="store_true")
    stat = sub.add_parser("status")
    stat.add_argument("run_dir")
    return p


def main() -> int:
    command_parser = parser()
    args = command_parser.parse_args()
    if args.command == "status":
        return status(Path(args.run_dir))
    if args.command == "book":
        if args.source_dir:
            discovered = sorted(Path(args.source_dir).glob("chapter_*.txt"))
            args.source = (args.source or []) + [str(path) for path in discovered]
        if not args.source:
            command_parser.error("book requires --source or --source-dir with chapter_*.txt")
        result = asyncio.run(JapaneseBookHarness(args).run())
        return 0 if result["status"] == "complete" else 2
    if args.resume_annotations and args.repair_vocabulary:
        command_parser.error(
            "--resume-annotations and --repair-vocabulary are mutually exclusive"
        )
    harness = JapaneseChapterHarness(args)
    asyncio.run(
        harness.repair_vocabulary()
        if args.repair_vocabulary else
        harness.resume_annotations()
        if args.resume_annotations else
        harness.run()
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
