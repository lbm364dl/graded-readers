import 'package:flutter/material.dart';
import '../data.dart';
import '../main.dart';
import '../models.dart';
import '../theme.dart';
import '../services/progress_service.dart';
import 'book_screen.dart';
import 'reader_screen.dart';
import 'vocabulary_screen.dart';

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
    final langNotifier = LanguageScope.of(context);
    final language = langNotifier.value;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Graded Readers'),
        actions: [
          _LanguageToggle(
            language: language,
            onChanged: (lang) => langNotifier.switchTo(lang),
          ),
        ],
      ),
      body: _selectedTab == 2
          ? const VocabularyScreen()
          : FutureBuilder<List<Book>>(
              future: widget.repo.loadBooks(language),
              builder: (context, snapshot) {
                if (!snapshot.hasData) {
                  return const Center(child: CircularProgressIndicator());
                }
                final books = snapshot.data!;
                return _selectedTab == 0
                    ? _buildLibrary(books, language)
                    : _buildByLevel(books, language);
              },
            ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _selectedTab,
        onDestinationSelected: (i) => setState(() => _selectedTab = i),
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.library_books_outlined),
            selectedIcon: Icon(Icons.library_books),
            label: 'Library',
          ),
          NavigationDestination(
            icon: Icon(Icons.bar_chart_outlined),
            selectedIcon: Icon(Icons.bar_chart),
            label: 'By Level',
          ),
          NavigationDestination(
            icon: Icon(Icons.bookmark_border_outlined),
            selectedIcon: Icon(Icons.bookmark),
            label: 'Vocabulary',
          ),
        ],
      ),
    );
  }

  Widget _buildLibrary(List<Book> books, Language language) {
    final mainBooks = books.where((b) => b.key != 'readers').toList();
    final standaloneReaders =
        books.where((b) => b.key == 'readers').toList();

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        _ContinueReadingCard(
          repo: widget.repo,
          language: language,
          onBrowse: (book, lang) => _openBook(context, book, lang),
        ),
        ...mainBooks.map((book) => _BookCard(
              book: book,
              language: language,
              onTap: () => _openBook(context, book, language),
            )),
        if (standaloneReaders.isNotEmpty) ...[
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 12),
            child: Text(
              'Short Readers',
              style: TextStyle(
                fontSize: 18,
                fontWeight: FontWeight.bold,
              ),
            ),
          ),
          ...standaloneReaders.map((book) => _BookCard(
                book: book,
                language: language,
                onTap: () => _openBook(context, book, language),
              )),
        ],
      ],
    );
  }

  Widget _buildByLevel(List<Book> books, Language language) {
    final maxLevel = language == Language.chinese ? 6 : 5;

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        for (int level = 1; level <= maxLevel; level++) ...[
          _LevelHeader(level: level, language: language),
          ...books
              .where((b) => b.levels.containsKey(level))
              .map((book) => _LevelBookTile(
                    book: book,
                    level: level,
                    language: language,
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

  void _openBook(BuildContext context, Book book, Language language) {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => BookOverviewScreen(
          book: book,
          repo: widget.repo,
          language: language,
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------

class _LanguageToggle extends StatelessWidget {
  final Language language;
  final ValueChanged<Language> onChanged;

  const _LanguageToggle({required this.language, required this.onChanged});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(right: 8),
      child: Container(
        decoration: BoxDecoration(
          color: Colors.white.withValues(alpha: 0.15),
          borderRadius: BorderRadius.circular(20),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            _langChip('中文', Language.chinese),
            _langChip('日本語', Language.japanese),
          ],
        ),
      ),
    );
  }

  Widget _langChip(String label, Language value) {
    final selected = language == value;
    return GestureDetector(
      onTap: () => onChanged(value),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
        decoration: BoxDecoration(
          color: selected ? Colors.white : Colors.transparent,
          borderRadius: BorderRadius.circular(20),
        ),
        child: Text(
          label,
          style: TextStyle(
            fontSize: 13,
            fontWeight: FontWeight.w600,
            color: selected ? AppTheme.primary : Colors.white70,
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------

class _BookCard extends StatelessWidget {
  final Book book;
  final Language language;
  final VoidCallback onTap;

  const _BookCard({
    required this.book,
    required this.language,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
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
                width: 56,
                height: 72,
                decoration: BoxDecoration(
                  color: AppTheme.primary.withValues(alpha: 0.08),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Center(
                  child: Text(
                    book.title.characters.first,
                    style: TextStyle(
                      fontSize: 28,
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
                        fontSize: 18,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      book.titleEn,
                      style: TextStyle(
                        fontSize: 13,
                        color: Colors.grey[600],
                      ),
                    ),
                  ],
                ),
              ),
              Icon(Icons.chevron_right, color: Colors.grey[400]),
            ],
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------

class _LevelHeader extends StatelessWidget {
  final int level;
  final Language language;

  const _LevelHeader({required this.level, required this.language});

  @override
  Widget build(BuildContext context) {
    final color = AppTheme.levelColor(level, language);

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.15),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        children: [
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
            decoration: BoxDecoration(
              color: color,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Text(
              _levelTag(level),
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

  String _levelTag(int level) {
    if (language == Language.japanese) {
      const labels = {1: 'N5', 2: 'N4', 3: 'N3', 4: 'N2', 5: 'N1'};
      return 'JLPT ${labels[level] ?? level}';
    }
    return 'HSK $level';
  }

  String _levelDescription(int level) {
    if (language == Language.japanese) {
      switch (level) {
        case 1:
          return 'Beginner (~800 words)';
        case 2:
          return 'Elementary (~1,500 words)';
        case 3:
          return 'Intermediate (~3,750 words)';
        case 4:
          return 'Upper Intermediate (~6,000 words)';
        case 5:
          return 'Advanced (~10,000 words)';
        default:
          return '';
      }
    }
    switch (level) {
      case 1:
        return 'Beginner (~500 words)';
      case 2:
        return 'Elementary (~1,200 words)';
      case 3:
        return 'Intermediate (~2,200 words)';
      case 4:
        return 'Upper Intermediate (~3,200 words)';
      case 5:
        return 'Advanced (~4,200 words)';
      case 6:
        return 'Proficient (~5,400 words)';
      default:
        return '';
    }
  }
}

// ---------------------------------------------------------------------------

class _LevelBookTile extends StatelessWidget {
  final Book book;
  final int level;
  final Language language;
  final VoidCallback onTap;

  const _LevelBookTile({
    required this.book,
    required this.level,
    required this.language,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final reader = book.levels[level]!;
    return ListTile(
      leading: CircleAvatar(
        backgroundColor:
            AppTheme.levelColor(level, language).withValues(alpha: 0.2),
        child: Text(
          book.title.characters.first,
          style: TextStyle(color: AppTheme.levelColor(level, language)),
        ),
      ),
      title: Text(book.title),
      subtitle: Text('${reader.chapters.length} chapters'),
      trailing: const Icon(Icons.chevron_right, size: 20),
      onTap: onTap,
    );
  }
}

// ---------------------------------------------------------------------------

class BookOverviewScreen extends StatelessWidget {
  final Book book;
  final ContentRepository repo;
  final Language language;

  const BookOverviewScreen({
    super.key,
    required this.book,
    required this.repo,
    required this.language,
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
            'Select difficulty level:',
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
                    color: AppTheme.levelColor(level, language),
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: Center(
                    child: Text(
                      reader.levelLabel.replaceFirst(' ', '\n'),
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                        color: Colors.white,
                        fontWeight: FontWeight.bold,
                        fontSize: 12,
                        height: 1.2,
                      ),
                    ),
                  ),
                ),
                title: Text('${reader.levelLabel} version'),
                subtitle: Text(
                  '${reader.chapters.length} chapters · ${_formatCharCount(totalChars)} chars',
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
    if (count >= 1000) {
      return '${(count / 1000).toStringAsFixed(1)}k';
    }
    return '$count';
  }
}

// ---------------------------------------------------------------------------

class _ContinueReadingCard extends StatefulWidget {
  final ContentRepository repo;
  final Language language;
  final void Function(Book book, Language language)? onBrowse;

  const _ContinueReadingCard({
    required this.repo,
    required this.language,
    this.onBrowse,
  });

  @override
  State<_ContinueReadingCard> createState() => _ContinueReadingCardState();
}

class _ContinueReadingCardState extends State<_ContinueReadingCard>
    with WidgetsBindingObserver {
  ReadingProgress? _progress;
  Reader? _reader;
  Book? _book;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _load();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _load();
  }

  @override
  void didUpdateWidget(_ContinueReadingCard old) {
    super.didUpdateWidget(old);
    _load();
  }

  Future<void> _load() async {
    final progress = await ProgressService.instance.getLastRead();
    if (!mounted) return;
    if (progress == null) {
      setState(() {
        _progress = null;
        _reader = null;
        _book = null;
      });
      return;
    }

    // Find the matching reader and book
    final readers = await widget.repo.loadReaders(widget.language);
    final reader = readers.cast<Reader?>().firstWhere(
          (r) => r!.id == progress.readerId,
          orElse: () => null,
        );

    Book? book;
    if (reader != null) {
      final books = await widget.repo.loadBooks(widget.language);
      book = books.cast<Book?>().firstWhere(
            (b) => b!.key == reader.book,
            orElse: () => null,
          );
    }

    if (!mounted) return;
    setState(() {
      if (reader != null) {
        _progress = progress;
        _reader = reader;
        _book = book;
      } else {
        _progress = null;
        _reader = null;
        _book = null;
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    if (_progress == null || _reader == null) return const SizedBox.shrink();
    final p = _progress!;
    final r = _reader!;

    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: Card(
        color: AppTheme.primary.withValues(alpha: 0.06),
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: () {
            Navigator.push(
              context,
              MaterialPageRoute(
                builder: (_) => ReaderScreen(
                  reader: r,
                  initialChapter: p.chapter,
                ),
              ),
            ).then((_) => _load());
          },
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Row(
              children: [
                Icon(Icons.play_circle_fill,
                    color: AppTheme.primary, size: 36),
                const SizedBox(width: 14),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text(
                        'Continue Reading',
                        style: TextStyle(
                          fontSize: 12,
                          fontWeight: FontWeight.w600,
                          color: AppTheme.primary,
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        '${p.bookTitle} · ${p.levelLabel}',
                        style: const TextStyle(
                          fontSize: 15,
                          fontWeight: FontWeight.bold,
                        ),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                      const SizedBox(height: 4),
                      // Progress bar
                      ClipRRect(
                        borderRadius: BorderRadius.circular(4),
                        child: LinearProgressIndicator(
                          value: p.progress,
                          backgroundColor:
                              AppTheme.primary.withValues(alpha: 0.12),
                          color: AppTheme.primary,
                          minHeight: 4,
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        'Chapter ${p.chapter + 1} of ${p.totalChapters}',
                        style: TextStyle(
                          fontSize: 11,
                          color: Colors.grey[600],
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 8),
                Column(
                  children: [
                    Icon(Icons.chevron_right, color: Colors.grey[400]),
                    if (_book != null && widget.onBrowse != null)
                      const SizedBox(height: 4),
                    if (_book != null && widget.onBrowse != null)
                      GestureDetector(
                        onTap: () {
                          widget.onBrowse!(_book!, widget.language);
                        },
                        child: Text(
                          'Browse',
                          style: TextStyle(
                            fontSize: 11,
                            color: AppTheme.primary,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
