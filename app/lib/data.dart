import 'dart:convert';
import 'package:flutter/services.dart';
import 'models.dart';

class ContentRepository {
  List<Reader>? _readers;
  List<Book>? _books;

  Future<List<Reader>> loadReaders() async {
    if (_readers != null) return _readers!;

    final jsonStr = await rootBundle.loadString('assets/content.json');
    final List<dynamic> jsonList = json.decode(jsonStr);
    _readers = jsonList.map((j) => Reader.fromJson(j)).toList();
    return _readers!;
  }

  Future<List<Book>> loadBooks() async {
    if (_books != null) return _books!;

    final readers = await loadReaders();
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

    _books = bookMap.values.toList();
    return _books!;
  }

  Future<List<Reader>> getReadersForLevel(int level) async {
    final readers = await loadReaders();
    return readers.where((r) => r.level == level).toList();
  }

  Future<List<Reader>> getReadersForBook(String bookKey) async {
    final readers = await loadReaders();
    return readers.where((r) => r.book == bookKey).toList()
      ..sort((a, b) => a.level.compareTo(b.level));
  }
}
