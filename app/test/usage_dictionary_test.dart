import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/screens/usage_dictionary_screen.dart';
import 'package:hsk_graded/screens/reader_screen.dart';
import 'package:hsk_graded/services/usage_dictionary.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() => SharedPreferences.setMockInitialValues({}));

  test('published usages retain exact source spans and sense links', () async {
    final dictionary = await UsageDictionary.load();
    expect(dictionary.entries, isNotEmpty);
    for (final use in dictionary.occurrences) {
      final text = dictionary.sources[use['source']]['text'] as String;
      expect(
          String.fromCharCodes(text.runes
              .toList()
              .sublist(use['start'] as int, use['end'] as int)),
          use['surface']);
      if (use['layer'] != 'expression') {
        expect(
            dictionary.occurrence(
                use['source'] as String, use['segment_index'] as int, text),
            same(use));
        expect(
            dictionary.occurrence(use['source'] as String,
                use['segment_index'] as int, '$text changed'),
            isNull);
        expect(
            dictionary.occurrence(
                use['source'] as String, use['segment_index'] as int, text,
                gloss: 'edited annotation'),
            isNull);
        expect(
            dictionary.occurrence(
                use['source'] as String, use['segment_index'] as int, text,
                startOffset: -1),
            isNull);
      }
      final entry =
          dictionary.entries.singleWhere((e) => e['id'] == use['entry_id']);
      expect((entry['senses'] as List).any((s) => s['id'] == use['sense_id']),
          isTrue);
    }
  });

  test('search supports headwords, readings and English definitions', () async {
    final dictionary = await UsageDictionary.load();
    final entry = dictionary.entries.first;
    expect(dictionary.search(entry['headword'] as String), contains(entry));
    expect(
        dictionary.search('  ${(entry['reading'] as String).toUpperCase()}  '),
        contains(entry));
    expect(dictionary.search(entry['senses'][0]['definition'] as String),
        contains(entry));
    expect(dictionary.search('not-a-real-dictionary-word'), isEmpty);
  });

  test('HSK2 reuses the HSK1 entry and sense for drinking alcohol', () async {
    final dictionary = await UsageDictionary.load();
    final entry = dictionary.entries.singleWhere((e) => e['headword'] == '喝酒');
    final uses =
        dictionary.occurrences.where((o) => o['entry_id'] == entry['id']);
    expect(uses.map((o) => dictionary.sources[o['source']]['level']).toSet(),
        containsAll({1, 2}));
    expect(uses.map((o) => o['sense_id']).toSet(), hasLength(1));
    expect(entry['senses'][0]['definition'], 'drink alcohol');
  });

  test('pinyin search accepts tone marks, tone numbers and umlaut aliases', () {
    final dictionary = UsageDictionary.fromJson({
      'sources': <String, dynamic>{},
      'occurrences': <Map<String, dynamic>>[],
      'entries': <Map<String, dynamic>>[
        {
          'id': 'many',
          'headword': '很多',
          'reading': 'hěn duō',
          'senses': <dynamic>[]
        },
        {
          'id': 'come',
          'headword': '上来',
          'reading': 'shànglai',
          'senses': <dynamic>[]
        },
        {
          'id': 'woman',
          'headword': '女',
          'reading': 'nǚ',
          'senses': <dynamic>[]
        },
      ],
    });
    for (final query in [
      'hěn duō',
      'hen duo',
      'hen3 duo1',
      'hen3duo1',
      'HEN3 DUO1'
    ]) {
      expect(dictionary.search(query).single['id'], 'many');
    }
    expect(dictionary.search('hen2duo1'), isEmpty);
    for (final query in ['shang4lai5', 'shang4lai0']) {
      expect(dictionary.search(query).single['id'], 'come');
    }
    expect(dictionary.search('shang4lai2'), isEmpty);
    for (final query in ['nü3', 'nv3', 'nu:3', 'nǚ']) {
      expect(dictionary.search(query).single['id'], 'woman');
    }
  });

  test('pinyin search also finds observed readings without duplicating entries',
      () {
    final dictionary = UsageDictionary.fromJson({
      'sources': <String, dynamic>{},
      'occurrences': <Map<String, dynamic>>[],
      'entries': <Map<String, dynamic>>[
        {
          'id': 'name',
          'headword': '张角',
          'reading': 'Zhāng Jiǎo',
          'observed_readings': ['Zhāng Jiǎo', 'Zhāng Jué'],
          'senses': <dynamic>[],
        },
      ],
    });
    for (final query in ['zhang1jiao3', 'zhang1jue2', 'zhāng jué']) {
      expect(dictionary.search(query).single['id'], 'name');
    }
    expect(dictionary.search('zhang1jue3'), isEmpty);
  });

  testWidgets('HSK2 example is labeled correctly and opens its own source',
      (tester) async {
    final dictionary = (await tester.runAsync(UsageDictionary.load))!;
    final entry = dictionary.entries.singleWhere((e) => e['headword'] == '喝酒');
    final use = dictionary.occurrences.firstWhere((o) =>
        o['entry_id'] == entry['id'] &&
        dictionary.sources[o['source']]['level'] == 2);
    await tester.pumpWidget(MaterialApp(
        home: UsageEntryScreen(dictionary: dictionary, entry: entry)));
    await tester.pumpAndSettle();
    final example = find.byKey(ValueKey('dictionary-example-${use['id']}'));
    await tester.scrollUntilVisible(example.hitTestable(), 200,
        scrollable: find.byType(Scrollable).first);
    expect(find.descendant(of: example, matching: find.textContaining('HSK 2')),
        findsOneWidget);
    // Opening a source starts compute() work; start it outside the fake clock.
    await tester.runAsync(() => tester.tap(example));
    for (var attempt = 0; attempt < 30; attempt++) {
      await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 100)));
      await tester.pump(const Duration(milliseconds: 100));
      if (find.byType(PageView).evaluate().isNotEmpty) break;
    }
    await tester.pumpAndSettle();
    final reader = tester.widget<ReaderScreen>(find.byType(ReaderScreen));
    expect(reader.reader.id, 'sanguoyanyi_hsk2');
    expect(reader.initialCharacterOffset, use['start']);
    expect(tester.takeException(), isNull);
  });

  test(
      'surface forms resolve to canonical senses without changing lexical lookup',
      () async {
    final dictionary = await UsageDictionary.load();
    for (final surface in ['看到了', '来了', '看着']) {
      final binding = dictionary.readingBindings.singleWhere((b) =>
          b['surface'] == surface &&
          dictionary.sources[b['source']]['level'] == 1);
      final source = binding['source'] as String;
      final text = dictionary.sources[source]['text'] as String;
      final use = dictionary.occurrence(
          source, binding['segment_index'] as int, text,
          surface: surface,
          reading: binding['reading'] as String,
          gloss: binding['gloss'] as String,
          startOffset: binding['start'] as int)!;
      expect(dictionary.entry(use['entry_id'] as String)['headword'],
          surface == '看到了' ? '看到' : surface.substring(0, 1));
      expect(
          dictionary.occurrence(source, binding['segment_index'] as int, text,
              surface: surface, reading: 'stale reading'),
          isNull);
    }
    expect(dictionary.entries.any((e) => e['headword'] == '看到了'), isFalse);
  });

  testWidgets('expression entry and base verb link to each other',
      (tester) async {
    final dictionary = (await tester.runAsync(UsageDictionary.load))!;
    final expression =
        dictionary.entries.singleWhere((e) => e['headword'] == '看到');
    final base = dictionary.entry(expression['base_entry_id'] as String);
    final binding = dictionary.readingBindings.singleWhere((b) =>
        b['surface'] == '看到了' && dictionary.sources[b['source']]['level'] == 1);
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: UsageDictionaryLink(
      source: binding['source'] as String,
      sourceText: dictionary.sources[binding['source']]['text'] as String,
      segmentIndex: binding['segment_index'] as int,
      surface: '看到了',
      reading: binding['reading'] as String,
      gloss: binding['gloss'] as String,
      startOffset: binding['start'] as int,
    ))));
    await tester.pumpAndSettle();
    final count = dictionary.occurrences
        .where((use) => use['entry_id'] == expression['id'])
        .length;
    expect(
        find.text(
            'Dictionary · 看到 · $count ${count == 1 ? 'usage' : 'usages'}'),
        findsOneWidget);
    await tester.tap(find.byType(TextButton));
    await tester.pumpAndSettle();
    expect(find.text('Used in this passage'), findsOneWidget);
    final baseLink = find.byKey(ValueKey('related-entry-${base['id']}'));
    await tester.scrollUntilVisible(baseLink.hitTestable(), 200);
    await tester.pumpAndSettle();
    await tester.tap(baseLink);
    await tester.pumpAndSettle();
    expect(
        tester
            .widget<UsageEntryScreen>(find.byType(UsageEntryScreen))
            .entry['id'],
        base['id']);
    final expressionsHeading = find.text('Expressions with 看');
    await tester.scrollUntilVisible(expressionsHeading.hitTestable(), 200);
    await tester.pumpAndSettle();
    expect(expressionsHeading, findsOneWidget);
    final expressionLink =
        find.byKey(ValueKey('related-entry-${expression['id']}'));
    await tester.scrollUntilVisible(expressionLink.hitTestable(), 200);
    await tester.pumpAndSettle();
    await tester.tap(expressionLink);
    await tester.pumpAndSettle();
    expect(find.text('How this word makes sense'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('search opens shared senses and real examples', (tester) async {
    final dictionary = (await tester.runAsync(UsageDictionary.load))!;
    await tester.pumpWidget(const MaterialApp(home: UsageDictionaryScreen()));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField), '一起');
    await tester.pumpAndSettle();
    final expectedEntry =
        dictionary.search('一起').singleWhere((e) => e['headword'] == '一起');
    final exactEntry = find.widgetWithText(
        ListTile, '${expectedEntry['headword']}  ${expectedEntry['reading']}');
    expect(exactEntry, findsOneWidget);
    await tester.tap(exactEntry);
    await tester.pumpAndSettle();
    expect(find.byType(UsageEntryScreen), findsOneWidget);
    expect(find.text('Used in this passage'), findsNothing);
    await tester.scrollUntilVisible(find.byType(Card).first, 200);
    expect(find.byType(Card), findsWidgets);
    expect(find.textContaining('三国演义 · HSK 1 · Chapter 1'), findsWidgets);
    expect(dictionary.search('一起').where((e) => e['headword'] == '一起'),
        hasLength(1));
    expect(tester.takeException(), isNull);
  });

  testWidgets('reader link selects the meaning used here', (tester) async {
    final dictionary = (await tester.runAsync(UsageDictionary.load))!;
    final use = dictionary.occurrences.first;
    final source = use['source'] as String;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: UsageDictionaryLink(
      source: source,
      segmentIndex: use['segment_index'] as int,
      sourceText: dictionary.sources[source]['text'] as String,
    ))));
    await tester.pumpAndSettle();
    await tester.tap(find.byType(TextButton));
    await tester.pumpAndSettle();
    expect(find.text('Used in this passage'), findsOneWidget);
    final sense =
        (dictionary.entry(use['entry_id'] as String)['senses'] as List)
            .singleWhere((s) => s['id'] == use['sense_id']);
    expect(
        tester.getBottomLeft(find.text('Used in this passage')).dy,
        lessThan(
            tester.getTopLeft(find.text(sense['definition'] as String)).dy));
    expect(find.textContaining('Meaning here'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('example opens its source at the exact occurrence',
      (tester) async {
    final dictionary = (await tester.runAsync(UsageDictionary.load))!;
    final entry =
        dictionary.search('一起').singleWhere((e) => e['headword'] == '一起');
    final use =
        dictionary.occurrences.firstWhere((o) => o['entry_id'] == entry['id']);
    await tester.pumpWidget(MaterialApp(
        home: UsageEntryScreen(
      dictionary: dictionary,
      entry: entry,
    )));
    await tester.pumpAndSettle();
    final example = find.byKey(ValueKey('dictionary-example-${use['id']}'));
    await tester.scrollUntilVisible(example.hitTestable(), 200,
        scrollable: find.byType(Scrollable).first);
    await tester.pumpAndSettle();
    await tester.runAsync(() => tester.tap(example));
    for (var attempt = 0; attempt < 30; attempt++) {
      await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 100)));
      await tester.pump(const Duration(milliseconds: 100));
      if (find.byType(PageView).evaluate().isNotEmpty) break;
    }
    await tester.pumpAndSettle();
    final reader = tester.widget<ReaderScreen>(find.byType(ReaderScreen));
    expect(reader.reader.id, 'sanguoyanyi_hsk1');
    expect(reader.initialChapter, 0);
    expect(reader.initialCharacterOffset, use['start']);
    expect(find.byType(PageView), findsOneWidget);
    final highlighted = <String>[];
    void collect(InlineSpan span) {
      if (span is! TextSpan) return;
      if (span.style?.backgroundColor != null && span.text == '一起') {
        highlighted.add(span.text!);
      }
      for (final child in span.children ?? const <InlineSpan>[]) {
        collect(child);
      }
    }

    for (final text in tester.widgetList<Text>(find.byType(Text))) {
      if (text.textSpan != null) collect(text.textSpan!);
    }
    expect(highlighted, ['一起']);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    await tester.pumpAndSettle();
  });
}
