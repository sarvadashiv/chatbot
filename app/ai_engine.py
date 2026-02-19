import json
from datetime import datetime
import logging
from typing import Any

import requests
from app.config import (
    GEMINI_API_KEY,
    GEMINI_ENABLE_GOOGLE_SEARCH,
    GEMINI_MODEL,
    GEMINI_REQUIRE_SEARCH_GROUNDING,
)

logger = logging.getLogger(__name__)

VALID_QUERY_MODES = {"smalltalk", "official_info"}


def _build_system_prompt(today: str) -> str:
    return (
        "You are an assistant only for AKTU and AKGEC queries. "
        f"Today's date (UTC) is {today}. "
        "Use the live Google Search grounding tool to research factual claims. "
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
        seen_urls.add(url)
        title = str(web.get("title", "")).strip() or url
        sources.append({"title": title, "url": url})

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
            response = requests.post(
                url,
                params={"key": GEMINI_API_KEY},
                json=payload,
                timeout=timeout,
            )
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
            logger.error(
                "Gemini RequestException type=%s model=%s tool=%s",
                type(exc).__name__,
                GEMINI_MODEL,
                tool_name,
            )
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
    answer = _append_sources(answer, result.get("sources", []))
    if GEMINI_REQUIRE_SEARCH_GROUNDING and GEMINI_ENABLE_GOOGLE_SEARCH and not result.get("sources"):
        answer = (
            f"{answer}\n\n"
            "Source verification: live search did not return verifiable source metadata for this answer."
        )
    return mode, answer
