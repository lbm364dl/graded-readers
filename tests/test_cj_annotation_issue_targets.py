"""New review submissions bind exact fields; historical schemas remain replayable."""
import asyncio
import json
from argparse import Namespace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pipeline.agent_harness import ChapterHarness
from pipeline.japanese_agent_harness import JapaneseChapterHarness
from pipeline.annotation_issue_targets import IssueTargetError, validate_issue_targets

SCHEMAS = Path(__file__).parents[1] / 'pipeline/schemas'

@pytest.mark.parametrize('prefix', ['annotation-review', 'japanese-annotation-review'])
def test_historical_review_schema_replays_and_new_schema_requires_targets(prefix):
    issue = {'segment_text': '人', 'problem': 'meaning', 'explanation': 'Incorrect meaning.',
             'suggested_fix': 'person', 'start': 0, 'end': 1}
    if prefix.startswith('japanese'):
        issue.pop('start'); issue.pop('end')
    legacy = {'verdict': 'revise', 'issues': [issue]}
    old = json.loads((SCHEMAS / f'{prefix}.schema.json').read_text())
    new = json.loads((SCHEMAS / f'{prefix}-targets.schema.json').read_text())
    assert Draft202012Validator(old).is_valid(legacy)
    assert not Draft202012Validator(new).is_valid(legacy)
    issue.update(candidate_paths=['/segments/1/meaning_en'], supporting_paths=[])
    assert Draft202012Validator(new).is_valid(legacy)

@pytest.mark.parametrize('cls,representation,surface_key', [
    (ChapterHarness, 'chinese-annotation', 'text'),
    (JapaneseChapterHarness, 'japanese-annotation', 'surface'),
])
def test_new_producers_require_targets_before_normalization(cls, representation, surface_key):
    class Runner:
        async def call(self, job, prompt, schema, *args, **kwargs):
            assert schema.name.endswith('-targets.schema.json')
            assert 'candidate_paths' in prompt and 'supporting_paths' in prompt
            context = kwargs['workspace_context']['annotation_issue_targets_validation']
            assert context['require_typed'] is True
            assert context['representation'] == representation
            return {'verdict': 'revise', 'issues': [{'segment_text': '人', 'problem': 'meaning',
                'explanation': 'Incorrect meaning.', 'suggested_fix': 'person'}]}
    harness = object.__new__(cls)
    harness.runner = Runner()
    harness.args = Namespace(annotation_review_effort='low', refresh=False)
    annotation = {'segments': [{surface_key:'人', 'type':'word', 'pinyin':'rén',
                               'meaning_en':'wrong'}], 'grammar_overlays':[]}
    with pytest.raises(IssueTargetError, match='require'):
        asyncio.run(harness.review_annotation(0, '人', annotation, 'review'))


def test_chinese_typed_pointer_cannot_override_wrong_source_position():
    candidate = {'segments': [{'text':'人', 'meaning_en':'person'},
                              {'text':'人', 'meaning_en':'wrong'}], 'grammar_overlays':[]}
    issue = {'start':0, 'end':1, 'segment_text':'人',
             'candidate_paths':['/segments/1/meaning_en'], 'supporting_paths':[]}
    with pytest.raises(IssueTargetError, match='source position'):
        validate_issue_targets({'issues':[issue]}, candidate, source_text='人人',
                               representation='chinese-annotation', require_typed=True)
    issue.update(start=1,end=2)
    validate_issue_targets({'issues':[issue]}, candidate, source_text='人人',
                           representation='chinese-annotation', require_typed=True)


def test_japanese_repeated_surface_binds_distinct_occurrence_and_context():
    candidate = {'segments': [{'surface':'人', 'meaning_en':'person'},
                              {'surface':'人', 'meaning_en':'wrong'}], 'grammar_overlays':[]}
    issue = {'segment_text':'人', 'candidate_paths':['/segments/1/meaning_en'],
             'supporting_paths':['/segments/0/meaning_en']}
    validate_issue_targets({'issues':[issue]}, candidate, source_text='人人',
                           representation='japanese-annotation', require_typed=True)
    issue['supporting_paths'] = ['/segments/1/meaning_en']
    with pytest.raises(IssueTargetError, match='disjoint'):
        validate_issue_targets({'issues':[issue]}, candidate, source_text='人人',
                               representation='japanese-annotation', require_typed=True)
