import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/data.dart';
import 'package:hsk_graded/models.dart';
import 'package:hsk_graded/services/korean_dictionary.dart';
import 'package:hsk_graded/services/korean_sentence_breakdowns.dart';
import 'package:hsk_graded/screens/korean_dictionary_screen.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('Korean pilot loads with the reviewed source-aligned annotation',
      () async {
    final books = await ContentRepository().loadBooks(Language.korean);
    expect(books, hasLength(1));
    expect(books.single.title, '홍길동전');
    final reader = books.single.levels[1]!;
    expect(reader.levelLabel, 'Level 1');
    final chapter = reader.chapters.single;
    final annotation =
        jsonDecode(await rootBundle.loadString(chapter.annotationAsset))
            as Map<String, dynamic>;
    expect(annotation['text'], chapter.content);
    final segments =
        (annotation['segments'] as List).cast<Map<String, dynamic>>();
    expect(segments.map((segment) => segment['text']).join(), chapter.content);
    expect(
        segments
            .where((segment) => segment['type'] == 'word')
            .every((segment) => (segment['meaning_en'] as String).isNotEmpty),
        isTrue);
  });

  test('Korean word and grammar links require the exact source occurrence',
      () async {
    final dictionary = await KoreanDictionary.load();
    final source = dictionary.words['sources'].keys.single as String;
    final text = dictionary.words['sources'][source]['text'] as String;
    final use = dictionary.wordUse(source, text, 0, '옛날에', 'long ago');
    expect(use?['entry_id'], '옛날/명');
    expect(dictionary.wordUse(source, '$text!', 0, '옛날에', 'long ago'), isNull);
    expect(dictionary.wordUse(source, text, 0, '옛날에', 'yesterday'), isNull);
    expect(dictionary.grammarUses(source, text, 3, '이라는').single['entry_id'],
        'naming-iran');
    expect(dictionary.grammarUses(source, text, 3, '라고'), isEmpty);
  });

  test('Korean story cue, form stages, and selective sentence help load',
      () async {
    final books = await ContentRepository().loadBooks(Language.korean);
    final source = books.single.levels[1]!.chapters.single.annotationAsset;
    final annotation =
        jsonDecode(await rootBundle.loadString(source)) as Map<String, dynamic>;
    final segments =
        (annotation['segments'] as List).cast<Map<String, dynamic>>();
    expect(segments[16]['lookup_reason'], 'story_term');
    expect(segments[16]['learning_focus'], 'lookup');
    expect(segments[2]['lookup_reason'], 'proper_name');
    expect(segments[14]['learning_focus'], 'target');
    final stages =
        (segments[7]['form_steps'] as List).cast<Map<String, dynamic>>();
    expect(stages.map((step) => step['form']).toList(), ['살았다', '살았습니다']);
    expect(stages.last['meaning_en'], 'lived');
    final breakdowns = await KoreanSentenceBreakdowns.load();
    final text = annotation['text'] as String;
    expect(breakdowns.at(source, text, 34)?.start, 22);
    expect(breakdowns.at(source, text, 0), isNull);
    expect(breakdowns.at(source, '$text!', 34), isNull);
  });

  testWidgets('Korean form rows own their links; plain taps retain direct links',
      (tester) async {
    final dictionary = (await tester.runAsync(KoreanDictionary.load))!;
    final source = dictionary.words['sources'].keys.single as String;
    final text = dictionary.words['sources'][source]['text'] as String;
    final annotation = (await tester.runAsync(() async => jsonDecode(
        await rootBundle.loadString(source)) as Map<String, dynamic>))!;
    final segments = (annotation['segments'] as List)
        .cast<Map<String, dynamic>>()
        .map(AgentSegment.fromJson)
        .toList();

    Future<void> show(int index) async {
      await tester.pumpWidget(MaterialApp(
          home: Scaffold(
              body: SingleChildScrollView(
                  child: KoreanTapLinks(
                      segment: segments[index],
                      source: source,
                      sourceText: text,
                      segmentIndex: index)))));
      await tester.runAsync(() async => Future<void>.delayed(Duration.zero));
      await tester.pumpAndSettle();
    }

    for (var index = 0; index < segments.length; index++) {
      if (segments[index].formSteps.isEmpty) continue;
      expect(dictionary.wordUse(source, text, index, segments[index].text,
          segments[index].meaningEn), isNotNull);
      await show(index);
      expect(find.byType(KoreanFormChain), findsOneWidget);
      expect(find.byType(TextButton), findsNothing,
          reason: 'duplicate top-level links on ${segments[index].text}');
      expect(find.byType(ListTile),
          findsNWidgets(1 + segments[index].formSteps.length +
              (index == 34 ? 1 : 0)));
    }
    await show(34);
    expect(find.text('→ 부를 수 없었습니다'), findsOneWidget);
    expect(find.text('could not call'), findsOneWidget);
    await tester.tap(find.text('→ 부를 수 없었습니다'));
    await tester.pumpAndSettle();
    expect(find.text('In this passage'), findsOneWidget);
    expect(find.textContaining('Gildong could not call him that'),
        findsWidgets);
    await tester.pageBack();
    await tester.pumpAndSettle();
    await show(16);
    expect(find.text('Dictionary · 벼슬'), findsOneWidget);
    expect(find.text('Grammar · Object marker'), findsOneWidget);
    await show(3);
    expect(find.textContaining('Grammar ·'), findsOneWidget);
    expect(find.textContaining('Dictionary ·'), findsNothing);
  });
}
