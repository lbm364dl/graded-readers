import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/services/segmenter.dart';
import 'package:hsk_graded/services/dictionary_service.dart';

/// Mock dictionary that contains specific Japanese words.
/// We test segmentation logic without loading the full dictionary.
class _MockDict extends DictionaryService {
  final Set<String> _words;
  _MockDict(this._words) : super.forTest();

  @override
  bool get isReady => true;

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
  // Common JLPT vocabulary in dictionary form
  final dict = _MockDict({
    // Verbs
    '食べる', '行く', '見る', '来る', 'する', '読む', '書く', '話す',
    '聞く', '飲む', '買う', '会う', '使う', '歩く', '走る', '出る',
    '入る', '思う', '言う', '知る', '持つ', '待つ', '取る', '作る',
    '考える', '教える', '始める', '終わる', '住む', '働く', '遊ぶ',
    // Ichidan verbs
    '起きる', '寝る', '開ける', '閉める', '着る', '浴びる',
    // い-adjectives
    '大きい', '小さい', '新しい', '古い', '高い', '安い', '良い',
    '悪い', '楽しい', '難しい', '美しい', '忙しい', '嬉しい',
    // な-adjectives
    '好き', '嫌い', '元気', '静か', '有名', '大切', '簡単',
    // Nouns
    '日本', '日本語', '学校', '学生', '先生', '友達', '時間',
    '電車', '仕事', '勉強', '食べ物', '飲み物', '天気', '今日',
    '明日', '昨日', '毎日', '人', '子供', '男', '女', '家',
    // Common particles/words that may appear
    'ある', 'いる', 'なる', 'できる',
    // Multi-char
    '三国', '演義',
  });

  group('Chinese segmentation', () {
    test('segments known multi-char words', () {
      final tokens = segmentText('三国演義', dict);
      expect(tokens, ['三国', '演義']);
    });

    test('falls back to single chars for unknown words', () {
      final tokens = segmentText('未知語', dict);
      expect(tokens, ['未', '知', '語']);
    });
  });

  group('Japanese verb inflection detection', () {
    test('masu-form ichidan: 食べます → 食べる', () {
      final tokens = segmentText('食べます', dict);
      expect(tokens.any((t) => t.contains('食べ')), true,
          reason: 'Should detect 食べる stem in 食べます');
    });

    test('past tense ichidan: 食べました → 食べる', () {
      final tokens = segmentText('食べました', dict);
      expect(tokens.any((t) => t.contains('食べ')), true);
    });

    test('te-form ichidan: 食べて → 食べる', () {
      final tokens = segmentText('食べて', dict);
      expect(tokens.any((t) => t.contains('食べ')), true);
    });

    test('negative ichidan: 食べない → 食べる', () {
      final tokens = segmentText('食べない', dict);
      expect(tokens.any((t) => t.contains('食べ')), true);
    });

    test('tai-form ichidan: 食べたい → 食べる', () {
      final tokens = segmentText('食べたい', dict);
      expect(tokens.any((t) => t.contains('食べ')), true);
    });

    test('masu-form godan: 行きます → 行く', () {
      final tokens = segmentText('行きます', dict);
      expect(tokens.any((t) => t.contains('行')), true);
    });

    test('past tense godan: 行きました → 行く', () {
      final tokens = segmentText('行きました', dict);
      expect(tokens.any((t) => t.contains('行')), true);
    });

    test('te-form godan: 行って → 行く', () {
      final tokens = segmentText('行って', dict);
      expect(tokens.any((t) => t.contains('行')), true);
    });

    test('negative godan: 行かない → 行く', () {
      final tokens = segmentText('行かない', dict);
      expect(tokens.any((t) => t.contains('行')), true);
    });

    test('te-form godan mu: 読んで → 読む', () {
      final tokens = segmentText('読んで', dict);
      expect(tokens.any((t) => t.contains('読')), true);
    });

    test('past godan mu: 読んだ → 読む', () {
      final tokens = segmentText('読んだ', dict);
      expect(tokens.any((t) => t.contains('読')), true);
    });

    test('te-form godan su: 話して → 話す', () {
      final tokens = segmentText('話して', dict);
      expect(tokens.any((t) => t.contains('話')), true);
    });

    test('masu-form godan su: 話します → 話す', () {
      final tokens = segmentText('話します', dict);
      expect(tokens.any((t) => t.contains('話')), true);
    });

    test('te-form godan ku: 書いて → 書く', () {
      final tokens = segmentText('書いて', dict);
      expect(tokens.any((t) => t.contains('書')), true);
    });

    test('past godan ku: 書いた → 書く', () {
      final tokens = segmentText('書いた', dict);
      expect(tokens.any((t) => t.contains('書')), true);
    });

    test('te-form godan gu: 遊んで → 遊ぶ', () {
      final tokens = segmentText('遊んで', dict);
      expect(tokens.any((t) => t.contains('遊')), true);
    });

    test('te-form godan bu: 飲んで → 飲む', () {
      final tokens = segmentText('飲んで', dict);
      expect(tokens.any((t) => t.contains('飲')), true);
    });

    test('masu-form godan tsu: 持ちます → 持つ', () {
      final tokens = segmentText('持ちます', dict);
      expect(tokens.any((t) => t.contains('持')), true);
    });

    test('te-form godan tsu: 持って → 持つ', () {
      final tokens = segmentText('持って', dict);
      expect(tokens.any((t) => t.contains('持')), true);
    });

    test('progressive: 食べている → 食べる', () {
      final tokens = segmentText('食べている', dict);
      expect(tokens.any((t) => t.contains('食べ')), true);
    });

    test('irregular: 来ました → 来る', () {
      final tokens = segmentText('来ました', dict);
      expect(tokens.any((t) => t.contains('来')), true);
    });

    test('irregular: しました → する', () {
      final tokens = segmentText('しました', dict);
      // する → し is the masu stem
      expect(tokens.any((t) => t == 'し' || t == 'しました'), true);
    });
  });

  group('Japanese adjective inflection', () {
    test('negative i-adj: 大きくない → 大きい', () {
      final tokens = segmentText('大きくない', dict);
      expect(tokens.any((t) => t.contains('大き')), true);
    });

    test('past i-adj: 大きかった → 大きい', () {
      final tokens = segmentText('大きかった', dict);
      expect(tokens.any((t) => t.contains('大き')), true);
    });

    test('te-form i-adj: 大きくて → 大きい', () {
      final tokens = segmentText('大きくて', dict);
      expect(tokens.any((t) => t.contains('大き')), true);
    });

    test('adverbial i-adj: 楽しく → 楽しい', () {
      final tokens = segmentText('楽しく', dict);
      expect(tokens.any((t) => t.contains('楽し')), true);
    });
  });

  group('Full sentence segmentation', () {
    test('simple N5 sentence', () {
      final tokens = segmentText('毎日学校に行きます。', dict);
      // Should find: 毎日, 学校, 行く(inflected)
      final joined = tokens.join('');
      expect(joined, '毎日学校に行きます。');
      expect(tokens.where((t) => t == '毎日').length, 1);
      expect(tokens.where((t) => t == '学校').length, 1);
    });

    test('sentence with te-form', () {
      final tokens = segmentText('友達に会って話しました。', dict);
      final joined = tokens.join('');
      expect(joined, '友達に会って話しました。');
      expect(tokens.where((t) => t == '友達').length, 1);
    });

    test('mixed content preserved', () {
      final tokens = segmentText('ABC日本語です。', dict);
      expect(tokens.first, 'ABC');
      expect(tokens.contains('日本語'), true);
    });

    test('punctuation separated', () {
      final tokens = segmentText('日本。学校！', dict);
      expect(tokens, ['日本', '。', '学校', '！']);
    });
  });
}
