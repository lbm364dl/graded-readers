import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/main.dart';
import 'package:hsk_graded/models.dart';

void main() {
  testWidgets('App launches without errors', (WidgetTester tester) async {
    await tester.pumpWidget(const GradedReadersApp(initialLanguage: Language.chinese));
    expect(find.text('Graded Readers'), findsOneWidget);
  });
}
