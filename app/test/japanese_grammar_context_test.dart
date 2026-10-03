import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/models.dart';
import 'package:hsk_graded/services/grammar_dictionary.dart';
import 'package:hsk_graded/widgets/japanese_grammar_context.dart';

const source = 'assets/annotations/japanese_wagahai_n5_001.json';
Future<(AgentChapterAnnotation, GrammarDictionary)> fixture() async => (
      AgentChapterAnnotation.fromJson(
          jsonDecode(await rootBundle.loadString(source))
              as Map<String, dynamic>),
      await GrammarDictionary.load()
    );

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  testWidgets('Particles use exact reviewed functions and spoken readings',
      (tester) async {
    final (annotation, dictionary) = (await tester.runAsync(fixture))!;
    var offset = 0;
    for (var i = 0; i < annotation.segments.length; i++) {
      final segment = annotation.segments[i];
      final lesson = dictionary.particleLesson(
          source, annotation.text, offset, segment.text, i);
      if (segment.type == 'particle' && lesson != null) {
        for (final reading in [false, true]) {
          await tester.pumpWidget(MaterialApp(
              home: JapaneseSegmentText(
                  segment: segment,
                  source: source,
                  sourceText: annotation.text,
                  segmentIndex: i,
                  startOffset: offset,
                  reading: reading)));
          await tester.pumpAndSettle();
          expect(
              find.text((reading ? lesson['reading'] : lesson['summary_en'])
                  as String),
              findsOneWidget);
        }
        expect(
            dictionary.particleLesson(
                source, '${annotation.text}changed', offset, segment.text, i),
            isNull);
        expect(
            dictionary.particleLesson(
                source, annotation.text, offset, segment.text, i + 1000),
            isNull);
      }
      offset += segment.text.length;
    }
  });
  testWidgets(
      'Only reviewed grammar context appears, with no legacy lexical cards',
      (tester) async {
    final (annotation, dictionary) = (await tester.runAsync(fixture))!;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: SingleChildScrollView(
      child: JapaneseGrammarContext(
          source: source,
          sourceText: annotation.text,
          overlays: annotation.grammarOverlays),
    ))));
    await tester.pumpAndSettle();
    final reviewed = dictionary.occurrences
        .where((use) => use['layer'] == 'overlay' && use['source'] == source);
    for (final use in reviewed) {
      expect(find.text(use['surface'] as String), findsOneWidget);
      expect(find.text(use['context_en'] as String), findsOneWidget);
    }
    expect(find.text('In this phrase'), findsOneWidget);
    for (final expression in ['男の人', '女の人', '目が回りました']) {
      expect(find.text(expression), findsNothing);
    }
    expect(find.textContaining('From '), findsNothing);
    expect(find.byType(InkWell), findsNothing);
    expect(find.textContaining('Dictionary entry'), findsNothing);
  });

  testWidgets('Context fails closed for a different source edition',
      (tester) async {
    final (annotation, _) = (await tester.runAsync(fixture))!;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: JapaneseGrammarContext(
      source: source,
      sourceText: '${annotation.text}changed',
      overlays: annotation.grammarOverlays,
    ))));
    await tester.pumpAndSettle();
    expect(find.text('In this phrase'), findsNothing);
  });
}
