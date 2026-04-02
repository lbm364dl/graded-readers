import 'dart:convert';
import 'package:flutter/services.dart';
import 'models.dart';

class ContentRepository {
  final Map<Language, List<Reader>> _readers = {};
  final Map<Language, List<Book>> _books = {};

  static const _assetPaths = {
    Language.chinese: 'assets/content.json',
    Language.japanese: 'assets/content_ja.json',
  };

  Future<List<Reader>> loadReaders(Language language) async {
    if (_readers.containsKey(language)) return _readers[language]!;

    final path = _assetPaths[language]!;
    final jsonStr = await rootBundle.loadString(path);
    final List<dynamic> jsonList = json.decode(jsonStr);
    _readers[language] =
        jsonList.map((j) => Reader.fromJson(j, language)).toList();
    return _readers[language]!;
  }

  Future<List<Book>> loadBooks(Language language) async {
    if (_books.containsKey(language)) return _books[language]!;

    final readers = await loadReaders(language);
    final Map<String, Book> bookMap = {};

    for (final reader in readers) {
      if (!bookMap.containsKey(reader.book)) {
        bookMap[reader.book] = Book(
          key: reader.book,
          title: reader.bookTitle,
          titleEn: reader.bookTitleEn,
          levels: {},
        );
      }
      bookMap[reader.book]!.levels[reader.level] = reader;
    }

    _books[language] = bookMap.values.toList();
    return _books[language]!;
  }

  Future<List<Reader>> getReadersForLevel(Language language, int level) async {
    final readers = await loadReaders(language);
    return readers.where((r) => r.level == level).toList();
  }

  Future<List<Reader>> getReadersForBook(
      Language language, String bookKey) async {
    final readers = await loadReaders(language);
    return readers.where((r) => r.book == bookKey).toList()
      ..sort((a, b) => a.level.compareTo(b.level));
  }
}
