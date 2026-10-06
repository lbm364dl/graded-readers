import 'package:flutter/material.dart';
import '../models.dart';
import '../services/usage_dictionary.dart';
import '../services/grammar_dictionary.dart';
import '../screens/usage_dictionary_screen.dart';
import '../screens/grammar_dictionary_screen.dart';
import '../screens/reader_screen.dart' show formatJapaneseLemmaReading;

/// Links belong to reviewed formation steps, not guessed suffixes or glosses.
class JapaneseFormChain extends StatefulWidget {
  final AgentSegment segment;
  final String source, sourceText;
  final int segmentIndex, startOffset;
  final List<({AgentSegment segment, int index, int start})> parts;
  final AgentGrammarOverlay? overlay;
  final bool includeSurfaceMeaning;
  const JapaneseFormChain(
      {super.key,
      required this.segment,
      required this.source,
      required this.sourceText,
      required this.segmentIndex,
      required this.startOffset,
      this.parts = const [],
      this.overlay,
      this.includeSurfaceMeaning = false});
  @override
  State<JapaneseFormChain> createState() => _JapaneseFormChainState();
}

class _JapaneseFormChainState extends State<JapaneseFormChain> {
  late final _dictionaries = Future.wait<Object>([
    UsageDictionary.load(language: Language.japanese),
    GrammarDictionary.load()
  ]);

  void _openGrammar(GrammarDictionary grammar, List<String> ids) {
    if (ids.isEmpty) return;
    Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (_) => ids.length == 1
            ? GrammarEntryScreen(
                dictionary: grammar, entry: grammar.entry(ids.single))
            : GrammarDictionaryScreen(entryIds: ids)));
  }

  @override
  Widget build(BuildContext context) => FutureBuilder<List<Object>>(
      future: _dictionaries,
      builder: (context, snapshot) {
        final words =
            snapshot.hasData ? snapshot.data![0] as UsageDictionary : null;
        final grammar =
            snapshot.hasData ? snapshot.data![1] as GrammarDictionary : null;
        final display = widget.segment;
        final merged = widget.parts.isNotEmpty;
        final tail = merged ? widget.parts.last : null;
        final segment = tail?.segment ?? display;
        final prefix = merged
            ? widget.parts
                .take(widget.parts.length - 1)
                .map((p) => p.segment.text)
                .join()
            : '';
        final prefixReading = merged
            ? widget.parts
                .take(widget.parts.length - 1)
                .map((p) => p.segment.pinyin)
                .join()
            : '';
        Map<String, dynamic>? formLink(int index) {
          final step = segment.formSteps[index];
          return grammar?.formStep(
              widget.source,
              widget.sourceText,
              tail?.start ?? widget.startOffset,
              segment.text,
              tail?.index ?? widget.segmentIndex,
              index,
              step.form,
              step.reading,
              step.label,
              step.meaningEn);
        }

        bool hasWholeMeaning(Map<String, dynamic>? link, int index) =>
            link != null &&
            link['display_surface'] == display.text &&
            link['display_form'] == '$prefix${segment.formSteps[index].form}' &&
            link['display_reading'] ==
                '$prefixReading${segment.formSteps[index].reading}' &&
            link['display_base_form'] == display.lemma &&
            link['display_base_reading'] == display.lemmaReading;
        final firstLink = segment.formSteps.isEmpty ? null : formLink(0);
        final wholeBaseMeaning =
            segment.formSteps.isNotEmpty && hasWholeMeaning(firstLink, 0)
                ? (firstLink?['display_base_meaning_en'] as String?)
                : null;
        final lastIndex = segment.formSteps.length - 1;
        final lastLink = lastIndex < 0 ? null : formLink(lastIndex);
        final surfaceMeaning = lastIndex >= 0 &&
                hasWholeMeaning(lastLink, lastIndex)
            ? lastLink!['display_meaning_en'] as String? ?? display.meaningEn
            : display.meaningEn;
        final lexical = merged && display.type != 'idiom'
            ? widget.parts.first.segment
            : display;
        final lexicalIndex =
            merged ? widget.parts.first.index : widget.segmentIndex;
        final use = words?.occurrence(
            widget.source, lexicalIndex, widget.sourceText,
            startOffset: widget.startOffset,
            surface: lexical.text,
            reading: lexical.pinyin,
            gloss: lexical.meaningEn);
        final base =
            use == null ? null : words!.entry(use['entry_id'] as String);
        final baseMatches = base != null &&
            base['headword'] == lexical.lemma &&
            base['reading'] == lexical.lemmaReading;
        final baseSense = baseMatches
            ? (base['senses'] as List)
                .cast<Map<String, dynamic>>()
                .where((sense) => sense['id'] == use!['sense_id'])
                .map((sense) => sense['definition'] as String)
                .firstOrNull
            : null;
        final lexicalMeaning =
            lexical.lemma == display.lemma && wholeBaseMeaning != null
                ? wholeBaseMeaning
                : baseSense;
        VoidCallback? openBase;
        if (baseMatches && !words!.isGrammarOnly(base)) {
          openBase = () => Navigator.of(context).push(MaterialPageRoute<void>(
              builder: (_) =>
                  UsageEntryScreen(dictionary: words, entry: base)));
        } else if (baseMatches && grammar != null) {
          final ids = (grammar.wordRoutes[base['id']] as List? ?? [])
              .cast<String>()
              .where((id) => grammar.entry(id)['kind'] == 'construction')
              .toList();
          if (ids.isNotEmpty) openBase = () => _openGrammar(grammar, ids);
        }
        Widget row(String form, String reading, String subtitle, int index,
                VoidCallback? onTap) =>
            ListTile(
                key: ValueKey('japanese-form-step-$index'),
                contentPadding: EdgeInsets.zero,
                leading:
                    SizedBox(width: 20, child: Text(index == -1 ? '' : '→')),
                minLeadingWidth: 20,
                title: Text(formatJapaneseLemmaReading(form, reading),
                    style: TextStyle(
                        fontSize: 14,
                        fontWeight: FontWeight.w600,
                        color: onTap == null
                            ? null
                            : Theme.of(context).colorScheme.primary)),
                subtitle:
                    Text(subtitle, style: const TextStyle(fontSize: 12.5)),
                trailing: onTap == null
                    ? null
                    : const Icon(Icons.chevron_right, size: 18),
                onTap: onTap);
        return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          if (widget.includeSurfaceMeaning)
            Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Text(surfaceMeaning,
                    key: const ValueKey('japanese-surface-meaning'),
                    style: const TextStyle(fontSize: 15, height: 1.4))),
          if (lexical.lemma.isNotEmpty)
            row(
                lexical.lemma,
                lexical.lemmaReading,
                lexicalMeaning != null
                    ? 'Base form · $lexicalMeaning'
                    : 'Base form',
                -1,
                openBase),
          if (merged && lexical.lemma != display.lemma)
            Builder(builder: (context) {
              final laterStepIds = <String>{
                for (var index = 0; index < segment.formSteps.length; index++)
                  ...((formLink(index)?['entry_ids'] as List? ?? [])
                      .cast<String>())
              };
              // An overlay describes the whole observed phrase, including
              // later inflections. This row is the base construction stage;
              // each later form already has its own reviewed destination.
              final ids = grammar
                      ?.uses(widget.source, widget.sourceText,
                          widget.startOffset, display.text)
                      .where((use) => use['layer'] == 'overlay')
                      .expand(
                          (use) => (use['entry_ids'] as List).cast<String>())
                      .toSet()
                      .where((id) => !laterStepIds.contains(id))
                      .toList() ??
                  <String>[];
              final meanings = ids
                  .map((id) => grammar!.entry(id)['summary_en'] as String)
                  .where((meaning) => meaning.isNotEmpty)
                  .toSet();
              return row(
                  display.lemma,
                  display.lemmaReading,
                  wholeBaseMeaning ?? meanings.join(' · '),
                  -2,
                  ids.isEmpty ? null : () => _openGrammar(grammar!, ids));
            }),
          for (var index = 0; index < segment.formSteps.length; index++)
            Builder(builder: (context) {
              final step = segment.formSteps[index];
              final linked = formLink(index);
              final ids = (linked?['entry_ids'] as List? ?? []).cast<String>();
              // Never present a tail-only gloss as the complete merged form.
              // Older assets can safely show the final contextual translation;
              // intermediate meanings require reviewed whole-form data.
              final meaning = hasWholeMeaning(linked, index)
                  ? linked!['display_meaning_en'] as String? ?? ''
                  : merged
                      ? (index == segment.formSteps.length - 1
                          ? display.meaningEn
                          : '')
                      : step.meaningEn;
              return row(
                  '$prefix${step.form}',
                  '$prefixReading${step.reading}',
                  [step.label, if (meaning.isNotEmpty) meaning].join(' · '),
                  index,
                  ids.isEmpty ? null : () => _openGrammar(grammar!, ids));
            })
        ]);
      });
}
