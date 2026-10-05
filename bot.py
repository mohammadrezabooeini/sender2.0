import asyncio
import re

from telegram import BotCommand, Update
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError, TimedOut
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from admin_panel import (
    add_target_channel,
    admin_panel,
    button_click,
    channel_input,
    format_channel_list,
    panel_keyboard,
    remove_target_channel,
)
from config import ADMIN_ID, BOT_TOKEN, SOURCE_CHANNEL, is_admin
from database import (
    get_channel_details,
    get_last_source_message_id,
    init_db,
    remember_source_message,
    update_last_sent,
)
from logger import logger

_COPY_ATTEMPTS = 3
_SECRET_RE = re.compile(r"\d{6,}:[A-Za-z0-9_-]{20,}")


def _safe_error(error):
    text = str(error).split("\n", 1)[0][:180]
    if "api.telegram.org" in text or _SECRET_RE.search(text):
        return error.__class__.__name__
    return text


async def _copy_message(bot, channel_id, message_id):
    delay = 1.0
    last_error = None
    for attempt in range(1, _COPY_ATTEMPTS + 1):
        try:
            await bot.copy_message(
                chat_id=channel_id,
                from_chat_id=SOURCE_CHANNEL,
                message_id=message_id,
            )
            return True, None
        except RetryAfter as exc:
            last_error = exc
            wait = min(float(exc.retry_after), 20.0)
            logger.warning(
                "rate limit chat=%s message=%s wait=%s",
                channel_id,
                message_id,
                exc.retry_after,
            )
            await asyncio.sleep(wait + 0.3)
        except (Forbidden, BadRequest) as exc:
            return False, exc
        except (TimedOut, NetworkError) as exc:
            last_error = exc
            logger.warning(
                "transient forward error chat=%s message=%s attempt=%s: %s",
                channel_id,
                message_id,
                attempt,
                exc,
            )
            await asyncio.sleep(delay)
            delay *= 2
    return False, last_error


async def _notify_failure(bot, bot_data, channel_id, message_id, error):
    seen = bot_data.setdefault("fail_notice", set())
    key = str(channel_id)
    if key in seen:
        return
    seen.add(key)
    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"ارسال پیام {message_id} به {channel_id} ناموفق بود.\n"
                f"{_safe_error(error)}"
            ),
        )
    except TelegramError as exc:
        logger.warning("could not notify admin: %s", exc)


async def forward(update, context):
    msg = update.channel_post
    if msg is None or msg.chat.id != SOURCE_CHANNEL:
        return

    remember_source_message(msg.message_id)
    pending = [
        channel_id
        for channel_id, last_sent in get_channel_details()
        if msg.message_id > last_sent
    ]
    if not pending:
        return

    stats = context.bot_data.setdefault("stats", {"ok": 0, "fail": 0})
    delivered = 0
    for channel_id in pending:
        ok, error = await _copy_message(context.bot, channel_id, msg.message_id)
        if ok:
            update_last_sent(channel_id, msg.message_id)
            stats["ok"] += 1
            delivered += 1
            context.bot_data.get("fail_notice", set()).discard(str(channel_id))
            continue

        stats["fail"] += 1
        logger.error(
            "forward failed chat=%s message=%s error=%s",
            channel_id,
            msg.message_id,
            error,
        )
        if error is not None:
            await _notify_failure(
                context.bot,
                context.bot_data,
                channel_id,
                msg.message_id,
                error,
            )

    logger.info(
        "source message %s delivered to %s/%s channels",
        msg.message_id,
        delivered,
        len(pending),
    )


async def _admin_message(update):
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return None
    if not is_admin(user.id):
        await message.reply_text("دسترسی ندارید.")
        return None
    return message


async def addchannel(update, context):
    message = await _admin_message(update)
    if message is None:
        return
    if not context.args:
        await message.reply_text("مثال: /addchannel -1001234567890")
        return
    try:
        ok, text = await add_target_channel(
            context.bot,
            context.args[0],
            get_last_source_message_id(),
        )
    except TelegramError as exc:
        logger.warning("addchannel failed: %s", exc)
        await message.reply_text("ارتباط با تلگرام برقرار نشد. دوباره تلاش کن.")
        return
    markup = panel_keyboard() if ok else None
    await message.reply_text(text, reply_markup=markup)


async def removechannel(update, context):
    message = await _admin_message(update)
    if message is None:
        return
    if not context.args:
        await message.reply_text("مثال: /removechannel -1001234567890")
        return
    try:
        _ok, text = await remove_target_channel(context.bot, context.args[0])
    except TelegramError as exc:
        logger.warning("removechannel failed: %s", exc)
        await message.reply_text("ارتباط با تلگرام برقرار نشد. دوباره تلاش کن.")
        return
    await message.reply_text(text, reply_markup=panel_keyboard())


async def channels(update, context):
    message = await _admin_message(update)
    if message is None:
        return
    await message.reply_text(format_channel_list(), reply_markup=panel_keyboard())


async def post_init(application):
    me = await application.bot.get_me()
    application.bot_data["bot_id"] = me.id
    try:
        await application.bot.set_my_commands(
            [
                BotCommand("start", "پنل مدیریت"),
                BotCommand("channels", "لیست کانال‌های مقصد"),
                BotCommand("addchannel", "افزودن کانال مقصد"),
                BotCommand("removechannel", "حذف کانال مقصد"),
            ]
        )
    except TelegramError as exc:
        logger.warning("could not set bot commands: %s", exc)
    logger.info("bot started as @%s", me.username)


async def on_error(update, context):
    logger.error("unhandled error", exc_info=context.error)
    message = update.effective_message if update else None
    user = update.effective_user if update else None
    if message is None or user is None or not is_admin(user.id):
        return
    try:
        await message.reply_text("خطایی رخ داد. جزئیات در لاگ ذخیره شد.")
    except TelegramError:
        logger.warning("could not report the error to the admin")


def main():
    init_db()
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(20)
        .read_timeout(20)
        .write_timeout(20)
        .pool_timeout(5)
        .get_updates_connect_timeout(20)
        .get_updates_read_timeout(20)
        .get_updates_write_timeout(20)
        .get_updates_pool_timeout(5)
        .post_init(post_init)
        .build()
    )

    private = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler("start", admin_panel, filters=private))
    app.add_handler(CommandHandler("addchannel", addchannel, filters=private))
    app.add_handler(CommandHandler("removechannel", removechannel, filters=private))
    app.add_handler(CommandHandler("channels", channels, filters=private))
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, forward))
    app.add_handler(CallbackQueryHandler(button_click))
    app.add_handler(
        MessageHandler(private & ~filters.COMMAND, channel_input)
    )
    app.add_error_handler(on_error)

    logger.info("polling started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
