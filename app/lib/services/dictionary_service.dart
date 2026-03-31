import 'dart:convert';
import 'package:flutter/services.dart';

class DictEntry {
  final String word;
  final String pinyin;
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
  static final DictionaryService instance = DictionaryService._();

  Map<String, dynamic>? _dict;
  Set<String>? _wordSet;
  int _maxWordLength = 1;

  Future<void> initialize() async {
    if (_dict != null) return;
    final jsonStr = await rootBundle.loadString('assets/dictionary.json');
    _dict = json.decode(jsonStr) as Map<String, dynamic>;
    _wordSet = _dict!.keys.toSet();
    _maxWordLength =
        _wordSet!.fold(0, (m, w) => w.length > m ? w.length : m);
  }

  bool get isReady => _dict != null;
  Set<String> get wordSet => _wordSet ?? const {};
  int get maxWordLength => _maxWordLength;

  DictEntry? lookup(String word) {
    final raw = _dict?[word];
    if (raw == null) return null;
    return DictEntry(
      word: word,
      pinyin: (raw['p'] as String?) ?? '',
      definitions: List<String>.from((raw['d'] as List?) ?? []),
      hskLevel: raw['l'] as int?,
    );
  }

  bool hasWord(String word) => _wordSet?.contains(word) ?? false;
}
