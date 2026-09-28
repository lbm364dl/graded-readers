import 'package:flutter/material.dart';
import '../data.dart';
import '../models.dart';
import '../services/grammar_dictionary.dart';
import 'reader_screen.dart';

class GrammarDictionaryScreen extends StatefulWidget {
  final List<String>? entryIds;
  const GrammarDictionaryScreen({super.key, this.entryIds});
  @override
  State<GrammarDictionaryScreen> createState() =>
      _GrammarDictionaryScreenState();
}

class _GrammarDictionaryScreenState extends State<GrammarDictionaryScreen> {
  late final _dictionary = GrammarDictionary.load();
  String _query = '';
  @override
  Widget build(BuildContext context) => Scaffold(
      appBar: AppBar(title: const Text('Japanese grammar dictionary')),
      body: Column(children: [
        Padding(
            padding: const EdgeInsets.all(16),
            child: TextField(
                decoration: const InputDecoration(
                    labelText: 'Search patterns, kana, or functions'),
                onChanged: (q) => setState(() => _query = q))),
        Expanded(
            child: FutureBuilder<GrammarDictionary>(
                future: _dictionary,
                builder: (context, snapshot) {
                  if (snapshot.hasError) {
                    return const Center(
                        child: Text('Grammar dictionary could not be loaded.'));
                  }
                  if (!snapshot.hasData) {
                    return const Center(child: CircularProgressIndicator());
                  }
                  final dictionary = snapshot.data!;
                  final entries = dictionary
                      .search(_query)
                      .where((e) =>
                          widget.entryIds == null ||
                          widget.entryIds!.contains(e['id']))
                      .toList();
                  return ListView(
                      children: entries
                          .map((e) => ListTile(
                              title: Text(e['title'] as String),
                              subtitle: Text(e['summary_en'] as String),
                              trailing: const Icon(Icons.chevron_right),
                              onTap: () => Navigator.of(context).push(
                                  MaterialPageRoute<void>(
                                      builder: (_) => GrammarEntryScreen(
                                          dictionary: dictionary, entry: e)))))
                          .toList());
                }))
      ]));
}

class GrammarEntryScreen extends StatelessWidget {
  final GrammarDictionary dictionary;
  final Map<String, dynamic> entry;
  final String? contextExplanation;
  const GrammarEntryScreen(
      {super.key,
      required this.dictionary,
      required this.entry,
      this.contextExplanation});
  Future<void> _openSource(
      BuildContext context, Map<String, dynamic> use) async {
    try {
      final source = dictionary.sources[use['source']];
      final readers = await ContentRepository().loadReaders(Language.japanese);
      final reader = readers.singleWhere((r) => r.id == source['reader_id']);
      final chapter = (source['chapter'] as int) - 1;
      if (reader.chapters[chapter].content != source['text']) {
        throw StateError('Source edition changed');
      }
      final offset = String.fromCharCodes(
              (source['text'] as String).runes.take(use['start'] as int))
          .length;
      if (!context.mounted) return;
      Navigator.of(context).push(MaterialPageRoute<void>(
          builder: (_) => ReaderScreen(
              reader: reader,
              initialChapter: chapter,
              initialCharacterOffset: offset)));
    } catch (_) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
            content: Text('This source edition is no longer available.')));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final linkedUses = dictionary.occurrences
        .where((o) => (o['entry_ids'] as List).contains(entry['id']))
        .toList();
    final actualUses = linkedUses.where((o) => o['layer'] != 'form').toList();
    final showingChains = actualUses.isEmpty;
    final uses = dictionary.examples(entry['id'] as String);
    return Scaffold(
        appBar: AppBar(title: Text(entry['title'] as String)),
        body: ListView(padding: const EdgeInsets.all(20), children: [
          Text('${entry['reading']} · ${entry['kind']}'),
          if (contextExplanation != null) ...[
            const SizedBox(height: 16),
            const Text('Used in this passage'),
            Text(contextExplanation!)
          ],
          const SizedBox(height: 16),
          Text(entry['summary_en'] as String,
              style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 12),
          Text(entry['explanation_en'] as String),
          if ((entry['formation'] as List).isNotEmpty) ...[
            const SizedBox(height: 20),
            const Text('How it is formed'),
            ...(entry['formation'] as List).map((f) {
              final related = f['grammar_entry_id'] == null
                  ? null
                  : dictionary.entry(f['grammar_entry_id'] as String);
              return ListTile(
                title: Text(f['form'] as String),
                subtitle: Text(
                    '${f['explanation_en']}${related == null ? '' : '\nUses ${related['title']}'}'),
                trailing:
                    related == null ? null : const Icon(Icons.chevron_right),
                onTap: related == null
                    ? null
                    : () => Navigator.of(context).push(MaterialPageRoute<void>(
                        builder: (_) => GrammarEntryScreen(
                            dictionary: dictionary, entry: related))),
              );
            })
          ],
          ...(entry['notes_en'] as List).map((n) => Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(n as String))),
          const SizedBox(height: 24),
          Text(showingChains
              ? 'Conjugation chains from our texts'
              : 'Examples from our texts'),
          ...uses.map((o) {
            final source = dictionary.sources[o['source']] as Map;
            return ListTile(
                title: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                          'JLPT ${source['level']} · Chapter ${source['chapter']}',
                          style: Theme.of(context).textTheme.labelSmall),
                      Text(o['sentence'] as String)
                    ]),
                subtitle: Text(
                    '${showingChains ? 'Intermediate form: ${o['form']} · ${o['form_label']}\n' : ''}'
                    '${o['context_en']}\n${source['title']}'),
                trailing: const Icon(Icons.open_in_new),
                onTap: () => _openSource(context, o));
          })
        ]));
  }
}

class GrammarDictionaryLinks extends StatelessWidget {
  final String source, sourceText, surface;
  final int startOffset;
  const GrammarDictionaryLinks(
      {super.key,
      required this.source,
      required this.sourceText,
      required this.surface,
      required this.startOffset});
  @override
  Widget build(BuildContext context) => FutureBuilder<GrammarDictionary>(
      future: GrammarDictionary.load(),
      builder: (context, snapshot) {
        if (!snapshot.hasData) return const SizedBox.shrink();
        final dictionary = snapshot.data!;
        final uses = dictionary.uses(source, sourceText, startOffset, surface);
        final linked = <String, String>{};
        for (final use in uses) {
          for (final id in (use['entry_ids'] as List).cast<String>()) {
            linked.putIfAbsent(id, () => use['context_en'] as String);
          }
        }
        return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: linked.entries
                .map((link) => TextButton.icon(
                    icon: const Icon(Icons.account_tree_outlined),
                    label: Text(
                        'Grammar · ${dictionary.entry(link.key)['title']}'),
                    onPressed: () => Navigator.of(context).push(
                        MaterialPageRoute<void>(
                            builder: (_) => GrammarEntryScreen(
                                dictionary: dictionary,
                                entry: dictionary.entry(link.key),
                                contextExplanation: link.value)))))
                .toList());
      });
}
