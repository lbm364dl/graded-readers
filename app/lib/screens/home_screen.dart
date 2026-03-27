import 'package:flutter/material.dart';
import '../data.dart';
import '../models.dart';
import '../theme.dart';
import 'book_screen.dart';

class HomeScreen extends StatefulWidget {
  final ContentRepository repo;

  const HomeScreen({super.key, required this.repo});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  int _selectedTab = 0;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('HSK 分级阅读'),
      ),
      body: FutureBuilder<List<Book>>(
        future: widget.repo.loadBooks(),
        builder: (context, snapshot) {
          if (!snapshot.hasData) {
            return const Center(child: CircularProgressIndicator());
          }
          final books = snapshot.data!;
          if (_selectedTab == 0) {
            return _buildLibrary(books);
          } else {
            return _buildByLevel(books);
          }
        },
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _selectedTab,
        onDestinationSelected: (i) => setState(() => _selectedTab = i),
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.library_books_outlined),
            selectedIcon: Icon(Icons.library_books),
            label: '书库',
          ),
          NavigationDestination(
            icon: Icon(Icons.bar_chart_outlined),
            selectedIcon: Icon(Icons.bar_chart),
            label: '按级别',
          ),
        ],
      ),
    );
  }

  Widget _buildLibrary(List<Book> books) {
    // Separate main books from standalone readers
    final mainBooks = books.where((b) => b.key != 'readers').toList();
    final standaloneReaders =
        books.where((b) => b.key == 'readers').toList();

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        ...mainBooks.map((book) => _BookCard(
              book: book,
              onTap: () => _openBook(context, book),
            )),
        if (standaloneReaders.isNotEmpty) ...[
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 12),
            child: Text(
              '独立读物',
              style: TextStyle(
                fontSize: 18,
                fontWeight: FontWeight.bold,
              ),
            ),
          ),
          ...standaloneReaders.map((book) => _BookCard(
                book: book,
                onTap: () => _openBook(context, book),
              )),
        ],
      ],
    );
  }

  Widget _buildByLevel(List<Book> books) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        for (int level = 1; level <= 6; level++) ...[
          _LevelHeader(level: level),
          ...books
              .where((b) => b.levels.containsKey(level))
              .map((book) => _LevelBookTile(
                    book: book,
                    level: level,
                    onTap: () {
                      final reader = book.levels[level]!;
                      Navigator.push(
                        context,
                        MaterialPageRoute(
                          builder: (_) => BookScreen(reader: reader),
                        ),
                      );
                    },
                  )),
          const SizedBox(height: 16),
        ],
      ],
    );
  }

  void _openBook(BuildContext context, Book book) {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => BookOverviewScreen(
          book: book,
          repo: widget.repo,
        ),
      ),
    );
  }
}

class _BookCard extends StatelessWidget {
  final Book book;
  final VoidCallback onTap;

  const _BookCard({required this.book, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final levels = book.levels.keys.toList()..sort();
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(12),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Row(
            children: [
              Container(
                width: 60,
                height: 80,
                decoration: BoxDecoration(
                  color: AppTheme.primary.withValues(alpha: 0.1),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Center(
                  child: Text(
                    book.title.characters.first,
                    style: TextStyle(
                      fontSize: 32,
                      color: AppTheme.primary,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      book.title,
                      style: const TextStyle(
                        fontSize: 20,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      book.titleEn,
                      style: TextStyle(
                        fontSize: 14,
                        color: Colors.grey[600],
                      ),
                    ),
                    const SizedBox(height: 8),
                    Wrap(
                      spacing: 4,
                      children: levels.map((l) {
                        return Container(
                          padding: const EdgeInsets.symmetric(
                            horizontal: 8,
                            vertical: 2,
                          ),
                          decoration: BoxDecoration(
                            color: AppTheme.levelColor(l),
                            borderRadius: BorderRadius.circular(10),
                          ),
                          child: Text(
                            'HSK$l',
                            style: const TextStyle(
                              color: Colors.white,
                              fontSize: 11,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        );
                      }).toList(),
                    ),
                  ],
                ),
              ),
              const Icon(Icons.chevron_right),
            ],
          ),
        ),
      ),
    );
  }
}

class _LevelHeader extends StatelessWidget {
  final int level;

  const _LevelHeader({required this.level});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: AppTheme.levelColor(level).withValues(alpha: 0.15),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        children: [
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
            decoration: BoxDecoration(
              color: AppTheme.levelColor(level),
              borderRadius: BorderRadius.circular(12),
            ),
            child: Text(
              'HSK $level',
              style: const TextStyle(
                color: Colors.white,
                fontWeight: FontWeight.bold,
              ),
            ),
          ),
          const SizedBox(width: 12),
          Text(
            _levelDescription(level),
            style: TextStyle(
              color: Colors.grey[700],
              fontSize: 13,
            ),
          ),
        ],
      ),
    );
  }

  String _levelDescription(int level) {
    switch (level) {
      case 1:
        return '入门 (~500词)';
      case 2:
        return '基础 (~1200词)';
      case 3:
        return '进阶 (~2200词)';
      case 4:
        return '中级 (~3200词)';
      case 5:
        return '高级 (~4200词)';
      case 6:
        return '精通 (~5400词)';
      default:
        return '';
    }
  }
}

class _LevelBookTile extends StatelessWidget {
  final Book book;
  final int level;
  final VoidCallback onTap;

  const _LevelBookTile({
    required this.book,
    required this.level,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final reader = book.levels[level]!;
    return ListTile(
      leading: CircleAvatar(
        backgroundColor: AppTheme.levelColor(level).withValues(alpha: 0.2),
        child: Text(
          book.title.characters.first,
          style: TextStyle(color: AppTheme.levelColor(level)),
        ),
      ),
      title: Text(book.title),
      subtitle: Text('${reader.chapters.length} 章节'),
      trailing: const Icon(Icons.chevron_right, size: 20),
      onTap: onTap,
    );
  }
}

class BookOverviewScreen extends StatelessWidget {
  final Book book;
  final ContentRepository repo;

  const BookOverviewScreen({
    super.key,
    required this.book,
    required this.repo,
  });

  @override
  Widget build(BuildContext context) {
    final levels = book.levels.keys.toList()..sort();

    return Scaffold(
      appBar: AppBar(
        title: Text(book.title),
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text(
            book.titleEn,
            style: TextStyle(
              fontSize: 16,
              color: Colors.grey[600],
            ),
          ),
          const SizedBox(height: 16),
          const Text(
            '选择难度级别：',
            style: TextStyle(
              fontSize: 16,
              fontWeight: FontWeight.w500,
            ),
          ),
          const SizedBox(height: 12),
          ...levels.map((level) {
            final reader = book.levels[level]!;
            final totalChars = reader.chapters
                .fold<int>(0, (sum, ch) => sum + ch.content.length);
            return Card(
              margin: const EdgeInsets.only(bottom: 8),
              child: ListTile(
                leading: Container(
                  width: 50,
                  height: 50,
                  decoration: BoxDecoration(
                    color: AppTheme.levelColor(level),
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: Center(
                    child: Text(
                      'HSK\n$level',
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                        color: Colors.white,
                        fontWeight: FontWeight.bold,
                        fontSize: 13,
                        height: 1.2,
                      ),
                    ),
                  ),
                ),
                title: Text('HSK $level 版本'),
                subtitle: Text(
                  '${reader.chapters.length} 章节 · ${_formatCharCount(totalChars)}字',
                ),
                trailing: const Icon(Icons.chevron_right),
                onTap: () {
                  Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) => BookScreen(reader: reader),
                    ),
                  );
                },
              ),
            );
          }),
        ],
      ),
    );
  }

  String _formatCharCount(int count) {
    if (count >= 10000) {
      return '${(count / 10000).toStringAsFixed(1)}万';
    } else if (count >= 1000) {
      return '${(count / 1000).toStringAsFixed(1)}千';
    }
    return '$count';
  }
}
