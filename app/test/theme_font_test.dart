import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/models.dart';
import 'package:hsk_graded/theme.dart';

void main() {
  test('Latin annotation text uses Latin font with bundled CJK fallbacks', () {
    for (final language in Language.values) {
      final theme = AppTheme.lightThemeFor(language);
      final style = theme.textTheme.bodyMedium!;

      expect(style.fontFamily, 'NotoSans');
      expect(style.fontFamilyFallback, contains('NotoSansSC'));
      expect(style.fontFamilyFallback, contains('NotoSansJP'));
    }
  });

  testWidgets('curly possessive stays a single unbroken text run',
      (tester) async {
    const text = 'Explains Dong Zhuo’s contempt for the heroes.';
    await tester.pumpWidget(MaterialApp(
      theme: AppTheme.lightThemeFor(Language.chinese),
      home: const Scaffold(body: Text(text)),
    ));

    final rendered = tester.widget<Text>(find.text(text));
    expect(rendered.data, text);
    expect(rendered.data, isNot(contains('’ ')));
  });
}
