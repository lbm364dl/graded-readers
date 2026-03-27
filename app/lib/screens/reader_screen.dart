import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../models.dart';
import '../theme.dart';

class ReaderScreen extends StatefulWidget {
  final Reader reader;
  final int initialChapter;

  const ReaderScreen({
    super.key,
    required this.reader,
    required this.initialChapter,
  });

  @override
  State<ReaderScreen> createState() => _ReaderScreenState();
}

class _ReaderScreenState extends State<ReaderScreen> {
  late int _currentChapter;
  late PageController _pageController;
  double _fontSize = 20.0;
  final double _minFontSize = 14.0;
  final double _maxFontSize = 32.0;

  @override
  void initState() {
    super.initState();
    _currentChapter = widget.initialChapter;
    _pageController = PageController(initialPage: _currentChapter);
    _loadPreferences();
  }

  Future<void> _loadPreferences() async {
    final prefs = await SharedPreferences.getInstance();
    setState(() {
      _fontSize = prefs.getDouble('reader_font_size') ?? 20.0;
    });
  }

  Future<void> _saveFontSize() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble('reader_font_size', _fontSize);
  }

  @override
  void dispose() {
    _pageController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;

    return Scaffold(
      appBar: AppBar(
        title: Text(
          '${_currentChapter + 1}/${widget.reader.chapters.length}',
          style: const TextStyle(fontSize: 16),
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.text_decrease),
            onPressed: _fontSize > _minFontSize
                ? () {
                    setState(() => _fontSize -= 2);
                    _saveFontSize();
                  }
                : null,
          ),
          IconButton(
            icon: const Icon(Icons.text_increase),
            onPressed: _fontSize < _maxFontSize
                ? () {
                    setState(() => _fontSize += 2);
                    _saveFontSize();
                  }
                : null,
          ),
        ],
      ),
      body: PageView.builder(
        controller: _pageController,
        itemCount: widget.reader.chapters.length,
        onPageChanged: (index) {
          setState(() => _currentChapter = index);
        },
        itemBuilder: (context, index) {
          final ch = widget.reader.chapters[index];
          return _ChapterView(
            chapter: ch,
            fontSize: _fontSize,
            isDark: isDark,
            level: widget.reader.level,
          );
        },
      ),
      bottomNavigationBar: _buildBottomBar(),
    );
  }

  Widget _buildBottomBar() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      decoration: BoxDecoration(
        border: Border(
          top: BorderSide(color: Colors.grey[300]!),
        ),
      ),
      child: SafeArea(
        child: Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            TextButton.icon(
              onPressed: _currentChapter > 0
                  ? () {
                      _pageController.previousPage(
                        duration: const Duration(milliseconds: 300),
                        curve: Curves.easeInOut,
                      );
                    }
                  : null,
              icon: const Icon(Icons.arrow_back_ios, size: 16),
              label: const Text('上一章'),
            ),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
              decoration: BoxDecoration(
                color: AppTheme.levelColor(widget.reader.level),
                borderRadius: BorderRadius.circular(12),
              ),
              child: Text(
                widget.reader.levelLabel,
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 12,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ),
            TextButton.icon(
              onPressed:
                  _currentChapter < widget.reader.chapters.length - 1
                      ? () {
                          _pageController.nextPage(
                            duration: const Duration(milliseconds: 300),
                            curve: Curves.easeInOut,
                          );
                        }
                      : null,
              icon: const Icon(Icons.arrow_forward_ios, size: 16),
              label: const Text('下一章'),
            ),
          ],
        ),
      ),
    );
  }
}

class _ChapterView extends StatelessWidget {
  final Chapter chapter;
  final double fontSize;
  final bool isDark;
  final int level;

  const _ChapterView({
    required this.chapter,
    required this.fontSize,
    required this.isDark,
    required this.level,
  });

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Chapter title
          Text(
            chapter.title,
            style: TextStyle(
              fontSize: fontSize + 4,
              fontWeight: FontWeight.bold,
              height: 1.4,
            ),
          ),
          const Divider(height: 24),
          // Chapter content - render with basic markdown support
          _RichContent(
            text: chapter.content,
            fontSize: fontSize,
            isDark: isDark,
          ),
          const SizedBox(height: 60),
        ],
      ),
    );
  }
}

class _RichContent extends StatelessWidget {
  final String text;
  final double fontSize;
  final bool isDark;

  const _RichContent({
    required this.text,
    required this.fontSize,
    required this.isDark,
  });

  @override
  Widget build(BuildContext context) {
    final paragraphs = text.split('\n\n');
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: paragraphs.map((para) {
        final trimmed = para.trim();
        if (trimmed.isEmpty) return const SizedBox.shrink();

        // Bold text (simple ** support)
        if (trimmed.startsWith('**') && trimmed.contains('**')) {
          return Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: Text(
              trimmed.replaceAll('**', ''),
              style: TextStyle(
                fontSize: fontSize - 2,
                fontWeight: FontWeight.bold,
                color: isDark ? Colors.grey[300] : Colors.grey[700],
                height: 1.6,
              ),
            ),
          );
        }

        // Regular paragraph
        return Padding(
          padding: const EdgeInsets.only(bottom: 16),
          child: Text(
            trimmed,
            style: TextStyle(
              fontSize: fontSize,
              height: 1.8,
              letterSpacing: 0.5,
              color: isDark ? Colors.grey[200] : AppTheme.textPrimary,
            ),
          ),
        );
      }).toList(),
    );
  }
}
