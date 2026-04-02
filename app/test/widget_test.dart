import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/main.dart';

void main() {
  testWidgets('App launches without errors', (WidgetTester tester) async {
    await tester.pumpWidget(const GradedReadersApp());
    expect(find.text('Graded Readers'), findsOneWidget);
  });
}
