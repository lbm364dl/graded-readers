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

    // Try max forward matching (exact dictionary match first)
    final maxLen = dict.maxWordLength.clamp(1, text.length - i);
    bool found = false;

    for (int len = maxLen; len > 1; len--) {
      final candidate = text.substring(i, i + len);
      if (dict.hasWord(candidate)) {
        result.add(candidate);
        i += len;
        found = true;
        break;
      }
    }

    if (found) continue;

    // Try Japanese deinflection: look ahead for an inflected word
    final deinflected = _tryDeinflect(text, i, dict);
    if (deinflected != null) {
      result.add(text.substring(i, i + deinflected.consumedLength));
      i += deinflected.consumedLength;
      continue;
    }

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

/// Try to find an inflected Japanese word starting at position [start].
/// Returns the dictionary form and how many characters were consumed,
/// or null if no match.
_DeinflectResult? _tryDeinflect(String text, int start, DictionaryService dict) {
  final remaining = text.length - start;
  if (remaining < 2) return null;
  final maxTry = remaining.clamp(2, 12);

  for (int len = maxTry; len >= 2; len--) {
    final end = start + len;
    if (end > text.length) continue;
    // Only try spans that end at a non-CJK boundary or end of text
    // to avoid consuming partial words
    final span = text.substring(start, end);
    // Skip spans that contain non-CJK in the middle
    if (span.codeUnits.any((c) => !_isCJK(c))) continue;

    final candidates = deinflectWord(span);
    for (final dictForm in candidates) {
      if (dictForm.isNotEmpty && dict.hasWord(dictForm)) {
        return _DeinflectResult(dictForm, len);
      }
    }
  }
  return null;
}

/// Generate possible dictionary forms for an inflected Japanese word.
/// Returns a list of candidates (most specific first).
List<String> deinflectWord(String word) {
  final results = <String>[];

  // --- Verb masu-form endings ---
  // ichidan: 食べます → 食べる
  if (word.endsWith('ます')) {
    final stem = word.substring(0, word.length - 2);
    results.add('${stem}る'); // ichidan
  }
  if (word.endsWith('ました')) {
    final stem = word.substring(0, word.length - 3);
    results.add('${stem}る');
  }
  if (word.endsWith('ません')) {
    final stem = word.substring(0, word.length - 3);
    results.add('${stem}る');
  }

  // godan masu-form: 行きます→行く, 飲みます→飲む, etc.
  // The masu-stem maps: き→く, ぎ→ぐ, し→す, ち→つ, に→ぬ, び→ぶ, み→む, り→る, い→う
  const masuToDict = {
    'き': 'く', 'ぎ': 'ぐ', 'し': 'す', 'ち': 'つ', 'に': 'ぬ',
    'び': 'ぶ', 'み': 'む', 'り': 'る', 'い': 'う',
  };

  for (final suffix in ['ます', 'ました', 'ません']) {
    if (word.endsWith(suffix) && word.length > suffix.length) {
      final beforeSuffix = word.substring(0, word.length - suffix.length);
      if (beforeSuffix.isNotEmpty) {
        final lastChar = beforeSuffix[beforeSuffix.length - 1];
        final dictEnd = masuToDict[lastChar];
        if (dictEnd != null) {
          final stem = beforeSuffix.substring(0, beforeSuffix.length - 1);
          results.add('$stem$dictEnd');
        }
      }
    }
  }

  // --- Te-form / past tense ---
  // ichidan: 食べて→食べる, 食べた→食べる
  for (final suffix in ['て', 'た', 'ている', 'ていた', 'てい']) {
    if (word.endsWith(suffix) && word.length > suffix.length) {
      final stem = word.substring(0, word.length - suffix.length);
      results.add('${stem}る'); // ichidan guess
    }
  }

  // godan te-form patterns:
  // 行って→行く (く→って), 書いて→書く (く→いて)
  // 飲んで→飲む (む→んで), 読んで→読む
  // 話して→話す (す→して)
  // 持って→持つ (つ→って)
  // 遊んで→遊ぶ (ぶ→んで)
  // 走って→走る (る→って) — but this conflicts with ichidan
  // 買って→買う (う→って)
  final _teFormRules = <String, List<String>>{
    'って': ['く', 'つ', 'う', 'る'], // 行って→行く, 持って→持つ, 買って→買う
    'いて': ['く'],           // 書いて→書く
    'いだ': ['ぐ'],           // 泳いだ→泳ぐ
    'いで': ['ぐ'],           // 泳いで→泳ぐ
    'んで': ['む', 'ぶ', 'ぬ'], // 飲んで→飲む, 遊んで→遊ぶ
    'んだ': ['む', 'ぶ', 'ぬ'], // 飲んだ→飲む, 遊んだ→遊ぶ
    'して': ['す'],           // 話して→話す
    'した': ['す'],           // 話した→話す
    'った': ['く', 'つ', 'う', 'る'], // 行った→行く
  };

  for (final entry in _teFormRules.entries) {
    final suffix = entry.key;
    if (word.endsWith(suffix) && word.length > suffix.length) {
      final stem = word.substring(0, word.length - suffix.length);
      for (final dictEnd in entry.value) {
        results.add('$stem$dictEnd');
      }
    }
  }

  // --- Negative form ---
  // ichidan: 食べない→食べる
  // godan: 行かない→行く
  if (word.endsWith('ない') && word.length > 2) {
    final beforeNai = word.substring(0, word.length - 2);
    results.add('${beforeNai}る'); // ichidan: 食べない→食べる

    // godan: Xあない→Xう, Xかない→Xく, etc.
    if (beforeNai.isNotEmpty) {
      final lastChar = beforeNai[beforeNai.length - 1];
      const negToDict = {
        'か': 'く', 'が': 'ぐ', 'さ': 'す', 'た': 'つ', 'な': 'ぬ',
        'ば': 'ぶ', 'ま': 'む', 'ら': 'る', 'わ': 'う',
      };
      final dictEnd = negToDict[lastChar];
      if (dictEnd != null) {
        final stem = beforeNai.substring(0, beforeNai.length - 1);
        results.add('$stem$dictEnd');
      }
    }
  }

  // --- Tai-form (want to) ---
  if (word.endsWith('たい') && word.length > 2) {
    final stem = word.substring(0, word.length - 2);
    results.add('${stem}る'); // ichidan
    // godan
    if (stem.isNotEmpty) {
      final lastChar = stem[stem.length - 1];
      final dictEnd = masuToDict[lastChar];
      if (dictEnd != null) {
        final baseStem = stem.substring(0, stem.length - 1);
        results.add('$baseStem$dictEnd');
      }
    }
  }

  // --- i-adjective inflections ---
  // 大きくない→大きい, 大きかった→大きい, 大きくて→大きい, 大きく→大きい
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

  // --- Passive / causative ---
  // 食べられる→食べる, 行かれる→行く
  if (word.endsWith('られる') && word.length > 3) {
    final stem = word.substring(0, word.length - 3);
    results.add('${stem}る');
  }
  if (word.endsWith('れる') && word.length > 2) {
    final stem = word.substring(0, word.length - 2);
    // godan passive: 行かれる stem=行か → 行く
    if (stem.isNotEmpty) {
      final lastChar = stem[stem.length - 1];
      const negToDict = {
        'か': 'く', 'が': 'ぐ', 'さ': 'す', 'た': 'つ', 'な': 'ぬ',
        'ば': 'ぶ', 'ま': 'む', 'ら': 'る', 'わ': 'う',
      };
      final dictEnd = negToDict[lastChar];
      if (dictEnd != null) {
        final baseStem = stem.substring(0, stem.length - 1);
        results.add('$baseStem$dictEnd');
      }
    }
  }

  // --- Potential form ---
  // 食べられる (same as passive for ichidan)
  // 行ける→行く (godan: replace え with corresponding dict ending)
  if (word.endsWith('える') && word.length > 2) {
    final stem = word.substring(0, word.length - 2);
    // Could be godan potential: 行ける stem=行k, but we need the あ→え mapping
    // Actually the stem before える gives us: 行ける → 行 + ける
    results.add('${stem}く'); // 行ける→行く? No, need to think about this differently
  }

  return results;
}
