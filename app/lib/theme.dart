import 'package:flutter/material.dart';
import 'models.dart';

class AppTheme {
  static const Color primary = Color(0xFFB71C1C);
  static const Color primaryLight = Color(0xFFEF5350);
  static const Color background = Color(0xFFFAF8F5);
  static const Color surface = Colors.white;
  static const Color textPrimary = Color(0xFF1A1A1A);
  static const Color textSecondary = Color(0xFF666666);

  static const hskColors = <int, Color>{
    1: Color(0xFF4CAF50),
    2: Color(0xFF8BC34A),
    3: Color(0xFFFFC107),
    4: Color(0xFFFF9800),
    5: Color(0xFFFF5722),
    6: Color(0xFFF44336),
  };

  static const jlptColors = <int, Color>{
    1: Color(0xFF4CAF50), // N5
    2: Color(0xFF29B6F6), // N4
    3: Color(0xFFFFC107), // N3
    4: Color(0xFFFF9800), // N2
    5: Color(0xFFF44336), // N1
  };

  static Color levelColor(int level, [Language language = Language.chinese]) {
    final colors =
        language == Language.japanese ? jlptColors : hskColors;
    return colors[level] ?? const Color(0xFF9E9E9E);
  }

  static ThemeData get lightTheme {
    return ThemeData(
      useMaterial3: true,
      colorScheme: ColorScheme.fromSeed(
        seedColor: primary,
        brightness: Brightness.light,
        surface: background,
      ),
      appBarTheme: const AppBarTheme(
        centerTitle: true,
        elevation: 0,
        backgroundColor: primary,
        foregroundColor: Colors.white,
      ),
      cardTheme: CardThemeData(
        elevation: 1,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
        ),
      ),
    );
  }

  static ThemeData get darkTheme {
    return ThemeData(
      useMaterial3: true,
      colorScheme: ColorScheme.fromSeed(
        seedColor: primary,
        brightness: Brightness.dark,
      ),
      appBarTheme: const AppBarTheme(
        centerTitle: true,
        elevation: 0,
      ),
      cardTheme: CardThemeData(
        elevation: 1,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
        ),
      ),
    );
  }
}
