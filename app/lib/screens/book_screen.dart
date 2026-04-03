import 'package:flutter/material.dart';
import '../models.dart';
import '../theme.dart';
import '../services/progress_service.dart';
import 'reader_screen.dart';

class BookScreen extends StatefulWidget {
  final Reader reader;

  const BookScreen({super.key, required this.reader});

  @override
  State<BookScreen> createState() => _BookScreenState();
}

class _BookScreenState extends State<BookScreen> {
  int _savedChapter = 0;

  @override
  void initState() {
    super.initState();
    _loadProgress();
  }

  Future<void> _loadProgress() async {
    final ch = await ProgressService.instance.getChapter(widget.reader.id);
    if (!mounted) return;
    setState(() => _savedChapter = ch);
  }

  void _openReader(int chapter) {
    Navigator.push(
      context,
      PageRouteBuilder(
        pageBuilder: (_, __, ___) => ReaderScreen(
          reader: widget.reader,
          initialChapter: chapter,
        ),
        transitionDuration: Duration.zero,
        reverseTransitionDuration: Duration.zero,
      ),
    ).then((_) => _loadProgress());
  }

  @override
  Widget build(BuildContext context) {
    final reader = widget.reader;
    final levelColor = AppTheme.levelColor(reader.level, reader.language);

    return Scaffold(
      appBar: AppBar(
        title: Text('${reader.bookTitle} · ${reader.levelLabel}'),
      ),
      body: Column(
        children: [
          // Continue reading banner
          if (_savedChapter > 0 && _savedChapter < reader.chapters.length)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
              child: Card(
                color: levelColor.withValues(alpha: 0.08),
                child: ListTile(
                  leading: Icon(Icons.play_circle_fill,
                      color: levelColor, size: 32),
                  title: const Text('Continue reading',
                      style: TextStyle(fontWeight: FontWeight.w600)),
                  subtitle: Text(
                    'Chapter ${_savedChapter + 1}: ${reader.chapters[_savedChapter].title}',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                  trailing: Icon(Icons.chevron_right, color: levelColor),
                  onTap: () => _openReader(_savedChapter),
                ),
              ),
            ),
          // Chapter list
          Expanded(
            child: ListView.builder(
              padding: const EdgeInsets.all(16),
              itemCount: reader.chapters.length,
              itemBuilder: (context, index) {
                final chapter = reader.chapters[index];
                final isRead = index < _savedChapter;
                final isCurrent = index == _savedChapter;

                return Card(
                  margin: const EdgeInsets.only(bottom: 8),
                  child: ListTile(
                    contentPadding: const EdgeInsets.symmetric(
                      horizontal: 16,
                      vertical: 4,
                    ),
                    leading: Container(
                      width: 40,
                      height: 40,
                      decoration: BoxDecoration(
                        color: isCurrent
                            ? levelColor.withValues(alpha: 0.3)
                            : levelColor.withValues(alpha: 0.12),
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: Center(
                        child: isRead
                            ? Icon(Icons.check, color: levelColor, size: 20)
                            : Text(
                                '${index + 1}',
                                style: TextStyle(
                                  color: levelColor,
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                      ),
                    ),
                    title: Text(
                      chapter.title,
                      style: TextStyle(
                        fontWeight:
                            isCurrent ? FontWeight.bold : FontWeight.w600,
                      ),
                    ),
                    trailing: Icon(Icons.chevron_right,
                        size: 20, color: Colors.grey[400]),
                    onTap: () => _openReader(index),
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}
