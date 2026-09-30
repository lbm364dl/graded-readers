import 'package:flutter/material.dart';

import '../data.dart';
import '../models.dart';
import '../services/korean_dictionary.dart';
import 'reader_screen.dart';

class KoreanDictionaryScreen extends StatefulWidget {
  final bool grammar;
  const KoreanDictionaryScreen({super.key, this.grammar = false});

  @override
  State<KoreanDictionaryScreen> createState() => _KoreanDictionaryScreenState();
}

class _KoreanDictionaryScreenState extends State<KoreanDictionaryScreen> {
  String query = '';

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(
            title:
                Text(widget.grammar ? 'Korean grammar' : 'Korean dictionary')),
        body: FutureBuilder<KoreanDictionary>(
          future: KoreanDictionary.load(),
          builder: (context, snapshot) {
            if (snapshot.hasError) {
              return const Center(
                  child: Text('Dictionary could not be loaded.'));
            }
            if (!snapshot.hasData) {
              return const Center(child: CircularProgressIndicator());
            }
            final dictionary = snapshot.data!;
            final entries = ((widget.grammar
                    ? dictionary.grammar
                    : dictionary.words)['entries'] as List)
                .cast<Map<String, dynamic>>()
                .where((entry) => '${entry['headword'] ?? entry['pattern']} '
                        '${entry['definition_en'] ?? entry['title_en']} '
                        '${entry['explanation_en'] ?? ''}'
                    .toLowerCase()
                    .contains(query.toLowerCase()))
                .toList();
            return Column(children: [
              Padding(
                padding: const EdgeInsets.all(16),
                child: TextField(
                  decoration: const InputDecoration(
                      labelText: 'Search Korean or English'),
                  onChanged: (value) => setState(() => query = value),
                ),
              ),
              Expanded(
                child: ListView.builder(
                  itemCount: entries.length,
                  itemBuilder: (context, index) {
                    final entry = entries[index];
                    return ListTile(
                      title: Text(widget.grammar
                          ? '${entry['title_en']} · ${entry['pattern']}'
                          : entry['headword'] as String),
                      subtitle: Text(widget.grammar
                          ? entry['explanation_en'] as String
                          : entry['definition_en'] as String),
                      onTap: () => Navigator.of(context).push(
                          MaterialPageRoute<void>(
                              builder: (_) => KoreanEntryScreen(
                                  dictionary: dictionary,
                                  entry: entry,
                                  grammar: widget.grammar))),
                    );
                  },
                ),
              ),
            ]);
          },
        ),
      );
}

class KoreanEntryScreen extends StatelessWidget {
  final KoreanDictionary dictionary;
  final Map<String, dynamic> entry;
  final bool grammar;
  final Map<String, dynamic>? selectedUse;

  const KoreanEntryScreen(
      {super.key,
      required this.dictionary,
      required this.entry,
      required this.grammar,
      this.selectedUse});

  Future<void> _openSource(
      BuildContext context, Map<String, dynamic> use) async {
    final source = (grammar ? dictionary.grammar : dictionary.words)['sources']
        [use['source']] as Map<String, dynamic>;
    final readers = await ContentRepository().loadReaders(Language.korean);
    final reader =
        readers.singleWhere((reader) => reader.id == source['reader_id']);
    final chapterIndex = (source['chapter'] as int) - 1;
    if (reader.chapters[chapterIndex].content != source['text']) {
      throw StateError('Korean dictionary source has changed');
    }
    if (!context.mounted) return;
    Navigator.of(context).push(MaterialPageRoute<void>(
      builder: (_) => ReaderScreen(
        reader: reader,
        initialChapter: chapterIndex,
        initialCharacterOffset: use['start'] as int,
      ),
    ));
  }

  @override
  Widget build(BuildContext context) {
    final uses = ((grammar
            ? dictionary.grammar
            : dictionary.words)['occurrences'] as List)
        .cast<Map<String, dynamic>>()
        .where((use) => use['entry_id'] == entry['id'])
        .toList();
    return Scaffold(
      appBar: AppBar(
          title: Text(grammar
              ? entry['title_en'] as String
              : entry['headword'] as String)),
      body: ListView(padding: const EdgeInsets.all(20), children: [
        if (grammar)
          Text(entry['pattern'] as String,
              style: Theme.of(context).textTheme.titleLarge),
        if (!grammar) ...[
          Text(entry['kind'] == 'word' ? 'Dictionary form' : 'Dictionary entry',
              style: Theme.of(context).textTheme.labelMedium),
          Text(entry['headword'] as String,
              style: Theme.of(context).textTheme.titleLarge),
        ],
        const SizedBox(height: 12),
        Text(
            grammar
                ? entry['explanation_en'] as String
                : entry['definition_en'] as String,
            style: Theme.of(context).textTheme.titleMedium),
        if (selectedUse != null) ...[
          const SizedBox(height: 18),
          Text(grammar ? 'In this passage' : 'Form in this passage',
              style: Theme.of(context).textTheme.labelMedium),
          if (!grammar)
            Text(selectedUse!['surface'] as String,
                style: Theme.of(context).textTheme.titleMedium),
          Text((grammar ? selectedUse!['context_en'] : selectedUse!['gloss'])
              as String),
        ],
        const SizedBox(height: 24),
        Text('Examples from our texts',
            style: Theme.of(context).textTheme.titleMedium),
        for (final use in uses)
          ListTile(
            title: Text(use['sentence'] as String),
            subtitle: Text(grammar
                ? use['context_en'] as String
                : '${use['surface']} · ${use['gloss']}'),
            trailing: const Icon(Icons.open_in_new),
            onTap: () => _openSource(context, use),
          ),
      ]),
    );
  }
}

/// Keep the lexical and grammar destinations on form rows when a chain exists.
class KoreanTapLinks extends StatelessWidget {
  final AgentSegment segment;
  final String source;
  final String sourceText;
  final int segmentIndex;

  const KoreanTapLinks(
      {super.key,
      required this.segment,
      required this.source,
      required this.sourceText,
      required this.segmentIndex});

  @override
  Widget build(BuildContext context) => segment.formSteps.isNotEmpty
      ? KoreanFormChain(
          segment: segment,
          source: source,
          sourceText: sourceText,
          segmentIndex: segmentIndex)
      : KoreanDictionaryLinks(
          source: source,
          sourceText: sourceText,
          segmentIndex: segmentIndex,
          surface: segment.text,
          gloss: segment.meaningEn);
}

class KoreanDictionaryLinks extends StatelessWidget {
  final String source;
  final String sourceText;
  final int segmentIndex;
  final String surface;
  final String gloss;
  const KoreanDictionaryLinks(
      {super.key,
      required this.source,
      required this.sourceText,
      required this.segmentIndex,
      required this.surface,
      required this.gloss});

  @override
  Widget build(BuildContext context) => FutureBuilder<KoreanDictionary>(
        future: KoreanDictionary.load(),
        builder: (context, snapshot) {
          if (!snapshot.hasData) return const SizedBox.shrink();
          final dictionary = snapshot.data!;
          final word = dictionary.wordUse(
              source, sourceText, segmentIndex, surface, gloss);
          final grammar =
              dictionary.grammarUses(source, sourceText, segmentIndex, surface);
          return Wrap(spacing: 8, children: [
            if (word != null)
              TextButton.icon(
                icon: const Icon(Icons.menu_book_outlined),
                label: Text(
                    'Dictionary · ${dictionary.entry(word['entry_id'] as String, isGrammar: false)['headword']}'),
                onPressed: () =>
                    Navigator.of(context).push(MaterialPageRoute<void>(
                  builder: (_) => KoreanEntryScreen(
                      dictionary: dictionary,
                      entry: dictionary.entry(word['entry_id'] as String,
                          isGrammar: false),
                      grammar: false,
                      selectedUse: word),
                )),
              ),
            for (final use in grammar)
              TextButton.icon(
                icon: const Icon(Icons.account_tree_outlined),
                label: Text(
                    'Grammar · ${dictionary.entry(use['entry_id'] as String, isGrammar: true)['title_en']}'),
                onPressed: () =>
                    Navigator.of(context).push(MaterialPageRoute<void>(
                  builder: (_) => KoreanEntryScreen(
                      dictionary: dictionary,
                      entry: dictionary.entry(use['entry_id'] as String,
                          isGrammar: true),
                      grammar: true,
                      selectedUse: use),
                )),
              ),
          ]);
        },
      );
}

/// Reviewed complete-form meanings; each stage opens its grammar lesson.
class KoreanFormChain extends StatelessWidget {
  final AgentSegment segment;
  final String source;
  final String sourceText;
  final int segmentIndex;
  const KoreanFormChain(
      {super.key,
      required this.segment,
      required this.source,
      required this.sourceText,
      required this.segmentIndex});

  @override
  Widget build(BuildContext context) => FutureBuilder<KoreanDictionary>(
        future: KoreanDictionary.load(),
        builder: (context, snapshot) {
          if (!snapshot.hasData || segment.formSteps.isEmpty) {
            return const SizedBox.shrink();
          }
          final dictionary = snapshot.data!;
          final baseUse = dictionary.wordUse(source, sourceText, segmentIndex,
              segment.text, segment.meaningEn);
          if (baseUse == null) return const SizedBox.shrink();
          final base =
              dictionary.entry(baseUse['entry_id'] as String, isGrammar: false);
          final grammarUses = dictionary.grammarUses(
              source, sourceText, segmentIndex, segment.text);
          final stageIds = {
            for (final step in segment.formSteps) ...step.grammarEntryIds
          };
          final constructionUses = grammarUses
              .where((use) => !stageIds.contains(use['entry_id']))
              .toList();
          return Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const SizedBox(height: 10),
                Text('How this form is built',
                    style: Theme.of(context).textTheme.labelMedium),
                ListTile(
                  dense: true,
                  contentPadding: EdgeInsets.zero,
                  title: Text(base['headword'] as String),
                  subtitle:
                      Text('Dictionary form · ${base['definition_en']}'),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () =>
                      Navigator.of(context).push(MaterialPageRoute<void>(
                    builder: (_) => KoreanEntryScreen(
                        dictionary: dictionary,
                        entry: base,
                        grammar: false,
                        selectedUse: baseUse),
                  )),
                ),
                for (final step in segment.formSteps)
                  ListTile(
                    dense: true,
                    contentPadding: EdgeInsets.zero,
                    title: Text('→ ${step.form}'),
                    subtitle: Text('${step.label} · ${step.meaningEn}'),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () {
                      final id = step.grammarEntryIds.single;
                      final use = dictionary
                          .grammarUses(
                              source, sourceText, segmentIndex, segment.text)
                          .singleWhere((use) => use['entry_id'] == id);
                      Navigator.of(context).push(MaterialPageRoute<void>(
                        builder: (_) => KoreanEntryScreen(
                            dictionary: dictionary,
                            entry: dictionary.entry(id, isGrammar: true),
                            grammar: true,
                            selectedUse: use),
                      ));
                    },
                  ),
                for (final use in constructionUses)
                  ListTile(
                    dense: true,
                    contentPadding: EdgeInsets.zero,
                    title: Text('→ ${use['display_form']}'),
                    subtitle: Text(use['display_meaning_en'] as String),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () => Navigator.of(context).push(
                        MaterialPageRoute<void>(
                      builder: (_) => KoreanEntryScreen(
                          dictionary: dictionary,
                          entry: dictionary.entry(
                              use['entry_id'] as String,
                              isGrammar: true),
                          grammar: true,
                          selectedUse: use),
                    )),
                  ),
              ]);
        },
      );
}
