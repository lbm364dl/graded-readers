import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/models.dart';
import 'package:hsk_graded/services/grammar_dictionary.dart';
import 'package:hsk_graded/services/usage_dictionary.dart';
import 'package:hsk_graded/screens/grammar_dictionary_screen.dart';
import 'package:hsk_graded/screens/usage_dictionary_screen.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  testWidgets('か distinguishes separate graded versions of one example',
      (tester) async {
    final dictionary = (await tester.runAsync(() => GrammarDictionary.load()))!;
    await tester.pumpWidget(MaterialApp(
        home: GrammarEntryScreen(
            dictionary: dictionary,
            entry: dictionary.entry('ja-grammar-embedded-question-ka'))));
    await tester.pumpAndSettle();
    expect(find.text('どこで生まれたか分かりません。'), findsOneWidget);
    expect(find.text('JLPT N5 · Chapter 1'), findsOneWidget);
    await tester.scrollUntilVisible(
        find.text('どこで生まれたか分からない。'), 200);
    expect(find.text('どこで生まれたか分からない。'), findsOneWidget);
    expect(find.text('JLPT N4 · Chapter 1'), findsWidgets);
  });
  test('Example cards group annotation layers, not distinct sentence positions',
      () async {
    final published = await GrammarDictionary.load();
    final dictionary = GrammarDictionary.fromJson({
      'sources': published.sources,
      'entries': published.entries,
      'occurrences': published.occurrences
          .where((o) =>
              o['source'] == 'assets/annotations/japanese_wagahai_n5_001.json')
          .toList(),
    });
    final id = 'ja-grammar-embedded-question-ka';
    final linked = dictionary.occurrences
        .where((o) =>
            (o['entry_ids'] as List).contains(id) && o['layer'] != 'form')
        .toList();
    expect(linked, hasLength(2));
    expect(dictionary.examples(id), hasLength(1));
    for (final use in linked) {
      expect(dictionary.examples(id).single['context_en'],
          contains(use['context_en']));
    }
    final repeated = GrammarDictionary.fromJson({
      'sources': dictionary.sources,
      'entries': dictionary.entries,
      'occurrences': [
        ...linked,
        {...linked.first, 'sentence_start': 999}
      ],
    });
    expect(repeated.examples(id), hasLength(2));
    final otherSource = GrammarDictionary.fromJson({
      'sources': dictionary.sources,
      'entries': dictionary.entries,
      'occurrences': [
        ...linked,
        {...linked.first, 'source': 'another-chapter'}
      ],
    });
    expect(otherSource.examples(id), hasLength(2));
  });
  test('One shared stem lesson underlies distinct complete forms', () async {
    final dictionary = await GrammarDictionary.load();
    final stem = dictionary.entry('ja-grammar-conjunctive-stem');
    expect(stem['title'], '連用形（ます語幹）');
    expect(stem['explanation_en'], contains('ます-stem'));
    expect(stem['explanation_en'], isNot(contains('past-tense')));
    for (final id in [
      'ja-grammar-polite-nonpast',
      'ja-grammar-polite-past',
      'ja-grammar-polite-negative',
      'ja-grammar-nagara'
    ]) {
      expect(
          (dictionary.entry(id)['formation'] as List)
              .any((f) => f['grammar_entry_id'] == stem['id']),
          isTrue);
    }
    expect(dictionary.search('ます').where((entry) => entry['id'] == stem['id']),
        hasLength(1));
  });
  testWidgets(
      'A complete ます lesson links to its independent shared stem prerequisite',
      (tester) async {
    final dictionary = (await tester.runAsync(() => GrammarDictionary.load()))!;
    await tester.pumpWidget(MaterialApp(
        home: GrammarEntryScreen(
      dictionary: dictionary,
      entry: dictionary.entry('ja-grammar-polite-nonpast'),
    )));
    await tester.pumpAndSettle();
    await tester.tap(find.textContaining('Uses 連用形（ます語幹）'));
    await tester.pumpAndSettle();
    expect(find.text('連用形（ます語幹）'), findsOneWidget);
    expect(find.text('Used in this passage'), findsNothing);
  });
  test('Functional words route to grammar, verbs retain lexical identity',
      () async {
    final grammar = await GrammarDictionary.load();
    final words = await UsageDictionary.load(language: Language.japanese);
    final wa = words.entries.singleWhere((e) => e['headword'] == 'は');
    expect(words.search('').contains(wa), isFalse);
    expect(grammar.wordRoutes[wa['id']], isNotEmpty);
    expect(words.search('見る'), isNotEmpty);
    final use = grammar.occurrences.firstWhere((o) => o['surface'] == '見ました');
    final text = grammar.sources[use['source']]['text'] as String;
    final start =
        String.fromCharCodes(text.runes.take(use['start'] as int)).length;
    expect(
        grammar.uses(use['source'] as String, text, start, '見ました'), isNotEmpty);
    expect(grammar.uses(use['source'] as String, '$text stale', start, '見ました'),
        isEmpty);
    expect(grammar.uses(use['source'] as String, text, start + 1, '見ました'),
        isEmpty);
  });
  test('Offsets use Unicode code points but taps use UTF16', () {
    final grammar = GrammarDictionary.fromJson({
      'sources': {
        's': {'text': '😀は'}
      },
      'entries': [],
      'occurrences': [
        {'source': 's', 'start': 1, 'end': 2}
      ],
    });
    expect(grammar.uses('s', '😀は', 2, 'は'), hasLength(1));
    expect(grammar.uses('s', '😀は', 1, 'は'), isEmpty);
  });
  testWidgets('Inflected verb offers both word and grammar destinations',
      (tester) async {
    final words = await UsageDictionary.load(language: Language.japanese);
    final use = words.occurrences.firstWhere((o) => o['surface'] == '見ました');
    final text = words.sources[use['source']]['text'] as String;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: Column(children: [
      UsageDictionaryLink(
          language: Language.japanese,
          source: use['source'] as String,
          sourceText: text,
          segmentIndex: use['segment_index'] as int,
          surface: '見ました',
          reading: use['reading'] as String,
          gloss: use['gloss'] as String),
      GrammarDictionaryLinks(
          source: use['source'] as String,
          sourceText: text,
          surface: '見ました',
          startOffset: use['start'] as int)
    ]))));
    await tester.pumpAndSettle();
    expect(find.textContaining('Dictionary · 見る'), findsOneWidget);
    expect(find.textContaining('Grammar ·'), findsOneWidget);
  });
  testWidgets('Particle taps show grammar instead of a word entry',
      (tester) async {
    final words = await UsageDictionary.load(language: Language.japanese);
    final use = words.occurrences.firstWhere((o) => o['surface'] == 'は');
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: Column(children: [
      UsageDictionaryLink(
          language: Language.japanese,
          source: use['source'] as String,
          sourceText: words.sources[use['source']]['text'] as String,
          segmentIndex: use['segment_index'] as int,
          surface: 'は'),
      GrammarDictionaryLinks(
          source: use['source'] as String,
          sourceText: words.sources[use['source']]['text'] as String,
          surface: 'は',
          startOffset: use['start'] as int)
    ]))));
    await tester.pumpAndSettle();
    expect(find.textContaining('Dictionary ·'), findsNothing);
    expect(find.textContaining('Grammar ·'), findsWidgets);
    await tester.tap(find.textContaining('Grammar ·').first);
    await tester.pumpAndSettle();
    expect(find.text('Used in this passage'), findsOneWidget);
    expect(find.text('Examples from our texts'), findsOneWidget);
  });
  testWidgets('Merged decision pattern keeps the embedded lexical verb',
      (tester) async {
    final words = await UsageDictionary.load(language: Language.japanese);
    const surface = '住むことにしました';
    final binding =
        words.readingBindings.singleWhere((b) => b['surface'] == surface);
    final text = words.sources[binding['source']]['text'] as String;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: Column(children: [
      UsageDictionaryLink(
          language: Language.japanese,
          source: binding['source'] as String,
          sourceText: text,
          segmentIndex: binding['segment_index'] as int,
          startOffset: binding['start'] as int,
          surface: surface,
          reading: binding['reading'] as String,
          gloss: binding['gloss'] as String),
      GrammarDictionaryLinks(
          source: binding['source'] as String,
          sourceText: text,
          surface: surface,
          startOffset: binding['start'] as int)
    ]))));
    await tester.pumpAndSettle();
    expect(find.textContaining('Dictionary · 住む'), findsOneWidget);
    expect(find.textContaining('Grammar · Vることにする'), findsOneWidget);
  });
}
