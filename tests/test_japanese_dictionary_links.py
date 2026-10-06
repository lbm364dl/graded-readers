from pipeline.japanese_dictionary_links import (
    candidate_for_lemma,
    canonical_entry,
    dictionary_link_issue,
)


def test_exact_lexical_link_accepts_canonical_headword_and_selected_sense():
    assert dictionary_link_issue(
        lemma="捕まえる",
        lemma_kana="つかまえる",
        key="捕まえる",
        definition="to catch, to arrest",
    ) is None


def test_contextual_link_label_need_not_copy_a_sparse_dictionary_gloss():
    assert dictionary_link_issue(
        lemma="する", lemma_kana="する", key="する",
        definition="make; cause to become",
    ) is None


def test_reading_alias_is_not_an_authored_dictionary_target():
    assert canonical_entry("つかまえる") is None
    assert "canonical" in dictionary_link_issue(
        lemma="捕まえる",
        lemma_kana="つかまえる",
        key="つかまえる",
        definition="to catch, to arrest",
    )


def test_grammar_homograph_is_deliberately_not_linkable():
    # The local dictionary has a canonical と entry, but that lexical sense is
    # not the quotative particle. Functional metadata prevents the wrong card.
    assert canonical_entry("と")["d"] == ["and"]
    assert "functional grammar" in dictionary_link_issue(
        lemma="と",
        lemma_kana="と",
        key="と",
        definition="and",
        functional=True,
    )


def test_lexical_nai_can_link_while_auxiliary_nai_cannot():
    assert dictionary_link_issue(
        lemma="ない",
        lemma_kana="ない",
        key="ない",
        definition="does not exist; is absent",
        functional=False,
        require_available=True,
    ) is None
    assert "functional grammar" in dictionary_link_issue(
        lemma="ない",
        lemma_kana="ない",
        key="ない",
        definition="not",
        functional=True,
    )


def test_no_link_requires_no_fake_selected_sense():
    assert dictionary_link_issue(
        lemma="だ", lemma_kana="だ", key="", definition="",
        functional=True,
    ) is None
    assert "must be empty" in dictionary_link_issue(
        lemma="だ", lemma_kana="だ", key="", definition="is",
        functional=True,
    )


def test_safe_available_lexical_entry_cannot_be_silently_left_unlinked():
    assert "must be linked" in dictionary_link_issue(
        lemma="捕まえる", lemma_kana="つかまえる", key="", definition="",
        require_available=True,
    )


def test_absent_compound_entry_may_rely_on_linked_root_components():
    assert dictionary_link_issue(
        lemma="動き出す", lemma_kana="うごきだす", key="", definition="",
        require_available=True,
    ) is None


def test_kana_lemma_offers_written_canonical_homophones_for_semantic_choice():
    evidence = candidate_for_lemma("ある", "ある")
    assert evidence is not None
    keys = {
        item["dictionary_key"]
        for item in evidence["canonical_candidates"]
    }
    assert {"在る", "有る"}.issubset(keys)
    assert "ある" not in keys


def test_kana_lemma_can_link_to_the_correct_written_canonical_entry():
    assert dictionary_link_issue(
        lemma="ある",
        lemma_kana="ある",
        key="在る",
        definition="exist; be present",
        require_available=True,
    ) is None
    assert "must be linked" in dictionary_link_issue(
        lemma="ある",
        lemma_kana="ある",
        key="",
        definition="",
        require_available=True,
    )


def test_kana_lemma_without_a_local_candidate_is_not_forced_to_link():
    assert candidate_for_lemma("すく", "すく") is None
    assert dictionary_link_issue(
        lemma="すく",
        lemma_kana="すく",
        key="",
        definition="",
        require_available=True,
    ) is None
    assert "does not match" in dictionary_link_issue(
        lemma="すく",
        lemma_kana="すく",
        key="空く",
        definition="become empty; become hungry",
        require_available=True,
    )


def test_source_spelling_variant_can_use_verified_canonical_spelling():
    evidence = candidate_for_lemma("棄てる", "すてる")
    assert evidence is not None
    assert evidence["dictionary_key"] == "捨てる"
    assert dictionary_link_issue(
        lemma="棄てる",
        lemma_kana="すてる",
        key="捨てる",
        definition="abandon; cast away",
        require_available=True,
    ) is None


def test_exact_written_lemma_cannot_drift_to_same_reading_homograph():
    assert "same-reading homograph" in dictionary_link_issue(
        lemma="方",
        lemma_kana="ほう",
        key="法",
        definition="option; alternative",
        require_available=True,
    )
