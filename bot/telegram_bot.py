import asyncio
from contextlib import suppress
import logging
import os

import requests
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

API_URL = "http://backend:8000/query"
RESET_URL = "http://backend:8000/reset_session"
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
QUERY_TIMEOUT_SECONDS = 90
TELEGRAM_SEND_RETRIES = 2
TELEGRAM_SEND_RETRY_DELAY_SECONDS = 1.0
_chat_locks: dict[int, asyncio.Lock] = {}
_shortcut_menu_expanded: dict[int, bool] = {}
SHORTCUT_QUERIES = {
    "result": "Results :- https://erp.aktu.ac.in/WebPages/OneView/OneView.aspx",
    "calendar": "Calendar :- https://www.akgec.ac.in/academics/academic-calendar/",
    "admission": "Admission :- https://admissions.akgec.ac.in/",
    "fee": "Fee Structure :- https://www.akgec.ac.in/admissions/fee-structure/",
    "syllabus": "Syllabus :- https://aktu.ac.in/syllabus.html",
    "circulars": "AKTU Circulars :- https://aktu.ac.in/circulars.html",
}


def _is_shortcut_menu_expanded(chat_id: int | None) -> bool:
    if chat_id is None:
        return False
    return _shortcut_menu_expanded.get(chat_id, False)


def _set_shortcut_menu(chat_id: int, expanded: bool) -> None:
    _shortcut_menu_expanded[chat_id] = expanded


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


async def _typing_loop(context, chat_id: int):
    try:
        while True:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        return


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
    if update.message:
        await _safe_reply(update.message, get_start_text(), reply_markup=fresh_button(chat_id))


async def handle_start_fresh(update: Update, context):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is not None:
        reset_backend_session(str(chat_id))
        _set_shortcut_menu(chat_id, False)
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
        except requests.exceptions.RequestException:
            logging.exception("Backend request failed")
            answer = "Server is taking too long right now. Please try again in a few seconds."
        except ValueError:
            logging.exception("Backend returned invalid JSON")
            answer = "I got an invalid response from the server. Please try again."
        finally:
            typing_task.cancel()
            with suppress(asyncio.CancelledError):
                await typing_task

    await _safe_reply(message, answer, reply_markup=fresh_button(chat_id))


async def handle(update: Update, context):
    if not update.message or not update.message.text or not update.effective_chat:
        return
    await _run_query(update.message, context, update.effective_chat.id, update.message.text)


async def handle_shortcut(update: Update, context):
    query = update.callback_query
    if not query or not query.data or not update.effective_chat:
        return

    await query.answer()
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

    await query.answer()
    action = query.data.split(":", 1)[1].lower()
    _set_shortcut_menu(update.effective_chat.id, action == "show")

    if not query.message:
        return

    try:
        await query.message.edit_reply_markup(reply_markup=fresh_button(update.effective_chat.id))
    except BadRequest:
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
    logging.exception("Telegram handler error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await _safe_reply(
            update.effective_message,
            "Something went wrong. Please try again.",
            reply_markup=fresh_button(update.effective_chat.id if update.effective_chat else None),
        )


def main():
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
    app.run_polling()


if __name__ == "__main__":
    main()
