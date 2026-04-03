import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/services/etymology_service.dart';

void main() {
  test('romajiToKatakana', () {
    expect(romajiToKatakana('GEI SEI'), 'ゲイ・セイ');
    expect(romajiToKatakana('NETSU ZETSU NECHI'), 'ネツ・ゼツ・ネチ');
    expect(romajiToKatakana('CHI'), 'チ');
    expect(romajiToKatakana('JI JOU'), 'ジ・ジョウ');
    expect(romajiToKatakana('GOKU KYOKU'), 'ゴク・キョク');
    expect(romajiToKatakana('TOU TO'), 'トウ・ト');
    expect(romajiToKatakana('SHIN'), 'シン');
  });

  test('romajiToHiragana', () {
    expect(romajiToHiragana('ATSUI'), 'あつい');
    expect(romajiToHiragana('NOBORU'), 'のぼる');
    expect(romajiToHiragana('KOKORO'), 'こころ');
    expect(romajiToHiragana('MIMI NOMI'), 'みみ・のみ');
    expect(romajiToHiragana('HAJIRU HAJI HAZUKASHII'), 'はじる・はじ・はずかしい');
    expect(romajiToHiragana('TSUKARERU TSUKARE'), 'つかれる・つかれ');
  });
}
