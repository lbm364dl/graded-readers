import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/screens/usage_dictionary_screen.dart';
import 'package:hsk_graded/services/usage_dictionary.dart';
import 'package:hsk_graded/services/dictionary_service.dart';
import 'package:hsk_graded/services/etymology_service.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('变得 is clearly a grammatical pattern, not an indivisible word',
      (tester) async {
    final dictionary = (await tester.runAsync(UsageDictionary.load))!;
    final entry = dictionary.entries.singleWhere((e) => e['headword'] == '变得');
    expect(entry['kind'], 'construction');
    await tester.pumpWidget(MaterialApp(
        home: UsageEntryScreen(dictionary: dictionary, entry: entry)));
    await tester.pumpAndSettle();
    expect(find.text('Grammar construction'), findsOneWidget);
    expect(
        find.text('A reusable grammatical pattern, not an indivisible word.'),
        findsOneWidget);
    expect((entry['meaning_guide']['parts'] as List).map((p) => p['text']),
        ['变', '得']);
  });

  test('every published entry has a guide with valid written components',
      () async {
    final dictionary = await UsageDictionary.load();
    for (final entry in dictionary.entries) {
      final guide = entry['meaning_guide'] as Map<String, dynamic>;
      expect((guide['explanation_en'] as String).trim(), isNotEmpty);
      final parts = guide['parts'] as List;
      if (parts.isNotEmpty) {
        expect(parts.map((p) => p['text']).join(), entry['headword']);
        for (final part in parts) {
          expect((part['contribution_en'] as String).trim(), isNotEmpty);
          if (part['entry_id'] != null) {
            final target = dictionary.entry(part['entry_id'] as String);
            expect(target['headword'], part['text']);
            expect(target['id'], isNot(entry['id']));
          }
        }
      }
      if (guide['structure'] == 'lexicalized') {
        final caveat = (guide['caveat_en'] as String).trim();
        final inlineLimit = RegExp(
          r"\b(?:not(?: fully)? (?:predictable|derivable|recoverable|explained)|(?:does not|cannot|can't) (?:by itself )?(?:fully )?(?:explain|predict|derive)|rather than (?:a )?(?:literal|character-by-character))\b",
          caseSensitive: false,
        ).hasMatch(guide['explanation_en'] as String);
        expect(caveat.isNotEmpty || inlineLimit, isTrue);
      }
    }
  });

  for (final word in ['看到', '叹气', '有难']) {
    testWidgets(
        '$word displays the whole-word explanation and contextual parts',
        (tester) async {
      final dictionary = (await tester.runAsync(UsageDictionary.load))!;
      final entry =
          dictionary.entries.singleWhere((e) => e['headword'] == word);
      final guide = entry['meaning_guide'] as Map<String, dynamic>;
      await tester.pumpWidget(MaterialApp(
          home: UsageEntryScreen(dictionary: dictionary, entry: entry)));
      await tester.pumpAndSettle();
      expect(find.text('How this word makes sense'), findsOneWidget);
      expect(find.text(guide['explanation_en'] as String), findsOneWidget);
      if (word == '有难') {
        expect(guide['explanation_en'], contains('nàn'));
        expect(guide['explanation_en'], contains('noun'));
      }
      expect(guide['parts'], hasLength(2));
      for (final part in guide['parts'] as List) {
        expect(find.text('${part['text']} — ${part['contribution_en']}'),
            findsOneWidget);
      }
      final firstPart = (guide['parts'] as List).first;
      final link = find.byKey(ValueKey('meaning-part-${firstPart['start']}'));
      if (firstPart['character_ref'] != null) {
        await tester.runAsync(() async {
          await DictionaryService.instance.initialize();
          await EtymologyService.instance.initialize();
        });
      }
      await tester.ensureVisible(link);
      await tester.tap(link);
      await tester.pumpAndSettle();
      if (firstPart['character_ref'] != null) {
        expect(find.byType(BottomSheet), findsOneWidget);
        expect(find.text(firstPart['text'] as String), findsWidgets);
        expect(tester.takeException(), isNull);
        return;
      }
      expect(find.widgetWithText(AppBar, firstPart['text'] as String),
          findsOneWidget);
      final backLink = find.byKey(ValueKey('related-entry-${entry['id']}'));
      await tester.scrollUntilVisible(backLink.hitTestable(), 200,
          scrollable: find.byType(Scrollable).first);
      // The link can appear under either component uses or complement
      // expressions as more corpus entries become available.
      expect(backLink, findsWidgets);
      await tester.tap(backLink.hitTestable().first);
      await tester.pumpAndSettle();
      expect(find.widgetWithText(AppBar, word), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets(
      'word components recurse into real entries and then character lookups',
      (tester) async {
    final dictionary = (await tester.runAsync(UsageDictionary.load))!;
    final army = dictionary.entries.singleWhere((e) => e['headword'] == '黄巾军');
    await tester.pumpWidget(MaterialApp(
        home: UsageEntryScreen(dictionary: dictionary, entry: army)));
    final wordLink = find.byKey(const ValueKey('meaning-part-0'));
    await tester.ensureVisible(wordLink);
    await tester.tap(wordLink);
    await tester.pumpAndSettle();
    expect(find.widgetWithText(AppBar, '黄巾'), findsOneWidget);
    final word = dictionary.entries.singleWhere((e) => e['headword'] == '黄巾');
    final uses = dictionary.occurrences
        .where((use) => use['entry_id'] == word['id'])
        .toList();
    expect(uses, isNotEmpty);
    expect(word['reading'], isNotEmpty);
    expect((word['meaning_guide']['parts'] as List).map((p) => p['text']),
        ['黄', '巾']);
    expect(find.text('Character · etymology'), findsNWidgets(2));
    final military =
        dictionary.entries.singleWhere((e) => e['headword'] == '军');
    expect(
        dictionary.occurrences
            .where((use) => use['entry_id'] == military['id']),
        isNotEmpty);
    final militaryPart = (army['meaning_guide']['parts'] as List)
        .singleWhere((part) => part['text'] == '军');
    expect(militaryPart['entry_id'], military['id']);
    final example =
        find.byKey(ValueKey('dictionary-example-${uses.first['id']}'));
    await tester.scrollUntilVisible(example.hitTestable(), 200,
        scrollable: find.byType(Scrollable).first);
    expect(example, findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('researched entries expose structured sources and limits',
      (tester) async {
    final dictionary = (await tester.runAsync(UsageDictionary.load))!;
    final entry = dictionary.entries.singleWhere((e) => e['headword'] == '国家');
    await tester.pumpWidget(MaterialApp(
        home: UsageEntryScreen(dictionary: dictionary, entry: entry)));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
        find.text('Sources and research notes').hitTestable(), 250,
        scrollable: find.byType(Scrollable).first);
    await tester.tap(find.text('Sources and research notes'));
    await tester.pumpAndSettle();
    final source =
        (entry['meaning_guide']['research']['sources'] as List).first;
    expect(find.text(source['url'] as String), findsOneWidget);
    expect(find.text('Copy source link'), findsWidgets);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'single-character lexical entries also link to character etymology',
      (tester) async {
    final dictionary = (await tester.runAsync(UsageDictionary.load))!;
    await tester.runAsync(() async {
      await DictionaryService.instance.initialize();
      await EtymologyService.instance.initialize();
    });
    final entry = dictionary.entries.singleWhere((e) => e['headword'] == '到');
    await tester.pumpWidget(MaterialApp(
        home: UsageEntryScreen(dictionary: dictionary, entry: entry)));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('entry-character-link')));
    await tester.pumpAndSettle();
    expect(find.byType(BottomSheet), findsOneWidget);
    expect(find.text('到'), findsWidgets);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'non-transparent words show limitations without forced decomposition',
      (tester) async {
    final dictionary = UsageDictionary.fromJson({
      'sources': <String, dynamic>{},
      'occurrences': <Map<String, dynamic>>[],
      'entries': <Map<String, dynamic>>[
        {
          'id': 'fixture',
          'headword': 'test word',
          'reading': '',
          'kind': 'word',
          'senses': [
            {'id': 'fixture-s1', 'definition': 'a test definition'}
          ],
          'meaning_guide': {
            'structure': 'lexicalized',
            'explanation_en': 'Learn this conventional meaning as a whole.',
            'parts': [],
            'caveat_en': 'The written parts do not fully predict its meaning.',
          },
        }
      ],
    });
    await tester.pumpWidget(MaterialApp(
        home: UsageEntryScreen(
            dictionary: dictionary, entry: dictionary.entries.single)));
    await tester.pumpAndSettle();
    expect(find.text('The written parts do not fully predict its meaning.'),
        findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
