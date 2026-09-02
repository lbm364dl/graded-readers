import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/gestures.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/models.dart';
import 'package:hsk_graded/screens/reader_screen.dart';
import 'package:hsk_graded/services/dictionary_service.dart';
import 'package:hsk_graded/services/etymology_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUpAll(() async {
    SharedPreferences.setMockInitialValues({});
    await DictionaryService.instance.initialize(language: Language.chinese);
    await EtymologyService.instance.initialize();
  });

  testWidgets(
      'agent segment opens reviewed subwords, dictionary, and hanzi etymology',
      (tester) async {
    String? copiedText;
    tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
      SystemChannels.platform,
      (call) async {
        if (call.method == 'Clipboard.setData') {
          copiedText =
              (call.arguments as Map<dynamic, dynamic>)['text'] as String?;
        }
        return null;
      },
    );
    addTearDown(() => tester.binding.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform, null));

    final content = json.decode(
      await rootBundle.loadString('assets/content.json'),
    ) as List<dynamic>;
    final rawReader = content.cast<Map<String, dynamic>>().firstWhere(
          (item) => item['id'] == 'sanguoyanyi_hsk1',
        );
    final reader = Reader.fromJson(rawReader, Language.chinese);

    await tester.pumpWidget(MaterialApp(
      home: ReaderScreen(reader: reader, initialChapter: 0),
    ));
    await tester.runAsync(
      () => Future<void>.delayed(const Duration(milliseconds: 200)),
    );
    for (var attempt = 0; attempt < 30; attempt++) {
      await tester.pump(const Duration(milliseconds: 100));
      if (find.byType(PageView).evaluate().isNotEmpty) break;
    }
    expect(find.byType(PageView), findsOneWidget);
    expect(find.byType(SelectionArea), findsOneWidget);

    final firstParagraph = reader.chapters.first.content.split('\n\n').first;
    expect(find.text(firstParagraph, findRichText: true), findsOneWidget);

    final paragraphText = tester.widget<RichText>(
      find.text(firstParagraph, findRichText: true),
    );
    TextSpan? punctuationSpan;
    void findPunctuation(InlineSpan span) {
      if (punctuationSpan != null || span is! TextSpan) return;
      if (span.text == '。') {
        punctuationSpan = span;
        return;
      }
      for (final child in span.children ?? const <InlineSpan>[]) {
        findPunctuation(child);
      }
    }

    findPunctuation(paragraphText.text);
    expect(
      punctuationSpan?.style?.fontFeatures,
      contains(const FontFeature('palt')),
    );

    await tester.tap(find.byTooltip('Copy chapter text'));
    await tester.pump();
    expect(copiedText, reader.chapters.first.content);

    TextSpan targetSpan() {
      final readingText = tester.widgetList<Text>(find.byType(Text)).firstWhere(
          (widget) => widget.textSpan?.toPlainText().contains('黄巾军') == true);
      TextSpan? target;
      void findSpan(InlineSpan span) {
        if (target != null) return;
        if (span is TextSpan) {
          if (span.text == '黄巾军') {
            target = span;
            return;
          }
          for (final child in span.children ?? const <InlineSpan>[]) {
            findSpan(child);
          }
        }
      }

      findSpan(readingText.textSpan!);
      return target!;
    }

    var target = targetSpan();
    expect(target.recognizer, isA<TapGestureRecognizer>());
    (target.recognizer! as TapGestureRecognizer).onTap!();
    await tester.pumpAndSettle();

    expect(find.text('How it is built'), findsOneWidget);
    expect(find.text('Agent annotation'), findsNothing);
    expect(find.text('word'), findsNothing);
    final readingCue = tester.widget<Text>(
      find.byKey(const ValueKey('definition-sheet-reading-cue')),
    );
    final cueSpan = readingCue.textSpan! as TextSpan;
    final cueIcon = cueSpan.children!.whereType<WidgetSpan>().single;
    expect(cueIcon.alignment, PlaceholderAlignment.middle);
    expect(find.text('huáng jīn'), findsOneWidget);
    expect(find.text('Huáng jīn'), findsNothing);
    expect(
      tester.widget<BottomSheet>(find.byType(BottomSheet)).showDragHandle,
      isTrue,
    );
    final viewport = find.byKey(const ValueKey('definition-sheet-viewport'));
    expect(viewport, findsOneWidget);
    final logicalScreenHeight =
        tester.view.physicalSize.height / tester.view.devicePixelRatio;
    expect(tester.getSize(viewport).height,
        lessThanOrEqualTo(logicalScreenHeight * 0.5 + 1));
    final scrollingHeader =
        find.byKey(const ValueKey('definition-sheet-scrolling-header'));
    final headerTop = tester.getTopLeft(scrollingHeader).dy;
    final sheetScrollable = find.descendant(
      of: viewport,
      matching: find.byType(Scrollable),
    );
    final scrollState = tester.state<ScrollableState>(sheetScrollable);
    scrollState.position.jumpTo(scrollState.position.maxScrollExtent);
    await tester.pump();
    expect(tester.getTopLeft(scrollingHeader).dy, lessThan(headerTop));

    Navigator.of(tester.element(find.text('How it is built'))).pop();
    await tester.pumpAndSettle();
    target = targetSpan();
    expect(target.style?.backgroundColor, isNotNull);

    (target.recognizer! as TapGestureRecognizer).onTap!();
    await tester.pumpAndSettle();

    expect(find.byTooltip('Copy word'), findsOneWidget);
    expect(
        find.byKey(const ValueKey('agent-subsegment-黄巾军-0')), findsOneWidget);

    final copyWord = find.byTooltip('Copy word');
    await tester.ensureVisible(copyWord);
    await tester.pump(const Duration(milliseconds: 200));
    await tester.tap(copyWord);
    await tester.pump();
    expect(copiedText, '黄巾军');

    final yellowTurbanPart =
        find.byKey(const ValueKey('agent-subsegment-黄巾军-0'));
    await tester.ensureVisible(yellowTurbanPart);
    await tester.pump(const Duration(milliseconds: 200));
    await tester.tap(yellowTurbanPart);
    await tester.pump(const Duration(milliseconds: 500));
    expect(find.text('黄巾', findRichText: true), findsWidgets);
    expect(find.text('huáng jīn'), findsWidgets);
    expect(find.text('Huáng jīn'), findsNothing);

    final yellowCharacter = find.text('黄').last;
    await tester.ensureVisible(yellowCharacter);
    await tester.pump(const Duration(milliseconds: 200));
    await tester.tap(yellowCharacter);
    await tester.pump(const Duration(milliseconds: 500));
    expect(find.text('Etymology'), findsOneWidget);
  });
}
