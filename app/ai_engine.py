import json
from datetime import datetime
import logging
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from app.config import (
    GEMINI_API_KEY,
    GEMINI_ENABLE_GOOGLE_SEARCH,
    GEMINI_MODEL,
    GEMINI_REQUEST_RETRIES,
    GEMINI_RETRY_BACKOFF_SECONDS,
    GEMINI_REQUIRE_SEARCH_GROUNDING,
)

logger = logging.getLogger(__name__)

VALID_QUERY_MODES = {"smalltalk", "official_info"}
UNWANTED_SOURCE_HOST_SUBSTRINGS = {
    "vertexaisearch.cloud.google.com",
    "vercel.app",
}


def _resolve_redirect_url(url: str, timeout_seconds: int = 6) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("url", "q", "u"):
        values = query.get(key) or []
        if values:
            candidate = values[0].strip()
            if candidate.startswith("http://") or candidate.startswith("https://"):
                return candidate

    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if "vertexaisearch.cloud.google.com" in host and "grounding-api-redirect" in path:
        for method in ("head", "get"):
            try:
                if method == "head":
                    response = requests.head(url, allow_redirects=True, timeout=timeout_seconds)
                else:
                    response = requests.get(url, allow_redirects=True, timeout=timeout_seconds, stream=True)
                final_url = (response.url or "").strip()
                response.close()
                if final_url and "vertexaisearch.cloud.google.com" not in final_url.lower():
                    return final_url
            except requests.exceptions.RequestException:
                continue
        return ""

    return url


def _scan_quoted_value(text: str, start: int, quote: str) -> tuple[str | None, int]:
    out: list[str] = []
    i = start
    escaped = False
    while i < len(text):
        ch = text[i]
        if escaped:
            out.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == quote:
            return "".join(out), i
        else:
            out.append(ch)
        i += 1
    return None, -1


def _unescape_fallback_value(value: str, quote: str) -> str:
    text = value
    text = text.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r")
    text = text.replace('\\"', '"').replace("\\\\", "\\")
    if quote == "'":
        text = text.replace("\\'", "'")
    return text.strip()


def _extract_object_like_field(text: str, field: str) -> str | None:
    for key_quote in ('"', "'"):
        key_pattern = f"{key_quote}{field}{key_quote}"
        search_pos = 0
        while True:
            key_index = text.find(key_pattern, search_pos)
            if key_index == -1:
                break
            colon_index = text.find(":", key_index + len(key_pattern))
            if colon_index == -1:
                break
            value_start = colon_index + 1
            while value_start < len(text) and text[value_start].isspace():
                value_start += 1
            if value_start >= len(text):
                break
            first = text[value_start]
            if first in {'"', "'"}:
                raw_value, end_idx = _scan_quoted_value(text, value_start + 1, first)
                if raw_value is not None and end_idx != -1:
                    return _unescape_fallback_value(raw_value, first)
            else:
                end_idx = value_start
                while end_idx < len(text) and text[end_idx] not in ",}":
                    end_idx += 1
                return text[value_start:end_idx].strip()
            search_pos = key_index + len(key_pattern)
    return None


def _build_system_prompt(today: str) -> str:
    return (
        "You are an assistant only for AKTU and AKGEC queries. Politely reply to general convo divert user towards asking AKTU/AKGEC related queries. "
        f"Today's date (UTC) is {today}. "
        "Use the live Google Search grounding tool to research factual claims. Help user to get to their destination."
        "Do not guess; if you cannot verify a claim, say that clearly. "
        "Return ONLY valid JSON with exactly two keys: mode and answer. "
        "No markdown, no code fences, no extra keys.\n"
        "Output contract:\n"
        '- JSON shape must be {"mode":"smalltalk|official_info","answer":"..."}.\n'
    )


def _messages_to_prompt(messages) -> str:
    lines = []
    for msg in messages:
        role = msg.get("role", "user").upper()
        content = msg.get("content", "")
        lines.append(f"{role}: {content}")
    lines.append("ASSISTANT:")
    return "\n\n".join(lines)


def _tool_attempts() -> list[tuple[str, dict[str, Any] | None]]:
    if not GEMINI_ENABLE_GOOGLE_SEARCH:
        return [("none", None)]

    return [
        ("google_search", {"google_search": {}}),
        ("googleSearch", {"googleSearch": {}}),
    ]


def _extract_grounding_sources(candidate: dict[str, Any]) -> list[dict[str, str]]:
    metadata = candidate.get("groundingMetadata") or {}
    chunks = metadata.get("groundingChunks") or []
    sources: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        web = chunk.get("web") or {}
        if not isinstance(web, dict):
            continue
        url = str(web.get("uri", "")).strip()
        if not url or url in seen_urls:
            continue
        resolved_url = _resolve_redirect_url(url)
        if not resolved_url:
            continue
        resolved_host = urlparse(resolved_url).netloc.lower()
        if any(blocked in resolved_host for blocked in UNWANTED_SOURCE_HOST_SUBSTRINGS):
            continue
        if resolved_url in seen_urls:
            continue
        seen_urls.add(resolved_url)
        title = str(web.get("title", "")).strip() or url
        title = re.sub(r"\s+", " ", title)
        sources.append({"title": title, "url": resolved_url})

    return sources


def _extract_answer_text(data: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    candidates = data.get("candidates", [])
    if not candidates:
        logger.error("Gemini returned no candidates. Response=%s", str(data)[:1200])
        raise RuntimeError("Gemini returned no candidates.")

    candidate = candidates[0]
    parts = candidate.get("content", {}).get("parts", [])
    if not parts:
        logger.error("Gemini returned empty content. Response=%s", str(data)[:1200])
        raise RuntimeError("Gemini returned empty content.")

    text_parts = []
    for part in parts:
        if isinstance(part, dict):
            part_text = str(part.get("text", "")).strip()
            if part_text:
                text_parts.append(part_text)
    text = "\n".join(text_parts).strip()
    if not text:
        logger.error("Gemini returned blank text. Response=%s", str(data)[:1200])
        raise RuntimeError("Gemini returned blank text.")

    sources = _extract_grounding_sources(candidate)
    return text, sources


def _post_gemini_with_retries(url: str, payload: dict[str, Any], timeout: int) -> requests.Response:
    total_attempts = max(0, GEMINI_REQUEST_RETRIES) + 1
    last_exc: Exception | None = None

    for attempt in range(1, total_attempts + 1):
        try:
            return requests.post(
                url,
                params={"key": GEMINI_API_KEY},
                json=payload,
                timeout=timeout,
            )
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt >= total_attempts:
                break
            sleep_for = GEMINI_RETRY_BACKOFF_SECONDS * attempt
            logger.warning(
                "Gemini request transient failure type=%s attempt=%s/%s retry_in=%.2fs detail=%s",
                type(exc).__name__,
                attempt,
                total_attempts,
                sleep_for,
                str(exc)[:300],
            )
            time.sleep(sleep_for)

    if last_exc:
        raise last_exc
    raise RuntimeError("Gemini request failed unexpectedly without exception.")


def _chat(messages, timeout: int = 40) -> dict[str, Any]:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is missing.")

    prompt = _messages_to_prompt(messages)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    base_payload = {
        "contents": [
            {"parts": [{"text": prompt}]}
        ],
        "generationConfig": {
            "temperature": 0.1,
        },
    }

    attempts = _tool_attempts()
    for idx, (tool_name, tool_cfg) in enumerate(attempts):
        payload = dict(base_payload)
        if tool_cfg is not None:
            payload["tools"] = [tool_cfg]
        else:
            payload["generationConfig"] = {
                **base_payload["generationConfig"],
                "responseMimeType": "application/json",
            }

        try:
            response = _post_gemini_with_retries(url=url, payload=payload, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            text, sources = _extract_answer_text(data)
            if GEMINI_REQUIRE_SEARCH_GROUNDING and GEMINI_ENABLE_GOOGLE_SEARCH and not sources:
                logger.warning("Gemini returned answer without grounding metadata.")
            return {
                "text": text,
                "sources": sources,
                "tool": tool_name,
            }
        except requests.exceptions.HTTPError as exc:
            http_response = exc.response
            status = http_response.status_code if http_response is not None else "N/A"
            body = ((http_response.text if http_response is not None else "") or "")[:1200]
            logger.error(
                "Gemini HTTPError status=%s model=%s tool=%s body=%s",
                status,
                GEMINI_MODEL,
                tool_name,
                body,
            )
            is_last_attempt = idx == len(attempts) - 1
            if tool_cfg is not None and status == 400 and not is_last_attempt:
                logger.warning(
                    "Retrying Gemini with fallback search tool config. failed_tool=%s",
                    tool_name,
                )
                continue
            raise
        except requests.exceptions.RequestException as exc:
            is_last_attempt = idx == len(attempts) - 1
            logger.error(
                "Gemini RequestException type=%s model=%s tool=%s detail=%s",
                type(exc).__name__,
                GEMINI_MODEL,
                tool_name,
                str(exc)[:500],
            )
            if not is_last_attempt:
                logger.warning(
                    "Retrying Gemini with fallback search tool config after network failure. failed_tool=%s",
                    tool_name,
                )
                continue
            raise

    raise RuntimeError("Gemini chat failed.")


def _append_sources(answer: str, sources: list[dict[str, str]]) -> str:
    if not sources:
        return answer
    if "https://" in answer or "http://" in answer:
        return answer

    lines = [answer, "", "Sources:"]
    for source in sources[:5]:
        lines.append(f"- {source['title']}: {source['url']}")
    return "\n".join(lines).strip()


def _parse_mode_answer(raw_text: str) -> tuple[str, str]:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    json_candidate = cleaned
    if start != -1 and end != -1 and end > start:
        json_candidate = cleaned[start : end + 1]

    try:
        data = json.loads(json_candidate)
    except json.JSONDecodeError:
        mode_guess = _extract_object_like_field(cleaned, "mode")
        answer_guess = _extract_object_like_field(cleaned, "answer")
        if answer_guess:
            mode = (mode_guess or "official_info").strip().lower()
            if mode not in VALID_QUERY_MODES:
                mode = "official_info"
            return mode, answer_guess

        logger.warning("Gemini returned non-JSON answer, using text fallback.")
        fallback_answer = cleaned.strip()
        if not fallback_answer:
            raise RuntimeError("Gemini returned invalid JSON and empty text answer.")
        return "official_info", fallback_answer

    mode = str(data.get("mode", "official_info")).strip().lower()
    answer = str(data.get("answer", "")).strip()
    if not answer:
        raise RuntimeError("Gemini returned empty answer in JSON.")
    if mode not in VALID_QUERY_MODES:
        mode = "official_info"
    return mode, answer


def classify_and_reply(user_text: str, previous_user_text: str = "") -> tuple[str, str]:
    user_content = user_text
    if previous_user_text:
        user_content = (
            f"Previous user message: {previous_user_text}\n"
            f"Current user message: {user_text}"
        )

    today = datetime.utcnow().strftime("%B %d, %Y")
    result = _chat(
        [
            {
                "role": "system",
                "content": _build_system_prompt(today),
            },
            {"role": "user", "content": user_content},
        ],
        timeout=45,
    )
    mode, answer = _parse_mode_answer(result["text"])
    if answer.startswith("{") and ("\"answer\"" in answer or "'answer'" in answer):
        nested_mode, nested_answer = _parse_mode_answer(answer)
        if nested_answer and nested_answer != answer:
            mode = nested_mode
            answer = nested_answer
    answer = _append_sources(answer, result.get("sources", []))
    if GEMINI_REQUIRE_SEARCH_GROUNDING and GEMINI_ENABLE_GOOGLE_SEARCH and not result.get("sources"):
        answer = (
            f"{answer}"
        )
    return mode, answer
