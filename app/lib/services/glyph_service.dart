import 'dart:convert';
import 'package:flutter/services.dart';

class GlyphEntry {
  final Map<String, String> eras; // era name → SVG string
  const GlyphEntry(this.eras);

  static const eraOrder = [
    'oracle', 'bronze', 'seal', 'seal_shuowen', 'seal_wikimedia',
  ];
  static const eraLabels = {
    'oracle': 'Oracle Bone',
    'bronze': 'Bronze',
    'seal': 'Seal Script',
    'seal_shuowen': 'Seal (Shuowen)',
    'seal_wikimedia': 'Seal (Wikimedia)',
  };

  List<String> get sortedEras {
    final result = <String>[];
    for (final era in eraOrder) {
      if (eras.containsKey(era)) result.add(era);
    }
    // Any remaining eras not in the standard order
    for (final era in eras.keys) {
      if (!result.contains(era)) result.add(era);
    }
    return result;
  }
}

class GlyphService {
  GlyphService._();
  static final GlyphService instance = GlyphService._();

  Map<String, GlyphEntry>? _entries;

  bool get isReady => _entries != null;

  Future<void> initialize() async {
    if (_entries != null) return;
    final raw = await rootBundle.loadString('assets/glyphs.json');
    final data = json.decode(raw) as Map<String, dynamic>;
    _entries = {};
    for (final e in data.entries) {
      final eras = (e.value as Map<String, dynamic>)
          .map((k, v) => MapEntry(k, v as String));
      _entries![e.key] = GlyphEntry(eras);
    }
  }

  GlyphEntry? lookup(String character) => _entries?[character];
}
