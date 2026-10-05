import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageOriginChannel
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import BadRequest, Forbidden, TelegramError

from config import SOURCE_CHANNEL, is_admin
from database import (
    add_channel,
    get_channel_details,
    get_channels,
    get_last_source_message_id,
    remove_channel,
)
from logger import logger

ADD_PROMPT = (
    "مقصد را بفرست:\n"
    "آیدی عددی (مثل -100...)، @username، لینک t.me، "
    "یا یک پیام فورواردشده از خود کانال."
)
REMOVE_PROMPT = "آیدی، @username یا لینک کانالی که باید حذف شود را بفرست."


def panel_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ افزودن کانال", callback_data="add")],
            [InlineKeyboardButton("➖ حذف کانال", callback_data="remove")],
            [InlineKeyboardButton("📋 لیست کانال‌ها", callback_data="list")],
            [InlineKeyboardButton("📊 وضعیت", callback_data="status")],
        ]
    )


def back_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("بازگشت", callback_data="panel")]]
    )


def panel_text():
    return (
        "⚙️ پنل مدیریت\n\n"
        f"کانال مبدأ: {SOURCE_CHANNEL}\n"
        f"تعداد مقصد: {len(get_channels())}"
    )


def status_text(bot_data):
    stats = bot_data.get("stats", {})
    last_seen = get_last_source_message_id()
    return (
        "✅ ربات روشن است\n\n"
        f"کانال مبدأ: {SOURCE_CHANNEL}\n"
        f"تعداد مقصد: {len(get_channels())}\n"
        f"آخرین پیام دیده‌شده: {last_seen or '—'}\n"
        f"ارسال موفق در این اجرا: {stats.get('ok', 0)}\n"
        f"ارسال ناموفق در این اجرا: {stats.get('fail', 0)}"
    )


def format_channel_list():
    rows = get_channel_details()
    if not rows:
        return "هیچ کانالی ثبت نشده."
    lines = [f"📋 {len(rows)} کانال مقصد:"]
    for channel_id, last_sent in rows:
        sent = last_sent or "—"
        lines.append(f"• {channel_id}  (آخرین پیام: {sent})")
    return "\n".join(lines)[:4000]


def parse_target(raw):
    text = str(raw).strip()
    from_link = False
    for prefix in (
        "https://t.me/",
        "http://t.me/",
        "https://telegram.me/",
        "http://telegram.me/",
    ):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
            from_link = True
            break
    if text.lower().startswith("t.me/"):
        text = text[5:]
        from_link = True

    text = text.split("?")[0].strip().strip("/")
    parts = [part for part in text.split("/") if part]
    if not parts or parts[0].startswith("+"):
        return None

    if parts[0].lower() == "c" and len(parts) >= 2 and parts[1].isdigit():
        return int(f"-100{parts[1]}")

    head = parts[0]
    if head.startswith("@"):
        head = head[1:]
        from_link = True
    if re.fullmatch(r"-?\d+", head):
        return int(head)
    if (from_link or str(raw).strip().startswith("@")) and re.fullmatch(
        r"[A-Za-z0-9_]{4,32}", head
    ):
        return f"@{head}"
    return None


def channel_ref_from_message(message):
    origin = message.forward_origin
    if isinstance(origin, MessageOriginChannel):
        return origin.chat.id
    if message.text:
        return message.text.strip()
    if message.caption:
        return message.caption.strip()
    return None


def _can_post(member):
    if member.status == ChatMemberStatus.OWNER:
        return True
    if member.status != ChatMemberStatus.ADMINISTRATOR:
        return False
    if member.can_post_messages is False:
        return False
    if getattr(member, "can_send_messages", None) is False:
        return False
    return True


def _not_member_text():
    return (
        "کانال ثبت نشد.\n"
        "ربات داخل این کانال نیست. در خود کانال برو به Administrators، "
        "ربات را Admin کن و Post Messages را روشن کن."
    )


async def _posting_status(bot, chat_id):
    """Return yes, no, or unknown.

    Telegram often answers getChatMember with "member list is inaccessible"
    for channel admins. That is not proof the bot cannot post.
    """
    try:
        member = await bot.get_chat_member(chat_id, bot.id)
    except BadRequest as exc:
        if "member list is inaccessible" in str(exc).lower():
            logger.warning(
                "member list hidden for %s; saving the channel anyway",
                chat_id,
            )
            return "unknown"
        logger.warning("membership check failed for %s: %s", chat_id, exc)
        return "no"
    except Forbidden as exc:
        logger.warning("membership check failed for %s: %s", chat_id, exc)
        return "no"
    return "yes" if _can_post(member) else "no"


async def add_target_channel(bot, raw, last_sent):
    parsed = parse_target(raw)
    if parsed is None:
        return False, "آیدی نامعتبر است. عدد کانال، @username یا لینک t.me بفرست."
    if parsed == SOURCE_CHANNEL:
        return False, "کانال مبدأ را نمی‌توان مقصد کرد."

    try:
        chat = await bot.get_chat(parsed)
    except Forbidden as exc:
        logger.warning("get_chat failed for %s: %s", parsed, exc)
        return False, _not_member_text()
    except BadRequest as exc:
        logger.warning("get_chat failed for %s: %s", parsed, exc)
        return False, "کانال پیدا نشد. آیدی را از خود کانال کپی کن."

    if chat.id == SOURCE_CHANNEL:
        return False, "کانال مبدأ را نمی‌توان مقصد کرد."
    if chat.type not in {ChatType.CHANNEL, ChatType.SUPERGROUP, ChatType.GROUP}:
        return False, "فقط کانال یا گروه را می‌توان مقصد کرد."

    status = await _posting_status(bot, chat.id)
    if status == "no":
        return False, (
            "کانال ثبت نشد.\n"
            "ربات باید Admin باشد و مجوز Post Messages داشته باشد."
        )

    if not add_channel(chat.id, last_sent):
        return False, "این کانال قبلاً ثبت شده."

    title = chat.title or str(chat.id)
    logger.info("destination added: %s (%s) access=%s", title, chat.id, status)
    note = "از این به بعد فقط پیام‌های جدید فوروارد می‌شوند."
    if status == "unknown":
        note += (
            "\nتلگرام سطح دسترسی را نشان نداد. "
            "اگر پیام نرفت، Post Messages را برای ربات روشن کن."
        )
    return True, f"✅ کانال اضافه شد\n{title}\n{chat.id}\n{note}"


async def remove_target_channel(bot, raw):
    parsed = parse_target(raw)
    if parsed is None:
        return False, "آیدی نامعتبر است. عدد کانال، @username یا لینک t.me بفرست."

    channel_id = parsed
    if isinstance(parsed, str):
        try:
            chat = await bot.get_chat(parsed)
        except (BadRequest, Forbidden) as exc:
            logger.info("remove resolve failed for %s: %s", parsed, exc)
            return False, "این کانال پیدا نشد."
        channel_id = chat.id

    if remove_channel(channel_id):
        return True, f"✅ کانال {channel_id} حذف شد."
    return False, "این کانال در لیست نبود."


async def edit_text(query, text, reply_markup=None):
    try:
        await query.edit_message_text(text[:4000], reply_markup=reply_markup)
    except BadRequest as exc:
        lowered = str(exc).lower()
        if "message is not modified" in lowered:
            return
        if query.message is not None and (
            "message to edit not found" in lowered or "message can't be edited" in lowered
        ):
            await query.message.reply_text(text[:4000], reply_markup=reply_markup)
            return
        raise


async def admin_panel(update, context):
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    if not is_admin(user.id):
        await message.reply_text("دسترسی ندارید.")
        return
    await message.reply_text(panel_text(), reply_markup=panel_keyboard())


async def button_click(update, context):
    query = update.callback_query
    if query is None or query.from_user is None:
        return
    if not is_admin(query.from_user.id):
        await query.answer("دسترسی ندارید.", show_alert=True)
        return

    await query.answer()
    data = query.data or ""

    if data == "panel":
        context.user_data.pop("action", None)
        await edit_text(query, panel_text(), panel_keyboard())
    elif data == "list":
        await edit_text(query, format_channel_list(), back_keyboard())
    elif data == "status":
        await edit_text(query, status_text(context.bot_data), back_keyboard())
    elif data == "add":
        context.user_data["action"] = "add"
        await edit_text(query, ADD_PROMPT, back_keyboard())
    elif data == "remove":
        context.user_data["action"] = "remove"
        await edit_text(query, REMOVE_PROMPT, back_keyboard())


async def channel_input(update, context):
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None or not is_admin(user.id):
        return

    action = context.user_data.get("action")
    if action not in {"add", "remove"}:
        return

    raw = channel_ref_from_message(message)
    if raw is None:
        await message.reply_text(
            "پیام قابل خواندن نبود. آیدی یا لینک کانال را بفرست.",
            reply_markup=back_keyboard(),
        )
        return

    try:
        if action == "add":
            ok, text = await add_target_channel(
                context.bot,
                raw,
                get_last_source_message_id(),
            )
        else:
            ok, text = await remove_target_channel(context.bot, raw)
    except TelegramError as exc:
        logger.warning("channel action %s failed: %s", action, exc)
        ok, text = False, "ارتباط با تلگرام برقرار نشد. دوباره تلاش کن."

    if ok:
        context.user_data.pop("action", None)
        await message.reply_text(text, reply_markup=panel_keyboard())
    else:
        await message.reply_text(text, reply_markup=back_keyboard())
