import 'dart:convert';

import 'package:sqlite3/common.dart';

import '../models.dart';
import 'lexicon_store_base.dart';

abstract class SqlLexiconStore implements LexiconStore {
  CommonDatabase? database;

  @override
  bool get isReady => database != null;

  String languageCode(Language language) =>
      language == Language.chinese ? 'zh' : 'ja';

  @override
  List<String> words(Language language) {
    final rows = database!.select(
      'SELECT lookup_key FROM dictionary WHERE language = ?',
      [languageCode(language)],
    );
    return [for (final row in rows) row['lookup_key'] as String];
  }

  @override
  Map<String, dynamic>? dictionaryEntry(Language language, String word) {
    final rows = database!.select(
      'SELECT word, reading, definitions, part_of_speech, level, is_alias '
      'FROM dictionary WHERE language = ? AND lookup_key = ? LIMIT 1',
      [languageCode(language), word],
    );
    if (rows.isEmpty) return null;
    final row = rows.first;
    return {
      'w': row['word'] as String,
      'p': row['reading'] as String,
      'd': jsonDecode(row['definitions'] as String),
      'pos': jsonDecode(row['part_of_speech'] as String),
      'a': (row['is_alias'] as int) == 1,
      if (row['level'] != null) 'l': row['level'] as int,
    };
  }

  @override
  int? characterLevel(String character) {
    final rows = database!.select(
      'SELECT level FROM character_level WHERE character = ? LIMIT 1',
      [character],
    );
    return rows.isEmpty ? null : rows.first['level'] as int;
  }

  Map<String, dynamic>? _payload(String table, String character) {
    final rows = database!.select(
      'SELECT payload FROM $table WHERE character = ? LIMIT 1',
      [character],
    );
    if (rows.isEmpty) return null;
    return jsonDecode(rows.first['payload'] as String) as Map<String, dynamic>;
  }

  @override
  Map<String, dynamic>? etymologyEntry(String character) =>
      _payload('etymology', character);

  @override
  Map<String, dynamic>? glyphEntry(String character) =>
      _payload('glyph', character);
}
