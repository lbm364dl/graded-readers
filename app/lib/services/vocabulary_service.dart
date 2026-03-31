import 'dart:convert';
import 'package:shared_preferences/shared_preferences.dart';

class SavedWord {
  final String word;
  final String pinyin;
  final List<String> definitions;
  final DateTime savedAt;
  bool isLearned;

  SavedWord({
    required this.word,
    required this.pinyin,
    required this.definitions,
    required this.savedAt,
    this.isLearned = false,
  });

  Map<String, dynamic> toJson() => {
        'word': word,
        'pinyin': pinyin,
        'definitions': definitions,
        'savedAt': savedAt.toIso8601String(),
        'isLearned': isLearned,
      };

  factory SavedWord.fromJson(Map<String, dynamic> json) => SavedWord(
        word: json['word'] as String,
        pinyin: json['pinyin'] as String,
        definitions: List<String>.from(json['definitions'] as List),
        savedAt: DateTime.parse(json['savedAt'] as String),
        isLearned: (json['isLearned'] as bool?) ?? false,
      );
}

class VocabularyService {
  VocabularyService._();
  static final VocabularyService instance = VocabularyService._();

  static const _key = 'saved_words_v1';

  List<SavedWord>? _cache;

  /// Returns saved words (most recent first). Initializes cache on first call.
  Future<List<SavedWord>> loadWords() async {
    if (_cache != null) return _cache!;
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getStringList(_key) ?? [];
    _cache = raw
        .map((s) => SavedWord.fromJson(json.decode(s) as Map<String, dynamic>))
        .toList();
    _cache!.sort((a, b) => b.savedAt.compareTo(a.savedAt));
    return _cache!;
  }

  Future<void> saveWord(SavedWord word) async {
    final words = await loadWords();
    words.removeWhere((w) => w.word == word.word);
    words.insert(0, word);
    _cache = words;
    await _persist();
  }

  Future<void> removeWord(String word) async {
    final words = await loadWords();
    words.removeWhere((w) => w.word == word);
    _cache = words;
    await _persist();
  }

  Future<void> markLearned(String word, {required bool learned}) async {
    final words = await loadWords();
    final idx = words.indexWhere((w) => w.word == word);
    if (idx >= 0) {
      words[idx].isLearned = learned;
      await _persist();
    }
  }

  /// Synchronous check — only valid after [loadWords] has been awaited.
  bool isSaved(String word) => _cache?.any((w) => w.word == word) ?? false;

  Future<void> _persist() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setStringList(
      _key,
      _cache!.map((w) => json.encode(w.toJson())).toList(),
    );
  }
}
