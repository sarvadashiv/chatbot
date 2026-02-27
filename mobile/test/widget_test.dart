import 'package:flutter_test/flutter_test.dart';

import 'package:admission_chatbot/main.dart';

void main() {
  testWidgets('App renders title', (WidgetTester tester) async {
    await tester.pumpWidget(const AdmissionChatbotApp());
    await tester.pumpAndSettle();

    expect(find.text('AKTU / AKGEC Assistant'), findsOneWidget);
  });
}
