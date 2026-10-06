import importlib.util
from pathlib import Path
import copy
import pytest

from pipeline.annotation_lexical_references import lexical_review_references as select


def test_actual_korean_alternatives_preserve_identity_and_payload():
    from pipeline.korean_agent_harness import KoreanHarness
    h=KoreanHarness(Path('runs/korean-topik4/chapter-001'),1,level=4,workers=1)
    before=copy.deepcopy((h.words,h.catalog))
    result=select(used_ids={'있다01/형'},headwords={'있다'},approved=h.words,catalog=h.catalog)
    words={row['id']:row for row in result['approved_words']}
    assert set(words)=={'있다01/형','있다01/동','있다01/보'}
    assert all(words[key]==h.words[key] for key in words)
    assert {'있다01/형','있다01/동','있다01/보'} <= {row['id'] for row in result['lexical_candidates']}
    assert (h.words,h.catalog)==before
    words['있다01/동']['definition_en']='changed caller draft'
    assert h.words['있다01/동']==before[0]['있다01/동']


def test_unapproved_catalog_record_stays_candidate():
    entry={'id':'new','headword':'same','pos':'verb','meaning':'candidate meaning'}
    result=select(used_ids=set(),headwords={'same'},approved={},catalog={'same':[entry]})
    assert result['approved_words']==[] and result['lexical_candidates']==[entry]


def test_used_record_retained_even_when_no_vocabulary_lemma():
    word={'id':'used','headword':'other','kind':'expression'}
    result=select(used_ids={'used'},headwords=set(),approved={'used':word},catalog={'other':[word]})
    assert result=={'approved_words':[word],'lexical_candidates':[word]}


@pytest.mark.parametrize('headword',['있기','있','없다',''])
def test_no_suffix_surface_or_gloss_guessing(headword):
    row={'id':'있다01/동','headword':'있다','definition_en':'to stay'}
    same_gloss={'id':'머무르다','headword':'머무르다','definition_en':'to stay'}
    result=select(used_ids=set(),headwords={headword},approved={r['id']:r for r in (row,same_gloss)},catalog={})
    assert result['approved_words']==[]


def test_same_headword_readings_and_kinds_are_not_merged():
    rows=[{'id':'a','headword':'生','reading':'せい','kind':'word'},
          {'id':'b','headword':'生','reading':'なま','kind':'word'},
          {'id':'c','headword':'生','reading':'なま','kind':'expression'}]
    result=select(used_ids=set(),headwords={'生'},approved={r['id']:r for r in rows},catalog={'生':rows})
    assert result=={'approved_words':rows,'lexical_candidates':rows}
