import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/models.dart';
import 'package:hsk_graded/services/segmenter.dart';
import 'package:hsk_graded/services/dictionary_service.dart';

/// Mock dictionary for testing Chinese max-forward matching.
class _MockDict extends DictionaryService {
  final Set<String> _words;
  _MockDict(this._words) : super.forTest();

  @override
  bool get isReady => true;
  @override
  Language get activeLanguage => Language.chinese;
  @override
  Set<String> get wordSet => _words;
  @override
  int get maxWordLength =>
      _words.fold(0, (m, w) => w.length > m ? w.length : m);
  @override
  bool hasWord(String word) => _words.contains(word);
  @override
  DictEntry? lookup(String word) => _words.contains(word)
      ? DictEntry(word: word, pinyin: '', definitions: [])
      : null;
}

void main() {
  final dict = _MockDict({
    '三国', '演義', '食べる', '行く', '見る', '来る', 'する', '読む',
    '書く', '話す', '飲む', '買う', '使う', '歩く', '走る', '出る',
    '思う', '言う', '持つ', '待つ', '取る', '作る', '遊ぶ',
    '大きい', '小さい', '楽しい', '好き', '元気',
    '日本', '日本語', '学校', '学生', '友達', '毎日', '人',
    'ある', 'いる', 'なる', 'できる',
  });

  group('Chinese max-forward matching', () {
    test('segments known multi-char words', () {
      expect(segmentText('三国演義', dict), ['三国', '演義']);
    });

    test('falls back to single chars for unknown words', () {
      expect(segmentText('未知語', dict), ['未', '知', '語']);
    });

    test('separates punctuation', () {
      expect(segmentText('日本。学校！', dict), ['日本', '。', '学校', '！']);
    });

    test('handles mixed CJK and Latin', () {
      final tokens = segmentText('ABC日本語です。', dict);
      expect(tokens.first, 'ABC');
      expect(tokens.contains('日本語'), true);
    });

    test('empty text returns as-is', () {
      expect(segmentText('', dict), ['']);
    });

    test('pure Latin text returns single token', () {
      expect(segmentText('Hello World', dict), ['Hello World']);
    });
  });

  group('Japanese deinflection (rule-based fallback)', () {
    // Test the deinflectWord function directly — this is used
    // as fallback when kuromoji isn't available

    test('ichidan masu: 食べます → 食べる', () {
      expect(deinflectWord('食べます'), contains('食べる'));
    });

    test('ichidan past: 食べました → 食べる', () {
      expect(deinflectWord('食べました'), contains('食べる'));
    });

    test('ichidan te: 食べて → 食べる', () {
      expect(deinflectWord('食べて'), contains('食べる'));
    });

    test('ichidan negative: 食べない → 食べる', () {
      expect(deinflectWord('食べない'), contains('食べる'));
    });

    test('ichidan tai: 食べたい → 食べる', () {
      expect(deinflectWord('食べたい'), contains('食べる'));
    });

    test('godan ku masu: 行きます → 行く', () {
      expect(deinflectWord('行きます'), contains('行く'));
    });

    test('godan ku past: 行きました → 行く', () {
      expect(deinflectWord('行きました'), contains('行く'));
    });

    test('godan ku te: 行って → 行く', () {
      expect(deinflectWord('行って'), contains('行く'));
    });

    test('godan ku negative: 行かない → 行く', () {
      expect(deinflectWord('行かない'), contains('行く'));
    });

    test('godan ku past2: 行った → 行く', () {
      expect(deinflectWord('行った'), contains('行く'));
    });

    test('godan mu te: 読んで → 読む', () {
      expect(deinflectWord('読んで'), contains('読む'));
    });

    test('godan mu past: 読んだ → 読む', () {
      expect(deinflectWord('読んだ'), contains('読む'));
    });

    test('godan su te: 話して → 話す', () {
      expect(deinflectWord('話して'), contains('話す'));
    });

    test('godan su masu: 話します → 話す', () {
      expect(deinflectWord('話します'), contains('話す'));
    });

    test('godan ku te2: 書いて → 書く', () {
      expect(deinflectWord('書いて'), contains('書く'));
    });

    test('godan ku past3: 書いた → 書く', () {
      expect(deinflectWord('書いた'), contains('書く'));
    });

    test('godan bu te: 遊んで → 遊ぶ', () {
      expect(deinflectWord('遊んで'), contains('遊ぶ'));
    });

    test('godan mu te: 飲んで → 飲む', () {
      expect(deinflectWord('飲んで'), contains('飲む'));
    });

    test('godan tsu masu: 持ちます → 持つ', () {
      expect(deinflectWord('持ちます'), contains('持つ'));
    });

    test('godan tsu te: 持って → 持つ', () {
      expect(deinflectWord('持って'), contains('持つ'));
    });

    test('godan u te: 買って → 買う', () {
      expect(deinflectWord('買って'), contains('買う'));
    });

    test('godan u masu: 使います → 使う', () {
      expect(deinflectWord('使います'), contains('使う'));
    });

    // i-adjective inflections
    test('i-adj negative: 大きくない → 大きい', () {
      expect(deinflectWord('大きくない'), contains('大きい'));
    });

    test('i-adj past: 大きかった → 大きい', () {
      expect(deinflectWord('大きかった'), contains('大きい'));
    });

    test('i-adj te: 大きくて → 大きい', () {
      expect(deinflectWord('大きくて'), contains('大きい'));
    });

    test('i-adj adverbial: 楽しく → 楽しい', () {
      expect(deinflectWord('楽しく'), contains('楽しい'));
    });

    // Progressive
    test('progressive: 食べている → 食べる', () {
      expect(deinflectWord('食べている'), contains('食べる'));
    });

    test('progressive past: 食べていた → 食べる', () {
      expect(deinflectWord('食べていた'), contains('食べる'));
    });

    // Negative masu
    test('negative masu: 食べません → 食べる', () {
      expect(deinflectWord('食べません'), contains('食べる'));
    });

    test('godan negative masu: 行きません → 行く', () {
      expect(deinflectWord('行きません'), contains('行く'));
    });
  });
}
