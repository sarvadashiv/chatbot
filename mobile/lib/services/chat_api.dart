import 'dart:convert';

import 'package:http/http.dart' as http;

class ChatApiException implements Exception {
  final String message;

  const ChatApiException(this.message);

  @override
  String toString() => message;
}

class ChatApi {
  static const String _baseUrl = String.fromEnvironment(
    'BACKEND_BASE_URL',
    defaultValue: 'http://30.31.4.24:8000',
  );
  static const String _apiKey = String.fromEnvironment(
    'BACKEND_API_KEY',
    defaultValue: '',
  );

  static String get _normalizedBaseUrl =>
      _baseUrl.endsWith('/')
          ? _baseUrl.substring(0, _baseUrl.length - 1)
          : _baseUrl;

  static Map<String, String> _headers({bool jsonContentType = false}) {
    final headers = <String, String>{};
    if (_apiKey.isNotEmpty) {
      headers['X-API-Key'] = _apiKey;
    }
    if (jsonContentType) {
      headers['Content-Type'] = 'application/json';
    }
    return headers;
  }

  static Future<String> query({
    required String message,
    required String chatId,
  }) async {
    final uri = Uri.parse('$_normalizedBaseUrl/query').replace(
      queryParameters: <String, String>{'q': message, 'chat_id': chatId},
    );

    final response = await http.get(uri, headers: _headers());

    if (response.statusCode != 200) {
      throw ChatApiException(
        'Backend error ${response.statusCode}: ${response.body}',
      );
    }

    final Object? decoded = jsonDecode(response.body);
    if (decoded is! Map<String, dynamic>) {
      throw const ChatApiException('Invalid response format from backend.');
    }

    final answer = decoded['answer']?.toString().trim() ?? '';
    if (answer.isEmpty) {
      throw const ChatApiException('Backend returned empty answer.');
    }
    return answer;
  }

  static Future<void> resetSession({required String chatId}) async {
    final uri = Uri.parse(
      '$_normalizedBaseUrl/reset_session',
    ).replace(queryParameters: <String, String>{'chat_id': chatId});

    final response = await http.post(uri, headers: _headers());

    if (response.statusCode != 200) {
      throw ChatApiException(
        'Session reset failed (${response.statusCode}): ${response.body}',
      );
    }
  }
}
