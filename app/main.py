import json
import logging

import requests
from fastapi import FastAPI

from app import ai_engine
from app.cache import delete_cache, delete_cache_by_prefix, get_cache, set_cache
from app.config import LIVE_SEARCH_BYPASS_CACHE, QUERY_CACHE_TTL_SECONDS
from app.db.logger import init_db, log_query
from app.dashboard.routes import router as dashboard_router

logger = logging.getLogger(__name__)

app = FastAPI()
init_db()
app.include_router(dashboard_router)


@app.post("/reset_session")
def reset_session(chat_id: str):
    delete_cache(f"ctx:{chat_id}")
    delete_cache_by_prefix(f"q:{chat_id}:")
    return {"ok": True, "message": "Session reset"}


@app.get("/query")
def query(q: str, chat_id: str | None = None):
    cache_key = f"q:{chat_id}:{q}" if chat_id else f"q:{q}"
    use_cache = not LIVE_SEARCH_BYPASS_CACHE
    cached = get_cache(cache_key) if use_cache else None

    if cached:
        return {"answer": cached}

    previous_user_text = ""
    if chat_id:
        raw_ctx = get_cache(f"ctx:{chat_id}")
        if raw_ctx:
            try:
                previous_user_text = json.loads(raw_ctx).get("last_user_query", "")
            except Exception:
                previous_user_text = ""

    try:
        mode, reply = ai_engine.classify_and_reply(q, previous_user_text=previous_user_text)
        status = "AI_ONE_CALL"
    except requests.exceptions.HTTPError as exc:
        response = exc.response
        status_code = response.status_code if response is not None else None
        logger.error("classify_and_reply failed type=%s status=%s", type(exc).__name__, status_code)
        mode = "official_info"
        if status_code == 429:
            reply = "AI quota is currently exhausted. Please try again shortly."
            status = "LLM_QUOTA_EXCEEDED"
        else:
            reply = "AI service is unavailable right now. Please try again."
            status = "LLM_HTTP_ERROR"
    except requests.exceptions.RequestException as exc:
        logger.error("classify_and_reply failed type=%s", type(exc).__name__)
        mode = "official_info"
        reply = "AI service is taking too long right now. Please try again."
        status = "LLM_TIMEOUT"
    except RuntimeError as exc:
        logger.error("classify_and_reply failed type=%s", type(exc).__name__)
        mode = "official_info"
        if "currently unavailable" in str(exc).lower():
            reply = "All configured AI models are temporarily unavailable. Please try again later."
            status = "LLM_MODELS_UNAVAILABLE"
        else:
            reply = "AI service is taking too long right now. Please try again."
            status = "LLM_TIMEOUT"

    log_query(
        query=q,
        intent=mode,
        status=status,
        confidence="N/A",
    )

    if chat_id:
        set_cache(f"ctx:{chat_id}", json.dumps({"last_user_query": q}), 86400)
    if use_cache and QUERY_CACHE_TTL_SECONDS > 0:
        set_cache(cache_key, reply, QUERY_CACHE_TTL_SECONDS)
    return {"answer": reply}
