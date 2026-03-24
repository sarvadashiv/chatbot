import asyncio
import logging
import os
import sys
from pathlib import Path

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
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Ensure project root is importable when launched as a script.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.chat_service import query_chat, reset_chat_session

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


TELEGRAM_SECRET_HEADER_NAME = "X-Telegram-Bot-Api-Secret-Token"
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
SHOW_REPLY_SHORTCUT_KEYBOARD = _env_bool("SHOW_REPLY_SHORTCUT_KEYBOARD", False)
TELEGRAM_SEND_RETRIES = 2
TELEGRAM_SEND_RETRY_DELAY_SECONDS = 1.0
_chat_locks: dict[int, asyncio.Lock] = {}
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


def _require_token() -> str:
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    return TOKEN


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is not None:
        await asyncio.to_thread(reset_chat_session, str(chat_id))
    if update.message:
        await _safe_reply(update.message, get_start_text(), reply_markup=_active_reply_markup(chat_id))


async def _run_query(message, context: ContextTypes.DEFAULT_TYPE, chat_id: int, q: str):
    if not message:
        return

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
            answer = await asyncio.to_thread(query_chat, q, str(chat_id))
        except Exception:
            logging.exception("Telegram query handling failed")
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


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text or not update.effective_chat:
        return
    await _run_query(update.message, context, update.effective_chat.id, update.message.text)


async def handle_shortcut_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
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


def build_application(*, use_updater: bool) -> Application:
    builder = ApplicationBuilder().token(_require_token())
    if not use_updater:
        builder = builder.updater(None)
    app = builder.post_init(_post_init).build()
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


async def start_webhook_application(
    application: Application,
    *,
    webhook_url: str,
    webhook_secret: str = "",
    drop_pending_updates: bool = False,
) -> None:
    if not webhook_url:
        raise RuntimeError("TELEGRAM_WEBHOOK_URL must be configured when webhook mode is enabled.")
    await application.initialize()
    await _post_init(application)
    await application.start()
    await application.bot.set_webhook(
        url=webhook_url,
        secret_token=webhook_secret or None,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=drop_pending_updates,
    )


async def stop_webhook_application(application: Application) -> None:
    await application.stop()
    await application.shutdown()


def main():
    app = build_application(use_updater=True)
    app.run_polling()


if __name__ == "__main__":
    main()
