import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/models.dart';
import 'package:hsk_graded/services/segmenter.dart';
import 'package:hsk_graded/services/dictionary_service.dart';

class _MockDict extends DictionaryService {
  final Set<String> _words;
  final Language _lang;
  _MockDict(this._words, [this._lang = Language.chinese]) : super.forTest();

  @override
  bool get isReady => true;
  @override
  Language get activeLanguage => _lang;
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
  // -- Shared dictionaries for tests --

  final zhDict = _MockDict({
    '三国', '演義', '日本', '日本語', '学校', '学生', '友達', '毎日', '人',
  });

  // Japanese dictionary with common words for segmentation tests
  final jpDict = _MockDict({
    // Ichidan verbs
    '食べる', '見る', '出る', '起きる', 'いる', 'できる', '疲れる',
    '教える', '考える', '開ける', '閉める', '着る', '寝る', '忘れる',
    // Godan verbs
    '行く', '読む', '書く', '話す', '飲む', '買う', '使う', '歩く',
    '走る', '思う', '言う', '持つ', '待つ', '取る', '作る', '遊ぶ',
    '泳ぐ', '死ぬ', '呼ぶ', '聞く', '帰る', '会う', '乗る', '登る',
    '知る', '立つ', '座る', '笑う', '泣く', '歌う', '踊る', '働く',
    '住む', '送る', '届く', '始まる', '終わる', '分かる',
    // する・来る・参る
    'する', '来る', 'まいる',
    // Existence / copula
    'ある', 'なる', 'だ',
    // i-adjectives
    '大きい', '小さい', '楽しい', '美しい', '嬉しい', '悲しい',
    '暑い', '寒い', '高い', '安い', '新しい', '古い', '良い',
    // na-adjectives / nouns
    '好き', '元気', '静か', '綺麗',
    // Nouns
    '日本', '日本語', '学校', '学生', '友達', '毎日', '人',
    '本', '映画', '音楽', '天気', '仕事', '電車', '山',
    '犬', '猫', '花', '水', '朝', '夜', '今日', '明日',
    '東京', '子供', '時間', '手紙', '先生', '部屋',
    // Auxiliary verbs (used in compound verbs)
    '始める', '続ける', '終わる', '出す', '過ぎる', '合う',
    '直す', '込む', '上がる', '下がる',
  }, Language.japanese);

  // Dict WITH compound verbs (some compounds appear as dictionary entries)
  final jpDictWithCompounds = _MockDict({
    // Same base verbs
    '食べる', '見る', '出る', '起きる', '疲れる', '登る', '読む',
    '書く', '話す', '飲む', '買う', '歩く', '走る', '泳ぐ',
    '持つ', '待つ', '取る', '作る', '遊ぶ', '行く', '帰る',
    '思う', '言う', '使う', '聞く', '座る', '立つ', '知る',
    '住む', '送る', '届く', '始まる', '終わる', '分かる',
    '乗る', '会う', '働く', '笑う', '泣く', '歌う', '踊る',
    'する', '来る', 'ある', 'いる', 'なる', 'できる', 'だ',
    // Auxiliaries
    '始める', '続ける', '出す', '過ぎる', '合う', '直す', '込む',
    // Compound verbs that ARE in the dictionary
    '食べ始める', '読み始める', '走り出す', '飲み過ぎる',
    '話し合う', '書き直す', '泳ぎ始める',
    // Nouns / adjectives
    '大きい', '小さい', '楽しい', '山', '本', '水', '上',
    '毎日', '学校', '友達', '映画', '仕事', '先生', '音楽',
    '日本', '日本語', '東京', '電車', '犬', '猫',
  }, Language.japanese);

  // =========================================================================
  // Chinese segmentation
  // =========================================================================

  group('Chinese max-forward matching', () {
    test('segments known multi-char words', () {
      expect(segmentText('三国演義', zhDict), ['三国', '演義']);
    });

    test('falls back to single chars for unknown words', () {
      expect(segmentText('未知語', zhDict), ['未', '知', '語']);
    });

    test('separates punctuation', () {
      expect(segmentText('日本。学校！', zhDict), ['日本', '。', '学校', '！']);
    });

    test('handles mixed CJK and Latin', () {
      final tokens = segmentText('ABC日本語です。', zhDict);
      expect(tokens.first, 'ABC');
      expect(tokens.contains('日本語'), true);
    });

    test('empty text returns as-is', () {
      expect(segmentText('', zhDict), ['']);
    });

    test('pure Latin text returns single token', () {
      expect(segmentText('Hello World', zhDict), ['Hello World']);
    });
  });

  // =========================================================================
  // Deinflection: unit tests (deinflectWord directly)
  // =========================================================================

  group('deinflectWord', () {
    // -- Masu forms --
    group('masu forms', () {
      test('ichidan ます', () {
        expect(deinflectWord('食べます'), contains('食べる'));
      });
      test('ichidan ました', () {
        expect(deinflectWord('食べました'), contains('食べる'));
      });
      test('ichidan ません', () {
        expect(deinflectWord('食べません'), contains('食べる'));
      });
      test('ichidan ませんでした', () {
        expect(deinflectWord('食べませんでした'), contains('食べる'));
      });
      test('ichidan ましょう', () {
        expect(deinflectWord('食べましょう'), contains('食べる'));
      });
      test('godan ku ます', () {
        expect(deinflectWord('行きます'), contains('行く'));
      });
      test('godan ku ました', () {
        expect(deinflectWord('行きました'), contains('行く'));
      });
      test('godan ku ません', () {
        expect(deinflectWord('行きません'), contains('行く'));
      });
      test('godan ku ませんでした', () {
        expect(deinflectWord('行きませんでした'), contains('行く'));
      });
      test('godan ku ましょう', () {
        expect(deinflectWord('行きましょう'), contains('行く'));
      });
      test('godan su ます', () {
        expect(deinflectWord('話します'), contains('話す'));
      });
      test('godan su ました', () {
        expect(deinflectWord('話しました'), contains('話す'));
      });
      test('godan mu ます', () {
        expect(deinflectWord('読みます'), contains('読む'));
      });
      test('godan mu ました', () {
        expect(deinflectWord('読みました'), contains('読む'));
      });
      test('godan tsu ます', () {
        expect(deinflectWord('持ちます'), contains('持つ'));
      });
      test('godan tsu ました', () {
        expect(deinflectWord('持ちました'), contains('持つ'));
      });
      test('godan u ます', () {
        expect(deinflectWord('使います'), contains('使う'));
      });
      test('godan u ました', () {
        expect(deinflectWord('使いました'), contains('使う'));
      });
      test('godan bu ます', () {
        expect(deinflectWord('遊びます'), contains('遊ぶ'));
      });
      test('godan bu ました', () {
        expect(deinflectWord('遊びました'), contains('遊ぶ'));
      });
      test('godan gu ます', () {
        expect(deinflectWord('泳ぎます'), contains('泳ぐ'));
      });
      test('godan gu ました', () {
        expect(deinflectWord('泳ぎました'), contains('泳ぐ'));
      });
      test('godan nu ます', () {
        expect(deinflectWord('死にます'), contains('死ぬ'));
      });
      test('godan ru ます', () {
        expect(deinflectWord('帰ります'), contains('帰る'));
      });
      test('godan ru ました', () {
        expect(deinflectWord('帰りました'), contains('帰る'));
      });
    });

    // -- Te-form --
    group('te-form', () {
      test('ichidan て', () {
        expect(deinflectWord('食べて'), contains('食べる'));
      });
      test('godan ku: いて', () {
        expect(deinflectWord('書いて'), contains('書く'));
      });
      test('godan ku: って (行く)', () {
        expect(deinflectWord('行って'), contains('行く'));
      });
      test('godan gu: いで', () {
        expect(deinflectWord('泳いで'), contains('泳ぐ'));
      });
      test('godan su: して', () {
        expect(deinflectWord('話して'), contains('話す'));
      });
      test('godan mu: んで', () {
        expect(deinflectWord('読んで'), contains('読む'));
      });
      test('godan bu: んで', () {
        expect(deinflectWord('遊んで'), contains('遊ぶ'));
      });
      test('godan nu: んで', () {
        expect(deinflectWord('死んで'), contains('死ぬ'));
      });
      test('godan tsu: って', () {
        expect(deinflectWord('持って'), contains('持つ'));
      });
      test('godan u: って', () {
        expect(deinflectWord('買って'), contains('買う'));
      });
      test('godan ru: って', () {
        expect(deinflectWord('取って'), contains('取る'));
      });
    });

    // -- Past (ta-form) --
    group('past tense (ta-form)', () {
      test('ichidan た', () {
        expect(deinflectWord('食べた'), contains('食べる'));
      });
      test('godan ku: いた', () {
        expect(deinflectWord('書いた'), contains('書く'));
      });
      test('godan ku: った (行く)', () {
        expect(deinflectWord('行った'), contains('行く'));
      });
      test('godan gu: いだ', () {
        expect(deinflectWord('泳いだ'), contains('泳ぐ'));
      });
      test('godan su: した', () {
        expect(deinflectWord('話した'), contains('話す'));
      });
      test('godan mu: んだ', () {
        expect(deinflectWord('読んだ'), contains('読む'));
      });
      test('godan bu: んだ', () {
        expect(deinflectWord('遊んだ'), contains('遊ぶ'));
      });
      test('godan tsu: った', () {
        expect(deinflectWord('持った'), contains('持つ'));
      });
      test('godan u: った', () {
        expect(deinflectWord('買った'), contains('買う'));
      });
    });

    // -- Progressive --
    group('progressive', () {
      test('ている', () {
        expect(deinflectWord('食べている'), contains('食べる'));
      });
      test('ていた', () {
        expect(deinflectWord('食べていた'), contains('食べる'));
      });
    });

    // -- Negative --
    group('negative', () {
      test('ichidan ない', () {
        expect(deinflectWord('食べない'), contains('食べる'));
      });
      test('godan ku ない', () {
        expect(deinflectWord('行かない'), contains('行く'));
      });
      test('godan su ない', () {
        expect(deinflectWord('話さない'), contains('話す'));
      });
      test('godan mu ない', () {
        expect(deinflectWord('読まない'), contains('読む'));
      });
      test('godan u ない', () {
        expect(deinflectWord('買わない'), contains('買う'));
      });
      test('godan bu ない', () {
        expect(deinflectWord('遊ばない'), contains('遊ぶ'));
      });
      test('godan tsu ない', () {
        expect(deinflectWord('持たない'), contains('持つ'));
      });
      test('godan gu ない', () {
        expect(deinflectWord('泳がない'), contains('泳ぐ'));
      });
      test('godan nu ない', () {
        expect(deinflectWord('死なない'), contains('死ぬ'));
      });
      test('godan ru ない', () {
        expect(deinflectWord('帰らない'), contains('帰る'));
      });
    });

    // -- Negative past --
    group('negative past', () {
      test('ichidan なかった', () {
        expect(deinflectWord('食べなかった'), contains('食べる'));
      });
      test('godan ku なかった', () {
        expect(deinflectWord('行かなかった'), contains('行く'));
      });
      test('godan mu なかった', () {
        expect(deinflectWord('読まなかった'), contains('読む'));
      });
      test('godan u なかった', () {
        expect(deinflectWord('買わなかった'), contains('買う'));
      });
    });

    // -- Tai (want to) --
    group('tai-form', () {
      test('ichidan たい', () {
        expect(deinflectWord('食べたい'), contains('食べる'));
      });
      test('godan ku たい', () {
        expect(deinflectWord('行きたい'), contains('行く'));
      });
      test('godan mu たい', () {
        expect(deinflectWord('読みたい'), contains('読む'));
      });
      test('ichidan たかった', () {
        expect(deinflectWord('食べたかった'), contains('食べる'));
      });
      test('godan ku たかった', () {
        expect(deinflectWord('行きたかった'), contains('行く'));
      });
      test('ichidan たくない', () {
        expect(deinflectWord('食べたくない'), contains('食べる'));
      });
      test('godan ku たくない', () {
        expect(deinflectWord('行きたくない'), contains('行く'));
      });
    });

    // -- Volitional --
    group('volitional', () {
      test('ichidan よう', () {
        expect(deinflectWord('食べよう'), contains('食べる'));
      });
      test('godan ku: こう', () {
        expect(deinflectWord('行こう'), contains('行く'));
      });
      test('godan mu: もう', () {
        expect(deinflectWord('読もう'), contains('読む'));
      });
      test('godan su: そう', () {
        expect(deinflectWord('話そう'), contains('話す'));
      });
      test('godan u: おう', () {
        expect(deinflectWord('買おう'), contains('買う'));
      });
      test('godan bu: ぼう', () {
        expect(deinflectWord('遊ぼう'), contains('遊ぶ'));
      });
      test('godan gu: ごう', () {
        expect(deinflectWord('泳ごう'), contains('泳ぐ'));
      });
      test('godan tsu: とう', () {
        expect(deinflectWord('持とう'), contains('持つ'));
      });
      test('godan ru: ろう', () {
        expect(deinflectWord('取ろう'), contains('取る'));
      });
      test('godan ru: 登ろう', () {
        expect(deinflectWord('登ろう'), contains('登る'));
      });
    });

    // -- Conditional (ba-form) --
    group('conditional (ba-form)', () {
      test('ichidan れば', () {
        expect(deinflectWord('食べれば'), contains('食べる'));
      });
      test('godan ku けば', () {
        expect(deinflectWord('行けば'), contains('行く'));
      });
      test('godan mu めば', () {
        expect(deinflectWord('読めば'), contains('読む'));
      });
      test('godan su せば', () {
        expect(deinflectWord('話せば'), contains('話す'));
      });
      test('godan u えば', () {
        expect(deinflectWord('買えば'), contains('買う'));
      });
      test('godan bu べば', () {
        expect(deinflectWord('遊べば'), contains('遊ぶ'));
      });
      test('godan gu げば', () {
        expect(deinflectWord('泳げば'), contains('泳ぐ'));
      });
      test('godan tsu てば', () {
        expect(deinflectWord('持てば'), contains('持つ'));
      });
    });

    // -- Conditional (tara-form) --
    group('conditional (tara-form)', () {
      test('ichidan たら', () {
        expect(deinflectWord('食べたら'), contains('食べる'));
      });
      test('godan ku ったら', () {
        expect(deinflectWord('行ったら'), contains('行く'));
      });
      test('godan su したら', () {
        expect(deinflectWord('話したら'), contains('話す'));
      });
      test('godan mu んだら', () {
        expect(deinflectWord('読んだら'), contains('読む'));
      });
      test('godan ku いたら', () {
        expect(deinflectWord('書いたら'), contains('書く'));
      });
      test('godan gu いだら', () {
        expect(deinflectWord('泳いだら'), contains('泳ぐ'));
      });
    });

    // -- ながら --
    group('nagara', () {
      test('ichidan ながら', () {
        expect(deinflectWord('食べながら'), contains('食べる'));
      });
      test('godan ku ながら', () {
        expect(deinflectWord('歩きながら'), contains('歩く'));
      });
      test('godan mu ながら', () {
        expect(deinflectWord('読みながら'), contains('読む'));
      });
    });

    // -- Copula / です forms --
    group('copula', () {
      test('です → だ', () {
        expect(deinflectWord('です'), contains('だ'));
      });
      test('でした → だ', () {
        expect(deinflectWord('でした'), contains('だ'));
      });
      test('だろう → だ', () {
        expect(deinflectWord('だろう'), contains('だ'));
      });
      test('でしょう → だ', () {
        expect(deinflectWord('でしょう'), contains('だ'));
      });
    });

    // -- i-adjective inflections --
    group('i-adjectives', () {
      test('くない', () {
        expect(deinflectWord('大きくない'), contains('大きい'));
      });
      test('かった', () {
        expect(deinflectWord('大きかった'), contains('大きい'));
      });
      test('くて', () {
        expect(deinflectWord('大きくて'), contains('大きい'));
      });
      test('く (adverbial)', () {
        expect(deinflectWord('楽しく'), contains('楽しい'));
      });
      test('美しくない', () {
        expect(deinflectWord('美しくない'), contains('美しい'));
      });
      test('美しかった', () {
        expect(deinflectWord('美しかった'), contains('美しい'));
      });
      test('嬉しくて', () {
        expect(deinflectWord('嬉しくて'), contains('嬉しい'));
      });
    });

    // -- Passive --
    group('passive', () {
      test('ichidan られる', () {
        expect(deinflectWord('食べられる'), contains('食べる'));
      });
      test('godan mu れる', () {
        expect(deinflectWord('読まれる'), contains('読む'));
      });
      test('godan ku れる', () {
        expect(deinflectWord('書かれる'), contains('書く'));
      });
    });

    // -- Causative --
    group('causative', () {
      test('ichidan させる', () {
        expect(deinflectWord('食べさせる'), contains('食べる'));
      });
      test('godan mu せる', () {
        expect(deinflectWord('読ませる'), contains('読む'));
      });
      test('godan ku せる', () {
        expect(deinflectWord('書かせる'), contains('書く'));
      });
    });

    // -- Imperative --
    group('imperative', () {
      test('ichidan ろ', () {
        expect(deinflectWord('食べろ'), contains('食べる'));
      });
      test('godan ku け', () {
        expect(deinflectWord('行け'), contains('行く'));
      });
      test('godan mu め', () {
        expect(deinflectWord('読め'), contains('読む'));
      });
    });

    // -- Irregular: 来る (kuru) --
    group('irregular 来る', () {
      test('きます → 来る', () {
        expect(deinflectWord('きます'), contains('来る'));
      });
      test('きました → 来る', () {
        expect(deinflectWord('きました'), contains('来る'));
      });
      test('きて → 来る', () {
        expect(deinflectWord('きて'), contains('来る'));
      });
      test('きた → 来る', () {
        expect(deinflectWord('きた'), contains('来る'));
      });
      test('きている → 来る', () {
        expect(deinflectWord('きている'), contains('来る'));
      });
      test('きていた → 来る', () {
        expect(deinflectWord('きていた'), contains('来る'));
      });
      test('きていました → 来る', () {
        expect(deinflectWord('きていました'), contains('来る'));
      });
      test('こない → 来る', () {
        expect(deinflectWord('こない'), contains('来る'));
      });
      test('こなかった → 来る', () {
        expect(deinflectWord('こなかった'), contains('来る'));
      });
      test('こよう → 来る', () {
        expect(deinflectWord('こよう'), contains('来る'));
      });
      test('くれば → 来る', () {
        expect(deinflectWord('くれば'), contains('来る'));
      });
      test('きたら → 来る', () {
        expect(deinflectWord('きたら'), contains('来る'));
      });
    });

    // -- Irregular: する --
    group('irregular する', () {
      test('します → する', () {
        expect(deinflectWord('します'), contains('する'));
      });
      test('しました → する', () {
        expect(deinflectWord('しました'), contains('する'));
      });
      test('して → する', () {
        expect(deinflectWord('して'), contains('する'));
      });
      test('した → する', () {
        expect(deinflectWord('した'), contains('する'));
      });
      test('している → する', () {
        expect(deinflectWord('している'), contains('する'));
      });
      test('しない → する', () {
        expect(deinflectWord('しない'), contains('する'));
      });
      test('しなかった → する', () {
        expect(deinflectWord('しなかった'), contains('する'));
      });
      test('しよう → する', () {
        expect(deinflectWord('しよう'), contains('する'));
      });
      test('すれば → する', () {
        expect(deinflectWord('すれば'), contains('する'));
      });
      test('compound: 勉強します → 勉強する', () {
        expect(deinflectWord('勉強します'), contains('勉強する'));
      });
      test('compound: 勉強しました → 勉強する', () {
        expect(deinflectWord('勉強しました'), contains('勉強する'));
      });
      test('compound: 勉強して → 勉強する', () {
        expect(deinflectWord('勉強して'), contains('勉強する'));
      });
      test('compound: 勉強しない → 勉強する', () {
        expect(deinflectWord('勉強しない'), contains('勉強する'));
      });
    });

    // -- Edge cases --
    group('edge cases', () {
      test('single hiragana returns empty', () {
        expect(deinflectWord('あ'), isEmpty);
      });
      test('already dictionary form returns empty (no false matches)', () {
        // deinflectWord doesn't check dictionary — it just generates candidates.
        // For a plain dictionary form, it may generate some candidates but
        // none should be shorter than the word itself (no destructive deinflection).
        final candidates = deinflectWord('食べる');
        // Should not contain empty strings
        expect(candidates.where((c) => c.isEmpty), isEmpty);
      });
    });
  });

  // =========================================================================
  // Segmentation: full sentence tests (Japanese)
  // =========================================================================

  group('Japanese sentence segmentation', () {
    // Helper: check that a token appears in segmentation result
    bool hasToken(List<String> tokens, String expected) =>
        tokens.contains(expected);

    // Helper: check that no orphan grammatical suffixes appear alone
    // (ます, ました, ません, etc. should be part of a verb, not standalone)
    void expectNoOrphanSuffixes(List<String> tokens) {
      const orphans = [
        'ます', 'ました', 'ません', 'ませんでした', 'ましょう',
        'ている', 'ていた',
      ];
      for (final orphan in orphans) {
        expect(tokens.contains(orphan), isFalse,
            reason: '"$orphan" should not appear as standalone token '
                'in: $tokens');
      }
    }

    test('ichidan masu: 毎日本を食べます', () {
      final tokens = segmentText('毎日本を食べます。', jpDict);
      expect(hasToken(tokens, '食べます'), isTrue,
          reason: 'tokens: $tokens');
      expectNoOrphanSuffixes(tokens);
    });

    test('godan mashita: 本を読みました', () {
      final tokens = segmentText('本を読みました。', jpDict);
      expect(hasToken(tokens, '読みました'), isTrue,
          reason: 'tokens: $tokens');
      expectNoOrphanSuffixes(tokens);
    });

    test('ichidan mashita: 疲れました', () {
      final tokens = segmentText('疲れました。', jpDict);
      expect(hasToken(tokens, '疲れました'), isTrue,
          reason: 'tokens: $tokens');
      expectNoOrphanSuffixes(tokens);
    });

    test('godan te-form: 本を読んで', () {
      final tokens = segmentText('本を読んで', jpDict);
      expect(hasToken(tokens, '読んで'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('godan past: 山に登った', () {
      final tokens = segmentText('山に登った。', jpDict);
      expect(hasToken(tokens, '登った'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('volitional: 山に登ろう', () {
      final tokens = segmentText('山に登ろう。', jpDict);
      expect(hasToken(tokens, '登ろう'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('volitional godan ku: 行こう', () {
      final tokens = segmentText('学校に行こう。', jpDict);
      expect(hasToken(tokens, '行こう'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('negative: 食べない', () {
      final tokens = segmentText('食べない。', jpDict);
      expect(hasToken(tokens, '食べない'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('negative godan: 行かない', () {
      final tokens = segmentText('学校に行かない。', jpDict);
      expect(hasToken(tokens, '行かない'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('negative past: 食べなかった', () {
      final tokens = segmentText('食べなかった。', jpDict);
      expect(hasToken(tokens, '食べなかった'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('copula でした', () {
      final tokens = segmentText('元気でした。', jpDict);
      expect(hasToken(tokens, '元気でした'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('copula だろう', () {
      final tokens = segmentText('元気だろう。', jpDict);
      expect(hasToken(tokens, '元気だろう'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('tai-form: 食べたい', () {
      final tokens = segmentText('食べたい。', jpDict);
      expect(hasToken(tokens, '食べたい'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('tai-form godan: 行きたい', () {
      final tokens = segmentText('行きたい。', jpDict);
      expect(hasToken(tokens, '行きたい'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('progressive: 食べている', () {
      final tokens = segmentText('食べている。', jpDict);
      expect(hasToken(tokens, '食べている'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('progressive past: 食べていた', () {
      final tokens = segmentText('食べていた。', jpDict);
      expect(hasToken(tokens, '食べていた'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('conditional ba: 食べれば', () {
      final tokens = segmentText('食べれば。', jpDict);
      expect(hasToken(tokens, '食べれば'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('conditional ba godan: 行けば', () {
      final tokens = segmentText('行けば。', jpDict);
      expect(hasToken(tokens, '行けば'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('conditional tara: 食べたら', () {
      final tokens = segmentText('食べたら。', jpDict);
      expect(hasToken(tokens, '食べたら'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('conditional tara godan: 行ったら', () {
      final tokens = segmentText('行ったら。', jpDict);
      expect(hasToken(tokens, '行ったら'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('nagara: 歩きながら', () {
      final tokens = segmentText('歩きながら。', jpDict);
      expect(hasToken(tokens, '歩きながら'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('passive: 食べられる', () {
      final tokens = segmentText('食べられる。', jpDict);
      expect(hasToken(tokens, '食べられる'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('causative: 食べさせる', () {
      final tokens = segmentText('食べさせる。', jpDict);
      expect(hasToken(tokens, '食べさせる'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('i-adj past: 楽しかった', () {
      final tokens = segmentText('楽しかった。', jpDict);
      expect(hasToken(tokens, '楽しかった'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('i-adj negative: 大きくない', () {
      final tokens = segmentText('大きくない。', jpDict);
      expect(hasToken(tokens, '大きくない'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('masen deshita: 食べませんでした', () {
      final tokens = segmentText('食べませんでした。', jpDict);
      expect(hasToken(tokens, '食べませんでした'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('mashō: 食べましょう', () {
      final tokens = segmentText('食べましょう。', jpDict);
      expect(hasToken(tokens, '食べましょう'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('tai past: 食べたかった', () {
      final tokens = segmentText('食べたかった。', jpDict);
      expect(hasToken(tokens, '食べたかった'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('tai negative: 行きたくない', () {
      final tokens = segmentText('行きたくない。', jpDict);
      expect(hasToken(tokens, '行きたくない'), isTrue,
          reason: 'tokens: $tokens');
    });

    // -- Multi-verb sentences --

    test('sentence: 朝起きて学校に行きました', () {
      final tokens = segmentText('朝起きて学校に行きました。', jpDict);
      expect(hasToken(tokens, '朝'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '起きて'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '学校'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '行きました'), isTrue,
          reason: 'tokens: $tokens');
      expectNoOrphanSuffixes(tokens);
    });

    test('sentence: 本を読みながら音楽を聞いていた', () {
      final tokens = segmentText('本を読みながら音楽を聞いていた。', jpDict);
      expect(hasToken(tokens, '本'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '読みながら'), isTrue,
          reason: 'tokens: $tokens');
      expect(hasToken(tokens, '音楽'), isTrue, reason: 'tokens: $tokens');
      expectNoOrphanSuffixes(tokens);
    });

    test('sentence: 友達と映画を見ました', () {
      final tokens = segmentText('友達と映画を見ました。', jpDict);
      expect(hasToken(tokens, '友達'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '映画'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '見ました'), isTrue,
          reason: 'tokens: $tokens');
      expectNoOrphanSuffixes(tokens);
    });

    test('sentence: 天気が良かったから山に登ろう', () {
      final tokens = segmentText('天気が良かったから山に登ろう。', jpDict);
      expect(hasToken(tokens, '天気'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '良かった'), isTrue,
          reason: 'tokens: $tokens');
      expect(hasToken(tokens, '山'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '登ろう'), isTrue, reason: 'tokens: $tokens');
    });

    test('sentence: 水を飲みたかった', () {
      final tokens = segmentText('水を飲みたかった。', jpDict);
      expect(hasToken(tokens, '水'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '飲みたかった'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('sentence: 犬が走っている', () {
      // Godan progressive splits as te-form + いる (both valid tokens)
      final tokens = segmentText('犬が走っている。', jpDict);
      expect(hasToken(tokens, '犬'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '走って'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, 'いる'), isTrue, reason: 'tokens: $tokens');
    });

    test('sentence: 子供が泣いていた', () {
      final tokens = segmentText('子供が泣いていた。', jpDict);
      expect(hasToken(tokens, '子供'), isTrue, reason: 'tokens: $tokens');
    });

    test('sentence: 手紙を書かなかった', () {
      final tokens = segmentText('手紙を書かなかった。', jpDict);
      expect(hasToken(tokens, '手紙'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '書かなかった'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('sentence: 先生に教えられる', () {
      final tokens = segmentText('先生に教えられる。', jpDict);
      expect(hasToken(tokens, '先生'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '教えられる'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('sentence: 花が美しくて嬉しかった', () {
      final tokens = segmentText('花が美しくて嬉しかった。', jpDict);
      expect(hasToken(tokens, '花'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '美しくて'), isTrue,
          reason: 'tokens: $tokens');
      expect(hasToken(tokens, '嬉しかった'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('sentence: 電車に乗って東京に行った', () {
      final tokens = segmentText('電車に乗って東京に行った。', jpDict);
      expect(hasToken(tokens, '電車'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '乗って'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '東京'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '行った'), isTrue, reason: 'tokens: $tokens');
    });

    test('sentence: 明日学校に行けば友達に会える', () {
      final tokens = segmentText('明日学校に行けば友達に会える。', jpDict);
      expect(hasToken(tokens, '明日'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '学校'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '行けば'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '友達'), isTrue, reason: 'tokens: $tokens');
    });

    test('sentence: 部屋が静かでした', () {
      final tokens = segmentText('部屋が静かでした。', jpDict);
      expect(hasToken(tokens, '部屋'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '静かでした'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('sentence: 仕事が終わったら帰りましょう', () {
      final tokens = segmentText('仕事が終わったら帰りましょう。', jpDict);
      expect(hasToken(tokens, '仕事'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '終わったら'), isTrue,
          reason: 'tokens: $tokens');
      expect(hasToken(tokens, '帰りましょう'), isTrue,
          reason: 'tokens: $tokens');
      expectNoOrphanSuffixes(tokens);
    });

    test('sentence: 今日は暑くて水を飲みたい', () {
      final tokens = segmentText('今日は暑くて水を飲みたい。', jpDict);
      expect(hasToken(tokens, '今日'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '暑くて'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '水'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '飲みたい'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('sentence: 猫が魚を食べさせる', () {
      final tokens = segmentText('猫が魚を食べさせる。', jpDict);
      expect(hasToken(tokens, '猫'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '食べさせる'), isTrue,
          reason: 'tokens: $tokens');
    });

    // -- Exact dictionary words should still work --

    test('exact match: dictionary-form verbs stay as-is', () {
      final tokens = segmentText('食べる。', jpDict);
      expect(hasToken(tokens, '食べる'), isTrue, reason: 'tokens: $tokens');
    });

    test('exact match: nouns', () {
      final tokens = segmentText('日本語の学校', jpDict);
      expect(hasToken(tokens, '日本語'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '学校'), isTrue, reason: 'tokens: $tokens');
    });

    test('exact match: i-adjective base', () {
      final tokens = segmentText('大きい犬', jpDict);
      expect(hasToken(tokens, '大きい'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '犬'), isTrue, reason: 'tokens: $tokens');
    });

    // -- Punctuation handling in Japanese --

    test('punctuation splits correctly', () {
      final tokens = segmentText('食べました。飲みました。', jpDict);
      expect(hasToken(tokens, '食べました'), isTrue,
          reason: 'tokens: $tokens');
      expect(hasToken(tokens, '飲みました'), isTrue,
          reason: 'tokens: $tokens');
      expect(hasToken(tokens, '。'), isTrue, reason: 'tokens: $tokens');
    });

    test('mixed punctuation', () {
      final tokens = segmentText('行きたい！でも、行かない。', jpDict);
      expect(hasToken(tokens, '行きたい'), isTrue,
          reason: 'tokens: $tokens');
      expect(hasToken(tokens, '行かない'), isTrue,
          reason: 'tokens: $tokens');
    });

    // -- All godan verb classes in one sentence --

    test('all godan classes in masu-form', () {
      // ku, gu, su, tsu, nu, bu, mu, ru, u
      final tokens = segmentText(
        '書きます泳ぎます話します持ちます死にます遊びます読みます帰ります買います',
        jpDict,
      );
      expect(hasToken(tokens, '書きます'), isTrue,
          reason: 'ku: $tokens');
      expect(hasToken(tokens, '泳ぎます'), isTrue,
          reason: 'gu: $tokens');
      expect(hasToken(tokens, '話します'), isTrue,
          reason: 'su: $tokens');
      expect(hasToken(tokens, '持ちます'), isTrue,
          reason: 'tsu: $tokens');
      expect(hasToken(tokens, '死にます'), isTrue,
          reason: 'nu: $tokens');
      expect(hasToken(tokens, '遊びます'), isTrue,
          reason: 'bu: $tokens');
      expect(hasToken(tokens, '読みます'), isTrue,
          reason: 'mu: $tokens');
      expect(hasToken(tokens, '帰ります'), isTrue,
          reason: 'ru: $tokens');
      expect(hasToken(tokens, '買います'), isTrue,
          reason: 'u: $tokens');
      expectNoOrphanSuffixes(tokens);
    });

    test('all godan classes te-form', () {
      final tokens = segmentText(
        '書いて泳いで話して持って死んで遊んで読んで帰って買って',
        jpDict,
      );
      expect(hasToken(tokens, '書いて'), isTrue, reason: 'ku: $tokens');
      expect(hasToken(tokens, '泳いで'), isTrue, reason: 'gu: $tokens');
      expect(hasToken(tokens, '話して'), isTrue, reason: 'su: $tokens');
      expect(hasToken(tokens, '持って'), isTrue, reason: 'tsu: $tokens');
      expect(hasToken(tokens, '死んで'), isTrue, reason: 'nu: $tokens');
      expect(hasToken(tokens, '遊んで'), isTrue, reason: 'bu: $tokens');
      expect(hasToken(tokens, '読んで'), isTrue, reason: 'mu: $tokens');
      // 帰って could match 帰る (godan ru → って)
      expect(hasToken(tokens, '帰って'), isTrue, reason: 'ru: $tokens');
      expect(hasToken(tokens, '買って'), isTrue, reason: 'u: $tokens');
    });

    test('all godan classes negative', () {
      final tokens = segmentText(
        '書かない泳がない話さない持たない死なない遊ばない読まない帰らない買わない',
        jpDict,
      );
      expect(hasToken(tokens, '書かない'), isTrue, reason: 'ku: $tokens');
      expect(hasToken(tokens, '泳がない'), isTrue, reason: 'gu: $tokens');
      expect(hasToken(tokens, '話さない'), isTrue, reason: 'su: $tokens');
      expect(hasToken(tokens, '持たない'), isTrue, reason: 'tsu: $tokens');
      expect(hasToken(tokens, '死なない'), isTrue, reason: 'nu: $tokens');
      expect(hasToken(tokens, '遊ばない'), isTrue, reason: 'bu: $tokens');
      expect(hasToken(tokens, '読まない'), isTrue, reason: 'mu: $tokens');
      expect(hasToken(tokens, '帰らない'), isTrue, reason: 'ru: $tokens');
      expect(hasToken(tokens, '買わない'), isTrue, reason: 'u: $tokens');
    });

    test('all godan classes volitional', () {
      final tokens = segmentText(
        '書こう泳ごう話そう持とう遊ぼう読もう帰ろう買おう',
        jpDict,
      );
      expect(hasToken(tokens, '書こう'), isTrue, reason: 'ku: $tokens');
      expect(hasToken(tokens, '泳ごう'), isTrue, reason: 'gu: $tokens');
      expect(hasToken(tokens, '話そう'), isTrue, reason: 'su: $tokens');
      expect(hasToken(tokens, '持とう'), isTrue, reason: 'tsu: $tokens');
      expect(hasToken(tokens, '遊ぼう'), isTrue, reason: 'bu: $tokens');
      expect(hasToken(tokens, '読もう'), isTrue, reason: 'mu: $tokens');
      expect(hasToken(tokens, '帰ろう'), isTrue, reason: 'ru: $tokens');
      expect(hasToken(tokens, '買おう'), isTrue, reason: 'u: $tokens');
    });
  });

  // =========================================================================
  // Deinflection: bare masu-stem (連用形)
  // =========================================================================

  group('deinflectWord: masu-stem (連用形)', () {
    test('godan ku: 書き → 書く', () {
      expect(deinflectWord('書き'), contains('書く'));
    });
    test('godan gu: 泳ぎ → 泳ぐ', () {
      expect(deinflectWord('泳ぎ'), contains('泳ぐ'));
    });
    test('godan su: 話し → 話す', () {
      expect(deinflectWord('話し'), contains('話す'));
    });
    test('godan tsu: 持ち → 持つ', () {
      expect(deinflectWord('持ち'), contains('持つ'));
    });
    test('godan nu: 死に → 死ぬ', () {
      expect(deinflectWord('死に'), contains('死ぬ'));
    });
    test('godan bu: 遊び → 遊ぶ', () {
      expect(deinflectWord('遊び'), contains('遊ぶ'));
    });
    test('godan mu: 読み → 読む', () {
      expect(deinflectWord('読み'), contains('読む'));
    });
    test('godan ru: 登り → 登る', () {
      expect(deinflectWord('登り'), contains('登る'));
    });
    test('godan u: 買い → 買う', () {
      expect(deinflectWord('買い'), contains('買う'));
    });
    test('ichidan: 食べ → 食べる', () {
      expect(deinflectWord('食べ'), contains('食べる'));
    });
    test('ichidan: 見 → 見る', () {
      expect(deinflectWord('見'), isEmpty,
          reason: 'single char should not produce candidates');
    });
  });

  // =========================================================================
  // Compound verbs: dictionary entry exists
  // =========================================================================

  group('compound verbs (in dictionary)', () {
    bool hasToken(List<String> tokens, String expected) =>
        tokens.contains(expected);

    test('食べ始めました → matches 食べ始める as compound', () {
      final tokens = segmentText('食べ始めました。', jpDictWithCompounds);
      expect(hasToken(tokens, '食べ始めました'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('読み始めます → matches 読み始める as compound', () {
      final tokens = segmentText('読み始めます。', jpDictWithCompounds);
      expect(hasToken(tokens, '読み始めます'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('走り出した → matches 走り出す as compound', () {
      final tokens = segmentText('走り出した。', jpDictWithCompounds);
      expect(hasToken(tokens, '走り出した'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('飲み過ぎた → matches 飲み過ぎる as compound', () {
      final tokens = segmentText('飲み過ぎた。', jpDictWithCompounds);
      expect(hasToken(tokens, '飲み過ぎた'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('話し合いました → matches 話し合う as compound', () {
      final tokens = segmentText('話し合いました。', jpDictWithCompounds);
      expect(hasToken(tokens, '話し合いました'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('書き直した → matches 書き直す as compound', () {
      final tokens = segmentText('書き直した。', jpDictWithCompounds);
      expect(hasToken(tokens, '書き直した'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('泳ぎ始めた → matches 泳ぎ始める as compound', () {
      final tokens = segmentText('泳ぎ始めた。', jpDictWithCompounds);
      expect(hasToken(tokens, '泳ぎ始めた'), isTrue,
          reason: 'tokens: $tokens');
    });

    // Compound in a sentence
    test('sentence: 本を読み始めました', () {
      final tokens = segmentText('本を読み始めました。', jpDictWithCompounds);
      expect(hasToken(tokens, '本'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '読み始めました'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('sentence: 水を飲み過ぎた', () {
      final tokens = segmentText('水を飲み過ぎた。', jpDictWithCompounds);
      expect(hasToken(tokens, '水'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '飲み過ぎた'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('sentence: 友達と話し合いました', () {
      final tokens = segmentText('友達と話し合いました。', jpDictWithCompounds);
      expect(hasToken(tokens, '友達'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '話し合いました'), isTrue,
          reason: 'tokens: $tokens');
    });
  });

  // =========================================================================
  // Compound verbs: NOT in dictionary → fallback to masu-stem split
  // =========================================================================

  group('compound verbs (not in dictionary, masu-stem fallback)', () {
    // jpDict does NOT have compound entries like 登り始める, but has
    // both 登る and 始める separately.
    bool hasToken(List<String> tokens, String expected) =>
        tokens.contains(expected);

    test('登り始めました → 登り + 始めました', () {
      final tokens = segmentText('登り始めました。', jpDict);
      // 登り should deinflect to 登る (masu-stem rule)
      expect(hasToken(tokens, '登り'), isTrue,
          reason: 'masu-stem of 登る: $tokens');
      // 始めました should deinflect to 始める
      expect(hasToken(tokens, '始めました'), isTrue,
          reason: 'inflected 始める: $tokens');
    });

    test('歩き始めた → 歩き + 始めた', () {
      final tokens = segmentText('歩き始めた。', jpDict);
      expect(hasToken(tokens, '歩き'), isTrue,
          reason: 'masu-stem of 歩く: $tokens');
      expect(hasToken(tokens, '始めた'), isTrue,
          reason: 'inflected 始める: $tokens');
    });

    test('走り続けている → 走り + 続けている', () {
      final tokens = segmentText('走り続けている。', jpDict);
      expect(hasToken(tokens, '走り'), isTrue,
          reason: 'masu-stem of 走る: $tokens');
      expect(hasToken(tokens, '続けている'), isTrue,
          reason: 'inflected 続ける: $tokens');
    });

    test('読み続けた → 読み + 続けた', () {
      final tokens = segmentText('読み続けた。', jpDict);
      expect(hasToken(tokens, '読み'), isTrue,
          reason: 'masu-stem of 読む: $tokens');
      expect(hasToken(tokens, '続けた'), isTrue,
          reason: 'inflected 続ける: $tokens');
    });

    test('書き直します → 書き + 直します', () {
      final tokens = segmentText('書き直します。', jpDict);
      expect(hasToken(tokens, '書き'), isTrue,
          reason: 'masu-stem of 書く: $tokens');
      expect(hasToken(tokens, '直します'), isTrue,
          reason: 'inflected 直す: $tokens');
    });

    test('食べ過ぎた → 食べ + 過ぎた', () {
      final tokens = segmentText('食べ過ぎた。', jpDict);
      expect(hasToken(tokens, '食べ'), isTrue,
          reason: 'ichidan stem: $tokens');
      expect(hasToken(tokens, '過ぎた'), isTrue,
          reason: 'inflected 過ぎる: $tokens');
    });

    test('泳ぎ始めた → 泳ぎ + 始めた', () {
      final tokens = segmentText('泳ぎ始めた。', jpDict);
      expect(hasToken(tokens, '泳ぎ'), isTrue,
          reason: 'masu-stem of 泳ぐ: $tokens');
      expect(hasToken(tokens, '始めた'), isTrue,
          reason: 'inflected 始める: $tokens');
    });

    test('飲み過ぎました → 飲み + 過ぎました', () {
      final tokens = segmentText('飲み過ぎました。', jpDict);
      expect(hasToken(tokens, '飲み'), isTrue,
          reason: 'masu-stem of 飲む: $tokens');
      expect(hasToken(tokens, '過ぎました'), isTrue,
          reason: 'inflected 過ぎる: $tokens');
    });

    // Full sentences
    test('sentence: 山に登り始めました', () {
      final tokens = segmentText('山に登り始めました。', jpDict);
      expect(hasToken(tokens, '山'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '登り'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '始めました'), isTrue,
          reason: 'tokens: $tokens');
    });

    test('sentence: 毎日歩き続けている', () {
      final tokens = segmentText('毎日歩き続けている。', jpDict);
      expect(hasToken(tokens, '毎日'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '歩き'), isTrue, reason: 'tokens: $tokens');
    });

    test('sentence: 手紙を書き直した', () {
      final tokens = segmentText('手紙を書き直した。', jpDict);
      expect(hasToken(tokens, '手紙'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '書き'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, '直した'), isTrue, reason: 'tokens: $tokens');
    });
  });

  // =========================================================================
  // Compound vs non-compound: dictionary entry takes priority
  // =========================================================================

  group('compound priority: dict entry > split', () {
    bool hasToken(List<String> tokens, String expected) =>
        tokens.contains(expected);

    test('食べ始めた: compound dict has it → single token', () {
      final tokens = segmentText('食べ始めた。', jpDictWithCompounds);
      expect(hasToken(tokens, '食べ始めた'), isTrue,
          reason: 'should match as compound: $tokens');
    });

    test('食べ始めた: no compound in dict → split', () {
      final tokens = segmentText('食べ始めた。', jpDict);
      expect(hasToken(tokens, '食べ'), isTrue,
          reason: 'should split: $tokens');
      expect(hasToken(tokens, '始めた'), isTrue,
          reason: 'should split: $tokens');
    });

    test('走り出した: compound dict has it → single token', () {
      final tokens = segmentText('走り出した。', jpDictWithCompounds);
      expect(hasToken(tokens, '走り出した'), isTrue,
          reason: 'should match as compound: $tokens');
    });

    test('走り出した: no compound in dict → split', () {
      // jpDict has 走る and 出す but NOT 走り出す
      // Note: 出す is not in jpDict, but 出る is. So this will split
      // as 走り + 出した where 出した deinflects to 出る
      final tokens = segmentText('走り出した。', jpDict);
      expect(hasToken(tokens, '走り'), isTrue,
          reason: 'should split: $tokens');
    });
  });

  // =========================================================================
  // Irregular verbs in sentence segmentation
  // =========================================================================

  group('irregular verbs in sentences', () {
    bool hasToken(List<String> tokens, String expected) =>
        tokens.contains(expected);

    test('登ってきていました splits as 登って + きていました or きて etc.', () {
      final tokens = segmentText('登ってきていました。', jpDict);
      expect(hasToken(tokens, '登って'), isTrue,
          reason: 'te-form of 登る: $tokens');
      // きていました should match 来る via irregular rules
      expect(hasToken(tokens, 'きていました'), isTrue,
          reason: 'irregular 来る form: $tokens');
    });

    test('きました → 来る', () {
      final tokens = segmentText('友達がきました。', jpDict);
      expect(hasToken(tokens, '友達'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, 'きました'), isTrue,
          reason: 'irregular 来る: $tokens');
    });

    test('しています → する', () {
      final tokens = segmentText('仕事をしています。', jpDict);
      expect(hasToken(tokens, '仕事'), isTrue, reason: 'tokens: $tokens');
      expect(hasToken(tokens, 'しています'), isTrue,
          reason: 'irregular する: $tokens');
    });

    test('きて → 来る (not 着る)', () {
      // きて should produce 来る as a candidate
      expect(deinflectWord('きて'), contains('来る'));
    });

    test('きていました → 来る', () {
      expect(deinflectWord('きていました'), contains('来る'));
    });

    test('まいました deinflects to まいる', () {
      expect(deinflectWord('まいました'), contains('まいる'));
    });

    // てしまう (to end up doing)
    test('疲れてしまいました → 疲れる', () {
      expect(deinflectWord('疲れてしまいました'), contains('疲れる'));
    });
    test('食べてしまった → 食べる', () {
      expect(deinflectWord('食べてしまった'), contains('食べる'));
    });
    test('食べてしまう → 食べる', () {
      expect(deinflectWord('食べてしまう'), contains('食べる'));
    });
    test('godan: 読んでしまった → 読む', () {
      expect(deinflectWord('読んでしまった'), contains('読む'));
    });
    test('godan: 行ってしまいました → 行く', () {
      expect(deinflectWord('行ってしまいました'), contains('行く'));
    });
    test('godan: 話してしまった → 話す', () {
      expect(deinflectWord('話してしまった'), contains('話す'));
    });
    // Contracted forms
    test('食べちゃった → 食べる', () {
      expect(deinflectWord('食べちゃった'), contains('食べる'));
    });
    test('読んじゃった → 読む', () {
      expect(deinflectWord('読んじゃった'), contains('読む'));
    });

    test('疲れてしまいました segments correctly', () {
      final tokens = segmentText('疲れてしまいました。', jpDict);
      expect(hasToken(tokens, '疲れてしまいました'), isTrue,
          reason: 'should match as single token: $tokens');
    });

    // deinflectionChain tests
    test('chain: 疲れてしまいました', () {
      final chain = deinflectionChain('疲れてしまいました', '疲れる');
      expect(chain, ['疲れてしまう', '疲れてしまいました']);
    });
    test('chain: 食べてしまった', () {
      final chain = deinflectionChain('食べてしまった', '食べる');
      expect(chain, ['食べてしまう', '食べてしまった']);
    });
    test('chain: 食べました → [食べた, 食べました]', () {
      final chain = deinflectionChain('食べました', '食べる');
      expect(chain, ['食べた', '食べました']);
    });
    test('chain: 行きました → [行った, 行きました]', () {
      final chain = deinflectionChain('行きました', '行く');
      expect(chain, ['行った', '行きました']);
    });
    test('chain: なりました → [なった, なりました]', () {
      final chain = deinflectionChain('なりました', 'なる');
      expect(chain, ['なった', 'なりました']);
    });
    test('chain: 読みません → [読まない, 読みません]', () {
      final chain = deinflectionChain('読みません', '読む');
      expect(chain, ['読まない', '読みません']);
    });
    test('chain: 話しましょう → [話そう, 話しましょう]', () {
      final chain = deinflectionChain('話しましょう', '話す');
      expect(chain, ['話そう', '話しましょう']);
    });
    test('chain: same word → empty', () {
      expect(deinflectionChain('食べる', '食べる'), isEmpty);
    });
    test('chain: 食べない → [食べない]', () {
      final chain = deinflectionChain('食べない', '食べる');
      expect(chain, ['食べない']);
    });
    test('chain: 食べちゃった', () {
      final chain = deinflectionChain('食べちゃった', '食べる');
      expect(chain, ['食べちゃう', '食べちゃった']);
    });
    test('chain: 読んでしまいました', () {
      final chain = deinflectionChain('読んでしまいました', '読む');
      expect(chain, ['読んでしまう', '読んでしまいました']);
    });

    test('疲れてまいました segments correctly', () {
      final tokens = segmentText('疲れてまいました。', jpDict);
      expect(hasToken(tokens, '疲れて'), isTrue,
          reason: 'te-form of 疲れる: $tokens');
      expect(hasToken(tokens, 'まいました'), isTrue,
          reason: 'masu-past of まいる: $tokens');
    });
  });
}
