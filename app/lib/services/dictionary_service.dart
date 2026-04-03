import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import '../models.dart';
import 'segmenter.dart' show deinflectWord;

// Top-level function for compute() — JSON parsing in background isolate
Map<String, dynamic> _parseJson(String jsonStr) =>
    json.decode(jsonStr) as Map<String, dynamic>;

class DictEntry {
  final String word;
  final String pinyin; // pinyin for Chinese, reading for Japanese
  final List<String> definitions;
  final int? hskLevel;

  const DictEntry({
    required this.word,
    required this.pinyin,
    required this.definitions,
    this.hskLevel,
  });

  bool get hasDefinitions => definitions.isNotEmpty;
}

class DictionaryService {
  DictionaryService._();
  DictionaryService.forTest(); // for subclass mocking in tests
  static final DictionaryService instance = DictionaryService._();

  static const _assetPaths = {
    Language.chinese: 'assets/dictionary.json',
    Language.japanese: 'assets/dictionary_ja.json',
  };

  final Map<Language, Map<String, dynamic>> _dicts = {};
  final Map<Language, Set<String>> _wordSets = {};
  final Map<Language, int> _maxWordLengths = {};

  Language _activeLanguage = Language.chinese;

  Language get activeLanguage => _activeLanguage;

  Future<void> initialize({Language language = Language.chinese}) async {
    await _loadDict(language);
    _activeLanguage = language;
  }

  Future<void> switchLanguage(Language language) async {
    if (!_dicts.containsKey(language)) {
      await _loadDict(language);
    }
    _activeLanguage = language;
  }

  Future<void> _loadDict(Language language) async {
    if (_dicts.containsKey(language)) return;
    final path = _assetPaths[language]!;
    try {
      final jsonStr = await rootBundle.loadString(path);
      // Parse JSON in background isolate to avoid blocking UI
      final dict = await compute(
          _parseJson, jsonStr) as Map<String, dynamic>;
      _dicts[language] = dict;
      _wordSets[language] = dict.keys.toSet();
      _maxWordLengths[language] =
          dict.keys.fold(0, (m, w) => w.length > m ? w.length : m);
    } catch (_) {
      // Asset not found — use empty dict
      _dicts[language] = {};
      _wordSets[language] = {};
      _maxWordLengths[language] = 1;
    }
  }

  bool get isReady => _dicts.containsKey(_activeLanguage);
  Set<String> get wordSet => _wordSets[_activeLanguage] ?? const {};
  int get maxWordLength => _maxWordLengths[_activeLanguage] ?? 1;

  DictEntry? lookup(String word) {
    final raw = _dicts[_activeLanguage]?[word];
    if (raw != null) {
      return DictEntry(
        word: word,
        pinyin: (raw['p'] as String?) ?? '',
        definitions: List<String>.from((raw['d'] as List?) ?? []),
        hskLevel: raw['l'] as int?,
      );
    }
    // Try deinflection for Japanese
    if (_activeLanguage == Language.japanese) {
      return _lookupDeinflected(word);
    }
    return null;
  }

  DictEntry? _lookupDeinflected(String word) {
    final candidates = deinflectWord(word);
    for (final dictForm in candidates) {
      final raw = _dicts[_activeLanguage]?[dictForm];
      if (raw != null) {
        return DictEntry(
          word: dictForm,
          pinyin: (raw['p'] as String?) ?? '',
          definitions: List<String>.from((raw['d'] as List?) ?? []),
          hskLevel: raw['l'] as int?,
        );
      }
    }
    return null;
  }

  bool hasWord(String word) =>
      _wordSets[_activeLanguage]?.contains(word) ?? false;
}
