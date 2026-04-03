import 'dart:convert';
import 'package:flutter/services.dart';

class GlyphEntry {
  final Map<String, String> eras; // era name → SVG string
  const GlyphEntry(this.eras);

  // Known eras in display order; any unknown eras are appended at the end
  static const _eraOrder = [
    'oracle', 'bronze', 'seal',
    'seal_shuowen', 'seal_acc', 'seal_wikimedia', 'seal_ancient',
  ];

  /// Human-readable label for an era key (handles numbered variants like seal_acc_2)
  static String labelFor(String era) {
    const labels = {
      'oracle': 'Oracle Bone',
      'bronze': 'Bronze',
      'seal': 'Seal Script',
      'seal_shuowen': 'Seal (Shuowen)',
      'seal_acc': 'Seal (ACC)',
      'seal_wikimedia': 'Seal (Wikimedia)',
      'seal_ancient': 'Ancient',
    };
    // Strip _2, _3 etc. suffix for label lookup
    final base = era.replaceAll(RegExp(r'_\d+$'), '');
    final label = labels[base] ?? labels[era] ?? era;
    // Add variant number if present
    final match = RegExp(r'_(\d+)$').firstMatch(era);
    if (match != null) return '$label ${match.group(1)}';
    return label;
  }

  List<String> get sortedEras {
    final result = <String>[];
    // First add known eras in order (including numbered variants)
    for (final prefix in _eraOrder) {
      for (final era in eras.keys) {
        if (era == prefix || era.startsWith('${prefix}_')) {
          if (!result.contains(era)) result.add(era);
        }
      }
    }
    // Append any remaining
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
