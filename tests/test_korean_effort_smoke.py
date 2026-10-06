import json
import asyncio
import pytest
from pipeline.agent_harness import CodexRunner
from pipeline.korean_effort_smoke import usage_totals, check_result
from pipeline import korean_annotation_chunks as chunks


def test_usage_counts_all_completed_attempts_without_double_counting_reasoning(tmp_path):
    for n in (1, 2):
        events = [{'type': 'turn.started'}, {'type': 'turn.completed', 'usage': {
            'input_tokens': 100, 'cached_input_tokens': 40, 'output_tokens': 20,
            'reasoning_output_tokens': 5}}]
        (tmp_path / f'events.attempt-{n:02}.jsonl').write_text('\n'.join(map(json.dumps, events)))
    assert usage_totals(tmp_path) == {'input_tokens': 200, 'cached_input_tokens': 80,
                                     'output_tokens': 40, 'reasoning_output_tokens': 10}


def test_benchmark_effort_rejects_unsupported_values(tmp_path):
    with pytest.raises(ValueError, match='Benchmark effort'):
        CodexRunner(tmp_path, 'gpt-6-luna', asyncio.Semaphore(1), benchmark_effort='typo')


def test_smoke_does_not_accept_schema_valid_missing_source():
    raw = {'format': chunks.SPAN_FORMAT, 'segments': [{
        'type': 'punctuation', 'meaning_en': '', 'lemma': '', 'lexical_kind': '',
        'lexical_id': '', 'story_importance_en': '', 'form_steps': [],
        'is_inflected': False, 'grammar_links': [], 'expression_links': [],
        'source_start': 0, 'source_end': 1}]}
    case = {'source_text': ' ', 'lexical_catalog': {}, 'lexical_plan': {'entries': []}, 'words': {}}
    assert check_result(raw, case)['segments'][0]['text'] == ' '
    with pytest.raises(ValueError, match='cover all'):
        check_result(raw, {**case, 'source_text': ' . '})
    with pytest.raises(ValueError, match='cannot hide'):
        check_result(raw, {**case, 'source_text': '가'})
