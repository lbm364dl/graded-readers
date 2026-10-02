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

  testWidgets(
      'Lexical expressions retain word destinations and one card per source sentence',
      (tester) async {
    const source = 'fixture';
    const text = '마음 놓고. 마음 놓고.';
    final uses = <Map<String, dynamic>>[];
    for (final offset in [0, 7]) {
      final index = offset == 0 ? 0 : 4;
      uses.add({
        'id': 'mind-$index',
        'source': source,
        'segment_index': index,
        'surface': '마음',
        'gloss': 'mind',
        'entry_id': 'mind',
        'start': offset,
        'sentence': '마음 놓고.',
        'sentence_start': offset
      });
      uses.add({
        'id': 'word-$index',
        'source': source,
        'segment_index': index + 2,
        'surface': '놓고',
        'gloss': 'ease worries and',
        'entry_id': 'release',
        'start': offset + 3,
        'sentence': '마음 놓고.',
        'sentence_start': offset
      });
      uses.add({
        'id': 'expression-$index',
        'source': source,
        'occurrence_kind': 'expression',
        'segment_index': index,
        'end_segment_index': index + 2,
        'surface': '마음 놓고',
        'gloss': 'at ease',
        'entry_id': 'release',
        'start': offset,
        'context_en': 'The phrase expresses relief from worry.',
        'sentence': '마음 놓고.',
        'sentence_start': offset
      });
    }
    final dictionary = KoreanDictionary({
      'language': 'korean',
      'sources': {
        source: {'text': text}
      },
      'entries': [
        {
          'id': 'mind',
          'headword': '마음',
          'kind': 'word',
          'definition_en': 'mind'
        },
        {
          'id': 'release',
          'headword': '놓다',
          'kind': 'word',
          'definition_en': 'To ease worry or tension.'
        }
      ],
      'occurrences': uses
    }, {
      'language': 'korean',
      'sources': {},
      'entries': [],
      'occurrences': []
    });
    final expressions = dictionary.expressionUses(source, text, 2, '놓고');
    expect(expressions.single['entry_id'], 'release');
    expect(
        dictionary.expressionUses(source, '$text changed', 2, '놓고'), isEmpty);
    expect(
        dictionary.expressionUses(source, text, 2, 'wrong surface'), isEmpty);
    expect(dictionary.expressionUses(source, text, 99, '놓고'), isEmpty);
    expect(dictionary.wordUse(source, text, 2, '놓고', 'ease worries and')!['id'],
        'word-0');
    final groups = dictionary.exampleGroups('release', isGrammar: false);
    expect(groups.length, 2);
    expect(groups.every((group) => group.length == 2), isTrue);
    await tester.pumpWidget(MaterialApp(
        home: KoreanEntryScreen(
            dictionary: dictionary,
            entry: dictionary.entry('release', isGrammar: false),
            grammar: false,
            selectedUse: expressions.single)));
    expect(find.text('마음 놓고'), findsOneWidget);
    expect(find.text('at ease'), findsOneWidget);
    expect(find.text('To ease worry or tension.'), findsOneWidget);
    expect(find.text('마음 놓고.'), findsNWidgets(2));
    expect(find.textContaining('놓고 · ease worries and'), findsNWidgets(2));
    expect(find.textContaining('마음 놓고 · at ease'), findsNWidgets(2));
  });

  test('Optional grammar retains its true grade and is contextual', () {
    expect(koreanGrammarLevelCue(null), isEmpty);
    expect(
        koreanGrammarLevelCue({
          'optional_for_level': true,
          'matched_curriculum_level': null,
          'target_curriculum_level': 1,
        }),
        'Grammar level not listed · Optional for TOPIK 1');
    expect(
        koreanGrammarLevelCue({
          'optional_for_level': false,
          'matched_curriculum_level': 1,
          'target_curriculum_level': 1
        }),
        isEmpty);
    expect(
        koreanGrammarLevelCue({
          'optional_for_level': true,
          'matched_curriculum_level': 2,
          'target_curriculum_level': 1
        }),
        'TOPIK 2 grammar · Optional for TOPIK 1');
  });

  testWidgets(
      'Selected optional grammar is labeled without changing the lesson',
      (tester) async {
    final dictionary = (await tester.runAsync(KoreanDictionary.load))!;
    final original = (dictionary.grammar['occurrences'] as List).first
        as Map<String, dynamic>;
    final entry = dictionary.entry(original['entry_id'], isGrammar: true);
    final optional = {
      ...original,
      'optional_for_level': true,
      'matched_curriculum_level': 2,
      'target_curriculum_level': 1
    };
    await tester.pumpWidget(MaterialApp(
        home: KoreanEntryScreen(
            dictionary: dictionary,
            entry: entry,
            grammar: true,
            selectedUse: optional)));
    expect(find.text('TOPIK 2 grammar · Optional for TOPIK 1'), findsOneWidget);
    expect(find.text(entry['explanation_en']), findsOneWidget);
    await tester.pumpWidget(MaterialApp(
        home: KoreanEntryScreen(
            dictionary: dictionary,
            entry: entry,
            grammar: true,
            selectedUse: original)));
    expect(find.text('TOPIK 2 grammar · Optional for TOPIK 1'), findsNothing);
  });

  testWidgets('Complete optional phrase is available across its exact scope',
      (tester) async {
    final use = <String, dynamic>{
      'source': 's',
      'segment_index': 0,
      'surface': '안',
      'entry_id': 'possibility',
      'sentence': '안 들을 수도 있습니다.',
      'context_en': 'He fears the child might not listen.',
      'display_form': '안 들을 수도 있습니다',
      'display_meaning_en': 'might not listen',
      'display_end_segment_index': 4,
      'optional_for_level': true,
      'matched_curriculum_level': 2,
      'target_curriculum_level': 1
    };
    final dictionary = KoreanDictionary({
      'language': 'korean',
      'sources': {
        's': {'text': '안 들을 수도 있습니다.'}
      },
      'entries': [],
      'occurrences': [
        {'source': 's', 'segment_index': 2, 'surface': '들을'},
        {'source': 's', 'segment_index': 6, 'surface': '다른'}
      ]
    }, {
      'language': 'korean',
      'sources': {
        's': {'text': '안 들을 수도 있습니다.'}
      },
      'entries': [
        {
          'id': 'possibility',
          'title_en': 'Possibility',
          'pattern': '-(으)ㄹ 수도 있다',
          'explanation_en': 'Something might happen.'
        }
      ],
      'occurrences': [use],
      'forms': []
    });
    expect(dictionary.grammarUses('s', '안 들을 수도 있습니다.', 0, '안'), [use]);
    expect(dictionary.grammarUses('s', '안 들을 수도 있습니다.', 2, '들을'), [use]);
    expect(dictionary.grammarUses('s', '안 들을 수도 있습니다.', 6, '다른'), isEmpty);
    expect(dictionary.grammarUses('s', '안 들을 수도 있습니다.', 2, 'wrong'), isEmpty);
    expect(dictionary.grammarUses('s', 'changed', 2, '들을'), isEmpty);
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: KoreanGrammarLink(dictionary: dictionary, use: use))));
    expect(find.text('안 들을 수도 있습니다'), findsOneWidget);
    expect(find.textContaining('might not listen'), findsOneWidget);
    expect(find.textContaining('Optional for TOPIK 1'), findsOneWidget);
    await tester.tap(find.byType(ListTile));
    await tester.pumpAndSettle();
    expect(find.text('Something might happen.'), findsOneWidget);
    expect(find.text('He fears the child might not listen.'), findsOneWidget);
  });

  test('Korean published chapters reconstruct exactly and have useful taps',
      () async {
    final books = await ContentRepository().loadBooks(Language.korean);
    expect(books, hasLength(1));
    expect(books.single.title, '홍길동전');
    final reader = books.single.levels[1]!;
    expect(reader.levelLabel, 'TOPIK 1');
    expect(reader.maxLevel, 6);
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
            .toList();
        final inlineLessons = constructions
            .where((use) =>
                use['segment_index'] == index &&
                use['display_end_segment_index'] == index &&
                use['display_form'] == segments[index].formSteps.last.form &&
                use['display_meaning_en'] ==
                    segments[index].formSteps.last.meaningEn)
            .toList();
        final expressions = dictionary.expressionUses(
            source, text, index, segments[index].text);
        expect(find.byType(KoreanFormChain), findsOneWidget);
        expect(find.byType(TextButton), findsNWidgets(inlineLessons.length));
        for (final use in inlineLessons) {
          final title =
              dictionary.entry(use['entry_id'], isGrammar: true)['title_en'];
          expect(find.textContaining('Grammar · $title'), findsOneWidget);
        }
        expect(
            find.byType(ListTile),
            findsNWidgets(1 +
                segments[index].formSteps.length +
                constructions.length -
                inlineLessons.length +
                expressions.length));
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
