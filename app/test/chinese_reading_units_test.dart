import 'dart:convert';
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/data.dart';
import 'package:hsk_graded/models.dart';
import 'package:hsk_graded/screens/reader_screen.dart';
import 'package:hsk_graded/services/chinese_reading_units.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const asset = 'assets/annotations/chinese_sanguoyanyi_hsk1_001.json';
  late AgentChapterAnnotation annotation;
  late ChineseReadingUnits units;
  late Reader reader;
  final extraChapters = <int, AgentChapterAnnotation>{};
  final extraReaders = <int, Reader>{};
  setUpAll(() async {
    annotation = AgentChapterAnnotation.fromJson(
        jsonDecode(await rootBundle.loadString(asset)) as Map<String, dynamic>);
    units = await ChineseReadingUnits.load();
    reader = (await ContentRepository().loadReaders(Language.chinese))
        .singleWhere((r) => r.id == 'sanguoyanyi_hsk1');
    for (final level in [3, 4, 5, 6]) {
      extraChapters[level] = AgentChapterAnnotation.fromJson(jsonDecode(
              await rootBundle.loadString(
                  'assets/annotations/chinese_sanguoyanyi_hsk${level}_001.json'))
          as Map<String, dynamic>);
      extraReaders[level] =
          (await ContentRepository().loadReaders(Language.chinese))
              .singleWhere((r) => r.id == 'sanguoyanyi_hsk$level');
    }
  });
  setUp(() => SharedPreferences.setMockInitialValues({}));

  test('bound forms are whole but independent words stay separate', () {
    final display = units.forAnnotation(asset, annotation);
    expect(display.map((u) => u.segment.text),
        containsAll(['看到了', '来了', '看着', '有难']));
    for (final unit in display) {
      expect(unit.segment.text, isNot(contains('黄巾军')));
      expect(unit.segment.text, isNot(contains('张飞')));
      expect(unit.segment.text, isNot(contains('一起')));
      expect(unit.segment.text, isNot(contains('国家')));
      expect(
          annotation.segments
              .sublist(unit.firstSegment, unit.lastSegment + 1)
              .map((s) => s.text)
              .join(),
          unit.segment.text);
    }
    // The underlying dictionary boundaries have not been rewritten.
    expect(
        annotation.segments.sublist(1, 4).map((s) => s.text), ['看', '到', '了']);
  });

  test('stale annotations fail instead of changing tap boundaries silently',
      () {
    expect(
        () => units.forAnnotation(
            asset,
            AgentChapterAnnotation(
              text: '${annotation.text}changed',
              segments: annotation.segments,
              grammarOverlays: annotation.grammarOverlays,
            )),
        throwsStateError);
    expect(units.forAnnotation('unrelated_hsk2', annotation), isEmpty);
  });

  test('HSK2 survival complement remains a whole display unit', () async {
    const source = 'assets/annotations/chinese_sanguoyanyi_hsk2_001.json';
    final chapter = AgentChapterAnnotation.fromJson(
        jsonDecode(await rootBundle.loadString(source))
            as Map<String, dynamic>);
    final unit = units
        .forAnnotation(source, chapter)
        .singleWhere((u) => u.segment.text == '活下来');
    expect(
        chapter.segments
            .sublist(unit.firstSegment, unit.lastSegment + 1)
            .map((s) => s.text),
        ['活', '下来']);
  });

  for (final level in [3, 4, 5, 6]) {
    testWidgets('complete HSK$level reader opens linked units and grammar',
        (tester) async {
      final source =
          'assets/annotations/chinese_sanguoyanyi_hsk${level}_001.json';
      final chapter = extraChapters[level]!;
      final currentReader = extraReaders[level]!;
      final unit = units.forAnnotation(source, chapter).first;
      await tester.pumpWidget(MaterialApp(
          home: ReaderScreen(
              reader: currentReader,
              initialChapter: 0,
              initialCharacterOffset: unit.start)));
      for (var attempt = 0; attempt < 30; attempt++) {
        await tester.runAsync(
            () => Future<void>.delayed(const Duration(milliseconds: 100)));
        await tester.pump(const Duration(milliseconds: 100));
        if (find.byType(PageView).evaluate().isNotEmpty) break;
      }
      await tester.pumpAndSettle();
      final spans = <TextSpan>[];
      void collect(InlineSpan span) {
        if (span is! TextSpan) return;
        if (span.text == unit.segment.text &&
            span.recognizer is TapGestureRecognizer) {
          spans.add(span);
        }
        for (final child in span.children ?? const <InlineSpan>[]) {
          collect(child);
        }
      }

      for (final text in tester.widgetList<Text>(find.byType(Text))) {
        if (text.textSpan != null) collect(text.textSpan!);
      }
      expect(spans, isNotEmpty);
      (spans.first.recognizer! as TapGestureRecognizer).onTap!();
      await tester.pumpAndSettle();
      await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 200)));
      await tester.pumpAndSettle();
      expect(find.text('Grammar'), findsOneWidget);
      expect(find.textContaining(unit.grammar.meaningEn), findsOneWidget);
      expect(find.textContaining('Dictionary ·'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      await tester.pumpAndSettle();
    });
  }

  for (final form in ['看到了', '来了', '有难']) {
    testWidgets('$form taps as one unit with linked lexical components',
        (tester) async {
      await tester.pumpWidget(
          MaterialApp(home: ReaderScreen(reader: reader, initialChapter: 0)));
      for (var attempt = 0; attempt < 30; attempt++) {
        await tester.runAsync(
            () => Future<void>.delayed(const Duration(milliseconds: 100)));
        await tester.pump(const Duration(milliseconds: 100));
        if (find.byType(PageView).evaluate().isNotEmpty) break;
      }
      await tester.pumpAndSettle();
      final spans = <TextSpan>[];
      void collect(InlineSpan span) {
        if (span is! TextSpan) return;
        if (span.text == form && span.recognizer is TapGestureRecognizer) {
          spans.add(span);
        }
        for (final child in span.children ?? const <InlineSpan>[]) {
          collect(child);
        }
      }

      for (final text in tester.widgetList<Text>(find.byType(Text))) {
        if (text.textSpan != null) collect(text.textSpan!);
      }
      expect(spans, hasLength(1));
      (spans.single.recognizer! as TapGestureRecognizer).onTap!();
      await tester.pumpAndSettle();
      expect(find.text('Components'), findsOneWidget);
      if (form == '有难') {
        expect(find.textContaining('noun nàn'), findsOneWidget);
      }
      final unit = units
          .forAnnotation(asset, annotation)
          .singleWhere((u) => u.segment.text == form);
      final component =
          find.byKey(ValueKey('reading-unit-component-${unit.firstSegment}'));
      await tester.ensureVisible(component);
      await tester.tap(component);
      await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 100)));
      await tester.pumpAndSettle();
      // The underlying whole-form sheet and the nested lexical component now
      // each have their own dictionary link.
      expect(find.textContaining('Dictionary ·'), findsNWidgets(2));
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      await tester.pumpAndSettle();
    });
  }
}
