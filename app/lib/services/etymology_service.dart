import 'dart:convert';
import 'package:flutter/services.dart';

class EtymologyEntry {
  final String character;
  final String? formationType; // pictographic, ideographic, phono-semantic, indicative, phonetic-loan
  final String? semanticComponent;
  final String? phoneticComponent;
  final String? ids; // ideographic description sequence (decomposition)
  final String? etymology; // concise explanation
  final int? strokes;

  const EtymologyEntry({
    required this.character,
    this.formationType,
    this.semanticComponent,
    this.phoneticComponent,
    this.ids,
    this.etymology,
    this.strokes,
  });

  /// Human-readable formation label
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
      _entries![ch] = EtymologyEntry(
        character: ch,
        formationType: v['ft'] as String?,
        semanticComponent: v['s'] as String?,
        phoneticComponent: v['ph'] as String?,
        ids: v['ids'] as String?,
        etymology: v['e'] as String?,
        strokes: v['st'] as int?,
      );
    }
  }

  EtymologyEntry? lookup(String character) => _entries?[character];
}
