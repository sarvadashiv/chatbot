import asyncio
import logging
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from telegram import (
    BotCommand,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatAction
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    ApplicationBuilder,  
    CommandHandler,
    MessageHandler,
    filters,
)

# Ensure project root is importable when launched as a script.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import ai_engine

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://localhost:8000").rstrip("/")
API_URL = f"{BACKEND_BASE_URL}/query"
RESET_URL = f"{BACKEND_BASE_URL}/reset_session"
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BACKEND_API_KEY = os.getenv("BACKEND_API_KEY", "").strip()
BACKEND_HEADERS = {"X-API-Key": BACKEND_API_KEY} if BACKEND_API_KEY else {}
USE_BACKEND_API = _env_bool("USE_BACKEND_API", True)
LOCAL_AI_FALLBACK = _env_bool("LOCAL_AI_FALLBACK", not USE_BACKEND_API)
_legacy_backend_timeout = float(os.getenv("BACKEND_QUERY_TIMEOUT_SECONDS", "45"))
BACKEND_CONNECT_TIMEOUT_SECONDS = float(os.getenv("BACKEND_CONNECT_TIMEOUT_SECONDS", "5"))
BACKEND_READ_TIMEOUT_SECONDS = float(
    os.getenv(
        "BACKEND_READ_TIMEOUT_SECONDS",
        str(max(_legacy_backend_timeout, 90.0)),
    )
)
BACKEND_REQUEST_RETRIES = int(os.getenv("BACKEND_REQUEST_RETRIES", "1"))
BACKEND_RETRY_DELAY_SECONDS = float(os.getenv("BACKEND_RETRY_DELAY_SECONDS", "1.0"))
SHOW_REPLY_SHORTCUT_KEYBOARD = _env_bool("SHOW_REPLY_SHORTCUT_KEYBOARD", False)
TELEGRAM_SEND_RETRIES = 2
TELEGRAM_SEND_RETRY_DELAY_SECONDS = 1.0
_chat_locks: dict[int, asyncio.Lock] = {}
_local_previous_user_text: dict[int, str] = {}
if USE_BACKEND_API and not BACKEND_API_KEY:
    logging.warning("BACKEND_API_KEY is not set; backend requests may be rejected.")
SHORTCUT_QUERIES = {
    "result": "Results :- https://erp.aktu.ac.in/WebPages/OneView/OneView.aspx",
    "calendar": "Calendar :- https://www.akgec.ac.in/academics/academic-calendar/",
    "admission": "Admission :- https://admissions.akgec.ac.in/",
    "fee": "Structure :-\nNew Students:- https://www.akgec.ac.in/fee-new-students/\nExisting :- https://www.akgec.ac.in/academic-fee/",
    "syllabus": "Syllabus :- https://aktu.ac.in/syllabus.html",
    "circulars": "AKTU Circulars :- https://aktu.ac.in/circulars.html",
}
BOT_COMMANDS = [
    BotCommand("start", "Start bot or clear cache"),
    BotCommand("result", "Get result link"),
    BotCommand("calendar", "Get academic calendar"),
    BotCommand("admission", "Get admission link"),
    BotCommand("fee", "Get fee links"),
    BotCommand("syllabus", "Get syllabus link"),
    BotCommand("circulars", "Get circulars link"),
]
REPLY_SHORTCUT_ROWS = [
    ["/result", "/calendar"],
    ["/admission", "/fee"],
    ["/syllabus", "/circulars"],
    ["/start"],
]


def _set_local_previous_user_text(chat_id: int, text: str) -> None:
    _local_previous_user_text[chat_id] = text


def _get_local_previous_user_text(chat_id: int) -> str:
    return _local_previous_user_text.get(chat_id, "")


def _reset_local_session(chat_id: int) -> None:
    _local_previous_user_text.pop(chat_id, None)


def _reply_shortcut_keyboard():
    return ReplyKeyboardMarkup(
        REPLY_SHORTCUT_ROWS,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Ask about AKTU/AKGEC or use shortcuts...",
    )


def _active_reply_markup(chat_id: int | None = None):
    if SHOW_REPLY_SHORTCUT_KEYBOARD:
        return _reply_shortcut_keyboard()
    return ReplyKeyboardRemove()


async def _fetch_backend_answer(params: dict[str, str]) -> str:
    total_attempts = max(0, BACKEND_REQUEST_RETRIES) + 1
    for attempt in range(1, total_attempts + 1):
        try:
            response = await asyncio.to_thread(
                requests.get,
                API_URL,
                params=params,
                headers=BACKEND_HEADERS,
                timeout=(BACKEND_CONNECT_TIMEOUT_SECONDS, BACKEND_READ_TIMEOUT_SECONDS),
            )
            response.raise_for_status()
            data = response.json()
            return data.get("answer", "I could not process your request right now.")
        except (
            requests.exceptions.ReadTimeout,
            requests.exceptions.ConnectTimeout,
            requests.exceptions.ConnectionError,
        ) as exc:
            if attempt >= total_attempts:
                raise
            sleep_for = BACKEND_RETRY_DELAY_SECONDS * attempt
            logging.warning(
                "Backend transient failure type=%s attempt=%s/%s retry_in=%.2fs",
                type(exc).__name__,
                attempt,
                total_attempts,
                sleep_for,
            )
            await asyncio.sleep(sleep_for)


def reset_backend_session(chat_id: str):
    if not USE_BACKEND_API:
        return
    try:
        requests.post(RESET_URL, params={"chat_id": chat_id}, headers=BACKEND_HEADERS, timeout=10)
    except requests.exceptions.RequestException:
        logging.exception("Failed to reset backend session")


def get_start_text():
    return (
        "Hello!\n"
        "Here for any AKTU and AKGEC updates?\n"
        "You can also use the shortcut keyboard or directly type '/'."
    )


def _get_lock(chat_id: int) -> asyncio.Lock:
    lock = _chat_locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        _chat_locks[chat_id] = lock
    return lock


async def _typing_loop(context, chat_id: int):
    while True:
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except asyncio.CancelledError:
            return
        except (TimedOut, NetworkError):
            logging.warning("Typing indicator request timed out for chat_id=%s", chat_id)
        except Exception:
            logging.exception("Typing indicator failed for chat_id=%s", chat_id)
            return
        await asyncio.sleep(4)


async def _safe_reply(message, text: str, reply_markup=None) -> bool:
    for attempt in range(TELEGRAM_SEND_RETRIES + 1):
        try:
            await message.reply_text(text, reply_markup=reply_markup)
            return True
        except (TimedOut, NetworkError):
            if attempt == TELEGRAM_SEND_RETRIES:
                logging.exception("Failed to send Telegram message after retries")
                return False
            await asyncio.sleep(TELEGRAM_SEND_RETRY_DELAY_SECONDS * (attempt + 1))
    return False


async def start(update: Update, context):
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is not None:
        reset_backend_session(str(chat_id))
        _reset_local_session(chat_id)
    if update.message:
        await _safe_reply(update.message, get_start_text(), reply_markup=_active_reply_markup(chat_id))


async def _run_query(message, context, chat_id: int, q: str):
    if not message:
        return

    params = {"q": q, "chat_id": str(chat_id)}

    lock = _get_lock(chat_id)
    if lock.locked():
        await _safe_reply(
            message,
            "Please wait, I am still replying to your previous message.",
        )
        return

    async with lock:
        typing_task = asyncio.create_task(_typing_loop(context, chat_id))
        answer: str | None = None

        try:
            if USE_BACKEND_API:
                try:
                    answer = await _fetch_backend_answer(params)
                    _set_local_previous_user_text(chat_id, q)
                except requests.exceptions.RequestException:
                    logging.exception("Backend request failed")
                    if not LOCAL_AI_FALLBACK:
                        answer = "Backend is busy right now. Please try again in a few seconds."
                except ValueError:
                    logging.exception("Backend returned invalid JSON")
                    if not LOCAL_AI_FALLBACK:
                        answer = "Backend returned an invalid response. Please try again."

            if answer is None:
                try:
                    previous_user_text = _get_local_previous_user_text(chat_id)
                    _, answer = await asyncio.to_thread(
                        ai_engine.classify_and_reply,
                        q,
                        previous_user_text,
                    )
                    _set_local_previous_user_text(chat_id, q)
                except Exception:
                    logging.exception("Local AI fallback failed")
                    answer = "Server is taking too long right now. Please try again in a few seconds."
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logging.exception("Typing task ended with unexpected error")

    await _safe_reply(message, answer, reply_markup=_active_reply_markup(chat_id))


async def handle(update: Update, context):
    if not update.message or not update.message.text or not update.effective_chat:
        return
    await _run_query(update.message, context, update.effective_chat.id, update.message.text)


async def handle_shortcut_command(update: Update, context):
    if not update.message or not update.message.text or not update.effective_chat:
        return
    command = update.message.text.split()[0].lstrip("/").split("@")[0].lower()
    shortcut_link = SHORTCUT_QUERIES.get(command)
    if not shortcut_link:
        return
    await _safe_reply(
        update.message,
        shortcut_link,
        reply_markup=_active_reply_markup(update.effective_chat.id),
    )


async def on_error(update: object, context):
    if isinstance(context.error, (TimedOut, NetworkError)):
        logging.warning("Telegram transient network error: %s", type(context.error).__name__)
        return
    logging.exception("Telegram handler error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await _safe_reply(
            update.effective_message,
            "Something went wrong. Please try again.",
            reply_markup=_active_reply_markup(update.effective_chat.id if update.effective_chat else None),
        )


async def _post_init(application):
    await application.bot.set_my_commands(BOT_COMMANDS)


def _build_application():
    app = ApplicationBuilder().token(TOKEN).post_init(_post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("result", handle_shortcut_command))
    app.add_handler(CommandHandler("calendar", handle_shortcut_command))
    app.add_handler(CommandHandler("admission", handle_shortcut_command))
    app.add_handler(CommandHandler("fee", handle_shortcut_command))
    app.add_handler(CommandHandler("syllabus", handle_shortcut_command))
    app.add_handler(CommandHandler("circulars", handle_shortcut_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    app.add_error_handler(on_error)
    return app


def main():
    app = _build_application()
    app.run_polling()


if __name__ == "__main__":
    main()
