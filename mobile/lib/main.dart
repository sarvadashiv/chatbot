import 'dart:async';
import 'dart:math';

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:url_launcher/url_launcher.dart';

import 'services/chat_api.dart';

void main() {
  runApp(const AdmissionChatbotApp());
}

class AdmissionChatbotApp extends StatelessWidget {
  const AdmissionChatbotApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'AKTU / AKGEC Assistant',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF1E4F8A)),
        useMaterial3: true,
      ),
      home: const ChatScreen(),
    );
  }
}

class _ChatMessage {
  final bool isUser;
  final String text;
  final DateTime timestamp;
  final int visibleLength;

  const _ChatMessage({
    required this.isUser,
    required this.text,
    required this.timestamp,
    required this.visibleLength,
  });

  String get visibleText {
    final end = visibleLength.clamp(0, text.length).toInt();
    return text.substring(0, end);
  }

  bool get isFullyVisible => visibleLength >= text.length;

  _ChatMessage copyWith({int? visibleLength}) {
    return _ChatMessage(
      isUser: isUser,
      text: text,
      timestamp: timestamp,
      visibleLength: visibleLength ?? this.visibleLength,
    );
  }
}

class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  static const String _chatIdStorageKey = 'chat_id';
  static const Duration _typingTick = Duration(milliseconds: 18);
  static const Duration _waitingDotsTick = Duration(milliseconds: 320);

  final TextEditingController _controller = TextEditingController();
  final ScrollController _scrollController = ScrollController();
  final List<_ChatMessage> _messages = <_ChatMessage>[];

  late final Future<void> _initFuture;
  Timer? _typingTimer;
  Timer? _waitingDotsTimer;
  int? _typingMessageIndex;
  String _chatId = '';
  bool _sending = false;
  bool _waitingForReply = false;
  int _waitingDotsCount = 1;

  @override
  void initState() {
    super.initState();
    _initFuture = _initSession();
  }

  Future<void> _initSession() async {
    final prefs = await SharedPreferences.getInstance();
    var chatId = prefs.getString(_chatIdStorageKey)?.trim() ?? '';
    if (chatId.isEmpty) {
      chatId = _generateChatId();
      await prefs.setString(_chatIdStorageKey, chatId);
    }

    if (!mounted) {
      return;
    }

    setState(() {
      _chatId = chatId;
    });
    _addBotMessageAnimated('Hello!\nAny queries?');
  }

  String _generateChatId() {
    final random = Random.secure();
    final timestamp = DateTime.now().millisecondsSinceEpoch;
    final suffix = random.nextInt(1 << 31);
    return '$timestamp-$suffix';
  }

  _ChatMessage _botMessage(String text, {int visibleLength = 0}) =>
      _ChatMessage(
        isUser: false,
        text: text,
        timestamp: DateTime.now(),
        visibleLength: visibleLength,
      );

  _ChatMessage _userMessage(String text) => _ChatMessage(
    isUser: true,
    text: text,
    timestamp: DateTime.now(),
    visibleLength: text.length,
  );

  void _stopTyping({bool completeCurrent = false}) {
    _typingTimer?.cancel();
    _typingTimer = null;

    final typingIndex = _typingMessageIndex;
    if (completeCurrent &&
        typingIndex != null &&
        typingIndex >= 0 &&
        typingIndex < _messages.length) {
      final current = _messages[typingIndex];
      if (!current.isFullyVisible) {
        _messages[typingIndex] = current.copyWith(
          visibleLength: current.text.length,
        );
      }
    }

    _typingMessageIndex = null;
  }

  void _addBotMessageAnimated(String text) {
    if (!mounted) {
      return;
    }

    setState(() {
      _stopTyping(completeCurrent: true);
      _messages.add(_botMessage(text));
      _typingMessageIndex = _messages.length - 1;
    });

    _typingTimer = Timer.periodic(_typingTick, (_) {
      if (!mounted) {
        _stopTyping();
        return;
      }

      final typingIndex = _typingMessageIndex;
      if (typingIndex == null ||
          typingIndex < 0 ||
          typingIndex >= _messages.length) {
        _stopTyping();
        return;
      }

      final current = _messages[typingIndex];
      if (current.isFullyVisible) {
        setState(() {
          _stopTyping();
        });
        return;
      }

      final nextLength = (current.visibleLength + 1).clamp(
        0,
        current.text.length,
      );
      setState(() {
        _messages[typingIndex] = current.copyWith(visibleLength: nextLength);
      });
      _scrollToBottomSoon();
    });

    _scrollToBottomSoon();
  }

  void _startWaitingDots() {
    _waitingDotsTimer?.cancel();
    _waitingDotsCount = 1;
    _waitingDotsTimer = Timer.periodic(_waitingDotsTick, (_) {
      if (!mounted || !_waitingForReply) {
        _stopWaitingDots();
        return;
      }
      setState(() {
        _waitingDotsCount = (_waitingDotsCount % 3) + 1;
      });
      _scrollToBottomSoon();
    });
  }

  void _stopWaitingDots() {
    _waitingDotsTimer?.cancel();
    _waitingDotsTimer = null;
    _waitingDotsCount = 1;
  }

  Future<void> _sendMessage([String? forcedMessage]) async {
    if (_sending || _chatId.isEmpty) {
      return;
    }

    final text = (forcedMessage ?? _controller.text).trim();
    if (text.isEmpty) {
      return;
    }

    setState(() {
      _messages.add(_userMessage(text));
      _sending = true;
      _waitingForReply = true;
      _controller.clear();
    });
    _startWaitingDots();
    _scrollToBottomSoon();

    try {
      final reply = await ChatApi.query(message: text, chatId: _chatId);
      if (!mounted) {
        return;
      }
      setState(() {
        _waitingForReply = false;
      });
      _stopWaitingDots();
      _addBotMessageAnimated(reply);
    } catch (e) {
      if (!mounted) {
        return;
      }
      setState(() {
        _waitingForReply = false;
      });
      _stopWaitingDots();
      _addBotMessageAnimated('Request failed. ${e.toString()}');
    } finally {
      if (mounted) {
        setState(() {
          _sending = false;
          _waitingForReply = false;
        });
        _stopWaitingDots();
      }
      _scrollToBottomSoon();
    }
  }

  Future<void> _startFresh() async {
    if (_chatId.isEmpty || _sending) {
      return;
    }
    try {
      await ChatApi.resetSession(chatId: _chatId);
      if (!mounted) {
        return;
      }
      setState(() {
        _stopTyping();
        _messages.clear();
      });
      _addBotMessageAnimated('Session reset.');
    } catch (e) {
      if (!mounted) {
        return;
      }
      _addBotMessageAnimated('Could not reset session. ${e.toString()}');
    }
    _scrollToBottomSoon();
  }

  void _scrollToBottomSoon() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scrollController.hasClients) {
        return;
      }
      _scrollController.animateTo(
        _scrollController.position.maxScrollExtent,
        duration: const Duration(milliseconds: 250),
        curve: Curves.easeOut,
      );
    });
  }

  @override
  void dispose() {
    _stopTyping();
    _stopWaitingDots();
    _controller.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<void>(
      future: _initFuture,
      builder: (context, snapshot) {
        final ready = snapshot.connectionState == ConnectionState.done;
        return Scaffold(
          appBar: AppBar(
            title: const Text('AKGEC Assistant'),
            actions: <Widget>[
              IconButton(
                tooltip: 'Start fresh',
                onPressed: ready ? _startFresh : null,
                icon: const Icon(Icons.refresh),
              ),
            ],
          ),
          body: Column(
            children: <Widget>[
              if (!ready) const LinearProgressIndicator(minHeight: 2),
              Expanded(
                child: ListView.builder(
                  controller: _scrollController,
                  padding: const EdgeInsets.symmetric(
                    horizontal: 12,
                    vertical: 8,
                  ),
                  itemCount: _messages.length + (_waitingForReply ? 1 : 0),
                  itemBuilder: (context, index) {
                    if (index >= _messages.length) {
                      return _TypingDotsIndicator(dotsCount: _waitingDotsCount);
                    }
                    final msg = _messages[index];
                    return _MessageBubble(message: msg);
                  },
                ),
              ),
              SafeArea(
                top: false,
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(12, 8, 12, 12),
                  child: Row(
                    children: <Widget>[
                      Expanded(
                        child: TextField(
                          controller: _controller,
                          enabled: ready && !_sending,
                          onSubmitted: (_) => _sendMessage(),
                          decoration: const InputDecoration(
                            hintText: 'Ask anything...',
                            border: OutlineInputBorder(),
                          ),
                        ),
                      ),
                      const SizedBox(width: 8),
                      IconButton.filled(
                        onPressed: ready && !_sending ? _sendMessage : null,
                        icon: const Icon(Icons.send),
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}

class _MessageBubble extends StatelessWidget {
  final _ChatMessage message;

  const _MessageBubble({required this.message});

  @override
  Widget build(BuildContext context) {
    final textStyle =
        Theme.of(context).textTheme.bodyMedium?.copyWith(fontSize: 15) ??
        const TextStyle(fontSize: 15);

    if (!message.isUser) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 5, horizontal: 4),
        child: Align(
          alignment: Alignment.centerLeft,
          child: _FormattedBotText(
            text: message.visibleText,
            isTyping: !message.isFullyVisible,
            style: textStyle,
          ),
        ),
      );
    }

    return Align(
      alignment: Alignment.centerRight,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 5),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        constraints: const BoxConstraints(maxWidth: 340),
        decoration: BoxDecoration(
          color: const Color(0xFFD8E8FF),
          borderRadius: BorderRadius.circular(12),
        ),
        child: _LinkText(text: message.visibleText, style: textStyle),
      ),
    );
  }
}

class _FormattedBotText extends StatelessWidget {
  static final RegExp _urlRegex = RegExp(
    r'((?:https?:\/\/|www\.)[^\s]+|(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(?:\/[^\s]*)?)',
    caseSensitive: false,
  );
  final String text;
  final bool isTyping;
  final TextStyle style;

  const _FormattedBotText({
    required this.text,
    required this.isTyping,
    required this.style,
  });

  String _normalizeUrl(String rawUrl) {
    var normalized = rawUrl.trim();
    while (normalized.isNotEmpty &&
        ',.;:!?]}'.contains(normalized[normalized.length - 1])) {
      normalized = normalized.substring(0, normalized.length - 1);
    }
    while (normalized.endsWith(')') &&
        '('.allMatches(normalized).length < ')'.allMatches(normalized).length) {
      normalized = normalized.substring(0, normalized.length - 1);
    }
    return normalized;
  }

  Uri? _toLaunchableUri(String rawUrl) {
    final candidate = rawUrl.trim();
    if (candidate.isEmpty || candidate.contains('@')) {
      return null;
    }

    final parsed = Uri.tryParse(candidate);
    if (parsed != null &&
        (parsed.scheme == 'http' || parsed.scheme == 'https') &&
        parsed.host.isNotEmpty) {
      return parsed;
    }

    final shouldAssumeHttps =
        candidate.startsWith('www.') ||
        (!candidate.contains('://') && candidate.contains('.'));
    if (!shouldAssumeHttps) {
      return null;
    }

    final withHttps = Uri.tryParse('https://$candidate');
    if (withHttps == null || withHttps.host.isEmpty) {
      return null;
    }
    return withHttps;
  }

  String _markdownEscaped(String value) {
    return value
        .replaceAll(r'\', r'\\')
        .replaceAll('[', r'\[')
        .replaceAll(']', r'\]');
  }

  String _linkifyMarkdown(String input) {
    final buffer = StringBuffer();
    var cursor = 0;
    for (final match in _urlRegex.allMatches(input)) {
      if (match.start > cursor) {
        buffer.write(input.substring(cursor, match.start));
      }

      final rawUrl = match.group(0) ?? '';
      final normalizedUrl = _normalizeUrl(rawUrl);
      final trailing = rawUrl.substring(normalizedUrl.length);
      final launchableUri = _toLaunchableUri(normalizedUrl);

      if (launchableUri == null) {
        buffer.write(rawUrl);
      } else {
        final label = _markdownEscaped(normalizedUrl);
        final href = launchableUri.toString().replaceAll(')', '%29');
        buffer.write('[$label]($href)');
        buffer.write(trailing);
      }

      cursor = match.end;
    }

    if (cursor < input.length) {
      buffer.write(input.substring(cursor));
    }
    return buffer.toString();
  }

  Future<void> _openLink(String href) async {
    final uri = Uri.tryParse(href);
    if (uri == null) {
      return;
    }
    final openedInBrowser = await launchUrl(
      uri,
      mode: LaunchMode.externalApplication,
    );
    if (!openedInBrowser) {
      await launchUrl(uri, mode: LaunchMode.platformDefault);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (isTyping) {
      return _LinkText(text: text, style: style);
    }

    final markdownData = _linkifyMarkdown(text);
    final styleSheet = MarkdownStyleSheet.fromTheme(Theme.of(context)).copyWith(
      p: style.copyWith(height: 1.45),
      a: style.copyWith(
        color: Colors.blue.shade700,
        decoration: TextDecoration.underline,
        height: 1.45,
      ),
      h1: style.copyWith(
        fontSize: 22,
        fontWeight: FontWeight.w700,
        height: 1.3,
      ),
      h2: style.copyWith(
        fontSize: 19,
        fontWeight: FontWeight.w700,
        height: 1.3,
      ),
      h3: style.copyWith(
        fontSize: 17,
        fontWeight: FontWeight.w600,
        height: 1.3,
      ),
      blockSpacing: 10,
      listBullet: style.copyWith(height: 1.45),
      code: style.copyWith(
        backgroundColor: const Color(0xFFE9EEF5),
        fontFamily: 'monospace',
      ),
      codeblockDecoration: BoxDecoration(
        color: const Color(0xFFE9EEF5),
        borderRadius: BorderRadius.circular(8),
      ),
      codeblockPadding: const EdgeInsets.all(10),
    );

    return MarkdownBody(
      data: markdownData,
      styleSheet: styleSheet,
      onTapLink: (text, href, title) {
        if (href == null || href.isEmpty) {
          return;
        }
        _openLink(href);
      },
    );
  }
}

class _LinkText extends StatefulWidget {
  final String text;
  final TextStyle style;

  const _LinkText({required this.text, required this.style});

  @override
  State<_LinkText> createState() => _LinkTextState();
}

class _LinkTextState extends State<_LinkText> {
  static final RegExp _urlRegex = RegExp(
    r'((?:https?:\/\/|www\.)[^\s]+|(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(?:\/[^\s]*)?)',
    caseSensitive: false,
  );
  final List<TapGestureRecognizer> _recognizers = <TapGestureRecognizer>[];

  @override
  void didUpdateWidget(covariant _LinkText oldWidget) {
    super.didUpdateWidget(oldWidget);
    _disposeRecognizers();
  }

  @override
  void dispose() {
    _disposeRecognizers();
    super.dispose();
  }

  void _disposeRecognizers() {
    for (final recognizer in _recognizers) {
      recognizer.dispose();
    }
    _recognizers.clear();
  }

  String _normalizeUrl(String rawUrl) {
    var normalized = rawUrl.trim();
    while (normalized.isNotEmpty &&
        ',.;:!?]}'.contains(normalized[normalized.length - 1])) {
      normalized = normalized.substring(0, normalized.length - 1);
    }
    while (normalized.endsWith(')') &&
        '('.allMatches(normalized).length < ')'.allMatches(normalized).length) {
      normalized = normalized.substring(0, normalized.length - 1);
    }
    return normalized;
  }

  Uri? _toLaunchableUri(String rawUrl) {
    final candidate = rawUrl.trim();
    if (candidate.isEmpty || candidate.contains('@')) {
      return null;
    }

    final parsed = Uri.tryParse(candidate);
    if (parsed != null &&
        (parsed.scheme == 'http' || parsed.scheme == 'https') &&
        parsed.host.isNotEmpty) {
      return parsed;
    }

    final shouldAssumeHttps =
        candidate.startsWith('www.') ||
        (!candidate.contains('://') && candidate.contains('.'));
    if (!shouldAssumeHttps) {
      return null;
    }

    final withHttps = Uri.tryParse('https://$candidate');
    if (withHttps == null || withHttps.host.isEmpty) {
      return null;
    }
    return withHttps;
  }

  TapGestureRecognizer _buildLinkRecognizer(Uri uri) {
    final recognizer =
        TapGestureRecognizer()
          ..onTap = () async {
            final openedInBrowser = await launchUrl(
              uri,
              mode: LaunchMode.externalApplication,
            );
            if (!openedInBrowser) {
              await launchUrl(uri, mode: LaunchMode.platformDefault);
            }
          };
    _recognizers.add(recognizer);
    return recognizer;
  }

  List<InlineSpan> _buildSpans() {
    final spans = <InlineSpan>[];
    var cursor = 0;

    for (final match in _urlRegex.allMatches(widget.text)) {
      if (match.start > cursor) {
        spans.add(TextSpan(text: widget.text.substring(cursor, match.start)));
      }

      final rawUrl = match.group(0) ?? '';
      final normalizedUrl = _normalizeUrl(rawUrl);
      final trailing = rawUrl.substring(normalizedUrl.length);

      final launchableUri = _toLaunchableUri(normalizedUrl);
      if (launchableUri == null) {
        spans.add(TextSpan(text: rawUrl));
      } else {
        spans.add(
          TextSpan(
            text: normalizedUrl,
            style: widget.style.copyWith(
              color: Colors.blue.shade700,
              decoration: TextDecoration.underline,
            ),
            recognizer: _buildLinkRecognizer(launchableUri),
          ),
        );
        if (trailing.isNotEmpty) {
          spans.add(TextSpan(text: trailing));
        }
      }

      cursor = match.end;
    }

    if (cursor < widget.text.length) {
      spans.add(TextSpan(text: widget.text.substring(cursor)));
    }

    if (spans.isEmpty) {
      spans.add(TextSpan(text: widget.text));
    }
    return spans;
  }

  @override
  Widget build(BuildContext context) {
    return Text.rich(TextSpan(style: widget.style, children: _buildSpans()));
  }
}

class _TypingDotsIndicator extends StatelessWidget {
  final int dotsCount;

  const _TypingDotsIndicator({required this.dotsCount});

  @override
  Widget build(BuildContext context) {
    final dots = '.' * dotsCount.clamp(1, 3);
    return Padding(
      padding: const EdgeInsets.only(left: 16, right: 16, bottom: 8),
      child: Align(
        alignment: Alignment.centerLeft,
        child: Text(
          dots,
          style: Theme.of(context).textTheme.bodyMedium?.copyWith(
            fontSize: 22,
            letterSpacing: 3,
            color: Colors.black54,
          ),
        ),
      ),
    );
  }
}
