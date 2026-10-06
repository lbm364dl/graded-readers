import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/screens/reader_screen.dart';

void main() {
  test('kana-only dictionary forms do not repeat their readings', () {
    expect(formatJapaneseLemmaReading('こと', 'こと'), 'こと');
    expect(formatJapaneseLemmaReading('ます', 'ます'), 'ます');
    expect(formatJapaneseLemmaReading('する', 'する'), 'する');
  });

  test('kanji dictionary forms retain a distinct parenthesized reading', () {
    expect(formatJapaneseLemmaReading('歩く', 'あるく'), '歩く（あるく）');
    expect(formatJapaneseLemmaReading('生まれる', 'うまれる'), '生まれる（うまれる）');
  });
}
