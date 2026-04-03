import 'dart:convert';
import 'package:shared_preferences/shared_preferences.dart';

class ReadingProgress {
  final String readerId;
  final String bookTitle;
  final String bookTitleEn;
  final String levelLabel;
  final int chapter;
  final int totalChapters;
  final double scrollFraction;
  final DateTime lastRead;

  ReadingProgress({
    required this.readerId,
    required this.bookTitle,
    required this.bookTitleEn,
    required this.levelLabel,
    required this.chapter,
    required this.totalChapters,
    this.scrollFraction = 0.0,
    required this.lastRead,
  });

  double get progress =>
      totalChapters > 0 ? (chapter + 1) / totalChapters : 0;

  Map<String, dynamic> toJson() => {
        'readerId': readerId,
        'bookTitle': bookTitle,
        'bookTitleEn': bookTitleEn,
        'levelLabel': levelLabel,
        'chapter': chapter,
        'totalChapters': totalChapters,
        'scrollFraction': scrollFraction,
        'lastRead': lastRead.toIso8601String(),
      };

  factory ReadingProgress.fromJson(Map<String, dynamic> json) =>
      ReadingProgress(
        readerId: json['readerId'] as String,
        bookTitle: json['bookTitle'] as String,
        bookTitleEn: json['bookTitleEn'] as String,
        levelLabel: json['levelLabel'] as String,
        chapter: json['chapter'] as int,
        totalChapters: json['totalChapters'] as int,
        scrollFraction: (json['scrollFraction'] as num?)?.toDouble() ?? 0.0,
        lastRead: DateTime.parse(json['lastRead'] as String),
      );
}

class ProgressService {
  ProgressService._();
  static final ProgressService instance = ProgressService._();

  static const _progressPrefix = 'progress_';
  static const _lastReadKey = 'last_read_id';

  Future<void> saveProgress(ReadingProgress progress) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
      '$_progressPrefix${progress.readerId}',
      json.encode(progress.toJson()),
    );
    await prefs.setString(_lastReadKey, progress.readerId);
  }

  Future<ReadingProgress?> getProgress(String readerId) async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString('$_progressPrefix$readerId');
    if (raw == null) return null;
    return ReadingProgress.fromJson(
        json.decode(raw) as Map<String, dynamic>);
  }

  Future<int> getChapter(String readerId) async {
    final progress = await getProgress(readerId);
    return progress?.chapter ?? 0;
  }

  Future<ReadingProgress?> getLastRead() async {
    final prefs = await SharedPreferences.getInstance();
    final lastId = prefs.getString(_lastReadKey);
    if (lastId == null) return null;
    return getProgress(lastId);
  }
}
