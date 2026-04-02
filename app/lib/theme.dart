import 'package:flutter/material.dart';
import 'models.dart';

class AppTheme {
  static const Color primary = Color(0xFFC62828);
  static const Color primaryLight = Color(0xFFEF5350);
  static const Color background = Color(0xFFFAFAFA);
  static const Color surface = Colors.white;
  static const Color textPrimary = Color(0xFF212121);
  static const Color textSecondary = Color(0xFF757575);

  static const hskColors = <int, Color>{
    1: Color(0xFF43A047),
    2: Color(0xFF7CB342),
    3: Color(0xFFFFB300),
    4: Color(0xFFFB8C00),
    5: Color(0xFFF4511E),
    6: Color(0xFFE53935),
  };

  static const jlptColors = <int, Color>{
    1: Color(0xFF43A047), // N5
    2: Color(0xFF039BE5), // N4
    3: Color(0xFFFFB300), // N3
    4: Color(0xFFFB8C00), // N2
    5: Color(0xFFE53935), // N1
  };

  static Color levelColor(int level, [Language language = Language.chinese]) {
    final colors = language == Language.japanese ? jlptColors : hskColors;
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
        scrolledUnderElevation: 1,
        backgroundColor: primary,
        foregroundColor: Colors.white,
        titleTextStyle: TextStyle(
          fontSize: 17,
          fontWeight: FontWeight.w600,
          color: Colors.white,
        ),
      ),
      cardTheme: CardThemeData(
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
          side: BorderSide(color: Colors.grey[200]!),
        ),
      ),
      dividerTheme: DividerThemeData(
        color: Colors.grey[200],
        thickness: 1,
      ),
      navigationBarTheme: NavigationBarThemeData(
        elevation: 0,
        indicatorColor: primary.withValues(alpha: 0.12),
        labelTextStyle: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return TextStyle(
                fontSize: 12, fontWeight: FontWeight.w600, color: primary);
          }
          return TextStyle(fontSize: 12, color: Colors.grey[600]);
        }),
        iconTheme: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected)) {
            return IconThemeData(color: primary, size: 22);
          }
          return IconThemeData(color: Colors.grey[600], size: 22);
        }),
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
        scrolledUnderElevation: 1,
        titleTextStyle: TextStyle(
          fontSize: 17,
          fontWeight: FontWeight.w600,
          color: Colors.white,
        ),
      ),
      cardTheme: CardThemeData(
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
          side: BorderSide(color: Colors.grey[800]!),
        ),
      ),
      navigationBarTheme: NavigationBarThemeData(
        elevation: 0,
        indicatorColor: primary.withValues(alpha: 0.2),
      ),
    );
  }
}
