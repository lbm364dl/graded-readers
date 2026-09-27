import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/models.dart';

void main() {
  const phrase = AgentGrammarOverlay(
    start: 2,
    end: 8,
    text: '越说越高兴',
    grammarCandidateKey: 'yue-yue',
    pattern: '越…越…',
    meaningEn: 'the more …, the more …',
  );
  const unit = AgentGrammarOverlay(
    start: 3,
    end: 5,
    text: '说了',
    grammarCandidateKey: 'unit',
    pattern: '说了',
    meaningEn: '了 marks the completed speaking action',
  );

  test('Chinese phrase notes appear on interior tokens, not adjacent tokens',
      () {
    expect(
        chineseGrammarForSpan(start: 4, end: 5, overlays: [phrase]), [phrase]);
    expect(
        chineseGrammarForSpan(start: 0, end: 2, overlays: [phrase]), isEmpty);
    expect(
        chineseGrammarForSpan(start: 8, end: 9, overlays: [phrase]), isEmpty);
  });

  test('grouped taps retain unit and surrounding phrase explanations', () {
    expect(
        chineseGrammarForSpan(
            start: 3, end: 5, overlays: [phrase], readingUnitGrammar: unit),
        [unit, phrase]);
    expect(
        chineseGrammarForSpan(
            start: 3,
            end: 5,
            overlays: [unit, phrase],
            readingUnitGrammar: unit),
        [unit, phrase]);
  });

  test('Japanese matching remains unchanged', () {
    const token = AgentSegment(
        text: '说', type: 'word', pinyin: 'shuō', meaningEn: 'say', lemma: '说');
    expect(
        japaneseGrammarForSegment(
            segment: token, start: 4, end: 5, overlays: [phrase]),
        isEmpty);
  });
}
