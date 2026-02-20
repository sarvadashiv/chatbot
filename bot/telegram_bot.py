import asyncio
import logging
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
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

BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://localhost:8000").rstrip("/")
API_URL = f"{BACKEND_BASE_URL}/query"
RESET_URL = f"{BACKEND_BASE_URL}/reset_session"
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BOT_MODE = os.getenv("BOT_MODE", "polling").strip().lower()
WEBHOOK_PUBLIC_BASE_URL = os.getenv("WEBHOOK_PUBLIC_BASE_URL", "").strip().rstrip("/")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/telegram").strip()
if not WEBHOOK_PATH.startswith("/"):
    WEBHOOK_PATH = f"/{WEBHOOK_PATH}"
WEBHOOK_LISTEN = os.getenv("WEBHOOK_LISTEN", "0.0.0.0").strip() or "0.0.0.0"
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8443"))
WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN", "").strip() or None
QUERY_TIMEOUT_SECONDS = 90
TELEGRAM_SEND_RETRIES = 2
TELEGRAM_SEND_RETRY_DELAY_SECONDS = 1.0
_chat_locks: dict[int, asyncio.Lock] = {}
_shortcut_menu_expanded: dict[int, bool] = {}
_local_previous_user_text: dict[int, str] = {}
SHORTCUT_QUERIES = {
    "result": "Results :- https://erp.aktu.ac.in/WebPages/OneView/OneView.aspx",
    "calendar": "Calendar :- https://www.akgec.ac.in/academics/academic-calendar/",
    "admission": "Admission :- https://admissions.akgec.ac.in/",
    "fee": "Fee Structure :-\nNew :- https://www.akgec.ac.in/fee-new-students/\nExisting :- https://www.akgec.ac.in/academic-fee/",
    "syllabus": "Syllabus :- https://aktu.ac.in/syllabus.html",
    "circulars": "AKTU Circulars :- https://aktu.ac.in/circulars.html",
}


def _is_shortcut_menu_expanded(chat_id: int | None) -> bool:
    if chat_id is None:
        return False
    return _shortcut_menu_expanded.get(chat_id, False)


def _set_shortcut_menu(chat_id: int, expanded: bool) -> None:
    _shortcut_menu_expanded[chat_id] = expanded


def _set_local_previous_user_text(chat_id: int, text: str) -> None:
    _local_previous_user_text[chat_id] = text


def _get_local_previous_user_text(chat_id: int) -> str:
    return _local_previous_user_text.get(chat_id, "")


def _reset_local_session(chat_id: int) -> None:
    _local_previous_user_text.pop(chat_id, None)


def fresh_button(chat_id: int | None = None):
    if not _is_shortcut_menu_expanded(chat_id):
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("More...", callback_data="toggle_shortcuts:show")],
                [InlineKeyboardButton("Start Fresh", callback_data="start_fresh")],
            ]
        )

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Result", callback_data="shortcut:result"),
                InlineKeyboardButton("Calendar", callback_data="shortcut:calendar"),
            ],
            [
                InlineKeyboardButton("Admission", callback_data="shortcut:admission"),
                InlineKeyboardButton("Fee", callback_data="shortcut:fee"),
            ],
            [
                InlineKeyboardButton("Syllabus", callback_data="shortcut:syllabus"),
                InlineKeyboardButton("Circulars", callback_data="shortcut:circulars"),
            ],
            [InlineKeyboardButton("Hide...", callback_data="toggle_shortcuts:hide")],
            [InlineKeyboardButton("Start Fresh", callback_data="start_fresh")],
        ]
    )


def reset_backend_session(chat_id: str):
    try:
        requests.post(RESET_URL, params={"chat_id": chat_id}, timeout=10)
    except requests.exceptions.RequestException:
        logging.exception("Failed to reset backend session")


def get_start_text():
    return (
        "Hello!\n"
        "Ask me anything about AKTU and AKGEC official information.\n"
        "You can also use the shortcut buttons below."
    )


def _get_lock(chat_id: int) -> asyncio.Lock:
    lock = _chat_locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        _chat_locks[chat_id] = lock
    return lock


async def _safe_callback_answer(query) -> bool:
    for attempt in range(TELEGRAM_SEND_RETRIES + 1):
        try:
            await query.answer()
            return True
        except (TimedOut, NetworkError):
            if attempt == TELEGRAM_SEND_RETRIES:
                logging.exception("Failed to answer callback query after retries")
                return False
            await asyncio.sleep(TELEGRAM_SEND_RETRY_DELAY_SECONDS * (attempt + 1))
        except BadRequest:
            return False
    return False


async def _typing_loop(context, chat_id: int):
    while True:
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except asyncio.CancelledError:
            return
        except (TimedOut, NetworkError):
            logging.warning("Typing indicator request timed out for chat_id=%s", chat_id)
        except BadRequest:
            logging.warning("Typing indicator rejected for chat_id=%s", chat_id)
            return
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
        _set_shortcut_menu(chat_id, False)
        _reset_local_session(chat_id)
    if update.message:
        await _safe_reply(update.message, get_start_text(), reply_markup=fresh_button(chat_id))


async def handle_start_fresh(update: Update, context):
    query = update.callback_query
    if not query:
        return
    await _safe_callback_answer(query)
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is not None:
        reset_backend_session(str(chat_id))
        _set_shortcut_menu(chat_id, False)
        _reset_local_session(chat_id)
    if query.message:
        await _safe_reply(query.message, get_start_text(), reply_markup=fresh_button(chat_id))


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

        try:
            response = await asyncio.to_thread(
                requests.get,
                API_URL,
                params=params,
                timeout=QUERY_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()
            answer = data.get("answer", "I could not process your request right now.")
            _set_local_previous_user_text(chat_id, q)
        except requests.exceptions.RequestException:
            logging.exception("Backend request failed, using local AI fallback")
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
        except ValueError:
            logging.exception("Backend returned invalid JSON")
            answer = "I got an invalid response from the server. Please try again."
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logging.exception("Typing task ended with unexpected error")

    await _safe_reply(message, answer, reply_markup=fresh_button(chat_id))


async def handle(update: Update, context):
    if not update.message or not update.message.text or not update.effective_chat:
        return
    await _run_query(update.message, context, update.effective_chat.id, update.message.text)


async def handle_shortcut(update: Update, context):
    query = update.callback_query
    if not query or not query.data or not update.effective_chat:
        return

    await _safe_callback_answer(query)
    if not query.data.startswith("shortcut:"):
        return

    key = query.data.split(":", 1)[1].lower()
    shortcut_link = SHORTCUT_QUERIES.get(key)
    if not shortcut_link:
        return

    await _safe_reply(
        query.message,
        shortcut_link,
        reply_markup=fresh_button(update.effective_chat.id),
    )


async def handle_toggle_shortcuts(update: Update, context):
    query = update.callback_query
    if not query or not query.data or not update.effective_chat:
        return

    await _safe_callback_answer(query)
    action = query.data.split(":", 1)[1].lower()
    _set_shortcut_menu(update.effective_chat.id, action == "show")

    if not query.message:
        return

    try:
        await query.message.edit_reply_markup(reply_markup=fresh_button(update.effective_chat.id))
    except (BadRequest, TimedOut, NetworkError):
        return


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
        reply_markup=fresh_button(update.effective_chat.id),
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
            reply_markup=fresh_button(update.effective_chat.id if update.effective_chat else None),
        )


def _build_application():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("result", handle_shortcut_command))
    app.add_handler(CommandHandler("calendar", handle_shortcut_command))
    app.add_handler(CommandHandler("admission", handle_shortcut_command))
    app.add_handler(CommandHandler("fee", handle_shortcut_command))
    app.add_handler(CommandHandler("syllabus", handle_shortcut_command))
    app.add_handler(CommandHandler("circulars", handle_shortcut_command))
    app.add_handler(CallbackQueryHandler(handle_toggle_shortcuts, pattern="^toggle_shortcuts:"))
    app.add_handler(CallbackQueryHandler(handle_shortcut, pattern="^shortcut:"))
    app.add_handler(CallbackQueryHandler(handle_start_fresh, pattern="^start_fresh$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    app.add_error_handler(on_error)
    return app


def _webhook_url() -> str:
    if not WEBHOOK_PUBLIC_BASE_URL:
        raise RuntimeError("WEBHOOK_PUBLIC_BASE_URL is required when BOT_MODE=webhook.")
    return f"{WEBHOOK_PUBLIC_BASE_URL}{WEBHOOK_PATH}"


def main():
    app = _build_application()
    if BOT_MODE == "webhook":
        app.run_webhook(
            listen=WEBHOOK_LISTEN,
            port=WEBHOOK_PORT,
            webhook_url=_webhook_url(),
            url_path=WEBHOOK_PATH.lstrip("/"),
            secret_token=WEBHOOK_SECRET_TOKEN,
            drop_pending_updates=False,
        )
        return
    if BOT_MODE != "polling":
        raise RuntimeError("BOT_MODE must be either 'polling' or 'webhook'.")
    app.run_polling()


if __name__ == "__main__":
    main()
