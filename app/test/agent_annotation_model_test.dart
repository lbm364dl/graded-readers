import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/models.dart';

void main() {
  test('parses agent chapter annotations without dictionary data', () {
    final annotation = AgentChapterAnnotation.fromJson({
      'text': '刘备来了。',
      'segments': [
        {
          'text': '刘备',
          'type': 'name',
          'pinyin': 'Liú Bèi',
          'meaning_en': 'Liu Bei',
          'story_term': '',
          'story_term_meaning_en': '',
          'story_term_importance_en': '',
          'target_hsk_level': 1,
          'hsk_status': 'unlisted',
          'matched_hsk_level': null,
          'character_hsk_level': null,
          'hsk_evidence': 'not_in_hsk_3_word_or_single_character_lists',
          'learning_focus': 'lookup',
          'lookup_reason': 'proper_name',
          'focus_review_note_en': '',
          'explanation_zh': '',
          'example_zh': '',
          'composition_en': 'The name combines the family and given names.',
          'subsegments': [
            {'start': 0, 'end': 1, 'text': '刘', 'meaning_en': 'surname Liu'},
            {
              'start': 1,
              'end': 2,
              'text': '备',
              'meaning_en': 'given-name character'
            },
          ],
        },
        {
          'text': '来了',
          'type': 'word',
          'pinyin': 'lái le',
          'meaning_en': 'arrived',
          'grammar_candidate_keys': ['test.completed_action'],
        },
        {
          'text': '。',
          'type': 'punctuation',
          'pinyin': '',
          'meaning_en': '',
        },
      ],
      'grammar_overlays': [
        {
          'start': 2,
          'end': 4,
          'text': '来了',
          'grammar_candidate_key': 'test.completed_action',
          'pattern': 'V了',
          'meaning_en': 'completed action',
        },
      ],
    });

    expect(
        annotation.segments.map((item) => item.text).join(), annotation.text);
    expect(annotation.segments.first.meaningEn, 'Liu Bei');
    expect(annotation.segments.first.isLookupOnly, isTrue);
    expect(annotation.segments.first.lookupReason, 'proper_name');
    expect(annotation.segments.first.targetHskLevel, 1);
    expect(annotation.segments.first.hasSubsegments, isTrue);
    expect(annotation.segments.first.subsegments.last.text, '备');
    expect(
        annotation.segments.first.subsegments.first.meaningEn, 'surname Liu');
    expect(annotation.segments[1].hasGrammar, isTrue);
    expect(
        annotation.segments[1].grammarCandidateKeys, ['test.completed_action']);
    expect(annotation.grammarOverlays.single.grammarCandidateKey,
        'test.completed_action');
    expect(annotation.grammarOverlays.single.pattern, 'V了');
  });
}
