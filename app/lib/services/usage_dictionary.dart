import 'dart:convert';
import 'package:flutter/services.dart';
import '../models.dart';

class _PinyinLetter {
  final String base;
  final int tone;

  const _PinyinLetter(this.base, this.tone);
}

const _pinyinToneLetters = <int, _PinyinLetter>{
  0x00fc: _PinyinLetter('v', 0), // ü
  0x0101: _PinyinLetter('a', 1), // ā
  0x00e1: _PinyinLetter('a', 2), // á
  0x01ce: _PinyinLetter('a', 3), // ǎ
  0x00e0: _PinyinLetter('a', 4), // à
  0x0113: _PinyinLetter('e', 1), // ē
  0x00e9: _PinyinLetter('e', 2), // é
  0x011b: _PinyinLetter('e', 3), // ě
  0x00e8: _PinyinLetter('e', 4), // è
  0x012b: _PinyinLetter('i', 1), // ī
  0x00ed: _PinyinLetter('i', 2), // í
  0x01d0: _PinyinLetter('i', 3), // ǐ
  0x00ec: _PinyinLetter('i', 4), // ì
  0x014d: _PinyinLetter('o', 1), // ō
  0x00f3: _PinyinLetter('o', 2), // ó
  0x01d2: _PinyinLetter('o', 3), // ǒ
  0x00f2: _PinyinLetter('o', 4), // ò
  0x016b: _PinyinLetter('u', 1), // ū
  0x00fa: _PinyinLetter('u', 2), // ú
  0x01d4: _PinyinLetter('u', 3), // ǔ
  0x00f9: _PinyinLetter('u', 4), // ù
  0x01d6: _PinyinLetter('v', 1), // ǖ
  0x01d8: _PinyinLetter('v', 2), // ǘ
  0x01da: _PinyinLetter('v', 3), // ǚ
  0x01dc: _PinyinLetter('v', 4), // ǜ
  0x1e3f: _PinyinLetter('m', 2), // ḿ
  0x0144: _PinyinLetter('n', 2), // ń
  0x0148: _PinyinLetter('n', 3), // ň
  0x01f9: _PinyinLetter('n', 4), // ǹ
};

class _PinyinPattern {
  final String base;
  final Map<int, int> tones;

  const _PinyinPattern(this.base, this.tones);
}

_PinyinPattern _pinyinPattern(String value) {
  final letters = <String>[];
  final tones = <int, int>{};

  for (final rune in value.toLowerCase().runes) {
    final accented = _pinyinToneLetters[rune];
    if (accented != null) {
      final index = letters.length;
      letters.add(accented.base);
      if (accented.tone != 0) tones[index] = accented.tone;
    } else if (rune >= 0x61 && rune <= 0x7a) {
      letters.add(String.fromCharCode(rune));
    } else if (rune == 0x0308) {
      // Accept decomposed ü as well as precomposed ü and keyboard input u:.
      if (letters.isNotEmpty && letters.last == 'u') {
        letters[letters.length - 1] = 'v';
      }
    } else if (rune == 0x0304 ||
        rune == 0x0301 ||
        rune == 0x030c ||
        rune == 0x0300) {
      if (letters.isNotEmpty) {
        tones[letters.length - 1] = switch (rune) {
          0x0304 => 1,
          0x0301 => 2,
          0x030c => 3,
          _ => 4,
        };
      }
    } else if (rune == 0x3a && letters.isNotEmpty && letters.last == 'u') {
      letters[letters.length - 1] = 'v';
    }
  }

  return _PinyinPattern(letters.join(), tones);
}

bool _isPinyinInputRune(int rune) =>
    (rune >= 0x61 && rune <= 0x7a) ||
    _pinyinToneLetters.containsKey(rune) ||
    rune == 0x0308 ||
    rune == 0x0304 ||
    rune == 0x0301 ||
    rune == 0x030c ||
    rune == 0x0300;

class _NumberedPinyinSyllable {
  final String base;
  final int tone;

  const _NumberedPinyinSyllable(this.base, this.tone);
}

_PinyinPattern? _numberedPinyinPattern(String value) {
  final syllables = <_NumberedPinyinSyllable>[];
  final current = <String>[];
  final normalized = value.toLowerCase().replaceAll('u:', 'v');

  for (final rune in normalized.runes) {
    if (_isPinyinInputRune(rune)) {
      current.add(String.fromCharCode(rune));
      continue;
    }
    if (rune >= 0x30 && rune <= 0x39) {
      if (current.isEmpty || rune > 0x35) return null;
      final pattern = _pinyinPattern(current.join());
      if (pattern.base.isEmpty || _toneVowelIndex(pattern.base) == null) {
        return null;
      }
      final tone = rune == 0x30 ? 5 : rune - 0x30;
      final writtenTone =
          pattern.tones.isEmpty ? null : pattern.tones.values.first;
      if (writtenTone != null && writtenTone != tone) return null;
      syllables.add(_NumberedPinyinSyllable(pattern.base, tone));
      current.clear();
      continue;
    }
    if (rune == 0x20 ||
        rune == 0x09 ||
        rune == 0x27 ||
        rune == 0x2019 ||
        rune == 0x2d) {
      if (current.isNotEmpty) return null;
      continue;
    }
    return null;
  }

  if (current.isNotEmpty || syllables.isEmpty) return null;

  final base = StringBuffer();
  final tones = <int, int>{};
  var offset = 0;
  for (final syllable in syllables) {
    final toneIndex = _toneVowelIndex(syllable.base);
    if (toneIndex == null) return null;
    base.write(syllable.base);
    tones[offset + toneIndex] = syllable.tone;
    offset += syllable.base.length;
  }
  return _PinyinPattern(base.toString(), tones);
}

int? _toneVowelIndex(String syllable) {
  final runes = syllable.runes.toList();
  int? find(int rune) {
    final index = runes.indexOf(rune);
    return index < 0 ? null : index;
  }

  return find(0x61) ?? // a
      find(0x65) ?? // e
      (syllable.contains('ou') ? find(0x6f) : null) ?? // ou
      (() {
        for (var index = runes.length - 1; index >= 0; index--) {
          if ('iouv'.runes.contains(runes[index])) return index;
        }
        return null;
      })();
}

bool _matchesPinyin(_PinyinPattern entry, _PinyinPattern query) {
  if (query.base.isEmpty) return false;
  var start = entry.base.indexOf(query.base);
  while (start >= 0) {
    final matchesTones = query.tones.entries.every((tone) {
      final entryTone = entry.tones[start + tone.key];
      return tone.value == 5 ? entryTone == null : entryTone == tone.value;
    });
    if (matchesTones) return true;
    start = entry.base.indexOf(query.base, start + 1);
  }
  return false;
}

/// Reviewed corpus links, separate from the general lookup dictionary.
class UsageDictionary {
  final Language language;
  final Map<String, dynamic> sources;
  final List<Map<String, dynamic>> entries;
  final List<Map<String, dynamic>> occurrences;
  final List<Map<String, dynamic>> readingBindings;
  late final Map<String, Map<String, dynamic>> _entriesById = {
    for (final entry in entries) entry['id'] as String: entry,
  };
  late final Map<String, Map<String, dynamic>> _occurrencesById = {
    for (final use in occurrences) use['id'] as String: use,
  };
  late final Map<(String, int), Map<String, dynamic>> _segments = {
    for (final use in occurrences.where((use) => use['layer'] != 'expression'))
      (use['source'] as String, use['segment_index'] as int): use,
  };
  late final Map<(String, int), Map<String, dynamic>> _readingBindings = {
    for (final binding in readingBindings)
      (binding['source'] as String, binding['segment_index'] as int): binding,
  };

  UsageDictionary.fromJson(Map<String, dynamic> json)
      : language = json['language'] == 'japanese'
            ? Language.japanese
            : Language.chinese,
        sources = Map<String, dynamic>.from(json['sources'] as Map),
        entries = (json['entries'] as List).cast<Map<String, dynamic>>(),
        occurrences =
            (json['occurrences'] as List).cast<Map<String, dynamic>>(),
        readingBindings = (json['reading_bindings'] as List? ?? const [])
            .cast<Map<String, dynamic>>();

  static final _pending = <Language, Future<UsageDictionary>>{};
  static final _cached = <Language, UsageDictionary>{};
  static Future<UsageDictionary> load({Language language = Language.chinese}) =>
      _cached.containsKey(language)
          ? Future.value(_cached[language])
          : _pending.putIfAbsent(language, () => _load(language));

  static Future<UsageDictionary> _load(Language language) async {
    try {
      return _cached[language] = UsageDictionary.fromJson(jsonDecode(
        await rootBundle.loadString(language == Language.japanese
            ? 'assets/usage_dictionary_ja.json'
            : 'assets/usage_dictionary.json'),
      ) as Map<String, dynamic>);
    } catch (_) {
      _pending.remove(language);
      rethrow;
    }
  }

  Map<String, dynamic>? occurrence(String source, int index, String text,
      {int? startOffset, String? surface, String? reading, String? gloss}) {
    if (sources[source]?['text'] != text) return null;
    if (surface != null) {
      final binding = _readingBindings[(source, index)];
      if (binding != null && binding['surface'] == surface) {
        if (reading != null && binding['reading'] != reading) return null;
        if (gloss != null && binding['gloss'] != gloss) return null;
        if (startOffset != null &&
            String.fromCharCodes(text.runes.take(binding['start'] as int))
                    .length !=
                startOffset) {
          return null;
        }
        return _occurrencesById[binding['occurrence_id']];
      }
    }
    final use = _segments[(source, index)];
    if (use == null) return null;
    if (surface != null && use['surface'] != surface) return null;
    if (reading != null && use['reading'] != reading) return null;
    if (gloss != null && use['gloss'] != gloss) return null;
    if (startOffset != null &&
        String.fromCharCodes(text.runes.take(use['start'] as int)).length !=
            startOffset) {
      return null;
    }
    return use;
  }

  Map<String, dynamic> entry(String id) {
    final seen = <String>{};
    var target = id;
    while (seen.add(target)) {
      final result = _entriesById[target];
      if (result == null) throw StateError('Unknown dictionary entry: $target');
      if (result['superseded_by'] == null) return result;
      target = result['superseded_by'] as String;
    }
    throw StateError('Cyclic dictionary aliases');
  }

  List<Map<String, dynamic>> expressionsFor(String baseId) =>
      entries.where((e) => e['base_entry_id'] == baseId).toList();

  List<Map<String, dynamic>> search(String query) {
    final q = query.trim().toLowerCase();
    final searchable = entries
        .where((e) => !isGrammarOnly(e) && e['superseded_by'] == null)
        .toList();
    if (q.isEmpty) return searchable;
    if (language == Language.japanese) {
      String kana(String value) => String.fromCharCodes(
          value.runes.map((r) => r >= 0x30a1 && r <= 0x30f6 ? r - 0x60 : r));
      final normalized = kana(q);
      return searchable.where((entry) {
        final definitions = (entry['senses'] as List)
            .map((sense) => sense['definition'])
            .join(' ');
        return kana('${entry['headword']} ${entry['reading']} $definitions'
                .toLowerCase())
            .contains(normalized);
      }).toList();
    }

    final numberedPinyin = _numberedPinyinPattern(q);
    final hasDigits = RegExp(r'\d').hasMatch(q);
    final pinyinQuery = numberedPinyin ?? _pinyinPattern(q);
    return entries.where((entry) {
      final definitions =
          (entry['senses'] as List).map((s) => s['definition']).join(' ');
      if ('${entry['headword']} $definitions'.toLowerCase().contains(q)) {
        return true;
      }

      final readings = <String>{
        entry['reading'] as String,
        ...(entry['observed_readings'] as List? ?? const []).cast<String>(),
      }.map(_pinyinPattern);
      if (numberedPinyin != null) {
        return readings
            .any((reading) => _matchesPinyin(reading, numberedPinyin));
      }
      if (hasDigits || pinyinQuery.base.isEmpty) return false;
      return readings.any((reading) => _matchesPinyin(reading, pinyinQuery));
    }).toList();
  }

  bool isGrammarOnly(Map<String, dynamic> entry) =>
      language == Language.japanese &&
      (entry['kind'] == 'particle' ||
          entry['kind'] == 'construction' ||
          entry['headword'] == 'です' ||
          entry['headword'] == 'か');
}
