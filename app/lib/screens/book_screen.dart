import 'package:flutter/material.dart';
import '../models.dart';
import '../theme.dart';
import 'reader_screen.dart';

class BookScreen extends StatelessWidget {
  final Reader reader;

  const BookScreen({super.key, required this.reader});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('${reader.bookTitle} · ${reader.levelLabel}'),
      ),
      body: ListView.builder(
        padding: const EdgeInsets.all(16),
        itemCount: reader.chapters.length,
        itemBuilder: (context, index) {
          final chapter = reader.chapters[index];
          final preview = chapter.content.length > 80
              ? '${chapter.content.substring(0, 80)}...'
              : chapter.content;

          return Card(
            margin: const EdgeInsets.only(bottom: 8),
            child: ListTile(
              contentPadding: const EdgeInsets.symmetric(
                horizontal: 16,
                vertical: 8,
              ),
              leading: Container(
                width: 40,
                height: 40,
                decoration: BoxDecoration(
                  color: AppTheme.levelColor(reader.level)
                      .withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Center(
                  child: Text(
                    '${index + 1}',
                    style: TextStyle(
                      color: AppTheme.levelColor(reader.level),
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
              ),
              title: Text(
                chapter.title,
                style: const TextStyle(fontWeight: FontWeight.w600),
              ),
              subtitle: Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  preview,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    color: Colors.grey[600],
                    fontSize: 13,
                  ),
                ),
              ),
              trailing: const Icon(Icons.chevron_right, size: 20),
              onTap: () {
                Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => ReaderScreen(
                      reader: reader,
                      initialChapter: index,
                    ),
                  ),
                );
              },
            ),
          );
        },
      ),
    );
  }
}
