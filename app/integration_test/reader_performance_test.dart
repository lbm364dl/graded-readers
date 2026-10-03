import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/main.dart' as app;
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
  final binding = IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('profiles scrolling an annotated HSK 3 chapter', (tester) async {
    final preferences = await SharedPreferences.getInstance();
    await preferences.clear();

    app.main();
    await _pumpUntil(tester, find.text('三国演义'));
    await tester.tap(find.text('三国演义').first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('HSK 3 version'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('桃园结义'));
    await _pumpUntil(tester, find.byTooltip('Copy chapter text'));

    final chapterList = find.byType(ListView).last;
    await binding.watchPerformance(
      () async {
        for (var i = 0; i < 3; i++) {
          await tester.fling(chapterList, const Offset(0, -650), 2200);
          await tester.pumpAndSettle();
        }
        for (var i = 0; i < 3; i++) {
          await tester.fling(chapterList, const Offset(0, 650), 2200);
          await tester.pumpAndSettle();
        }
      },
      reportKey: 'hsk3_reader_scroll',
    );

    expect(tester.takeException(), isNull);
  });
}
