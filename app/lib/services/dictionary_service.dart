import 'dart:collection';

import '../models.dart';
import 'lexicon_store.dart';
import 'segmenter.dart' show deinflectWord;

class DictEntry {
  final String word;
  final String pinyin; // pinyin for Chinese, reading for Japanese
  final List<String> definitions;
  final int? hskLevel;
  final int? characterHskLevel;
  final List<String> partsOfSpeech;
  final bool isAlias;

  const DictEntry({
    required this.word,
    required this.pinyin,
    required this.definitions,
    this.hskLevel,
    this.characterHskLevel,
    this.partsOfSpeech = const [],
    this.isAlias = false,
  });

  bool get hasDefinitions => definitions.isNotEmpty;
}

class DictionaryService {
  DictionaryService._();
  DictionaryService.forTest(); // for subclass mocking in tests
  static final DictionaryService instance = DictionaryService._();

  final Set<Language> _readyLanguages = {};
  final LinkedHashMap<String, DictEntry?> _entryCache = LinkedHashMap();

  Language _activeLanguage = Language.chinese;

  Language get activeLanguage => _activeLanguage;

  Future<void> initialize({Language language = Language.chinese}) async {
    _activeLanguage = language;
    await _loadDict(language);
  }

  Future<void> switchLanguage(Language language) async {
    _activeLanguage = language;
    if (!_readyLanguages.contains(language)) {
      await _loadDict(language);
    }
  }

  Future<void> _loadDict(Language language) async {
    if (_readyLanguages.contains(language)) return;
    try {
      await lexiconStore.initialize();
      _readyLanguages.add(language);
    } catch (_) {
      // Agent-authored annotations remain usable if lookup data is unavailable.
    }
  }

  bool get isReady => _readyLanguages.contains(_activeLanguage);

  // Published prose is segmented by the reviewed agent annotations. Keep the
  // legacy deterministic segmenter inert in production rather than eagerly
  // materializing every dictionary headword just to build a word set.
  Set<String> get wordSet => const {};
  int get maxWordLength => 1;

  DictEntry? lookup(String word) {
    final entry = _lookupExact(_activeLanguage, word);
    if (entry != null) return entry;

    // Try deinflection for Japanese
    if (_activeLanguage == Language.japanese) {
      return _lookupDeinflected(word);
    }
    return null;
  }

  /// Resolve an authored dictionary link without deinflection or reading
  /// aliases. The content pipeline has already selected a specific canonical
  /// headword and sense; silently falling through would make that link unsafe.
  DictEntry? lookupExactCanonical(String word) {
    final entry = _lookupExact(_activeLanguage, word);
    if (entry == null || entry.isAlias || entry.word != word) return null;
    return entry;
  }

  DictEntry? _lookupExact(Language language, String word) {
    if (!lexiconStore.isReady) return null;
    final cacheKey = '${language.name}\u0000$word';
    if (_entryCache.containsKey(cacheKey)) {
      final cached = _entryCache.remove(cacheKey);
      _entryCache[cacheKey] = cached;
      return cached;
    }

    final raw = lexiconStore.dictionaryEntry(language, word);
    DictEntry? entry;
    if (raw != null) {
      final characterHskLevel =
          language == Language.chinese && word.runes.length == 1
              ? lexiconStore.characterLevel(word)
              : null;
      entry = DictEntry(
        word: (raw['w'] as String?) ?? word,
        pinyin: (raw['p'] as String?) ?? '',
        definitions: List<String>.from((raw['d'] as List?) ?? []),
        hskLevel: raw['l'] as int?,
        characterHskLevel: characterHskLevel,
        partsOfSpeech: List<String>.from((raw['pos'] as List?) ?? const []),
        isAlias: raw['a'] as bool? ?? false,
      );
    }
    _entryCache[cacheKey] = entry;
    if (_entryCache.length > 512) {
      _entryCache.remove(_entryCache.keys.first);
    }
    return entry;
  }

  DictEntry? _lookupDeinflected(String word) {
    final candidates = deinflectWord(word);
    for (final dictForm in candidates) {
      final entry = _lookupExact(_activeLanguage, dictForm);
      if (entry != null) return entry;
    }
    return null;
  }

  bool hasWord(String word) => _lookupExact(_activeLanguage, word) != null;
}
