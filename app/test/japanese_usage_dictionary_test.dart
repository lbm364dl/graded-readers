import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/models.dart';
import 'package:hsk_graded/screens/usage_dictionary_screen.dart';
import 'package:hsk_graded/services/usage_dictionary.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('Japanese dictionaries and Chinese pinyin caches remain separate',
      () async {
    final japanese = await UsageDictionary.load(language: Language.japanese);
    final chinese = await UsageDictionary.load();
    expect(japanese.language, Language.japanese);
    expect(chinese.language, Language.chinese);
    expect(japanese.entries.every((e) => (e['id'] as String).startsWith('ja-')),
        isTrue);
    expect(chinese.search('hen3 duo1'), isNotEmpty);
    for (final query in ['猫', 'ねこ', 'ネコ', 'CAT']) {
      expect(japanese.search(query).any((e) => e['headword'] == '猫'), isTrue);
    }
  });

  test('Japanese inflections share lemma and sense without erasing context',
      () async {
    final dictionary = await UsageDictionary.load(language: Language.japanese);
    final entry = dictionary.entries.singleWhere((e) => e['headword'] == '見る');
    final uses = dictionary.occurrences.where((o) =>
        o['entry_id'] == entry['id'] &&
        o['source'] == 'assets/annotations/japanese_wagahai_n5_001.json');
    expect(uses.map((o) => o['surface']).toSet(), {'見ました', '見て', '見ながら'});
    expect(uses.map((o) => o['sense_id']).toSet(), hasLength(1));
    expect(uses.map((o) => o['gloss']).toSet().length, greaterThan(1));
    for (final use in dictionary.occurrences) {
      final text = dictionary.sources[use['source']]['text'] as String;
      expect(
          String.fromCharCodes(text.runes
              .toList()
              .sublist(use['start'] as int, use['end'] as int)),
          use['surface']);
      expect(
          dictionary.occurrence(use['source'] as String,
              use['segment_index'] as int, '$text stale',
              surface: use['surface'] as String),
          isNull);
    }
  });

  test('Merged N5 taps retain dictionary links and existing grammar', () async {
    final dictionary = await UsageDictionary.load(language: Language.japanese);
    const source = 'assets/annotations/japanese_wagahai_n5_001.json';
    final annotation = AgentChapterAnnotation.fromJson(
        jsonDecode(await rootBundle.loadString(source))
            as Map<String, dynamic>);
    final units = buildJapaneseDisplayUnits(annotation);
    for (final surface in ['目が回りました', '住むことにしました']) {
      final unit = units.singleWhere((u) => u.segment.text == surface);
      final use = dictionary.occurrence(
          source, unit.firstSegment, annotation.text,
          startOffset: unit.start,
          surface: surface,
          reading: unit.segment.pinyin,
          gloss: unit.segment.meaningEn);
      expect(use, isNotNull, reason: surface);
      final entry = dictionary.entry(use!['entry_id'] as String);
      expect(entry['headword'], surface == '目が回りました' ? '目が回る' : 'ことにする');
      expect(unit.grammar.explanationEn, isNotEmpty);
    }
  });

  testWidgets('Japanese browse uses kana search and JLPT source labels',
      (tester) async {
    final dictionary = (await tester
        .runAsync(() => UsageDictionary.load(language: Language.japanese)))!;
    final publishedLevels = dictionary.sources.values
        .map((source) => source['level'] as String)
        .toSet()
        .toList()
      ..sort((a, b) =>
          int.parse(b.substring(1)).compareTo(int.parse(a.substring(1))));
    await tester.pumpWidget(const MaterialApp(
        home: UsageDictionaryScreen(language: Language.japanese)));
    await tester.pumpAndSettle();
    expect(find.text('Search words, kana, or meanings'), findsOneWidget);
    expect(
        find.text('Japanese dictionary · JLPT ${publishedLevels.join(', ')}'),
        findsOneWidget);
    await tester.enterText(find.byType(TextField), 'ネコ');
    await tester.pumpAndSettle();
    await tester.tap(find.text('猫  ねこ'));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(find.textContaining('JLPT N5'), 200);
    expect(find.textContaining('JLPT N5'), findsWidgets);
    expect(find.textContaining('HSK'), findsNothing);
    expect(find.text('How this word makes sense'), findsOneWidget);
  });
}
