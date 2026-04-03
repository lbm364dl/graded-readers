import 'dart:convert';
import 'package:flutter/services.dart';

class EtymologyNote {
  final String source;
  final String text;
  const EtymologyNote({required this.source, required this.text});
}

class EtymologyEntry {
  final String character;
  final String? formationType;
  final String? semanticComponent;
  final String? phoneticComponent;
  final String? ids;
  final int? strokes;
  final List<EtymologyNote> notes;
  final List<String> phoneticFamily;
  final String? mandarinReading;
  final String? japaneseOn;
  final String? japaneseKun;
  final String? definitions;

  const EtymologyEntry({
    required this.character,
    this.formationType,
    this.semanticComponent,
    this.phoneticComponent,
    this.ids,
    this.strokes,
    this.notes = const [],
    this.phoneticFamily = const [],
    this.mandarinReading,
    this.japaneseOn,
    this.japaneseKun,
    this.definitions,
  });

  String? get formationLabel {
    switch (formationType) {
      case 'pictographic':
        return 'Pictographic (象形)';
      case 'ideographic':
        return 'Ideographic (會意)';
      case 'phono-semantic':
        return 'Phono-semantic (形聲)';
      case 'indicative':
        return 'Indicative (指事)';
      case 'phonetic-loan':
        return 'Phonetic loan (假借)';
      default:
        return null;
    }
  }

  /// Components that can be tapped into (semantic + phonetic, deduplicated)
  List<String> get components {
    final result = <String>[];
    if (semanticComponent != null && semanticComponent!.length == 1) {
      result.add(semanticComponent!);
    }
    if (phoneticComponent != null &&
        phoneticComponent!.length == 1 &&
        phoneticComponent != semanticComponent) {
      result.add(phoneticComponent!);
    }
    return result;
  }
}

class EtymologyService {
  EtymologyService._();
  static final EtymologyService instance = EtymologyService._();

  Map<String, EtymologyEntry>? _entries;

  bool get isReady => _entries != null;

  Future<void> initialize() async {
    if (_entries != null) return;
    final raw = await rootBundle.loadString('assets/etymology.json');
    final data = json.decode(raw) as Map<String, dynamic>;
    _entries = {};
    for (final e in data.entries) {
      final ch = e.key;
      final v = e.value as Map<String, dynamic>;
      final notesList = (v['en'] as List?)
              ?.map((n) => EtymologyNote(
                    source: (n['src'] as String?) ?? '',
                    text: (n['t'] as String?) ?? '',
                  ))
              .toList() ??
          [];
      final pf = (v['pf'] as List?)?.cast<String>() ?? [];
      final r = v['r'] as Map<String, dynamic>?;
      _entries![ch] = EtymologyEntry(
        character: ch,
        formationType: v['ft'] as String?,
        semanticComponent: v['s'] as String?,
        phoneticComponent: v['ph'] as String?,
        ids: v['ids'] as String?,
        strokes: v['st'] as int?,
        notes: notesList,
        phoneticFamily: pf,
        mandarinReading: r?['zh'] as String?,
        japaneseOn: r?['on'] as String?,
        japaneseKun: r?['kun'] as String?,
        definitions: v['d'] as String?,
      );
    }
  }

  EtymologyEntry? lookup(String character) => _entries?[character];
}
