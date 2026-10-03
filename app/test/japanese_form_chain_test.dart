import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/models.dart';
import 'package:hsk_graded/services/grammar_dictionary.dart';
import 'package:hsk_graded/services/usage_dictionary.dart';
import 'package:hsk_graded/screens/grammar_dictionary_screen.dart';
import 'package:hsk_graded/widgets/japanese_form_chain.dart';

const source = 'assets/annotations/japanese_wagahai_n5_001.json';

Future<(AgentChapterAnnotation, Map<String, dynamic>, GrammarDictionary)>
    fixture() async {
  final words = await UsageDictionary.load(language: Language.japanese);
  final grammar = await GrammarDictionary.load();
  final annotation = AgentChapterAnnotation.fromJson(
      jsonDecode(await rootBundle.loadString(source)) as Map<String, dynamic>);
  final use = words.occurrences.firstWhere((o) => o['surface'] == '入りました');
  return (annotation, use, grammar);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  testWidgets(
      'Every published level renders linked, reviewed complete form meanings',
      (tester) async {
    final words = (await tester
        .runAsync(() => UsageDictionary.load(language: Language.japanese)))!;
    final grammar = (await tester.runAsync(GrammarDictionary.load))!;
    var checked = 0;
    for (final publishedSource in words.sources.keys) {
      final annotation = (await tester.runAsync(() async =>
          AgentChapterAnnotation.fromJson(
              jsonDecode(await rootBundle.loadString(publishedSource))
                  as Map<String, dynamic>)))!;
      final mergedUnits = buildJapaneseDisplayUnits(annotation);
      final units = <({
        AgentSegment segment,
        int firstSegment,
        int lastSegment,
        int start,
        AgentGrammarOverlay? grammar
      })>[
        for (final unit in mergedUnits)
          (
            segment: unit.segment,
            firstSegment: unit.firstSegment,
            lastSegment: unit.lastSegment,
            start: unit.start,
            grammar: unit.grammar
          ),
      ];
      var start = 0;
      for (var index = 0; index < annotation.segments.length; index++) {
        if (!mergedUnits.any((unit) =>
            unit.firstSegment <= index && index <= unit.lastSegment)) {
          units.add((
            segment: annotation.segments[index],
            firstSegment: index,
            lastSegment: index,
            start: start,
            grammar: null
          ));
        }
        start += annotation.segments[index].text.length;
      }
      for (final unit in units) {
        if (annotation.segments[unit.lastSegment].formSteps.isEmpty) continue;
        var offset = unit.start;
        final parts = <({AgentSegment segment, int index, int start})>[];
        for (var index = unit.firstSegment;
            index <= unit.lastSegment;
            index++) {
          parts.add((
            segment: annotation.segments[index],
            index: index,
            start: offset
          ));
          offset += annotation.segments[index].text.length;
        }
        final merged = unit.firstSegment != unit.lastSegment;
        final tail = parts.last;
        await tester.pumpWidget(MaterialApp(
            home: Scaffold(
                body: SingleChildScrollView(
                    child: JapaneseFormChain(
                        key: ValueKey('$publishedSource:${unit.start}'),
                        segment: unit.segment,
                        source: publishedSource,
                        sourceText: annotation.text,
                        segmentIndex: unit.firstSegment,
                        startOffset: unit.start,
                        parts: merged ? parts : const [],
                        overlay: unit.grammar)))));
        await tester.pumpAndSettle();
        for (var index = 0; index < tail.segment.formSteps.length; index++) {
          final step = tail.segment.formSteps[index];
          final tile = tester.widget<ListTile>(
              find.byKey(ValueKey('japanese-form-step-$index')));
          expect(tile.onTap, isNotNull,
              reason: '$publishedSource: ${unit.segment.text} / ${step.form}');
          if (words.sources[publishedSource]['level'] != 'N5') {
            final use = grammar.formStep(
                publishedSource,
                annotation.text,
                tail.start,
                tail.segment.text,
                tail.index,
                index,
                step.form,
                step.reading,
                step.label,
                step.meaningEn)!;
            expect((tile.subtitle as Text).data,
                '${step.label} · ${use['display_meaning_en']}',
                reason:
                    '$publishedSource: ${unit.segment.text} / ${step.form}');
          }
          checked++;
        }
      }
    }
    expect(checked, greaterThan(0));
  });
  testWidgets('Every ordinary N5 inflection uses linked stages',
      (tester) async {
    final (annotation, _, _) = (await tester.runAsync(fixture))!;
    var start = 0;
    for (var index = 0; index < annotation.segments.length; index++) {
      final segment = annotation.segments[index];
      if (segment.formSteps.isNotEmpty) {
        await tester.pumpWidget(MaterialApp(
            home: Scaffold(
                body: ListView(
          children: [
            JapaneseFormChain(
                key: ValueKey(index),
                segment: segment,
                source: source,
                sourceText: annotation.text,
                segmentIndex: index,
                startOffset: start)
          ],
        ))));
        await tester.pumpAndSettle();
        for (var step = 0; step < segment.formSteps.length; step++) {
          final tile = tester.widget<ListTile>(
              find.byKey(ValueKey('japanese-form-step-$step')));
          expect(tile.onTap, isNotNull, reason: '${segment.text}, step $step');
        }
      }
      start += segment.text.length;
    }
  });
  for (final surface in ['住むことにしました', '目が回りました']) {
    testWidgets('$surface retains the reviewed linked formation chain',
        (tester) async {
      final (annotation, _, _) = (await tester.runAsync(fixture))!;
      final unit = buildJapaneseDisplayUnits(annotation)
          .firstWhere((unit) => unit.segment.text == surface);
      var start = unit.start;
      final parts = <({AgentSegment segment, int index, int start})>[];
      for (var index = unit.firstSegment; index <= unit.lastSegment; index++) {
        final segment = annotation.segments[index];
        parts.add((segment: segment, index: index, start: start));
        start += segment.text.length;
      }
      await tester.pumpWidget(MaterialApp(
          home: Scaffold(
              body: ListView(children: [
        JapaneseFormChain(
            segment: unit.segment,
            source: source,
            sourceText: annotation.text,
            segmentIndex: unit.firstSegment,
            startOffset: unit.start,
            includeSurfaceMeaning: true,
            parts: parts,
            overlay: unit.grammar),
      ]))));
      await tester.pumpAndSettle();
      final tiles = tester.widgetList<ListTile>(find.byType(ListTile)).toList();
      expect(tiles.length, surface.startsWith('住む') ? 4 : 3);
      expect(tiles.every((tile) => tile.onTap != null), isTrue);
      expect(find.textContaining(surface), findsOneWidget);
      expect(
          tester
              .widget<Text>(
                  find.byKey(const ValueKey('japanese-surface-meaning')))
              .data,
          surface.startsWith('住む') ? 'decided to live' : 'felt dizzy');
      if (surface.startsWith('住む')) {
        expect(find.textContaining('住むことにします'), findsOneWidget);
        expect(find.text('Construction'), findsNothing);
        expect(find.text('decide to live'), findsOneWidget);
        expect(find.text('polite · decide to live'), findsOneWidget);
        expect(find.text('polite past · decided to live'), findsOneWidget);
        expect(find.text('polite · decide to'), findsNothing);
        expect(find.text('polite past · decided to'), findsNothing);
        await tester.tap(find.byKey(const ValueKey('japanese-form-step--2')));
        await tester.pumpAndSettle();
        expect(find.text('Used in this passage'), findsNothing);
        expect(find.byType(JapaneseFormChain), findsNothing);
        expect(find.byType(GrammarEntryScreen), findsOneWidget);
        expect(find.byType(GrammarDictionaryScreen), findsNothing);
        expect(find.text('Vることにする'), findsOneWidget);
        await tester.tap(find.byTooltip('Back'));
        await tester.pumpAndSettle();
        await tester.tap(find.byKey(const ValueKey('japanese-form-step-1')));
        await tester.pumpAndSettle();
        expect(find.byType(GrammarEntryScreen), findsOneWidget);
        expect(find.text('ます形の過去 (ました)'), findsOneWidget);
      } else {
        expect(find.text('Base form · feel dizzy'), findsOneWidget);
        expect(find.text('polite · feels dizzy'), findsOneWidget);
        expect(find.text('polite past · felt dizzy'), findsOneWidget);
      }
    });
  }
  testWidgets('N4 decision base and plain-past stage have separate lessons',
      (tester) async {
    const n4Source = 'assets/annotations/japanese_wagahai_n4_001.json';
    await tester.runAsync(() async {
      await UsageDictionary.load(language: Language.japanese);
      await GrammarDictionary.load();
    });
    final annotation = (await tester.runAsync(() async =>
        AgentChapterAnnotation.fromJson(
            jsonDecode(await rootBundle.loadString(n4Source))
                as Map<String, dynamic>)))!;
    final unit = buildJapaneseDisplayUnits(annotation)
        .singleWhere((unit) => unit.segment.text == '住むことにした');
    var start = unit.start;
    final parts = <({AgentSegment segment, int index, int start})>[];
    for (var index = unit.firstSegment; index <= unit.lastSegment; index++) {
      final segment = annotation.segments[index];
      parts.add((segment: segment, index: index, start: start));
      start += segment.text.length;
    }
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: ListView(children: [
      JapaneseFormChain(
          segment: unit.segment,
          source: n4Source,
          sourceText: annotation.text,
          segmentIndex: unit.firstSegment,
          startOffset: unit.start,
          parts: parts,
          overlay: unit.grammar)
    ]))));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('japanese-form-step--2')));
    await tester.pumpAndSettle();
    expect(find.byType(GrammarEntryScreen), findsOneWidget);
    expect(find.text('Vることにする'), findsOneWidget);
    await tester.tap(find.byTooltip('Back'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('japanese-form-step-0')));
    await tester.pumpAndSettle();
    expect(find.byType(GrammarEntryScreen), findsOneWidget);
    expect(find.text('動詞のた形'), findsOneWidget);
  });
  testWidgets('Each chain step opens its own independent entry',
      (tester) async {
    final (annotation, use, grammar) = (await tester.runAsync(fixture))!;
    final segment = annotation.segments[use['segment_index'] as int];
    final start =
        String.fromCharCodes(annotation.text.runes.take(use['start'] as int))
            .length;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: ListView(children: [
      JapaneseFormChain(
          segment: segment,
          source: source,
          sourceText: annotation.text,
          segmentIndex: use['segment_index'] as int,
          startOffset: start)
    ]))));
    await tester.pumpAndSettle();
    expect(find.byType(ListTile), findsNWidgets(3));
    expect(find.textContaining('Dictionary entry'), findsNothing);
    expect(find.textContaining('Dictionary ·'), findsNothing);
    expect(find.textContaining('Grammar ·'), findsNothing);
    for (final index in [-1, 0, 1]) {
      final tile = tester
          .widget<ListTile>(find.byKey(ValueKey('japanese-form-step-$index')));
      expect(tile.onTap, isNotNull);
    }
    await tester.tap(find.byKey(const ValueKey('japanese-form-step--1')));
    await tester.pumpAndSettle();
    expect(find.text('入る'), findsOneWidget);
    expect(find.text('Used in this passage'), findsNothing);
    await tester.tap(find.byTooltip('Back'));
    await tester.pumpAndSettle();
    for (final index in [0, 1]) {
      final step = segment.formSteps[index];
      final link = grammar.formStep(
          source,
          annotation.text,
          start,
          segment.text,
          use['segment_index'] as int,
          index,
          step.form,
          step.reading,
          step.label,
          step.meaningEn)!;
      final id = (link['entry_ids'] as List).single as String;
      if (index == 1) expect(id, 'ja-grammar-polite-past');
      await tester.tap(find.byKey(ValueKey('japanese-form-step-$index')));
      await tester.pumpAndSettle();
      expect(find.text(grammar.entry(id)['title'] as String), findsOneWidget);
      expect(find.text('Used in this passage'), findsNothing);
      if (index == 0) {
        final hasDirectExample = grammar.occurrences.any((occurrence) =>
            occurrence['layer'] != 'form' &&
            (occurrence['entry_ids'] as List).contains(id));
        final heading = hasDirectExample
            ? 'Examples from our texts'
            : 'Conjugation chains from our texts';
        await tester.scrollUntilVisible(
            find.text(heading), 150,
            scrollable: find.byType(Scrollable).last);
        await tester.pumpAndSettle();
        expect(find.text(heading), findsOneWidget);
      }
      await tester.tap(find.byTooltip('Back'));
      await tester.pumpAndSettle();
    }
  });

  testWidgets(
      'Bare linking stem uses reviewed meaning, not an inherited past gloss',
      (tester) async {
    final (annotation, _, _) = (await tester.runAsync(fixture))!;
    final index = annotation.segments.indexWhere((s) => s.text == '歩き');
    final segment = annotation.segments[index];
    final start = annotation.segments
        .take(index)
        .map((s) => s.text.length)
        .fold(0, (a, b) => a + b);
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: JapaneseFormChain(
      segment: segment,
      source: source,
      sourceText: annotation.text,
      segmentIndex: index,
      startOffset: start,
      includeSurfaceMeaning: true,
    ))));
    await tester.pumpAndSettle();
    expect(find.textContaining('and walked'), findsNothing);
    expect(
        tester
            .widget<Text>(
                find.byKey(const ValueKey('japanese-surface-meaning')))
            .data,
        'walking');
    await tester.tap(find.byKey(const ValueKey('japanese-form-step-0')));
    await tester.pumpAndSettle();
    expect(find.text('連用形（ます語幹）'), findsOneWidget);
    expect(find.textContaining('not by itself a past'), findsNothing);
  });

  test('Form links validate the source edition and the exact stored step',
      () async {
    final (annotation, use, grammar) = await fixture();
    final segment = annotation.segments[use['segment_index'] as int];
    final step = segment.formSteps.first;
    final start =
        String.fromCharCodes(annotation.text.runes.take(use['start'] as int))
            .length;
    Map<String, dynamic>? link(String text, String label) => grammar.formStep(
        source,
        text,
        start,
        segment.text,
        use['segment_index'] as int,
        0,
        step.form,
        step.reading,
        label,
        step.meaningEn);
    expect(link(annotation.text, step.label), isNotNull);
    expect(link('${annotation.text} stale', step.label), isNull);
    expect(link(annotation.text, 'guessed label'), isNull);
    expect(
        grammar
            .uses(source, annotation.text, start, segment.text)
            .any((o) => o['layer'] == 'form'),
        isFalse);
  });
}
