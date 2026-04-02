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

    // For Japanese: try deinflection on progressively longer spans
    if (dict.activeLanguage == Language.japanese) {
      final deinflected = _tryDeinflect(text, i, dict);
      if (deinflected != null) {
        result.add(text.substring(i, i + deinflected.consumedLength));
        i += deinflected.consumedLength;
        continue;
      }
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

_DeinflectResult? _tryDeinflect(
    String text, int start, DictionaryService dict) {
  final remaining = text.length - start;
  if (remaining < 2) return null;
  final maxTry = remaining.clamp(2, 12);

  for (int len = maxTry; len >= 2; len--) {
    final end = start + len;
    if (end > text.length) continue;
    final span = text.substring(start, end);
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
List<String> deinflectWord(String word) {
  final results = <String>[];

  // --- Godan masu-stem mapping ---
  const masuToDict = {
    'き': 'く', 'ぎ': 'ぐ', 'し': 'す', 'ち': 'つ', 'に': 'ぬ',
    'び': 'ぶ', 'み': 'む', 'り': 'る', 'い': 'う',
  };
  const negToDict = {
    'か': 'く', 'が': 'ぐ', 'さ': 'す', 'た': 'つ', 'な': 'ぬ',
    'ば': 'ぶ', 'ま': 'む', 'ら': 'る', 'わ': 'う',
  };

  // --- Verb masu-form ---
  for (final suffix in ['ます', 'ました', 'ません']) {
    if (word.endsWith(suffix) && word.length > suffix.length) {
      final stem = word.substring(0, word.length - suffix.length);
      results.add('${stem}る'); // ichidan
      if (stem.isNotEmpty) {
        final last = stem[stem.length - 1];
        final dictEnd = masuToDict[last];
        if (dictEnd != null) {
          results.add('${stem.substring(0, stem.length - 1)}$dictEnd');
        }
      }
    }
  }

  // --- Te-form / past: ichidan ---
  for (final suffix in ['て', 'た', 'ている', 'ていた']) {
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
  if (word.endsWith('ない') && word.length > 2) {
    final before = word.substring(0, word.length - 2);
    results.add('${before}る'); // ichidan
    if (before.isNotEmpty) {
      final last = before[before.length - 1];
      final dictEnd = negToDict[last];
      if (dictEnd != null) {
        results.add('${before.substring(0, before.length - 1)}$dictEnd');
      }
    }
  }

  // --- Tai-form ---
  if (word.endsWith('たい') && word.length > 2) {
    final stem = word.substring(0, word.length - 2);
    results.add('${stem}る'); // ichidan
    if (stem.isNotEmpty) {
      final last = stem[stem.length - 1];
      final dictEnd = masuToDict[last];
      if (dictEnd != null) {
        results.add('${stem.substring(0, stem.length - 1)}$dictEnd');
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
  if (word.endsWith('られる') && word.length > 3) {
    results.add('${word.substring(0, word.length - 3)}る');
  }
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

  return results;
}
