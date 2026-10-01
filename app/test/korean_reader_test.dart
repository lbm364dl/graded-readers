import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/data.dart';
import 'package:hsk_graded/models.dart';
import 'package:hsk_graded/services/korean_dictionary.dart';
import 'package:hsk_graded/services/korean_sentence_breakdowns.dart';
import 'package:hsk_graded/screens/korean_dictionary_screen.dart';

Future<Map<String, dynamic>> annotation(String source) async =>
    jsonDecode(await rootBundle.loadString(source)) as Map<String, dynamic>;

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('Korean published chapters reconstruct exactly and have useful taps',
      () async {
    final books = await ContentRepository().loadBooks(Language.korean);
    expect(books, hasLength(1));
    expect(books.single.title, '홍길동전');
    final reader = books.single.levels[1]!;
    expect(reader.levelLabel, 'Level 1');
    expect(reader.chapters, hasLength(1));
    for (final chapter in reader.chapters) {
      final data = await annotation(chapter.annotationAsset);
      final segments = (data['segments'] as List).cast<Map<String, dynamic>>();
      expect(data['text'], chapter.content);
      expect(segments.map((s) => s['text']).join(), chapter.content);
      expect(
          segments
              .where((s) => s['type'] == 'word')
              .every((s) => (s['meaning_en'] as String).isNotEmpty),
          isTrue);
      expect(segments.any((s) => s['lookup_reason'] == 'proper_name'), isTrue);
      expect(segments.any((s) => s['learning_focus'] == 'target'), isTrue);
    }
  });

  test('Word, grammar and every form route require exact reviewed occurrence',
      () async {
    final dictionary = await KoreanDictionary.load();
    for (final use in (dictionary.words['occurrences'] as List)
        .cast<Map<String, dynamic>>()) {
      final source = use['source'] as String;
      final text = dictionary.words['sources'][source]['text'] as String;
      expect(
          dictionary.wordUse(
              source, text, use['segment_index'], use['surface'], use['gloss']),
          isNotNull);
      expect(
          dictionary.wordUse(source, '$text!', use['segment_index'],
              use['surface'], use['gloss']),
          isNull);
      expect(
          dictionary.wordUse(source, text, use['segment_index'], use['surface'],
              'wrong gloss'),
          isNull);
    }
    expect(dictionary.grammar['forms'], isNotEmpty);
    for (final form
        in (dictionary.grammar['forms'] as List).cast<Map<String, dynamic>>()) {
      final source = form['source'] as String;
      final text = dictionary.grammar['sources'][source]['text'] as String;
      final step = AgentFormStep.fromJson(form);
      expect(
          dictionary.formUse(source, text, form['segment_index'],
              form['surface'], form['step_index'], step),
          isNotNull);
      expect(
          dictionary.formUse(source, '$text!', form['segment_index'],
              form['surface'], form['step_index'], step),
          isNull);
      final stale = AgentFormStep.fromJson(
          {...form, 'meaning_en': 'wrong complete meaning'});
      expect(
          dictionary.formUse(source, text, form['segment_index'],
              form['surface'], form['step_index'], stale),
          isNull);
      expect(
          dictionary.grammarUses(
              source, text, form['segment_index'], 'wrong surface'),
          isEmpty);
    }
  });

  test('Selected sentence help is exact and simple sentences stay unselected',
      () async {
    final help = await KoreanSentenceBreakdowns.load();
    for (final item in help.breakdowns) {
      final text = help.sources[item.source] as String;
      expect(help.at(item.source, text, item.start), same(item));
      expect(help.at(item.source, '$text!', item.start), isNull);
      expect(item.parts.map((p) => p.text).join(), item.sentence);
    }
    final source = help.sources.keys.first;
    final text = help.sources[source] as String;
    expect(
        List.generate(text.length, (i) => help.at(source, text, i))
            .any((item) => item == null),
        isTrue);
  });

  testWidgets('Form rows own their links without a duplicate top list',
      (tester) async {
    final dictionary = (await tester.runAsync(KoreanDictionary.load))!;
    for (final source in dictionary.words['sources'].keys.cast<String>()) {
      final text = dictionary.words['sources'][source]['text'] as String;
      final data = (await tester.runAsync(() => annotation(source)))!;
      final segments = (data['segments'] as List)
          .cast<Map<String, dynamic>>()
          .map(AgentSegment.fromJson)
          .toList();
      for (var index = 0; index < segments.length; index++) {
        if (segments[index].formSteps.isEmpty) continue;
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
        final ids = {
          for (final step in segments[index].formSteps) ...step.grammarEntryIds
        };
        final constructions = dictionary
            .grammarUses(source, text, index, segments[index].text)
            .where((u) => !ids.contains(u['entry_id']))
            .length;
        expect(find.byType(KoreanFormChain), findsOneWidget);
        expect(find.byType(TextButton), findsNothing);
        expect(
            find.byType(ListTile),
            findsNWidgets(
                1 + segments[index].formSteps.length + constructions));
        expect(
            tester
                .widgetList<ListTile>(find.byType(ListTile))
                .every((tile) => tile.onTap != null),
            isTrue);
      }
    }
  });

  testWidgets('Dictionary form stays separate from a past passage form',
      (tester) async {
    final dictionary = (await tester.runAsync(KoreanDictionary.load))!;
    final form = (dictionary.grammar['forms'] as List)
        .cast<Map<String, dynamic>>()
        .firstWhere(
            (f) => (f['label'] as String).toLowerCase().contains('past'));
    final source = form['source'] as String;
    final data = (await tester.runAsync(() => annotation(source)))!;
    final index = form['segment_index'] as int;
    final segment = AgentSegment.fromJson(data['segments'][index]);
    final text = data['text'] as String;
    final use = dictionary.wordUse(
        source, text, index, segment.text, segment.meaningEn)!;
    final entry = dictionary.entry(use['entry_id'], isGrammar: false);
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: SingleChildScrollView(
                child: KoreanTapLinks(
                    segment: segment,
                    source: source,
                    sourceText: text,
                    segmentIndex: index)))));
    await tester.runAsync(() async => Future<void>.delayed(Duration.zero));
    await tester.pumpAndSettle();
    await tester.tap(find.byType(ListTile).first);
    await tester.pumpAndSettle();
    expect(find.text('Dictionary form'), findsOneWidget);
    expect(find.text(entry['headword']), findsWidgets);
    expect(find.text(entry['definition_en']), findsOneWidget);
    expect(find.text('Form in this passage'), findsOneWidget);
    expect(find.text(segment.text), findsOneWidget);
    expect(find.text(segment.meaningEn), findsOneWidget);
  });
}
