import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from telegram import Update

from app.chat_service import query_chat, reset_chat_session
from app.config import (
    BACKEND_API_KEY,
    TELEGRAM_WEBHOOK_DROP_PENDING_UPDATES,
    TELEGRAM_WEBHOOK_ENABLED,
    TELEGRAM_WEBHOOK_PATH,
    TELEGRAM_WEBHOOK_SECRET,
    TELEGRAM_WEBHOOK_URL,
)
from app.db.logger import init_db
from app.dashboard.routes import router as dashboard_router
from bot.telegram_bot import (
    TELEGRAM_SECRET_HEADER_NAME,
    build_application,
    start_webhook_application,
    stop_webhook_application,
)

logger = logging.getLogger(__name__)


def _require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    if not BACKEND_API_KEY:
        logger.error("BACKEND_API_KEY is not configured.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Backend authentication is not configured.",
        )
    if not x_api_key or not secrets.compare_digest(x_api_key, BACKEND_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.telegram_app = None
    if TELEGRAM_WEBHOOK_ENABLED:
        telegram_app = build_application(use_updater=False)
        await start_webhook_application(
            telegram_app,
            webhook_url=TELEGRAM_WEBHOOK_URL,
            webhook_secret=TELEGRAM_WEBHOOK_SECRET,
            drop_pending_updates=TELEGRAM_WEBHOOK_DROP_PENDING_UPDATES,
        )
        app.state.telegram_app = telegram_app
        logger.info("Telegram webhook initialized at path=%s", TELEGRAM_WEBHOOK_PATH)
    yield
    telegram_app = getattr(app.state, "telegram_app", None)
    if telegram_app is not None:
        await stop_webhook_application(telegram_app)


app = FastAPI(lifespan=lifespan)
init_db()
app.include_router(dashboard_router)


@app.post("/reset_session", dependencies=[Depends(_require_api_key)])
def reset_session(chat_id: str):
    return reset_chat_session(chat_id)


@app.get("/query", dependencies=[Depends(_require_api_key)])
def query(q: str, chat_id: str | None = None):
    return {"answer": query_chat(q, chat_id)}


@app.post(TELEGRAM_WEBHOOK_PATH, include_in_schema=False)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(
        default=None,
        alias=TELEGRAM_SECRET_HEADER_NAME,
    ),
):
    telegram_app = getattr(request.app.state, "telegram_app", None)
    if telegram_app is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram webhook is not enabled.",
        )
    if TELEGRAM_WEBHOOK_SECRET and not x_telegram_bot_api_secret_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Telegram webhook secret.",
        )
    if TELEGRAM_WEBHOOK_SECRET and not secrets.compare_digest(
        x_telegram_bot_api_secret_token or "",
        TELEGRAM_WEBHOOK_SECRET,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Telegram webhook secret.",
        )

    payload = await request.json()
    await telegram_app.update_queue.put(Update.de_json(payload, telegram_app.bot))
    return {"ok": True}
