enum Language { chinese, japanese }

class Chapter {
  final String title;
  final String content;

  Chapter({required this.title, required this.content});

  factory Chapter.fromJson(Map<String, dynamic> json) {
    return Chapter(
      title: json['title'] as String,
      content: json['content'] as String,
    );
  }
}

class Reader {
  final String id;
  final String book;
  final String bookTitle;
  final String bookTitleEn;
  final int level;
  final Language language;
  final List<Chapter> chapters;

  Reader({
    required this.id,
    required this.book,
    required this.bookTitle,
    required this.bookTitleEn,
    required this.level,
    required this.language,
    required this.chapters,
  });

  factory Reader.fromJson(Map<String, dynamic> json, Language language) {
    return Reader(
      id: json['id'] as String,
      book: json['book'] as String,
      bookTitle: json['bookTitle'] as String,
      bookTitleEn: json['bookTitleEn'] as String,
      level: json['level'] as int,
      language: language,
      chapters: (json['chapters'] as List)
          .map((c) => Chapter.fromJson(c as Map<String, dynamic>))
          .toList(),
    );
  }

  String get levelLabel {
    switch (language) {
      case Language.chinese:
        return 'HSK $level';
      case Language.japanese:
        const jlptLabels = {1: 'N5', 2: 'N4', 3: 'N3', 4: 'N2', 5: 'N1'};
        return 'JLPT ${jlptLabels[level] ?? level}';
    }
  }

  int get maxLevel => language == Language.chinese ? 6 : 5;
}

class Book {
  final String key;
  final String title;
  final String titleEn;
  final Map<int, Reader> levels;

  Book({
    required this.key,
    required this.title,
    required this.titleEn,
    required this.levels,
  });
}
