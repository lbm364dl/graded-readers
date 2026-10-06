import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/main.dart' as app;
import 'package:hsk_graded/services/dictionary_service.dart';
import 'package:hsk_graded/services/etymology_service.dart';
import 'package:hsk_graded/services/glyph_service.dart';
import 'package:integration_test/integration_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

Future<void> _pumpUntil(
  WidgetTester tester,
  Finder finder, {
  Duration timeout = const Duration(seconds: 20),
}) async {
  final deadline = DateTime.now().add(timeout);
  while (finder.evaluate().isEmpty && DateTime.now().isBefore(deadline)) {
    await tester.pump(const Duration(milliseconds: 100));
  }
  expect(finder, findsWidgets);
}

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('opens the new HSK 1 reader and exercises native clipboard',
      (tester) async {
    final preferences = await SharedPreferences.getInstance();
    await preferences.clear();

    app.main();
    await _pumpUntil(tester, find.text('三国演义'));

    await tester.tap(find.text('三国演义').first);
    await tester.pumpAndSettle();
    expect(find.text('HSK 1 version'), findsOneWidget);

    await tester.tap(find.text('HSK 1 version'));
    await tester.pumpAndSettle();
    expect(find.text('桃园结义'), findsOneWidget);

    await tester.tap(find.text('桃园结义'));
    await _pumpUntil(tester, find.byTooltip('Copy chapter text'));
    expect(find.text('Ch. 1/1'), findsOneWidget);
    expect(find.textContaining('Dotted underline'), findsOneWidget);

    final lookupDeadline = DateTime.now().add(const Duration(seconds: 20));
    while (!DictionaryService.instance.isReady &&
        DateTime.now().isBefore(lookupDeadline)) {
      await tester.pump(const Duration(milliseconds: 100));
    }
    final popularSupport = DictionaryService.instance.lookup('民心');
    expect(popularSupport?.pinyin, 'mín xīn');
    expect(popularSupport?.definitions, contains('popular sentiment'));
    await EtymologyService.instance.initialize();
    await GlyphService.instance.initialize();
    expect(EtymologyService.instance.lookup('民'), isNotNull);
    expect(GlyphService.instance.lookup('民')?.eras, isNotEmpty);

    await tester.tap(find.byTooltip('Copy chapter text'));
    await tester.pumpAndSettle();
    final clipboard = await Clipboard.getData(Clipboard.kTextPlain);
    expect(clipboard?.text, contains('刘备看到了招兵的告示'));
    expect(find.text('Chapter text copied'), findsOneWidget);

    await tester.tap(find.byTooltip('Increase text size'));
    await tester.pump();
    await tester.drag(find.byType(ListView).last, const Offset(0, -450));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });
}
