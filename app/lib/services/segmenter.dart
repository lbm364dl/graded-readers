import 'dart:async';
import 'dart:isolate';
import 'dart:typed_data';
import 'package:kuromoji/kuromoji.dart' as kuromoji;
import 'package:kuromoji/src/tokenizer.dart' as kuromoji_tok;
import 'package:kuromoji/src/dict/data/char.dart';
import 'package:kuromoji/src/dict/dynamic_dictionaries.dart';
import 'package:kuromoji/src/dictionary_loader.dart';
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

/// POS tags to skip (grammatical glue, not content words)
const _skipPos = {
  '助詞', // particles: は、が、を、に
  '助動詞', // auxiliaries: です、ます、た
  '記号', // symbols
  '接続詞', // conjunctions
};

// ---------------------------------------------------------------------------
// Kuromoji-based Japanese tokenizer (singleton)
// ---------------------------------------------------------------------------

class JapaneseTokenizer {
  JapaneseTokenizer._();
  static final JapaneseTokenizer instance = JapaneseTokenizer._();

  kuromoji_tok.Tokenizer? _tokenizer;
  Completer<void>? _initCompleter;
  bool get isReady => _tokenizer != null;

  /// Initialize kuromoji dictionary in a background isolate.
  /// The heavy GZip decompression runs off the main thread.
  Future<void> initialize() async {
    if (_tokenizer != null) return;
    if (_initCompleter != null) return _initCompleter!.future;

    _initCompleter = Completer<void>();
    try {
      // Heavy decompression in background isolate
      final data = await Isolate.run(() => DictionaryLoader().load());
      // Light construction on main isolate
      final dictionaries = DynamicDictionaries(data, charData);
      _tokenizer = kuromoji_tok.Tokenizer(dictionaries);
    } catch (_) {
      // If kuromoji fails to load, we fall back to the simple segmenter
    }
    _initCompleter!.complete();
  }

  /// Wait for initialization if in progress, or return immediately.
  Future<void> ensureReady() async {
    if (_tokenizer != null) return;
    if (_initCompleter != null) await _initCompleter!.future;
  }

  /// Tokenize Japanese text. Returns list of (surfaceForm, basicForm) pairs
  /// for content words, and (surface, surface) for non-content tokens.
  List<_JpToken> tokenize(String text) {
    if (_tokenizer == null || text.isEmpty) return [_JpToken(text, text)];

    final results = <_JpToken>[];
    try {
      final tokens = _tokenizer!.tokenize(text);
      int lastEnd = 0;

      for (final t in tokens) {
        final surface = t['surface_form'] as String? ?? '';
        final basic = t['basic_form'] as String? ?? surface;
        final pos = t['pos'] as String? ?? '';
        final position = t['word_position'] as int? ?? 0;

        // Fill any gap (punctuation between tokens that kuromoji splits on)
        if (position > lastEnd) {
          results.add(_JpToken(
            text.substring(lastEnd, position),
            text.substring(lastEnd, position),
          ));
        }

        if (surface.isNotEmpty) {
          results.add(_JpToken(surface, basic, pos: pos));
        }
        lastEnd = position + surface.length;
      }

      // Trailing text
      if (lastEnd < text.length) {
        results.add(_JpToken(
          text.substring(lastEnd),
          text.substring(lastEnd),
        ));
      }
    } catch (_) {
      return [_JpToken(text, text)];
    }

    return results;
  }
}

class _JpToken {
  final String surface;
  final String basicForm;
  final String pos;
  _JpToken(this.surface, this.basicForm, {this.pos = ''});

  bool get isContentWord =>
      surface.isNotEmpty &&
      !_skipPos.contains(pos) &&
      surface.trim().isNotEmpty;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Segments text into tokens. For Japanese, uses kuromoji morphological
/// analysis. For Chinese, uses max-forward dictionary matching.
List<String> segmentText(String text, DictionaryService dict) {
  if (!dict.isReady || text.isEmpty) return [text];

  // Use kuromoji for Japanese if available
  if (dict.activeLanguage == Language.japanese &&
      JapaneseTokenizer.instance.isReady) {
    return _segmentJapanese(text);
  }

  // Chinese: max-forward matching
  return _segmentChinese(text, dict);
}

/// Returns the dictionary (basic) form for a surface token.
/// Used by the reader to map tapped inflected words to dict entries.
String? getDictionaryForm(String surface) {
  final tokenizer = JapaneseTokenizer.instance;
  if (!tokenizer.isReady) return null;

  final tokens = tokenizer.tokenize(surface);
  if (tokens.length == 1 && tokens[0].basicForm != tokens[0].surface) {
    return tokens[0].basicForm;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Japanese segmentation via kuromoji
// ---------------------------------------------------------------------------

List<String> _segmentJapanese(String text) {
  final tokens = JapaneseTokenizer.instance.tokenize(text);
  return tokens.map((t) => t.surface).toList();
}

// ---------------------------------------------------------------------------
// Chinese segmentation via max-forward matching
// ---------------------------------------------------------------------------

List<String> _segmentChinese(String text, DictionaryService dict) {
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

    if (!found) {
      result.add(text.substring(i, i + 1));
      i++;
    }
  }

  return result;
}

// ---------------------------------------------------------------------------
// Deinflection (kept as fallback for dictionary lookup when kuromoji
// isn't available, and for the word definition sheet)
// ---------------------------------------------------------------------------

/// Generate possible dictionary forms for an inflected Japanese word.
List<String> deinflectWord(String word) {
  final results = <String>[];

  // Verb masu-form: ichidan
  if (word.endsWith('ます')) {
    results.add('${word.substring(0, word.length - 2)}る');
  }
  if (word.endsWith('ました')) {
    results.add('${word.substring(0, word.length - 3)}る');
  }
  if (word.endsWith('ません')) {
    results.add('${word.substring(0, word.length - 3)}る');
  }

  // Godan masu-form
  const masuToDict = {
    'き': 'く', 'ぎ': 'ぐ', 'し': 'す', 'ち': 'つ', 'に': 'ぬ',
    'び': 'ぶ', 'み': 'む', 'り': 'る', 'い': 'う',
  };

  for (final suffix in ['ます', 'ました', 'ません']) {
    if (word.endsWith(suffix) && word.length > suffix.length) {
      final before = word.substring(0, word.length - suffix.length);
      if (before.isNotEmpty) {
        final last = before[before.length - 1];
        final dictEnd = masuToDict[last];
        if (dictEnd != null) {
          results.add('${before.substring(0, before.length - 1)}$dictEnd');
        }
      }
    }
  }

  // Te-form / past: ichidan
  for (final suffix in ['て', 'た', 'ている', 'ていた']) {
    if (word.endsWith(suffix) && word.length > suffix.length) {
      results.add('${word.substring(0, word.length - suffix.length)}る');
    }
  }

  // Te-form / past: godan
  final teRules = <String, List<String>>{
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

  // Negative
  if (word.endsWith('ない') && word.length > 2) {
    final before = word.substring(0, word.length - 2);
    results.add('${before}る');
    if (before.isNotEmpty) {
      const negToDict = {
        'か': 'く', 'が': 'ぐ', 'さ': 'す', 'た': 'つ', 'な': 'ぬ',
        'ば': 'ぶ', 'ま': 'む', 'ら': 'る', 'わ': 'う',
      };
      final last = before[before.length - 1];
      final dictEnd = negToDict[last];
      if (dictEnd != null) {
        results.add('${before.substring(0, before.length - 1)}$dictEnd');
      }
    }
  }

  // Tai-form
  if (word.endsWith('たい') && word.length > 2) {
    final stem = word.substring(0, word.length - 2);
    results.add('${stem}る');
    if (stem.isNotEmpty) {
      final last = stem[stem.length - 1];
      final dictEnd = masuToDict[last];
      if (dictEnd != null) {
        results.add('${stem.substring(0, stem.length - 1)}$dictEnd');
      }
    }
  }

  // i-adjective
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

  return results;
}
