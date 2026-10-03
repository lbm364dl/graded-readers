import json

from pipeline.usage_dictionary import ROOT


def test_yellow_turban_dropdown_keeps_qishi_as_a_word():
    doc = json.loads((ROOT / 'content/chinese/sanguoyanyi/hsk2.annotations.json').read_text())
    segment = next(s for s in doc['chapters'][0]['segments'] if s['text'] == '黄巾起事')
    assert [s['text'] for s in segment['subsegments']] == ['黄巾', '起事']
    assert [(s['start'], s['end']) for s in segment['subsegments']] == [(0, 2), (2, 4)]
    assert 'uprising' in segment['subsegments'][1]['meaning_en']
