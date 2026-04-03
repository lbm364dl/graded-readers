import '../models.dart' show Language;
import 'dictionary_service.dart';

bool _isChinese(int code) =>
    (code >= 0x4E00 && code <= 0x9FFF) ||
    (code >= 0x3400 && code <= 0x4DBF) ||
    (code >= 0xF900 && code <= 0xFAFF);

bool _isHiragana(int code) => code >= 0x3040 && code <= 0x309F;
bool _isKatakana(int code) => code >= 0x30A0 && code <= 0x30FF;

bool _isCJK(int code) =>
    _isChinese(code) || _isHiragana(code) || _isKatakana(code);

/// Segments CJK text into words using max forward matching
/// with Japanese deinflection support.
List<String> segmentText(String text, DictionaryService dict) {
  if (!dict.isReady || text.isEmpty) return [text];
  return segmentTextRaw(
    text: text,
    wordSet: dict.wordSet,
    maxWordLength: dict.maxWordLength,
    language: dict.activeLanguage,
  );
}

/// Isolate-friendly segmentation that takes plain data.
List<String> segmentTextRaw({
  required String text,
  required Set<String> wordSet,
  required int maxWordLength,
  required Language language,
}) {
  if (text.isEmpty) return [text];

  bool hasWord(String w) => wordSet.contains(w);

  final result = <String>[];
  int i = 0;

  while (i < text.length) {
    final code = text.codeUnitAt(i);

    if (!_isCJK(code)) {
      int j = i + 1;
      while (j < text.length && !_isCJK(text.codeUnitAt(j))) {
        j++;
      }
      result.add(text.substring(i, j));
      i = j;
      continue;
    }

    // For Japanese: try deinflection first (prefers longer inflected spans
    // over shorter exact matches, e.g. 疲れました as one token not 疲れ+ました)
    if (language == Language.japanese) {
      final deinflected = _tryDeinflectRaw(text, i, hasWord, maxWordLength);
      if (deinflected != null) {
        result.add(text.substring(i, i + deinflected.consumedLength));
        i += deinflected.consumedLength;
        continue;
      }
    }

    // Max forward matching (exact dictionary match)
    final maxLen = maxWordLength.clamp(1, text.length - i);
    bool found = false;

    for (int len = maxLen; len > 1; len--) {
      final candidate = text.substring(i, i + len);
      if (hasWord(candidate)) {
        result.add(candidate);
        i += len;
        found = true;
        break;
      }
    }

    if (found) continue;

    // Single character fallback
    result.add(text.substring(i, i + 1));
    i++;
  }

  return result;
}

// ---------------------------------------------------------------------------
// Japanese deinflection
// ---------------------------------------------------------------------------

class _DeinflectResult {
  final String dictionaryForm;
  final int consumedLength;
  _DeinflectResult(this.dictionaryForm, this.consumedLength);
}

_DeinflectResult? _tryDeinflect(
    String text, int start, DictionaryService dict) {
  return _tryDeinflectRaw(text, start, dict.hasWord, dict.maxWordLength);
}

_DeinflectResult? _tryDeinflectRaw(
    String text, int start, bool Function(String) hasWord, int maxWordLength) {
  final remaining = text.length - start;
  if (remaining < 2) return null;
  final maxTry = remaining.clamp(2, 12);

  for (int len = maxTry; len >= 2; len--) {
    final end = start + len;
    if (end > text.length) continue;
    final span = text.substring(start, end);
    if (span.codeUnits.any((c) => !_isCJK(c))) continue;

    // Exact match at this length takes priority
    if (hasWord(span)) {
      return _DeinflectResult(span, len);
    }

    // Skip deinflection for short all-kana spans (likely particles/suffixes)
    final hasKanji = span.codeUnits.any((c) =>
        (c >= 0x4E00 && c <= 0x9FFF) ||
        (c >= 0x3400 && c <= 0x4DBF) ||
        (c >= 0xF900 && c <= 0xFAFF));
    if (!hasKanji && len <= 2) continue;

    final candidates = deinflectWord(span);
    for (final dictForm in candidates) {
      if (dictForm.isNotEmpty && hasWord(dictForm)) {
        return _DeinflectResult(dictForm, len);
      }
    }
  }
  return null;
}

/// Generate possible dictionary forms for an inflected Japanese word.
List<String> deinflectWord(String word) {
  final results = <String>[];

  // --- Irregular verbs: 来る (kuru) and する ---
  const kuruForms = <String, String>{
    'きます': '来る', 'きました': '来る', 'きません': '来る',
    'きませんでした': '来る', 'きましょう': '来る',
    'きて': '来る', 'きた': '来る', 'きている': '来る', 'きていた': '来る',
    'きています': '来る', 'きていました': '来る',
    'こない': '来る', 'こなかった': '来る',
    'くれば': '来る', 'きたら': '来る',
    'こよう': '来る', 'こられる': '来る', 'こさせる': '来る',
    'こい': '来る',
  };
  const suruForms = <String, String>{
    'します': 'する', 'しました': 'する', 'しません': 'する',
    'しませんでした': 'する', 'しましょう': 'する',
    'して': 'する', 'した': 'する', 'している': 'する', 'していた': 'する',
    'しています': 'する', 'していました': 'する',
    'しない': 'する', 'しなかった': 'する',
    'すれば': 'する', 'したら': 'する',
    'しよう': 'する', 'させる': 'する', 'される': 'する',
    'しろ': 'する', 'せよ': 'する',
  };
  // Check exact irregular matches
  if (kuruForms.containsKey(word)) results.add(kuruForms[word]!);
  if (suruForms.containsKey(word)) results.add(suruForms[word]!);
  // Check compound する verbs (e.g. 勉強します → 勉強する)
  for (final suffix in suruForms.keys) {
    if (word.endsWith(suffix) && word.length > suffix.length) {
      results.add('${word.substring(0, word.length - suffix.length)}する');
    }
  }

  // --- Godan masu-stem mapping ---
  const masuToDict = {
    'き': 'く', 'ぎ': 'ぐ', 'し': 'す', 'ち': 'つ', 'に': 'ぬ',
    'び': 'ぶ', 'み': 'む', 'り': 'る', 'い': 'う',
  };
  const negToDict = {
    'か': 'く', 'が': 'ぐ', 'さ': 'す', 'た': 'つ', 'な': 'ぬ',
    'ば': 'ぶ', 'ま': 'む', 'ら': 'る', 'わ': 'う',
  };

  // Helper: given a masu-stem, add both ichidan and godan dictionary forms
  void addFromMasuStem(String stem) {
    results.add('${stem}る'); // ichidan
    if (stem.isNotEmpty) {
      final last = stem[stem.length - 1];
      final dictEnd = masuToDict[last];
      if (dictEnd != null) {
        results.add('${stem.substring(0, stem.length - 1)}$dictEnd');
      }
    }
  }

  // Helper: given a negative-stem, add both ichidan and godan dictionary forms
  void addFromNegStem(String stem) {
    results.add('${stem}る'); // ichidan
    if (stem.isNotEmpty) {
      final last = stem[stem.length - 1];
      final dictEnd = negToDict[last];
      if (dictEnd != null) {
        results.add('${stem.substring(0, stem.length - 1)}$dictEnd');
      }
    }
  }

  // --- Verb masu-form ---
  for (final suffix in ['ませんでした', 'ましょう', 'ました', 'ません', 'ます']) {
    if (word.endsWith(suffix) && word.length > suffix.length) {
      addFromMasuStem(word.substring(0, word.length - suffix.length));
    }
  }

  // --- Te-form / past: ichidan ---
  for (final suffix in ['ている', 'ていた', 'て', 'た']) {
    if (word.endsWith(suffix) && word.length > suffix.length) {
      results.add('${word.substring(0, word.length - suffix.length)}る');
    }
  }

  // --- Te-form / past: godan ---
  const teRules = <String, List<String>>{
    'って': ['く', 'つ', 'う', 'る'],
    'った': ['く', 'つ', 'う', 'る'],
    'いて': ['く'],
    'いた': ['く'],
    'いで': ['ぐ'],
    'いだ': ['ぐ'],
    'んで': ['む', 'ぶ', 'ぬ'],
    'んだ': ['む', 'ぶ', 'ぬ'],
    'して': ['す'],
    'した': ['す'],
  };
  for (final entry in teRules.entries) {
    if (word.endsWith(entry.key) && word.length > entry.key.length) {
      final stem = word.substring(0, word.length - entry.key.length);
      for (final end in entry.value) {
        results.add('$stem$end');
      }
    }
  }

  // --- Negative ---
  if (word.endsWith('なかった') && word.length > 4) {
    addFromNegStem(word.substring(0, word.length - 4));
  }
  if (word.endsWith('ない') && word.length > 2) {
    addFromNegStem(word.substring(0, word.length - 2));
  }

  // --- Tai-form ---
  for (final suffix in ['たかった', 'たくない', 'たい']) {
    if (word.endsWith(suffix) && word.length > suffix.length) {
      addFromMasuStem(word.substring(0, word.length - suffix.length));
    }
  }

  // --- Volitional ---
  // Ichidan: stem + よう (食べよう → 食べる)
  if (word.endsWith('よう') && word.length > 2) {
    results.add('${word.substring(0, word.length - 2)}る');
  }
  // Godan: stem + おう (行こう → 行く, 読もう → 読む, etc.)
  const volToDict = {
    'こ': 'く', 'ご': 'ぐ', 'そ': 'す', 'と': 'つ', 'の': 'ぬ',
    'ぼ': 'ぶ', 'も': 'む', 'ろ': 'る', 'お': 'う',
  };
  if (word.endsWith('う') && word.length > 1) {
    // Check the char before う
    final beforeU = word[word.length - 2];
    final dictEnd = volToDict[beforeU];
    if (dictEnd != null) {
      results.add('${word.substring(0, word.length - 2)}$dictEnd');
    }
  }

  // --- Conditional (ば-form) ---
  // Ichidan: 食べれば → 食べる
  if (word.endsWith('れば') && word.length > 2) {
    results.add('${word.substring(0, word.length - 2)}る');
  }
  // Godan: 行けば → 行く (e-dan + ば)
  const ebaToDict = {
    'け': 'く', 'げ': 'ぐ', 'せ': 'す', 'て': 'つ', 'ね': 'ぬ',
    'べ': 'ぶ', 'め': 'む', 'れ': 'る', 'え': 'う',
  };
  if (word.endsWith('ば') && word.length > 2) {
    final beforeBa = word[word.length - 2];
    final dictEnd = ebaToDict[beforeBa];
    if (dictEnd != null) {
      results.add('${word.substring(0, word.length - 2)}$dictEnd');
    }
  }

  // --- Conditional (たら-form) ---
  // Ichidan: 食べたら → 食べる
  if (word.endsWith('たら') && word.length > 2) {
    results.add('${word.substring(0, word.length - 2)}る');
  }
  // Godan: same stems as past tense + ら
  const taraRules = <String, List<String>>{
    'ったら': ['く', 'つ', 'う', 'る'],
    'いたら': ['く'],
    'いだら': ['ぐ'],
    'んだら': ['む', 'ぶ', 'ぬ'],
    'したら': ['す'],
  };
  for (final entry in taraRules.entries) {
    if (word.endsWith(entry.key) && word.length > entry.key.length) {
      final stem = word.substring(0, word.length - entry.key.length);
      for (final end in entry.value) {
        results.add('$stem$end');
      }
    }
  }

  // --- ながら (while doing) ---
  if (word.endsWith('ながら') && word.length > 3) {
    addFromMasuStem(word.substring(0, word.length - 3));
  }

  // --- てしまう (to end up doing / completely do) ---
  // Ichidan te-form + しまう conjugations
  // e.g. 疲れてしまいました → 疲れる, 食べてしまった → 食べる
  const shimaiForms = [
    'てしまいました', 'てしまいます', 'てしまった', 'てしまって',
    'てしまう', 'てしまえば',
    // Contracted forms: ちゃう/じゃう
    'ちゃいました', 'ちゃいます', 'ちゃった', 'ちゃって', 'ちゃう',
    'じゃいました', 'じゃいます', 'じゃった', 'じゃって', 'じゃう',
  ];
  for (final suffix in shimaiForms) {
    if (word.endsWith(suffix) && word.length > suffix.length) {
      final before = word.substring(0, word.length - suffix.length);
      // Ichidan: stem + る (食べてしまう → 食べ → 食べる)
      results.add('${before}る');
    }
  }
  // Godan te-form + しまう: って/んで/いて/して + しまう variants
  const godanShimaiSuffixes = [
    'しまいました', 'しまいます', 'しまった', 'しまって', 'しまう', 'しまえば',
    // Contracted
    'ちゃいました', 'ちゃいます', 'ちゃった', 'ちゃって', 'ちゃう',
  ];
  const godanTeToDict = <String, List<String>>{
    'って': ['く', 'つ', 'う', 'る'],
    'んで': ['む', 'ぶ', 'ぬ'],
    'いて': ['く'],
    'いで': ['ぐ'],
    'して': ['す'],
  };
  for (final shimaiSuffix in godanShimaiSuffixes) {
    for (final te in godanTeToDict.entries) {
      final full = '${te.key}$shimaiSuffix';
      if (word.endsWith(full) && word.length > full.length) {
        final stem = word.substring(0, word.length - full.length);
        for (final end in te.value) {
          results.add('$stem$end');
        }
      }
    }
  }
  // Godan contracted じゃう: んでしまう → んじゃう
  const jaForms = [
    'じゃいました', 'じゃいます', 'じゃった', 'じゃって', 'じゃう',
  ];
  for (final jaSuffix in jaForms) {
    final full = 'ん$jaSuffix';
    if (word.endsWith(full) && word.length > full.length) {
      final stem = word.substring(0, word.length - full.length);
      for (final end in ['む', 'ぶ', 'ぬ']) {
        results.add('$stem$end');
      }
    }
  }

  // --- Fixed grammar patterns ---
  // かもしれません → かもしれない, etc.
  const grammarForms = <String, String>{
    'かもしれません': 'かもしれない',
    'かもしれませんでした': 'かもしれない',
  };
  if (grammarForms.containsKey(word)) results.add(grammarForms[word]!);
  for (final entry in grammarForms.entries) {
    if (word.endsWith(entry.key) && word.length > entry.key.length) {
      results.add('${word.substring(0, word.length - entry.key.length)}${entry.value}');
    }
  }

  // --- Copula / da-forms ---
  // でした → だ, だろう → だ, です → だ, でしょう → だ
  for (final suffix in ['でした', 'だろう', 'でしょう', 'です']) {
    if (word.endsWith(suffix)) {
      final stem = word.substring(0, word.length - suffix.length);
      if (stem.isEmpty) {
        results.add('だ');
      } else {
        // For na-adjectives: 元気でした → 元気
        results.add(stem);
      }
    }
  }

  // --- i-adjective ---
  if (word.endsWith('くない') && word.length > 3) {
    results.add('${word.substring(0, word.length - 3)}い');
  }
  if (word.endsWith('かった') && word.length > 3) {
    results.add('${word.substring(0, word.length - 3)}い');
  }
  if (word.endsWith('くて') && word.length > 2) {
    results.add('${word.substring(0, word.length - 2)}い');
  }
  if (word.endsWith('く') && word.length > 1) {
    results.add('${word.substring(0, word.length - 1)}い');
  }

  // --- Passive/causative ---
  // Ichidan passive/potential: 食べられる → 食べる
  if (word.endsWith('られる') && word.length > 3) {
    results.add('${word.substring(0, word.length - 3)}る');
  }
  // Godan passive: 読まれる → 読む
  if (word.endsWith('れる') && word.length > 2) {
    final stem = word.substring(0, word.length - 2);
    if (stem.isNotEmpty) {
      final last = stem[stem.length - 1];
      final dictEnd = negToDict[last];
      if (dictEnd != null) {
        results.add('${stem.substring(0, stem.length - 1)}$dictEnd');
      }
    }
  }
  // Causative: 食べさせる → 食べる, 読ませる → 読む
  if (word.endsWith('させる') && word.length > 3) {
    results.add('${word.substring(0, word.length - 3)}る');
  }
  if (word.endsWith('せる') && word.length > 2) {
    final stem = word.substring(0, word.length - 2);
    if (stem.isNotEmpty) {
      final last = stem[stem.length - 1];
      final dictEnd = negToDict[last];
      if (dictEnd != null) {
        results.add('${stem.substring(0, stem.length - 1)}$dictEnd');
      }
    }
  }

  // --- Imperative ---
  // Ichidan: 食べろ → 食べる
  if (word.endsWith('ろ') && word.length > 1) {
    results.add('${word.substring(0, word.length - 1)}る');
  }
  // Godan: 行け → 行く (e-dan)
  if (word.length > 1) {
    final last = word[word.length - 1];
    final dictEnd = ebaToDict[last]; // reuse e-dan mapping
    if (dictEnd != null) {
      results.add('${word.substring(0, word.length - 1)}$dictEnd');
    }
  }

  // --- Bare masu-stem (連用形) ---
  // Used in compound verbs (登り始める), as nouns (読み), etc.
  // Godan: 登り → 登る, 読み → 読む, 歩き → 歩く, etc.
  if (word.length > 1) {
    final last = word[word.length - 1];
    final dictEnd = masuToDict[last];
    if (dictEnd != null) {
      results.add('${word.substring(0, word.length - 1)}$dictEnd');
    }
  }
  // Ichidan: 食べ → 食べる (stem + る)
  if (word.length > 1) {
    results.add('${word}る');
  }

  return results;
}

/// Given an inflected word and its dictionary form, return the chain of
/// intermediate forms for display. E.g.:
///   inflected=疲れてしまいました, dictForm=疲れる
///   → [疲れてしまう, 疲れてしまいました]
///
/// Returns empty list if inflected == dictForm.
List<String> deinflectionChain(String inflected, String dictForm) {
  if (inflected == dictForm) return [];

  // Try to find intermediate forms by peeling polite/tense suffixes
  // to reveal the plain form, then show the plain form if different.

  // Suffixes sorted longest-first to avoid shorter matches shadowing longer ones
  const _formalToPlain = <(String, String)>[
    // Godan te+しまう formal (longest)
    ('ってしまいました', 'ってしまった'),
    ('ってしまいます', 'ってしまう'),
    ('んでしまいました', 'んでしまった'),
    ('んでしまいます', 'んでしまう'),
    ('いてしまいました', 'いてしまった'),
    ('いてしまいます', 'いてしまう'),
    ('いでしまいました', 'いでしまった'),
    ('いでしまいます', 'いでしまう'),
    ('してしまいました', 'してしまった'),
    ('してしまいます', 'してしまう'),
    // てしまう formal
    ('てしまいました', 'てしまった'),
    ('てしまいます', 'てしまう'),
    // Contracted forms
    ('ちゃいました', 'ちゃった'),
    ('ちゃいます', 'ちゃう'),
    ('じゃいました', 'じゃった'),
    ('じゃいます', 'じゃう'),
    // Masu forms (shortest — must come last)
    ('ませんでした', 'なかった'),
    ('ましょう', 'よう'),
    ('ました', 'た'),
    ('ません', 'ない'),
    ('ます', 'う'), // placeholder, works for godan
  ];

  // てしまう/ちゃう plain → te-form (strip しまう layer), longest first
  const _shimauToTe = <(String, String)>[
    ('ってしまった', 'って'),
    ('ってしまう', 'って'),
    ('んでしまった', 'んで'),
    ('んでしまう', 'んで'),
    ('いてしまった', 'いて'),
    ('いてしまう', 'いて'),
    ('いでしまった', 'いで'),
    ('いでしまう', 'いで'),
    ('してしまった', 'して'),
    ('してしまう', 'して'),
    ('てしまった', 'て'),
    ('てしまう', 'て'),
    ('てしまって', 'て'),
    ('んじゃった', 'んで'),
    ('んじゃう', 'んで'),
    ('ちゃった', 'て'),
    ('ちゃう', 'て'),
    ('ちゃって', 'て'),
  ];

  // All しまう forms → base てしまう / ちゃう
  const _shimauToBase = <(String, String)>[
    // Godan te + しまう (longest first)
    ('ってしまいました', 'ってしまう'), ('ってしまいます', 'ってしまう'),
    ('ってしまった', 'ってしまう'), ('ってしまって', 'ってしまう'),
    ('んでしまいました', 'んでしまう'), ('んでしまいます', 'んでしまう'),
    ('んでしまった', 'んでしまう'), ('んでしまって', 'んでしまう'),
    ('いてしまいました', 'いてしまう'), ('いてしまいます', 'いてしまう'),
    ('いてしまった', 'いてしまう'), ('いてしまって', 'いてしまう'),
    ('いでしまいました', 'いでしまう'), ('いでしまいます', 'いでしまう'),
    ('いでしまった', 'いでしまう'), ('いでしまって', 'いでしまう'),
    ('してしまいました', 'してしまう'), ('してしまいます', 'してしまう'),
    ('してしまった', 'してしまう'), ('してしまって', 'してしまう'),
    // Ichidan てしまう
    ('てしまいました', 'てしまう'), ('てしまいます', 'てしまう'),
    ('てしまった', 'てしまう'), ('てしまって', 'てしまう'),
    // Contracted ちゃう/じゃう
    ('ちゃいました', 'ちゃう'), ('ちゃいます', 'ちゃう'),
    ('ちゃった', 'ちゃう'), ('ちゃって', 'ちゃう'),
    ('じゃいました', 'じゃう'), ('じゃいます', 'じゃう'),
    ('じゃった', 'じゃう'), ('じゃって', 'じゃう'),
    ('んじゃいました', 'んじゃう'), ('んじゃいます', 'んじゃう'),
    ('んじゃった', 'んじゃう'), ('んじゃって', 'んじゃう'),
  ];

  // Masu suffixes and what tense they represent
  const _masuSuffixes = <(String, String)>[
    ('ませんでした', 'neg-past'),
    ('ましょう', 'volitional'),
    ('ました', 'past'),
    ('ません', 'negative'),
    ('ます', 'present'),
  ];

  // Godan masu-stem → dict form mapping (same as in deinflectWord)
  const _masuToDict = {
    'き': 'く', 'ぎ': 'ぐ', 'し': 'す', 'ち': 'つ', 'に': 'ぬ',
    'び': 'ぶ', 'み': 'む', 'り': 'る', 'い': 'う',
  };

  // Given a masu-stem and tense, build the correct plain form
  String? _buildPlainForm(String masuStem, String tense) {
    // Try godan first: check if last char of stem is in masu mapping
    final lastChar = masuStem.isNotEmpty ? masuStem[masuStem.length - 1] : '';
    final godanEnd = _masuToDict[lastChar];
    final godanBase = godanEnd != null
        ? '${masuStem.substring(0, masuStem.length - 1)}$godanEnd'
        : null;
    final ichidanBase = '${masuStem}る';

    // Use dictForm to decide ichidan vs godan
    final base = (godanBase != null && godanBase == dictForm)
        ? godanBase
        : ichidanBase;
    final isGodan = godanBase != null && godanBase == dictForm;

    switch (tense) {
      case 'present':
        return base; // dictionary form
      case 'past':
        if (isGodan && godanEnd != null) {
          // Godan past tense depends on verb ending
          // Special case: 行く → 行った (not 行いた)
          if (base.endsWith('行く')) return '${base.substring(0, base.length - 1)}った';
          final stem = base.substring(0, base.length - 1);
          switch (godanEnd) {
            case 'く': return '${stem}いた';
            case 'ぐ': return '${stem}いだ';
            case 'す': return '${stem}した';
            case 'つ': case 'う': case 'る': return '${stem}った';
            case 'む': case 'ぶ': case 'ぬ': return '${stem}んだ';
          }
        }
        return '${masuStem}た'; // ichidan: stem + た
      case 'negative':
        if (isGodan) {
          // Godan negative: a-dan + ない
          const _dictToNeg = {
            'く': 'か', 'ぐ': 'が', 'す': 'さ', 'つ': 'た', 'ぬ': 'な',
            'ぶ': 'ば', 'む': 'ま', 'る': 'ら', 'う': 'わ',
          };
          final stem = base.substring(0, base.length - 1);
          final neg = _dictToNeg[godanEnd];
          if (neg != null) return '${stem}${neg}ない';
        }
        return '${masuStem}ない'; // ichidan
      case 'neg-past':
        if (isGodan) {
          const _dictToNeg = {
            'く': 'か', 'ぐ': 'が', 'す': 'さ', 'つ': 'た', 'ぬ': 'な',
            'ぶ': 'ば', 'む': 'ま', 'る': 'ら', 'う': 'わ',
          };
          final stem = base.substring(0, base.length - 1);
          final neg = _dictToNeg[godanEnd];
          if (neg != null) return '${stem}${neg}なかった';
        }
        return '${masuStem}なかった';
      case 'volitional':
        if (isGodan) {
          const _dictToVol = {
            'く': 'こ', 'ぐ': 'ご', 'す': 'そ', 'つ': 'と', 'ぬ': 'の',
            'ぶ': 'ぼ', 'む': 'も', 'る': 'ろ', 'う': 'お',
          };
          final stem = base.substring(0, base.length - 1);
          final vol = _dictToVol[godanEnd];
          if (vol != null) return '${stem}${vol}う';
        }
        return '${masuStem}よう';
      default:
        return null;
    }
  }

  final chain = <String>[];

  // Step 1: try しまう layer → base しまう form
  String? shimauBase;
  for (final (suffix, replacement) in _shimauToBase) {
    if (inflected.endsWith(suffix) && inflected.length > suffix.length) {
      final stem = inflected.substring(0, inflected.length - suffix.length);
      shimauBase = '$stem$replacement';
      break;
    }
  }

  // Step 2: try masu formal → correct plain form
  String? plainForm;
  if (shimauBase == null) {
    for (final (suffix, tense) in _masuSuffixes) {
      if (inflected.endsWith(suffix) && inflected.length > suffix.length) {
        final masuStem = inflected.substring(0, inflected.length - suffix.length);
        plainForm = _buildPlainForm(masuStem, tense);
        break;
      }
    }
  }

  // Build chain: show intermediate forms between dictForm and inflected
  if (shimauBase != null && shimauBase != inflected) {
    chain.add(shimauBase);
  }
  if (plainForm != null && plainForm != inflected) {
    chain.add(plainForm);
  }
  chain.add(inflected);

  return chain;
}
